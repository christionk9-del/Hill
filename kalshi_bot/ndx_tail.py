"""Kalshi tail version of the NDX dip-put: on each signal, buy NO on the 4pm KXNASDAQ100U
strike `d` below the index (pays only if NDX closes below it, like an out-of-the-money put).
Entry price = 1 - YES bid at the check minute from Kalshi's 1-minute candles (the real NO ask)."""
import time
from datetime import timedelta

import requests

B = "https://api.elections.kalshi.com/trade-api/v2"
FEE = 0.07
S = requests.Session()


def get(path, **p):
    for i in range(10):
        try:
            r = S.get(B + path, params=p, timeout=30)
        except requests.RequestException:
            time.sleep(2); continue
        if r.status_code == 429:
            time.sleep(1.5 * (i + 1)); continue
        return r.json() if r.status_code == 200 else None
    return None


def event_markets(ev):
    for path in ("/markets", "/historical/markets"):
        d = get(path, event_ticker=ev, limit=1000)
        if d and d.get("markets"):
            return d["markets"], path
    return [], None


def candles(ticker, start, end, hist):
    path = (f"/historical/markets/{ticker}/candlesticks" if hist else
            f"/series/KXNASDAQ100U/markets/{ticker}/candlesticks")
    d = get(path, start_ts=int(start), end_ts=int(end), period_interval=1)
    if d is None:
        alt = (f"/series/KXNASDAQ100U/markets/{ticker}/candlesticks" if hist else
               f"/historical/markets/{ticker}/candlesticks")
        d = get(alt, start_ts=int(start), end_ts=int(end), period_interval=1)
    return (d or {}).get("candlesticks", [])


def dollars(x):
    if x is None:
        return None
    v = float(x)
    return v / 100 if v > 1.0001 else v


def tail_entries(sig, dists=(0.0, 0.005, 0.01, 0.015), wait_min=10):
    ev = f"KXNASDAQ100U-{sig['date']:%y%b%d}".upper() + "H1600"
    ms, path = event_markets(ev)
    ms = [m for m in ms if m.get("strike_type") == "greater_or_equal" and m.get("floor_strike") is not None
          and m.get("result") in ("yes", "no")]
    out = []
    if not ms:
        return [{"dist": d, "status": "no_event"} for d in dists]
    t0 = sig["check"].timestamp()
    for dd in dists:
        target = sig["spot"] * (1 - dd)
        m = min(ms, key=lambda m: abs(float(m["floor_strike"]) - target))
        cs = [c for c in candles(m["ticker"], t0 - 60, t0 + wait_min * 60, path == "/historical/markets")
              if c["end_period_ts"] > t0]
        ask = None
        for c in sorted(cs, key=lambda c: c["end_period_ts"]):
            yb = (c.get("yes_bid") or {})
            b = dollars(yb.get("close_dollars", yb.get("close")))
            if b is not None and 0 < b < 1:
                ask = round(1 - b, 4)
                break
        if ask is None or not 0.02 <= ask <= 0.98:
            out.append({"dist": dd, "status": "no_price", "ticker": m["ticker"]})
            continue
        out.append({"dist": dd, "status": "entry", "ticker": m["ticker"], "strike": float(m["floor_strike"]),
                    "no_ask": ask, "cost": ask * (1 + FEE * (1 - ask)), "win": int(m["result"] == "no")})
    return out
