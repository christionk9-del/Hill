from dataclasses import replace
from datetime import datetime, timedelta, timezone

from kalshi_bot.config import CONFIG
from kalshi_bot.combo_builder import (build_combo, execute, leg_pool, plan_combos,
                                      quote_yes_price, settle_paper)
from kalshi_bot.ledger import Ledger

CFG = replace(CONFIG, rfq_wait_seconds=0, min_bucket_samples=100)
CLOSE = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat().replace("+00:00", "Z")


def table(win_rate_70s):
    return {"ALL|66-75": {"n": 5000, "win_rate": win_rate_70s, "win_rate_lower90": win_rate_70s - 0.01,
                          "avg_price_c": 70, "return_per_dollar": win_rate_70s / 0.70}}


def events(games=6, props_per_game=3):
    evs = []
    for g in range(games):
        for p in range(props_per_game):
            et = f"KXPROP{p}-G{g}"
            evs.append({"event_ticker": et, "category": "Sports", "series_ticker": f"KXPROP{p}",
                        "markets": [{"ticker": f"{et}-M", "status": "active", "close_time": CLOSE,
                                     "yes_ask": 70, "yes_bid": 68}]})
    return evs


class FakeClient:
    def __init__(self, evs, quote_price):
        self.evs, self.quote_price, self.accepted, self.cancelled = evs, quote_price, [], []

    def events(self, status="open"):
        return iter(self.evs)

    def collections(self):
        return iter([{"collection_ticker": "KXMVE-NBA",
                      "associated_events": [{"ticker": e["event_ticker"]} for e in self.evs]}])

    def create_combo_market(self, coll, legs):
        self.legs = legs
        return "KXMVE-COMBO-1"

    def create_rfq(self, ticker, contracts):
        self.contracts = contracts
        return "rfq1"

    def rfq_quotes(self, rfq_id):
        return [{"id": "q1", "yes_bid_dollars": str(self.quote_price)}]

    def accept_quote(self, rfq_id, qid, side):
        self.accepted.append(qid)

    def cancel_rfq(self, rfq_id):
        self.cancelled.append(rfq_id)


def test_one_leg_per_game_and_needs_edge():
    evs = events()
    coll = FakeClient(evs, 0).collections().__next__()
    idx = {e["event_ticker"]: e for e in evs}
    assert build_combo(coll, leg_pool(coll, idx, table(0.70), CFG), CFG) is None  # fair legs: no combo
    plan = build_combo(coll, leg_pool(coll, idx, table(0.76), CFG), CFG)
    assert plan and len(plan.legs) == 6                                           # 6 games -> 6 legs
    assert len({l.game for l in plan.legs}) == 6


def test_quote_price_is_conservative():
    assert quote_yes_price({"yes_bid_dollars": "0.010", "no_bid_dollars": "0.995"}) == 0.010
    assert abs(quote_yes_price({"yes_bid_dollars": "0.004", "no_bid_dollars": "0.990"}) - 0.010) < 1e-12


def test_live_accepts_good_quote_and_rejects_bad(tmp_path):
    led = Ledger(str(tmp_path / "a.db"), CFG)
    good = FakeClient(events(), quote_price=0.70 ** 6 * 1.05)
    plan = plan_combos(good, table(0.76), CFG)[0]
    assert execute(plan, good, led, "live", CFG, log=lambda *_: None)
    assert good.accepted == ["q1"]

    led2 = Ledger(str(tmp_path / "b.db"), CFG)
    bad = FakeClient(events(), quote_price=0.70 ** 6 * 1.60)   # 60% market-maker markup
    assert not execute(plan, bad, led2, "live", CFG, log=lambda *_: None)
    assert bad.accepted == [] and bad.cancelled == ["rfq1"]


def test_quote_only_never_accepts(tmp_path):
    led = Ledger(str(tmp_path / "c.db"), CFG)
    c = FakeClient(events(), quote_price=0.70 ** 6)
    plan = plan_combos(c, table(0.76), CFG)[0]
    assert not execute(plan, c, led, "quote-only", CFG, log=lambda *_: None)
    assert c.accepted == [] and c.cancelled == ["rfq1"]


def test_budget_caps_combo_spend(tmp_path):
    led = Ledger(str(tmp_path / "d.db"), CFG)
    c = FakeClient(events(), quote_price=0.70 ** 6)
    plan = plan_combos(c, table(0.80), CFG)[0]
    bought = sum(execute(plan, c, led, "paper", CFG, log=lambda *_: None) for _ in range(5))
    assert bought == 1                                     # $5 stake uses the whole $5 day
    assert led.spent_since(led._today(), "paper")[0] <= CFG.daily_budget * 100


def test_settle_paper():
    import json
    legs = json.dumps([{"market_ticker": "A", "side": "yes"}, {"market_ticker": "B", "side": "no"}])
    res = {"A": {"result": "yes"}, "B": {"result": "no"}}
    assert settle_paper(legs, lambda t: res[t]) == "yes"
    res["B"] = {"result": "yes"}
    assert settle_paper(legs, lambda t: res[t]) == "no"
    res["B"] = {}
    assert settle_paper(legs, lambda t: res[t]) is None
