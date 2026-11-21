import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from edgelab.devig import METHODS, devig
from edgelab.ledger import Ledger
from edgelab.model import consensus, fit_calibration
from edgelab.odds import american_to_decimal, decimal_to_american, ev, kelly, sigmoid, logit
from edgelab.simulator import Engine, apply_mappings
from edgelab.sources.base import Quote
from edgelab.sources.demo import DemoWorld
from edgelab.stats import bets_needed, summarize


class TestOdds(unittest.TestCase):
    def test_american(self):
        self.assertAlmostEqual(american_to_decimal(-110), 1.9091, 3)
        self.assertAlmostEqual(american_to_decimal(150), 2.5)
        self.assertAlmostEqual(decimal_to_american(2.5), 150)
        self.assertAlmostEqual(decimal_to_american(1.5), -200)

    def test_ev_kelly(self):
        self.assertAlmostEqual(ev(0.5, 2.1), 0.05)
        self.assertAlmostEqual(kelly(0.5, 2.1), 0.05 / 1.1)
        self.assertEqual(kelly(0.4, 2.0), 0.0)


class TestDevig(unittest.TestCase):
    def test_all_methods_sum_to_one(self):
        for prices in ([1.91, 1.91], [1.5, 2.6], [2.2, 3.4, 3.6], [1.05, 11.0]):
            for m in list(METHODS) + ["worst_case"]:
                p = devig(prices, m)
                self.assertTrue(all(0 < x < 1 for x in p), m)
                if m != "worst_case":
                    self.assertAlmostEqual(sum(p), 1, 6, m)

    def test_symmetric_line(self):
        for m in METHODS:
            self.assertAlmostEqual(devig([1.91, 1.91], m)[0], 0.5, 6)

    def test_power_shades_longshot(self):
        mult, powr = devig([1.2, 5.0], "multiplicative"), devig([1.2, 5.0], "power")
        self.assertLess(powr[1], mult[1])  # power puts more margin on the longshot


class TestModel(unittest.TestCase):
    def q(self, venue, o, price):
        return Quote("t", venue, "sports", "e1", "A @ B", "h2h", o, price, "2099-01-01T00:00:00Z")

    def test_soft_books_ignored_and_exclude_self(self):
        qs = [self.q("pinnacle", "A", 1.91), self.q("pinnacle", "B", 1.91),
              self.q("softbook", "A", 1.5), self.q("softbook", "B", 2.8)]
        fair, n = consensus(qs, "power")
        self.assertEqual(n, 1)
        self.assertAlmostEqual(fair["A"], 0.5, 6)
        self.assertIsNone(consensus(qs, "power", exclude_venue="pinnacle"))

    def test_calibration_recovers_slope(self):
        import random
        r = random.Random(3)
        pairs = []
        for _ in range(4000):
            p = sigmoid(r.gauss(0, 1.5))
            q = sigmoid(0.8 * logit(p))  # biased model: too timid
            pairs.append((q, 1 if r.random() < p else 0))
        fit = fit_calibration(pairs)
        self.assertAlmostEqual(fit["b"], 1 / 0.8, delta=0.1)

    def test_mapping_inverts(self):
        qs = [Quote("polymarket", "polymarket", "politics", "pm:9", "x", "binary", "Yes", 2.0, "2099-01-01", ref={"id": "9"})]
        out = apply_mappings(qs, [{"id": "q1", "legs": {"polymarket": "9"}, "invert": {"polymarket": True}}])
        self.assertEqual(out[0].group_id, "x:q1|binary")
        self.assertEqual(out[0].outcome, "No")
        self.assertEqual(out[0].ref["side"], "Yes")


class TestStats(unittest.TestCase):
    def test_bets_needed(self):
        self.assertGreater(bets_needed(0.02, 2.0), 5000)
        self.assertLess(bets_needed(0.10, 2.0), 500)

    def test_summary_empty(self):
        self.assertEqual(summarize([])["n_bets"], 0)


class TestEndToEnd(unittest.TestCase):
    def test_demo_finds_planted_edge_and_control_fails(self):
        with tempfile.TemporaryDirectory() as d:
            L = Ledger(os.path.join(d, "t.db"))
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            w = DemoWorld(start, seed=11)
            strats = [
                {"name": "edge", "domains": ["sports"], "devig": "power", "min_ev": 0.02, "max_ev": 0.25, "bankroll": 1000},
                {"name": "ctrl", "domains": ["sports"], "devig": "power", "min_ev": -0.03, "max_ev": 0.0,
                 "staking": "flat", "flat_stake": 10, "bankroll": 1000},
            ]
            eng = Engine(L, [w], strats, log=lambda *a: None)
            t = start
            while t < start + timedelta(days=8):
                eng.scan(t)
                t += timedelta(hours=4)
            res = {}
            for name in ("edge", "ctrl"):
                rows = L.q("SELECT * FROM bets WHERE strategy=?", (name,))
                self.assertGreater(len(rows), 20)
                true_ev = sum(w.true_prob(r["group_id"].split("|")[0][5:], r["outcome"]) * r["price"] - 1 for r in rows) / len(rows)
                clv = [r["clv"] for r in rows if r["clv"] is not None]
                res[name] = (true_ev, sum(clv) / len(clv))
            self.assertGreater(res["edge"][0], 0)      # planted edge is real
            self.assertGreater(res["edge"][1], 0)      # and CLV sees it
            self.assertLess(res["ctrl"][0], 0)         # negative control loses
            self.assertLess(res["ctrl"][1], 0)

    def test_preregistration_blocks_rule_changes(self):
        with tempfile.TemporaryDirectory() as d:
            L = Ledger(os.path.join(d, "t.db"))
            L.register({"name": "s", "min_ev": 0.02}, "t0")
            with self.assertRaises(ValueError):
                L.register({"name": "s", "min_ev": 0.01}, "t1")


if __name__ == "__main__":
    unittest.main()


class TestEvents(unittest.TestCase):
    def test_events_demo_inclusive_edge_and_venue_scorecard(self):
        from edgelab.analysis import tournament
        with tempfile.TemporaryDirectory() as d:
            L = Ledger(os.path.join(d, "t.db"))
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            w = DemoWorld(start, seed=5, sports_per_batch=0, pm_per_batch=3)
            dom = ["politics", "geopolitics", "economics"]
            strats = [
                {"name": "incl", "domains": dom, "exclude_self": False, "min_ev": 0.03, "min_sources": 2, "max_price": 20},
                {"name": "ctrl", "domains": dom, "exclude_self": False, "min_ev": -0.03, "max_ev": 0.0, "min_sources": 2,
                 "staking": "flat", "flat_stake": 10},
            ]
            eng = Engine(L, [w], strats, log=lambda *a: None)
            t = start
            while t < start + timedelta(days=24):
                eng.scan(t)
                t += timedelta(hours=6)
            tev = {}
            for name in ("incl", "ctrl"):
                rows = L.q("SELECT * FROM bets WHERE strategy=?", (name,))
                self.assertGreater(len(rows), 20)
                tev[name] = sum(w.true_prob(r["group_id"].split("|")[0][5:], r["outcome"]) * r["price"] - 1 for r in rows) / len(rows)
            self.assertGreater(tev["incl"], 0)
            self.assertLess(tev["ctrl"], 0)
            venues = tournament(L, venues=True)
            self.assertEqual(venues[0]["method"], "venue:demo_exchange_2")  # the planted accurate venue wins

    def test_apr_and_horizon_filters(self):
        from edgelab.model import find_edges, group_quotes
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def mk(venue, o, price, days):
            return Quote("x", venue, "politics", "e", "Q?", "binary", o, price,
                         (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        for days, expect in ((5, 1), (300, 0)):
            qs = [mk("polymarket", "Yes", 1 / 0.50, days), mk("polymarket", "No", 1 / 0.52, days),
                  mk("kalshi", "Yes", 1 / 0.44, days), mk("kalshi", "No", 1 / 0.58, days)]
            c = find_edges(group_quotes(qs), {"min_ev": 0.03, "max_days": 30, "min_apr": 0.5, "min_sources": 1}, now=now)
            self.assertEqual(len([x for x in c if x["venue"] == "kalshi" and x["outcome"] == "Yes"]), expect, days)
            if expect:
                self.assertAlmostEqual(c[0]["apr"], c[0]["ev"] * 365 / 5, 6)

    def test_retiring_does_not_break_preregistration(self):
        with tempfile.TemporaryDirectory() as d:
            L = Ledger(os.path.join(d, "t.db"))
            L.register({"name": "s", "min_ev": 0.02}, "t0")
            L.register({"name": "s", "min_ev": 0.02, "active": False, "retired": "why"}, "t1")

    def test_match_pairs_same_question(self):
        import json as _j
        from unittest import mock
        from edgelab import cli

        class Fake:
            def __init__(self, name, items):
                self.name, self.items = name, items

            def fetch(self, now):
                return [Quote(self.name, self.name, "politics", f"{self.name}:{i}", n, "binary", "Yes", 2.0,
                              "2026-12-31T00:00:00Z", ref={"id": i}) for i, n in self.items]
        srcs = [Fake("kalshi", [("K1", "Will the Fed cut rates in December 2026?"), ("K2", "Will it snow in Paris?")]),
                Fake("polymarket", [("P9", "Fed rate cut in December 2026?"), ("P8", "Fed cut in December 2025?")])]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "build_sources", return_value=srcs):
            out = os.path.join(d, "c.json")
            cli.main(["--config", "/dev/null", "match", "--out", out])
            pairs = _j.load(open(out))
            self.assertEqual(len(pairs), 1)
            self.assertEqual(pairs[0]["legs"], {"kalshi": "K1", "polymarket": "P9"})


class TestForwardMechanics(unittest.TestCase):
    def _qs(self, now, size):
        def mk(venue, o, cost):
            return Quote("x", venue, "geopolitics", "e", "Q?", "binary", o, 1 / cost,
                         (now + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ"), size=size)
        return [mk("polymarket", "Yes", 0.50), mk("polymarket", "No", 0.52), mk("kalshi", "Yes", 0.44), mk("kalshi", "No", 0.58)]

    def test_depth_and_cash_caps(self):
        class Src:
            name, min_interval = "x", 0

            def __init__(s, qs):
                s.qs = qs

            def fetch(s, now):
                return s.qs

            def results(s, p, now):
                return {}
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        strat = {"name": "s", "min_ev": 0.03, "min_sources": 1, "kelly_fraction": 1.0, "max_stake_pct": 0.5,
                 "bankroll": 1000, "max_depth_frac": 0.5}
        with tempfile.TemporaryDirectory() as d:
            L = Ledger(os.path.join(d, "t.db"))
            Engine(L, [Src(self._qs(now, size=8.0))], [strat], log=lambda *a: None).scan(now)
            stakes = [r["stake"] for r in L.q("SELECT stake FROM bets")]
            self.assertTrue(stakes and all(s <= 4.0 for s in stakes), stakes)  # 50% of $8 at the top of book
            self.assertLessEqual(L.open_exposure("s"), 1000)

    def test_throttle_persists_in_ledger(self):
        class Src:
            name, min_interval, calls = "metered", 720, 0

            def fetch(s, now):
                s.calls += 1
                return []
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            src = Src()
            for h in (0, 1, 13):  # new Engine each time, like separate cron runs
                Engine(Ledger(path), [src], [], log=lambda *a: None).scan(now + timedelta(hours=h))
            self.assertEqual(src.calls, 2)

    def test_geopolitics_classifier(self):
        from edgelab.sources.base import classify_domain as c
        self.assertEqual(c("Who will succeed Netanyahu as Prime Minister of Israel?"), "geopolitics")
        self.assertEqual(c("Next Japanese general election winner?"), "geopolitics")
        self.assertEqual(c("Los Angeles Mayor winner?"), "politics")
        self.assertEqual(c("Will Elizabeth Warren run?"), "other")
        self.assertEqual(c("FIFA World Cup winner"), "sports")

    def test_forward_strategies_load_and_hash(self):
        from edgelab.cli import load_strategies
        from edgelab.ledger import config_hash
        names = set()
        for f in ("geopolitics", "economics", "sports"):
            ss = load_strategies(f"forwards/{f}/strategies")
            self.assertEqual(len(ss), 3)
            self.assertEqual(sum(1 for s in ss if s.get("control")), 1)
            for s in ss:
                config_hash(s)
                names.add(s["name"])
        self.assertEqual(len(names), 9)
