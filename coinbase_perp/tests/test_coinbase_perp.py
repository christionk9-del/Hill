"""python -m pytest coinbase_perp/tests -q"""
import base64
import json
import os
import sys
from datetime import date

import jwt
import pandas as pd
import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import donchian_lab as L                       # noqa: E402
from live.engine import DonchianEngine, Params  # noqa: E402
from coinbase_perp.bot import PRESETS           # noqa: E402
from coinbase_perp import coinbase_api as cb    # noqa: E402
from coinbase_perp.sim_exchange import load, run_day  # noqa: E402


def engine_backtest_mode(path, p):
    """Replay 5-minute bars through the live engine with backtest fills (as live/replay_test.py)."""
    d = pd.read_csv(path)
    d["ts"] = pd.to_datetime(d.ts, utc=True).dt.tz_convert("America/New_York")
    eng = DonchianEngine(Params(**p))
    out, cur = {}, None
    for day, g in d.groupby(d.ts.dt.date, sort=True):
        ok = len(g) == 78
        for k, b in enumerate(g.itertuples()):
            eng.begin_bar(day, k, b.open, ok)
            if eng.pos is None:
                if eng.entry_levels() is not None:
                    up, dn = b.high > eng.hi, b.low < eng.lo
                    if up != dn:
                        ev = eng.enter(1 if up else -1, max(eng.hi, b.open) if up else min(eng.lo, b.open))
                        cur = (day, ev.price, ev.side)
            else:
                ev = eng.on_price(b.high, b.low)
                if ev and ev.kind == "exit":
                    out.setdefault(cur[0], []).append((ev.price - cur[1]) * cur[2])
            for e in eng.end_bar(b.open, b.high, b.low, b.close, entry_bar_close_rule=True):
                if e.kind == "exit":
                    out.setdefault(cur[0], []).append((e.price - cur[1]) * cur[2])
    return out


@pytest.mark.parametrize("sym", ["BTC", "ETH"])
@pytest.mark.parametrize("preset", list(PRESETS))
def test_engine_matches_backtest(sym, preset):
    p = {k: v for k, v in PRESETS[preset].items() if k not in ("first_bar", "atr_len")}
    p["tick"] = 0.0
    days = L.prepare(f"data/5min/{sym}.csv.gz", usd=1.0, cost_pts=0.0)
    lab = {k: v for k, v in p.items() if k != "tick"}
    ref = {x["date"]: [r[0] for r in L.dx(x, **lab)] for x in days}
    mine = engine_backtest_mode(f"data/5min/{sym}.csv.gz", p)
    differ = [k for k in ref if [round(v, 4) for v in ref[k]] != [round(v, 4) for v in mine.get(k, [])]]
    assert len(differ) == 0, differ[:5]


@pytest.fixture(scope="module")
def btc():
    return load("BTC")


@pytest.mark.parametrize("preset", list(PRESETS))
def test_bot_on_sim_leaves_nothing_open(btc, preset):
    five, m = btc
    days = sorted(set(m.index.date))[20:30]
    for d in days:
        r = run_day("BTC", d, five, m, preset=preset)
        assert not r["leftover"], d
        assert r["fills"] == 2 * r["model_trades"] or r["fills"] >= r["model_trades"], r


def test_bot_survives_api_failures(btc):
    five, m = btc
    for d in sorted(set(m.index.date))[40:46]:
        r = run_day("BTC", d, five, m, fail_rate=0.05)
        assert not r["leftover"], d


def test_market_entry_mode(btc):
    five, m = btc
    for d in sorted(set(m.index.date))[50:54]:
        r = run_day("BTC", d, five, m, entry_mode="market")
        assert not r["leftover"], d


def test_jwt_es256_and_ed25519():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
    s = cb.Signer("organizations/x/apiKeys/y", pem.replace("\n", "\\n"))
    t = s.token("GET", "/api/v3/brokerage/cfm/positions")
    claims = jwt.decode(t, k.public_key(), algorithms=["ES256"])
    assert claims["uri"] == "GET api.coinbase.com/api/v3/brokerage/cfm/positions" and claims["iss"] == "cdp"
    assert jwt.get_unverified_header(t)["kid"] == "organizations/x/apiKeys/y"
    e = ed25519.Ed25519PrivateKey.generate()
    raw = e.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    s2 = cb.Signer("key-id", base64.b64encode(raw + e.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode())
    jwt.decode(s2.token("POST", "/api/v3/brokerage/orders"), e.public_key(), algorithms=["EdDSA"])


def test_order_bodies(monkeypatch):
    c = cb.Coinbase()
    sent = []
    monkeypatch.setattr(c, "_req", lambda m, p, params=None, body=None, auth=True, retries=3:
                        sent.append((m, p, body)) or {"success": True, "success_response": {"order_id": "abc"}})
    assert c.stop_order("BIP-20DEC30-CDE", -1, 3, 85000.0, 84575.0, tag="dv-sl") == "abc"
    m, p, b = sent[-1]
    assert p == "/api/v3/brokerage/orders" and b["side"] == "SELL" and b["client_order_id"].startswith("dv-sl-")
    sl = b["order_configuration"]["stop_limit_stop_limit_gtc"]
    assert sl == {"base_size": "3", "stop_price": "85000", "limit_price": "84575", "stop_direction": "STOP_DIRECTION_STOP_DOWN"}
    c.market("BIP-20DEC30-CDE", 1, 2)
    assert sent[-1][2]["order_configuration"] == {"market_market_ioc": {"base_size": "2"}}


def test_dry_run_sends_no_orders(btc, monkeypatch):
    from coinbase_perp import sim_exchange as S
    five, m = btc
    for name in ("market", "stop_order", "edit_stop", "cancel", "close_position"):
        monkeypatch.setattr(S.SimCoinbase, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("order sent in dry run")))
    d = sorted(set(m.index.date))[60]
    mday = m[m.index.date == d]
    clock = S.SimClock(pd.Timestamp(f"{d} 09:24", tz=S.NY).to_pydatetime())
    api = S.SimCoinbase("BTC-PERP", five[five.index.date < d].tail(800), mday, clock, 0.01)
    clock.broker = api
    from coinbase_perp.bot import Bot, load_config
    cfg = load_config("coinbase_perp/config.example.json")
    cfg.update(markets=[dict(product_id="BTC-PERP")], stop_file="/nonexistent", log_dir=S.SCRATCH)
    assert cfg["dry_run"] is True
    bot = Bot(cfg, api, clock)
    bot.start()
    bot.run(until=pd.Timestamp(f"{d} 16:06", tz=S.NY).to_pydatetime())
    assert api.fills == []
