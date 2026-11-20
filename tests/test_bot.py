import base64
import os
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_bot import calibration
from kalshi_bot.client import KalshiClient, cents
from kalshi_bot.config import CONFIG
from kalshi_bot.fees import breakeven_prob, max_contracts_for_budget, taker_fee_cents, total_cost_cents
from kalshi_bot.ledger import Ledger
from kalshi_bot.simulate import simulate_week
from kalshi_bot.strategy import Candidate, find_candidates


def test_fees():
    assert taker_fee_cents(1, 50) == 2          # 0.07*.25 = 1.75c -> 2c
    assert taker_fee_cents(100, 5) == 34        # 0.07*100*.05*.95 = 33.25c -> 34c
    assert taker_fee_cents(1, 3) == 1           # min-cent rounding: 33% fee on a 3c contract
    n = max_contracts_for_budget(100, 5)
    assert total_cost_cents(n, 5) <= 100 < total_cost_cents(n + 1, 5)
    assert breakeven_prob(1, 3) == 0.04


def test_cents_both_formats():
    assert cents({"yes_ask": 7}, "yes_ask") == 7
    assert cents({"yes_ask_dollars": "0.0700"}, "yes_ask") == 7
    assert cents({}, "yes_ask") is None


def test_signature_verifies():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with tempfile.NamedTemporaryFile("wb", delete=False, suffix=".pem") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))
    cfg = replace(CONFIG, key_id="abc", private_key_path=fh.name)
    h = KalshiClient(cfg)._headers("GET", "/portfolio/balance")
    msg = (h["KALSHI-ACCESS-TIMESTAMP"] + "GET/trade-api/v2/portfolio/balance").encode()
    key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), msg,
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                        salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    os.unlink(fh.name)


def _table(win_rate, n=5000, bucket="3-5", price=4.0):
    return {f"ALL|{bucket}": {"n": n, "win_rate": win_rate, "win_rate_lower90": win_rate * 0.9,
                              "avg_price_c": price, "return_per_dollar": win_rate * 100 / price}}


def _events(now):
    close = (now + timedelta(hours=10)).isoformat().replace("+00:00", "Z")
    return [{"event_ticker": "E1", "category": "Sports", "markets": [
        {"ticker": "E1-A", "status": "active", "volume_24h": 1000, "close_time": close,
         "yes_ask": 4, "yes_bid": 3},
        {"ticker": "E1-B", "status": "active", "volume_24h": 1000, "close_time": close,
         "yes_ask": 5, "yes_bid": 4},
    ]}]


def test_no_bets_when_market_is_efficient_or_biased():
    now = datetime.now(timezone.utc)
    assert find_candidates(_events(now), _table(0.03), now) == []   # longshot bias: no edge
    assert find_candidates(_events(now), _table(0.045), now) == []  # fair: fees eat it


def test_bets_when_edge_and_one_per_event():
    now = datetime.now(timezone.utc)
    cands = find_candidates(_events(now), _table(0.12), now)
    assert len(cands) == 1 and cands[0].edge_c > 0


def test_calibration_summary():
    obs = [{"cat": "X", "side": "yes", "price": 4, "won": i < 10} for i in range(100)]
    t = calibration.summarize(obs)
    assert t["ALL|3-5"]["n"] == 100 and abs(t["ALL|3-5"]["win_rate"] - 0.10) < 1e-9


def test_ledger_enforces_budgets(tmp_path):
    led = Ledger(str(tmp_path / "db.sqlite"))
    c = Candidate("T", "E", "c", "yes", 5, 18, 0.1, 0.055, 5)
    spent = 0
    while led.remaining_c("paper") >= c.cost_c:
        led.record(replace(c, event_ticker=f"E{spent}"), "paper")
        spent += 1
    assert spent <= CONFIG.max_bets_per_day
    assert led.spent_since(led._today(), "paper")[0] <= CONFIG.daily_budget * 100
    assert led.settle("T", "yes") == spent


def test_diversifying_kills_10x():
    one = simulate_week(5, 0.05, 1, trials=20000)
    many = simulate_week(5, 0.05, 35, trials=2000)
    assert many["p_reach_target"] < one["p_reach_target"] <= one["markov_upper_bound"] + 0.01


def test_combo_math():
    from kalshi_bot import combo
    lp = combo.per_leg_price(9.86, 1562.5, 16)
    assert 0.72 < lp < 0.74
    assert abs(combo.uniform_edge_ev(16, lp, 0.0) - 1.0) < 1e-9
    assert combo.uniform_edge_ev(16, lp, -0.03) < 0.55   # small per-leg vig halves EV
    assert combo.uniform_edge_ev(16, lp, 0.03) > 1.8     # small per-leg edge nearly doubles it


def test_updown_fair_value():
    from kalshi_bot.updown import fair_up
    assert abs(fair_up(100, 100, 10, 0.001) - 0.5) < 1e-9          # at the strike: coin flip
    assert fair_up(100.5, 100, 10, 0.001) > 0.9                     # 0.5% above, low vol
    assert fair_up(100.5, 100, 10, 0.01) < 0.6                      # same gap, high vol
    assert fair_up(99, 100, 0, 0.001) == 0.0


def test_forward_strategies_and_fee():
    import time
    from kalshi_bot import forward as fw
    assert fw.taker_fee(2, 0.45) == 0.04
    now = time.time()
    class F:  # fake feed: BTC well above strike -> YES underpriced at 40c
        spot = {"BTC": (101.0, now)}; sigma = {"BTC": 0.001}
    close = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 600))
    m = {"close_time": close, "floor_strike": 100.0, "yes_ask_dollars": "0.40", "no_ask_dollars": "0.62"}
    got = {(s, side) for s, side, _ in fw.strategies(m, "KXBTC15M", now, F)}
    assert ("model_edge_10", "yes") in got and not any(side == "no" and s.startswith("model") for s, side in got)
    m2 = dict(m, close_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 150)),
              yes_ask_dollars="0.92", no_ask_dollars="0.09")
    got2 = {(s, side) for s, side, _ in fw.strategies(m2, "KXINX15M", now, F)}
    assert ("late_favorite", "yes") in got2 and ("longshot_10c", "no") in got2
