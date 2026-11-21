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
