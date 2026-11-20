"""Measure how often cheap ("tail") contracts actually win.

For every settled market we look at the ask price N hours before close and
record (side price, category, did that side win). Grouping by price bucket
answers the only question that matters: are 5c contracts winning MORE than
5% of the time after fees? If not, there is no edge to automate.
"""
import json
import math
import os
from collections import defaultdict
from datetime import datetime

from .client import cents
from .config import CONFIG

BUCKETS = [(1, 2), (3, 5), (6, 10), (11, 15), (16, 25), (26, 35), (36, 45), (46, 55),
           (56, 65), (66, 75), (76, 85), (86, 90), (91, 95), (96, 99)]


def bucket_of(price_c):
    for lo, hi in BUCKETS:
        if lo <= price_c <= hi:
            return f"{lo}-{hi}"
    return None


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def snapshot_asks(candles):
    """Return (yes_ask_c, no_ask_c) from the last candle that has quotes."""
    for c in reversed(candles):
        ya = cents(c.get("yes_ask") or {}, "close")
        yb = cents(c.get("yes_bid") or {}, "close")
        if ya and yb is not None and 0 < ya < 100:
            return ya, 100 - yb
    return None


def collect(client, max_events=3000, cfg=CONFIG, log=print):
    """Pull settled events and build observations. Slow: one candle call per market."""
    obs = []
    for i, ev in enumerate(client.events(status="settled")):
        if i >= max_events:
            break
        series, cat = ev.get("series_ticker"), ev.get("category", "unknown")
        for m in ev.get("markets", []):
            result = m.get("result")
            if result not in ("yes", "no") or not m.get("close_time"):
                continue
            end = _ts(m["close_time"]) - cfg.snapshot_hours_before_close * 3600
            try:
                candles = client.candlesticks(series, m["ticker"], end - 6 * 3600, end)
            except Exception as e:  # noqa: BLE001
                log(f"skip {m['ticker']}: {e}")
                continue
            snap = snapshot_asks(candles)
            if not snap:
                continue
            ya, na = snap
            obs.append({"cat": cat, "series": series, "side": "yes", "price": ya, "won": result == "yes"})
            obs.append({"cat": cat, "series": series, "side": "no", "price": na, "won": result == "no"})
        if i % 100 == 0:
            log(f"{i} events, {len(obs)} observations")
    return obs


def wilson_lower(wins, n, z=1.64):
    if n == 0:
        return 0.0
    p = wins / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - a) / d


def summarize(obs):
    """-> {(cat, bucket): stats}, also an 'ALL' category."""
    agg = defaultdict(lambda: {"n": 0, "wins": 0, "price_sum": 0})
    for o in obs:
        b = bucket_of(o["price"])
        if not b:
            continue
        groups = [o["cat"], "ALL"] + ([f"S:{o['series']}"] if o.get("series") else [])
        for cat in groups:
            s = agg[f"{cat}|{b}"]
            s["n"] += 1
            s["wins"] += int(o["won"])
            s["price_sum"] += o["price"]
    out = {}
    for k, s in agg.items():
        n = s["n"]
        out[k] = {
            "n": n,
            "win_rate": s["wins"] / n,
            "win_rate_lower90": wilson_lower(s["wins"], n),
            "avg_price_c": s["price_sum"] / n,
            "return_per_dollar": (s["wins"] * 100) / s["price_sum"] if s["price_sum"] else 0,
        }
    return out


def save(table, path=CONFIG.calibration_path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(table, fh, indent=2, sort_keys=True)


def load(path=CONFIG.calibration_path):
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


def report(table):
    lines = [f"{'category|bucket':40} {'n':>7} {'win%':>7} {'avg px':>7} {'$ back per $1':>14}"]
    for k in sorted(table, key=lambda k: (k.split("|")[0] != "ALL", k)):
        s = table[k]
        lines.append(f"{k:40} {s['n']:>7} {s['win_rate']*100:>6.2f}% {s['avg_price_c']:>6.1f}c "
                     f"{s['return_per_dollar']:>13.2f}")
    return "\n".join(lines)
