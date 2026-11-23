"""Feature-based rule search for BTC 15-minute up/down markets.

Every taker BUY in the trade history is a candidate entry at the price that taker paid
(the ask). Each candidate gets features known at that moment:
  side, price, minutes left, seconds since open, our side's price change over the last
  30/60/120 s (from the last trade before then), UTC hour.
A rule is a set of feature ranges; it takes the FIRST qualifying candidate in each
market (one bet per window) and holds to settlement. Return = payout / (price + fee).

Discipline: rules are ranked on a training period only, must also pass separate
validation periods, and the final verdict comes from a holdout that is touched once.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

FEE = 0.07


def market_candidates(m, mi, step=5, capped=False):
    """Candidates for one market, at most one per side per `step` seconds (the first taker
    buy of that side in each bucket). Features use the full trade history."""
    t, y, ty = m.t, m.yes_px, m.taker_yes
    if len(t) < 3:
        return None
    my = np.where(ty, y, 1 - y)

    def past(L):
        j = np.searchsorted(t, t - L, side="right") - 1
        prev_yes = np.where(j >= 0, y[np.clip(j, 0, None)], np.nan)
        return my - np.where(ty, prev_yes, 1 - prev_yes)

    since = t - m.open_ts
    bucket = np.floor(since / step).astype(np.int64) * 2 + ty.astype(np.int64)
    _, first = np.unique(bucket, return_index=True)
    first.sort()
    k = first[(since[first] >= 0) & (my[first] >= 0.01) & (my[first] <= 0.995) & (t[first] < m.close_ts)]
    cov = float(t[0] - m.open_ts) if capped else 0.0       # data starts this many seconds into the window
    px = my[k]
    return {"mkt": np.full(len(k), mi, np.int32), "side_yes": ty[k], "px": px.astype(np.float32),
            "tau": ((m.close_ts - t[k]) / 60).astype(np.float32), "since": since[k].astype(np.float32),
            "d30": past(30)[k].astype(np.float32), "d60": past(60)[k].astype(np.float32),
            "d120": past(120)[k].astype(np.float32), "hour": ((t[k] // 3600) % 24).astype(np.int8),
            "win": (ty[k] == (m.result == "yes")).astype(np.float32),
            "open_ts": np.full(len(k), m.open_ts), "cov": np.full(len(k), cov, np.float32),
            "cost": (px * (1 + FEE * (1 - px))).astype(np.float32)}


def concat(parts):
    parts = [p for p in parts if p is not None and len(p["px"])]
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def build_candidates(markets, cap=20000):
    return concat([market_candidates(m, i, capped=len(m.t) >= cap) for i, m in enumerate(markets)])


@dataclass(frozen=True)
class Rule:
    side: str = "any"          # "yes", "no", "any"
    px: tuple = (0.0, 1.0)
    tau: tuple = (0.0, 15.0)
    mom_col: str | None = None  # "d30" / "d60" / "d120"
    mom: tuple = (-1.0, 1.0)    # range for our side's recent price change
    hours: tuple | None = None  # UTC hours allowed (lo, hi) inclusive

    def label(self):
        s = f"{self.side.upper():3} px {self.px[0]:.2f}-{self.px[1]:.2f} tau {self.tau[0]:g}-{self.tau[1]:g}m"
        if self.mom_col:
            s += f" {self.mom_col} {self.mom[0]:+.2f}..{self.mom[1]:+.2f}"
        if self.hours:
            s += f" UTC {self.hours[0]:02d}-{self.hours[1]:02d}h"
        return s


def apply(C, r: Rule, window=None):
    m = (C["px"] >= r.px[0]) & (C["px"] < r.px[1]) & (C["tau"] >= r.tau[0]) & (C["tau"] < r.tau[1])
    if r.side != "any":
        m &= C["side_yes"] == (r.side == "yes")
    if r.mom_col:
        v = C[r.mom_col]
        m &= (v >= r.mom[0]) & (v < r.mom[1])
    if r.hours:
        m &= (C["hour"] >= r.hours[0]) & (C["hour"] <= r.hours[1])
    if window:
        m &= (C["open_ts"] >= window[0]) & (C["open_ts"] < window[1])
    # only markets whose trade data covers the rule's whole time range (some downloads were capped)
    m &= C["cov"] <= 900 - r.tau[1] * 60 + 1
    idx = np.nonzero(m)[0]
    if not len(idx):
        return idx
    _, first = np.unique(C["mkt"][idx], return_index=True)   # candidates are time-ordered within a market
    return idx[first]


def score(C, idx):
    if len(idx) == 0:
        return {"n": 0, "ret": float("nan")}
    cost, win = C["cost"][idx], C["win"][idx]
    return {"n": int(len(idx)), "ret": float(win.sum() / cost.sum()), "hit": float(win.mean()),
            "avg_px": float(C["px"][idx].mean())}


def boot_ci(C, idx, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    cost, win = C["cost"][idx], C["win"][idx]
    k = rng.integers(0, len(idx), (n, len(idx)))
    r = win[k].sum(1) / cost[k].sum(1)
    return float(np.percentile(r, 5)), float(np.percentile(r, 95))
