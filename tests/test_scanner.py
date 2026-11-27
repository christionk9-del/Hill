import unittest
from datetime import datetime, timedelta, timezone

from ev_scanner import odds_math as om
from ev_scanner.config import load_config
from ev_scanner.promo_feed import detect_promos
from ev_scanner.scanner import (evaluate_promos, scan_dfs, scan_events,
                                scan_kalshi_direct)
from ev_scanner.sources import kalshi_team_matches

NOW = datetime(2026, 11, 27, 12, 30, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=8)).isoformat().replace("+00:00", "Z")


def book(key, markets):
    return {"key": key, "markets": [{"key": k, "outcomes": v} for k, v in markets.items()]}


def h2h(home, away):
    return [{"name": "Boston Celtics", "price": home}, {"name": "New York Knicks", "price": away}]


def event(bookmakers, sport="basketball_nba"):
    return {"id": "e1", "sport_key": sport, "commence_time": START,
            "home_team": "Boston Celtics", "away_team": "New York Knicks",
            "bookmakers": bookmakers}


class MathTests(unittest.TestCase):
    def test_conversions(self):
        self.assertAlmostEqual(om.american_to_decimal(150), 2.5)
        self.assertAlmostEqual(om.american_to_decimal(-200), 1.5)
        self.assertEqual(om.decimal_to_american(2.5), 150)
        self.assertEqual(om.decimal_to_american(1.5), -200)

    def test_devig_sums_to_one(self):
        for f in (om.devig_power, om.devig_multiplicative):
            self.assertAlmostEqual(sum(f([1.91, 1.91])), 1)
            self.assertAlmostEqual(f([1.91, 1.91])[0], 0.5)
            self.assertAlmostEqual(sum(f([1.4, 3.2])), 1)

    def test_boost(self):
        self.assertAlmostEqual(om.boosted_decimal(3.0, 50), 4.0)

    def test_kalshi_fee(self):
        self.assertEqual(om.kalshi_fee_per_contract(0.5), 0.02)  # 0.0175 -> 0.02
        self.assertLess(om.kalshi_effective_decimal(2.0), 2.0)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config("/nonexistent")

    def test_finds_20pct_ev_vs_pinnacle(self):
        e = event([book("pinnacle", {"h2h": h2h(1.95, 1.95)}),
                   book("draftkings", {"h2h": h2h(1.80, 2.45)})])
        opps, offers = scan_events([e], "basketball_nba", self.cfg, [], NOW)
        self.assertEqual(len(opps), 1)
        o = opps[0]
        self.assertEqual((o.book, o.selection), ("draftkings", "New York Knicks"))
        self.assertAlmostEqual(o.ev, 0.225, places=3)
        self.assertGreaterEqual(o.ev, self.cfg.ev_threshold)

    def test_totals_and_spreads_grouping(self):
        e = event([
            book("pinnacle", {"totals": [{"name": "Over", "price": 1.91, "point": 220.5},
                                         {"name": "Under", "price": 1.91, "point": 220.5}],
                              "spreads": [{"name": "Boston Celtics", "price": 1.91, "point": -4.5},
                                          {"name": "New York Knicks", "price": 1.91, "point": 4.5}]}),
            book("fanduel", {"totals": [{"name": "Over", "price": 2.30, "point": 220.5},
                                        {"name": "Under", "price": 1.60, "point": 220.5}],
                             "spreads": [{"name": "New York Knicks", "price": 2.0, "point": 4.5}]}),
        ])
        opps, _ = scan_events([e], "basketball_nba", self.cfg, [], NOW)
        sels = {o.selection: o.ev for o in opps}
        self.assertAlmostEqual(sels["Over 220.5"], 0.15, places=3)
        self.assertNotIn("New York Knicks +4.5", sels)  # 2.0 vs fair 0.5 = 0% EV
        _, offers = scan_events([e], "basketball_nba", self.cfg, [], NOW)
        spread = [o for o in offers if o["market"] == "spreads"][0]
        self.assertAlmostEqual(spread["p"], 0.5)

    def test_out_of_window_ignored(self):
        e = event([book("pinnacle", {"h2h": h2h(1.95, 1.95)}),
                   book("draftkings", {"h2h": h2h(1.80, 2.45)})])
        e["commence_time"] = (NOW - timedelta(hours=1)).isoformat()
        self.assertEqual(scan_events([e], "x", self.cfg, [], NOW)[0], [])

    def test_profit_boost_stacks_to_alert(self):
        e = event([book("pinnacle", {"h2h": h2h(1.95, 1.95)}),
                   book("draftkings", {"h2h": h2h(1.90, 1.95)})])
        _, offers = scan_events([e], "basketball_nba", self.cfg, [], NOW)
        promo = {"name": "50% boost", "type": "profit_boost", "book": "draftkings", "boost_pct": 50}
        best = evaluate_promos([promo], offers, self.cfg)[0]
        # 1.95 boosted -> 2.425, fair 0.5 -> +21.25%
        self.assertAlmostEqual(best.ev, 0.2125, places=3)
        self.assertEqual(best.kind, "boost")

    def test_bonus_bet_conversion_prefers_longshots(self):
        e = event([book("pinnacle", {"h2h": h2h(1.30, 4.00)}),
                   book("fanduel", {"h2h": h2h(1.28, 4.20)})])
        _, offers = scan_events([e], "basketball_nba", self.cfg, [], NOW)
        best = evaluate_promos([{"type": "bonus_bet", "book": "fanduel"}], offers, self.cfg)[0]
        self.assertEqual(best.selection, "New York Knicks")
        self.assertGreater(best.ev, 0.7)

    def test_dfs_leg_vs_breakeven(self):
        props = [{"name": "Over", "description": "Jayson Tatum", "price": 1.65, "point": 26.5},
                 {"name": "Under", "description": "Jayson Tatum", "price": 2.30, "point": 26.5}]
        e = event([book("pinnacle", {"player_points": props}),
                   book("prizepicks", {"player_points": [
                       {"name": "Over", "description": "Jayson Tatum", "price": 1.0, "point": 26.5}]})])
        opps = scan_dfs([e], "basketball_nba", self.cfg, [], NOW)
        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0].book, "prizepicks")
        self.assertGreater(opps[0].fair_prob, 0.542)

    def test_kalshi_direct(self):
        e = event([book("pinnacle", {"h2h": h2h(1.95, 1.95)})])
        kev = [{"event_ticker": "KXNBAGAME-X", "series": "KXNBAGAME",
                "expires": (NOW + timedelta(hours=12)).isoformat(),
                "sides": [{"team": "Boston", "ticker": "A", "yes_ask": 0.40, "yes_bid": 0.38},
                          {"team": "New York", "ticker": "B", "yes_ask": 0.62, "yes_bid": 0.60}]}]
        opps = scan_kalshi_direct(kev, [e], self.cfg, NOW)
        boston = [o for o in opps if o.kind == "kalshi" and "Boston" in o.selection]
        self.assertEqual(len(boston), 1)
        self.assertGreater(boston[0].ev, 0.18)  # 0.5 / 0.42 - 1 after fee


class MiscTests(unittest.TestCase):
    def test_team_matching(self):
        self.assertTrue(kalshi_team_matches("Sacramento", "Sacramento Kings"))
        self.assertTrue(kalshi_team_matches("Los Angeles L", "Los Angeles Lakers"))
        self.assertFalse(kalshi_team_matches("Los Angeles L", "Los Angeles Clippers"))
        self.assertTrue(kalshi_team_matches("New York J", "New York Jets"))
        self.assertFalse(kalshi_team_matches("New York J", "New York Giants"))

    def test_promo_detection(self):
        txt = "DraftKings: 50% Profit Boost on any NBA bet today!\nPrizePicks Taco Tuesday discounted line on Tatum"
        found = detect_promos(txt, "test")
        kinds = {(p["type"], p["book"]) for p in found}
        self.assertIn(("profit_boost", "draftkings"), kinds)
        self.assertIn(("dfs_discount", "prizepicks"), kinds)
        self.assertEqual(next(p for p in found if p["type"] == "profit_boost")["boost_pct"], 50)


if __name__ == "__main__":
    unittest.main()
