"""Are Kalshi's 15-minute crypto "stack" combos (every leg on the same side) mispriced
given how strongly the coins move together?

Fair value of a combo at the moment it trades, from:
  * each leg's live Kalshi mid price at that minute (1-minute candles), and
  * pair correlations implied by how often each pair of coins ended on the same side,
    over every settled 15-minute window since mid-September (Gaussian copula:
    P(same side) = 1/2 + arcsin(rho)/pi  =>  rho = sin(pi * (P_same - 1/2))).
Fair = P(all legs win) under a multivariate normal with those thresholds and correlations.
Compared with the combo price buyers actually paid, plus the taker fee.
"""
import json
import math
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import requests
from scipy.stats import multivariate_normal, norm

B = "https://api.elections.kalshi.com/trade-api/v2"
ET = ZoneInfo("America/New_York")
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


def pair_rho(results, a, b):
    ts = [t for t in results[a] if t in results[b]]
    same = np.mean([results[a][t] == results[b][t] for t in ts])
    return math.sin(math.pi * (same - 0.5))


def leg_ticker(series, close_iso):
    c = datetime.fromisoformat(close_iso.replace("Z", "+00:00")).astimezone(ET)
    return f"{series}-{c:%y%b%d%H%M}".upper() + f"-{c:%M}"


_candles = {}


def leg_mid(ticker, ts):
    """Mid of YES bid/ask in the 1-minute candle covering unix time ts."""
    if ticker not in _candles:
        series = ticker.split("-")[0]
        d = get(f"/series/{series}/markets/{ticker}/candlesticks",
                start_ts=int(ts) - 1200, end_ts=int(ts) + 1200, period_interval=1) or {}
        _candles[ticker] = {c["end_period_ts"]: c for c in d.get("candlesticks", [])}
    cs = _candles[ticker]
    end = (int(ts) // 60 + 1) * 60
    c = cs.get(end)
    if not c:
        return None
    f = lambda k: float((c.get(k) or {}).get("close_dollars") or "nan")
    b, a = f("yes_bid"), f("yes_ask")
    if not (0 < b < 1 and 0 < a <= 1):
        return None
    return (a + b) / 2


def fair_prob(sides, mids, coins, rho):
    """P(all legs win). Leg i wins with probability q_i = mid (YES) or 1 - mid (NO)."""
    q = np.array([m if s == "yes" else 1 - m for s, m in zip(sides, mids)])
    q = np.clip(q, 1e-4, 1 - 1e-4)
    z = norm.ppf(q)
    k = len(coins)
    cov = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            cov[i, j] = cov[j, i] = rho[(coins[i], coins[j])]
    # same-side legs: the win events are positively correlated; flip sign for opposite sides
    sgn = np.array([1 if s == sides[0] else -1 for s in sides])
    cov = cov * np.outer(sgn, sgn)
    return float(multivariate_normal(mean=np.zeros(k), cov=cov, allow_singular=True).cdf(z))
