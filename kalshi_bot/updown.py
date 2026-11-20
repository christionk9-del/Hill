"""Up/Down markets (KXBTC15M, KXETH15M, ...) are binary (digital) options:
YES pays $1 if the reference price at close >= the target (price at window open).

Fair value (driftless lognormal):  P(up) = N( ln(S/K) / (sigma * sqrt(tau)) )
  S = current spot, K = target, tau = minutes left, sigma = per-minute vol
  from trailing 1-minute returns. Spot from Coinbase; Kalshi settles on CF
  Benchmarks' BRTI, which tracks Coinbase closely but not exactly.

Backtest: every real taker trade in the public dataset is re-priced with the
model at the minute it happened, then scored by the market's actual result.
"""
import math
import os
import time
from datetime import datetime, timedelta, timezone

import requests

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com/products/{}/candles"
FEE = 0.07


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def fair_up(spot, strike, minutes_left, sigma_per_min):
    if minutes_left <= 0:
        return 1.0 if spot >= strike else 0.0
    if sigma_per_min <= 0:
        return 0.5
    return norm_cdf(math.log(spot / strike) / (sigma_per_min * math.sqrt(minutes_left)))


# ---------------- data ----------------
def fetch_strikes(series="KXBTC15M", since=None, session=None):
    """{ticker: (floor_strike, close_time, result)} from Kalshi's historical markets."""
    s, out, cursor = session or requests.Session(), {}, None
    while True:
        p = {"series_ticker": series, "limit": 1000, **({"cursor": cursor} if cursor else {})}
        d = s.get(f"{KALSHI}/historical/markets", params=p, timeout=30).json()
        for m in d.get("markets", []):
            ct = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            if m.get("floor_strike") is not None and m.get("result") in ("yes", "no"):
                out[m["ticker"]] = (float(m["floor_strike"]), ct, m["result"])
        cursor = d.get("cursor")
        oldest = min((v[1] for v in out.values()), default=None)
        if not cursor or (since and oldest and oldest < since):
            return out


def fetch_strikes_for(tickers, batch=100, session=None):
    """Same as fetch_strikes but for explicit tickers (works for any age)."""
    s, out, tickers = session or requests.Session(), {}, list(tickers)
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        for attempt in range(6):
            r = s.get(f"{KALSHI}/historical/markets", params={"tickers": ",".join(chunk), "limit": batch}, timeout=30)
            if r.status_code != 429:
                break
            time.sleep(2 ** attempt)
        r.raise_for_status()
        for m in r.json().get("markets", []):
            if m.get("floor_strike") is not None and m.get("result") in ("yes", "no"):
                out[m["ticker"]] = (float(m["floor_strike"]),
                                    datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")), m["result"])
        time.sleep(0.3)
    return out


def fetch_minutes(product, start, end, session=None):
    """{unix_minute: close} 1-minute candles from Coinbase (300 per call)."""
    s, out, t = session or requests.Session(), {}, start
    while t < end:
        t2 = min(t + timedelta(minutes=300), end)
        for _ in range(5):
            r = s.get(COINBASE.format(product), timeout=20, params={
                "granularity": 60, "start": t.isoformat(), "end": t2.isoformat()})
            if r.status_code == 200:
                break
            time.sleep(1)
        for ts, lo, hi, op, cl, vol in r.json():
            out[int(ts)] = float(cl)
        t = t2
        time.sleep(0.12)  # stay under Coinbase public rate limit
    return out


def trailing_sigma(closes, minute, window=60):
    rets, prev = [], closes.get(minute - window * 60)
    for k in range(window - 1, -1, -1):
        cur = closes.get(minute - k * 60)
        if prev and cur:
            rets.append(math.log(cur / prev))
        prev = cur if cur else prev
    if len(rets) < window // 2:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))


# ---------------- backtest ----------------
def score_trades(trades, strikes, closes, min_minutes_left=1.0):
    """trades: iterable of (ticker, created_time, yes_price_c, no_price_c, taker_side, count).
    Yields dicts with model fair value for the side the taker bought."""
    for ticker, ts, yp, np_, side, count in trades:
        k = strikes.get(ticker)
        if not k:
            continue
        strike, close, result = k
        minute = int(ts.timestamp()) // 60 * 60 - 60       # last COMPLETED candle: no look-ahead
        spot = closes.get(minute)
        sig = trailing_sigma(closes, minute)
        tau = (close - ts).total_seconds() / 60
        if not spot or not sig or tau < min_minutes_left or tau > 15:
            continue
        f_up = fair_up(spot, strike, tau, sig)
        px = (yp if side == "yes" else np_) / 100
        if not 0.01 <= px <= 0.99:
            continue
        fair = f_up if side == "yes" else 1 - f_up
        yield {"ts": ts, "side": side, "px": px, "fair": fair, "tau": tau,
               "edge": fair - px * (1 + FEE * (1 - px)), "win": int(result == side), "count": count}


def report(rows, thresholds=(-1, 0.0, 0.03, 0.05, 0.10)):
    import statistics
    rows = list(rows)
    mid = sorted(r["ts"] for r in rows)[len(rows) // 2]
    print(f"{len(rows)} scored taker trades; split at {mid:%Y-%m-%d}")
    print(f"{'bet only when model edge >=':30} {'trades':>7} {'hit%':>6} {'$ back/$1 (1st half)':>20} {'(2nd half)':>11} {'all':>6}")
    for th in thresholds:
        sel = [r for r in rows if r["edge"] >= th]
        if not sel:
            continue
        def ret(xs):
            c = sum(r["px"] * (1 + FEE * (1 - r["px"])) for r in xs)
            return sum(r["win"] for r in xs) / c if c else float("nan")
        a = [r for r in sel if r["ts"] < mid]; b = [r for r in sel if r["ts"] >= mid]
        label = "all trades (no filter)" if th < -0.5 else f"{th:+.0%}"
        print(f"{label:30} {len(sel):>7} {100*statistics.mean(r['win'] for r in sel):>5.1f}% "
              f"{ret(a):>20.3f} {ret(b):>11.3f} {ret(sel):>6.3f}")
    # calibration of the model itself
    print("\nModel calibration (is the fair value right?):")
    for lo in (0, .1, .3, .5, .7, .9):
        hi = lo + (.1 if lo in (0, .9) else .2)
        s = [r for r in rows if lo <= r["fair"] < hi]
        if s:
            print(f"  model says {lo:.0%}-{hi:.0%}: actual win rate {100*sum(r['win'] for r in s)/len(s):5.1f}%  "
                  f"(Kalshi avg price {100*sum(r['px'] for r in s)/len(s):5.1f}c, n={len(s)})")
    return rows


def run_backtest(series="KXBTC15M", product="BTC-USD", db="data/bt.duckdb", cache="data/updown"):
    import duckdb, pickle
    os.makedirs(cache, exist_ok=True)
    con = duckdb.connect(db, read_only=True)
    first, last = con.sql(f"""SELECT min(t.created_time), max(t.created_time) FROM 'data/hf/trades-*.parquet' t
                              WHERE t.ticker LIKE '{series}-%'""").fetchall()[0]
    sk = os.path.join(cache, f"{series}_strikes.pkl")
    tickers = [r[0] for r in con.sql(f"SELECT DISTINCT ticker FROM 'data/hf/trades-*.parquet' WHERE ticker LIKE '{series}-%'").fetchall()]
    strikes = pickle.load(open(sk, "rb")) if os.path.exists(sk) else {}
    missing = [t for t in tickers if t not in strikes]
    if missing:
        strikes.update(fetch_strikes_for(missing))
    pickle.dump(strikes, open(sk, "wb"))
    ck = os.path.join(cache, f"{product}_1m.pkl")
    closes = pickle.load(open(ck, "rb")) if os.path.exists(ck) else fetch_minutes(
        product, first - timedelta(hours=2), last + timedelta(minutes=20))
    pickle.dump(closes, open(ck, "wb"))
    print(f"{series}: {len(strikes)} markets with strikes, {len(closes)} {product} minutes")
    trades = con.sql(f"""SELECT ticker, created_time, yes_price, no_price, taker_side, count
                        FROM 'data/hf/trades-*.parquet' WHERE ticker LIKE '{series}-%'""").fetchall()
    return report(score_trades(trades, strikes, closes))
