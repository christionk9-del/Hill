"""Multi-day backtest of the speed rule on real Kalshi BTC 15-min taker trades.

For each day with Coinbase tick data, every taker buy in that day's windows is re-priced
with the settlement-accurate fair value (60-second averages, kalshi_bot.speedbot.fair_up)
using the Coinbase price `lat` seconds BEFORE the trade (what a bot that is `lat` seconds
behind would have seen). Rule: first taker buy per window with fair - (ask + fee) >= edge,
1-14 minutes left, ask 0.10-0.90; hold to settlement.

Windows whose downloaded trades were capped (missing the start) are skipped.
"""
import bisect
import math
import pickle
from glob import glob

import numpy as np

from .speedbot import fair_up

FEE = 0.07


class Ticks:
    def __init__(self, ticks):
        self.t = [x[0] for x in ticks]
        self.p = [x[1] for x in ticks]
        # 1-minute closes for volatility
        self.min_close = {}
        for t, p in ticks:
            self.min_close[int(t // 60)] = p

    def price(self, ts):
        i = bisect.bisect_right(self.t, ts) - 1
        return self.p[i] if i >= 0 else None

    def sigma(self, ts, n=60):
        m = int(ts // 60) - 1
        xs = [self.min_close.get(k) for k in range(m - n, m + 1)]
        xs = [x for x in xs if x]
        r = [math.log(b / a) for a, b in zip(xs, xs[1:])]
        if len(r) < 30:
            return None
        mu = sum(r) / len(r)
        return math.sqrt(sum((x - mu) ** 2 for x in r) / (len(r) - 1))


def score_day(markets, strikes, ticks, lats=(0, 1, 2, 5, 15), edges=(0.05, 0.10, 0.15), cap=20000):
    """Return {(lat, edge): [(cost, win, open_ts), ...]} for one day."""
    out = {(l, e): [] for l in lats for e in edges}
    for m in markets:
        if len(m.t) >= cap or m.ticker not in strikes:
            continue
        strike = strikes[m.ticker][0]
        my = np.where(m.taker_yes, m.yes_px, 1 - m.yes_px)
        tau = (m.close_ts - m.t) / 60
        ok = np.nonzero((tau >= 1) & (tau < 14) & (my >= 0.10) & (my <= 0.90))[0]
        sig_cache = {}
        done = {k: False for k in out}
        for i in ok:
            ts, px, side_yes = m.t[i], my[i], bool(m.taker_yes[i])
            cost = px * (1 + FEE * (1 - px))
            win = int(side_yes == (m.result == "yes"))
            for lat in lats:
                pending = [e for e in edges if not done[(lat, e)]]
                if not pending:
                    continue
                spot = ticks.price(ts - lat)
                mk = int((ts - lat) // 60)
                if mk not in sig_cache:
                    sig_cache[mk] = ticks.sigma(ts - lat)
                sig = sig_cache[mk]
                if not spot or not sig:
                    continue
                # the bot acts at the trade time; only its BTC price is `lat` seconds old
                f_yes = fair_up(spot, strike, (m.close_ts - ts) / 60, sig, None, 0.0002)
                fair = f_yes if side_yes else 1 - f_yes
                for e in pending:
                    if fair - cost >= e:
                        out[(lat, e)].append((cost, win, m.open_ts))
                        done[(lat, e)] = True
            if all(done.values()):
                break
    return out


def summarize(rows):
    if not rows:
        return None
    c = np.array([r[0] for r in rows]); w = np.array([r[1] for r in rows])
    rng = np.random.default_rng(0); k = rng.integers(0, len(rows), (4000, len(rows)))
    lo, hi = np.percentile(w[k].sum(1) / c[k].sum(1), [5, 95])
    return {"n": len(rows), "ret": float(w.sum() / c.sum()), "lo": float(lo), "hi": float(hi), "hit": float(w.mean())}
