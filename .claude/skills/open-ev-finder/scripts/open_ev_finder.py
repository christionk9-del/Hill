#!/usr/bin/env python3
"""Open EV Finder: top +EV contracts per Kalshi category, from public data only (stdlib, no key).

Fair prices come from Kalshi's OWN internal consistency (the EdgeLab devig idea applied inside an event):
  * mutually exclusive events (game winner, "who will...", range buckets): spread-weighted devig of all
    outcome mids -> fair probability of each (wide-spread prices absorb most of the overround).
    The contract you buy stays in its own fair line.
  * strike ladders ("above X", grouped per underlying): P(above X) must fall as X rises -> weighted
    isotonic fit of mids.
  * arbitrage: an exhaustive set whose YES asks (or NO asks) cost less than the guaranteed payout.
EV = fair / (ask + Kalshi taker fee, rounded up per order) - 1, for an order of --order dollars
(capped by the size shown at the best price).

Usage: python3 open_ev_finder.py [--top 5] [--order 100] [--tz America/New_York] [--out report.md] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

API = "https://api.elections.kalshi.com/trade-api/v2/events"
FEE_RATE = 0.07  # Kalshi general taker fee coefficient


# ------------------------------------------------------------------ data
def fetch_events(max_pages: int = 400, log=print) -> list[dict]:
    out, cursor = [], None
    for page in range(max_pages):
        q = {"status": "open", "with_nested_markets": "true", "limit": 200}
        if cursor:
            q["cursor"] = cursor
        for attempt in range(8):
            try:
                req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "open-ev-finder/1.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    d = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
        else:
            raise RuntimeError("Kalshi kept rate-limiting; try again in a minute")
        out += d.get("events", [])
        cursor = d.get("cursor")
        if page % 20 == 0:
            log(f"  fetched {len(out)} events...", file=sys.stderr)
        if not cursor or not d.get("events"):
            break
        time.sleep(0.25)
    return out


def f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def parse_t(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ math
def power_devig(r: list[float]) -> list[float]:
    s = sum(r)
    if abs(s - 1) < 1e-9:
        return list(r)
    lo, hi = 0.05, 20.0
    for _ in range(60):
        k = (lo + hi) / 2
        if sum(x ** k for x in r) > 1:
            lo = k
        else:
            hi = k
    p = [x ** ((lo + hi) / 2) for x in r]
    t = sum(p)
    return [x / t for x in p]


def spread_weighted_devig(mids: list[float], spreads: list[float]) -> list[float]:
    """Remove the overround (sum of mids - 1) mostly from outcomes whose prices are LEAST reliable.
    Each outcome absorbs a share proportional to spread^2 (its price variance), so a tight, liquid
    outcome barely moves while wide-spread longshots absorb the excess. Iterates to keep p > 0."""
    p = list(mids)
    v = [max(sp, 0.005) ** 2 for sp in spreads]
    free = [True] * len(p)
    for _ in range(50):
        excess = sum(p) - 1
        if abs(excess) < 1e-9:
            break
        tv = sum(vi for vi, fr in zip(v, free) if fr)
        if tv <= 0:
            break
        for i in range(len(p)):
            if free[i]:
                p[i] -= excess * v[i] / tv
                if p[i] < 1e-4:
                    p[i], free[i] = 1e-4, False
    t = sum(p)
    return [x / t for x in p]


def isotonic_decreasing(y: list[float], w: list[float]) -> list[float]:
    """Pool-adjacent-violators: weighted least squares fit that is non-increasing."""
    blocks = []  # [value, weight, count]
    for yi, wi in zip(y, w):
        blocks.append([yi, wi, 1])
        while len(blocks) > 1 and blocks[-2][0] < blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    out = []
    for v, _, c in blocks:
        out += [v] * c
    return out


def all_in_cost(price: float, order_usd: float) -> float:
    """Per-contract cost incl. Kalshi taker fee, rounded UP to the cent per order."""
    n = max(1, math.floor(order_usd / price))
    fee = math.ceil(FEE_RATE * n * price * (1 - price) * 100 - 1e-9) / 100
    return price + fee / n


def mid(m) -> float | None:
    b, a = f(m.get("yes_bid_dollars")), f(m.get("yes_ask_dollars"))
    if not (0 < a <= 1):
        return None
    return (b + a) / 2 if b > 0 else a / 2


# ------------------------------------------------------------------ models
def fair_mutually_exclusive(ev) -> tuple[dict, str] | None:
    ms = [m for m in ev.get("markets", []) if m.get("status") in ("active", "open")]
    mids = [mid(m) for m in ms]
    if len(ms) < 2 or any(x is None for x in mids):
        return None
    s = sum(mids)
    if not (1.0 <= s <= 1.6):
        # < 1: the missing mass may be outcomes Kalshi didn't list (third party, unlisted candidate),
        # so scaling the listed ones UP would invent edge. > 1.6: too illiquid to trust.
        return None
    spreads = [f(m.get("yes_ask_dollars")) - f(m.get("yes_bid_dollars")) if f(m.get("yes_bid_dollars")) > 0
               else f(m.get("yes_ask_dollars")) for m in ms]
    probs = spread_weighted_devig(mids, spreads)
    return {m["ticker"]: p for m, p in zip(ms, probs)}, f"event devig, {len(ms)} outcomes (mid sum {s:.2f})"


def fair_ladder(ev) -> dict | None:
    """Strike ladders, grouped by UNDERLYING (ticker minus its strike), because one Kalshi event can hold
    many ladders (every player's props in a game) or none (a list of cities sharing one threshold)."""
    groups = defaultdict(list)
    for m in ev.get("markets", []):
        if (m.get("status") in ("active", "open") and m.get("strike_type") in ("greater", "greater_or_equal")
                and m.get("floor_strike") is not None):
            groups[re.sub(r"[\d.]+$", "", m["ticker"])].append(m)
    out = {}
    for ms in groups.values():
        if len(ms) < 2:
            continue
        ms.sort(key=lambda m: f(m.get("floor_strike")))
        strikes = [f(m.get("floor_strike")) for m in ms]
        if len(set(strikes)) != len(strikes):
            continue  # not one price ladder
        ys, ws = [], []
        for m in ms:
            x = mid(m)
            if x is None:
                break
            spread = max(f(m.get("yes_ask_dollars")) - f(m.get("yes_bid_dollars")), 0.01)
            ys.append(x)
            ws.append(1 / spread ** 2)
        else:
            for m, p in zip(ms, isotonic_decreasing(ys, ws)):
                out[m["ticker"]] = p
    return out or None


# ------------------------------------------------------------------ scan
def weekend_window(now: datetime, tzname: str):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tzname)
    except Exception:
        tz = timezone(timedelta(hours=-5))
    local = now.astimezone(tz)
    sat = (local - timedelta(days=(local.weekday() - 5) % 7)) if local.weekday() >= 5 else local + timedelta(days=5 - local.weekday())
    start = sat.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=2)).astimezone(timezone.utc)


def event_time(ev):
    m = (ev.get("markets") or [{}])[0]
    t = parse_t(m.get("occurrence_datetime"))
    if t:
        return t
    t = parse_t(m.get("expected_expiration_time"))
    return t - timedelta(hours=3) if t else None


def scan(events, now, tzname="America/New_York", order_usd=100.0, min_ev=0.01, max_ev=0.5,
         min_depth=5.0, min_volume=100.0, min_prob=0.03):
    w0, w1 = weekend_window(now, tzname)
    cands, arbs = defaultdict(list), []
    cover = defaultdict(lambda: {"events": 0, "priced": 0})
    for ev in events:
        cat = ev.get("category") or "Other"
        if cat == "Sports":
            t = event_time(ev)
            if not t or not (w0 <= t < w1):
                continue  # sports: this weekend's games only
        models = []
        if ev.get("mutually_exclusive"):
            r = fair_mutually_exclusive(ev)
            if r:
                models.append(r)
            ms = [m for m in ev.get("markets", []) if m.get("status") in ("active", "open")]
            if len(ms) >= 2 and all(mid(m) is not None for m in ms):
                # arbitrage checks on exhaustive sets (cost per 1 contract of each outcome)
                ya = [f(m.get("yes_ask_dollars")) for m in ms]
                na = [1 - f(m.get("yes_bid_dollars")) for m in ms]
                close = max((parse_t(m.get("expected_expiration_time")) or parse_t(m.get("close_time")) or now) for m in ms)
                days = max((close - now).total_seconds() / 86400, 0.5)
                yd = min(f(m.get("yes_ask_size_fp")) * f(m.get("yes_ask_dollars")) for m in ms)
                nd = min(f(m.get("yes_bid_size_fp")) * (1 - f(m.get("yes_bid_dollars"))) for m in ms)
                looks_complete = sum(mid(m) for m in ms) >= 0.97  # buying every YES needs ALL outcomes listed
                if looks_complete and all(0 < a < 1 for a in ya) and yd >= min_depth:
                    c = sum(all_in_cost(a, order_usd / len(ms)) for a in ya)
                    if c < 1:
                        arbs.append({"category": cat, "event": ev.get("title"), "type": "buy every YES", "cost": c, "payout": 1.0,
                                     "profit_pct": 1 / c - 1, "legs": len(ms), "event_ticker": ev.get("event_ticker"),
                                     "days": days, "apr": (1 / c - 1) * 365 / days, "min_leg_depth": yd})
                # buying every NO pays >= N-1 even if the true winner isn't listed, so no completeness check
                if all(f(m.get("yes_bid_dollars")) > 0 for m in ms) and nd >= min_depth:
                    c = sum(all_in_cost(a, order_usd / len(ms)) for a in na)
                    if c < len(ms) - 1:
                        arbs.append({"category": cat, "event": ev.get("title"), "type": "buy every NO", "cost": c,
                                     "payout": len(ms) - 1.0, "profit_pct": (len(ms) - 1) / c - 1, "legs": len(ms),
                                     "event_ticker": ev.get("event_ticker"), "days": days,
                                     "apr": ((len(ms) - 1) / c - 1) * 365 / days, "min_leg_depth": nd})
        lad = fair_ladder(ev)
        if lad:
            models.append((lad, "strike-ladder monotonic fit"))
        cover[cat]["events"] += 1
        cover[cat]["priced"] += len({t for fair, _ in models for t in fair})
        for fair, how in models:
            for m in ev.get("markets", []):
                if m["ticker"] not in fair or f(m.get("volume_fp") or m.get("volume")) < min_volume:
                    continue
                q = fair[m["ticker"]]
                ya, yb = f(m.get("yes_ask_dollars")), f(m.get("yes_bid_dollars"))
                sides = []
                if 0 < ya < 1:
                    sides.append(("YES", ya, q, f(m.get("yes_ask_size_fp")) * ya))
                if 0 < yb < 1:
                    sides.append(("NO", 1 - yb, 1 - q, f(m.get("yes_bid_size_fp")) * (1 - yb)))
                for side, px, p, depth in sides:
                    if p < min_prob or depth < min_depth:
                        continue
                    order = min(order_usd, depth)
                    cost = all_in_cost(px, order)
                    evv = p / cost - 1
                    if not (min_ev <= evv <= max_ev):
                        continue
                    close = parse_t(m.get("expected_expiration_time")) or parse_t(m.get("close_time"))
                    days = max((close - now).total_seconds() / 86400, 0.5) if close else None
                    cands[cat].append({
                        "category": cat, "event": ev.get("title"), "outcome": m.get("yes_sub_title") or m.get("ticker"),
                        "ticker": m["ticker"], "side": side, "price": px, "cost": cost, "fair": p, "ev": evv,
                        "depth_usd": depth, "order_usd": order, "exp_profit": evv * order,
                        "resolves": close.strftime("%Y-%m-%d %H:%M UTC") if close else "",
                        "days": days, "apr": evv * 365 / days if days else None, "model": how,
                        "game_time": event_time(ev).strftime("%a %b %d %H:%M UTC") if cat == "Sports" and event_time(ev) else ""})
    top = {}
    for cat, cs in cands.items():
        best = {}
        for c in sorted(cs, key=lambda c: -c["ev"]):
            best.setdefault((c["ticker"], c["side"]), c)  # one row per contract side
        top[cat] = list(best.values())
    return top, sorted(arbs, key=lambda a: -a["profit_pct"]), (w0, w1), cover


def report(top, arbs, window, now, n, order_usd, n_events, cover=None) -> str:
    L = [f"# Open EV Finder: Kalshi, {now:%A %b %d %Y %H:%M} UTC", "",
         f"Scanned **{n_events:,} open events**. Sports limited to games {window[0]:%a %b %d %H:%M}–{window[1]:%a %b %d %H:%M} UTC (this weekend).",
         f"Costs include Kalshi's taker fee rounded up per order of ${order_usd:.0f} (smaller if the book is thinner).", ""]
    if arbs:
        L += ["## Arbitrage (exhaustive outcome sets)", "",
              "| Category | Event | Trade | Cost | Payout | Profit | Resolves in | APR | Thinnest leg |", "|---|---|---|---|---|---|---|---|---|"]
        for a in arbs[:10]:
            L.append(f"| {a['category']} | {a['event']} | {a['type']} ({a['legs']} legs) | ${a['cost']:.3f} | ${a['payout']:.2f} | "
                     f"{a['profit_pct']:+.1%} | {a['days']:.0f}d | {a['apr']:+.0%} | ${a['min_leg_depth']:,.0f} |")
        L += ["", "_Arbs assume the listed outcomes are exhaustive (e.g. no third-party winner) and fills at the shown prices. Check the rules and the depth on every leg. A tiny profit locked up for years loses to cash interest._", ""]
    if not arbs:
        L += ["**Arbitrage:** none open right now (expected in a working market).", ""]
    if cover:
        L += ["| Category | Events scanned | Contracts with a fair price | +EV found |", "|---|---|---|---|"]
        for cat in sorted(cover):
            L.append(f"| {cat} | {cover[cat]['events']:,} | {cover[cat]['priced']:,} | {len(top.get(cat, []))} |")
        L.append("")
    order = sorted(top, key=lambda c: (c == "Other", c))
    for cat in order:
        rows = sorted(top[cat], key=lambda c: -c["ev"])[:n]
        if not rows:
            continue
        L += [f"## {cat}", "", "| # | Market | Bet | Cost | Fair | EV | Depth | " + ("Game" if cat == "Sports" else "Resolves · APR") + " |",
              "|---|---|---|---|---|---|---|---|"]
        for i, c in enumerate(rows, 1):
            when = c["game_time"] if cat == "Sports" else f"{c['resolves'][:10]} · {c['apr']:.0%}" if c["apr"] is not None else c["resolves"][:10]
            L.append(f"| {i} | {c['event']}: **{c['outcome']}** <br><sub>{c['ticker']} · {c['model']}</sub> | {c['side']} | "
                     f"{c['cost']*100:.1f}¢ | {c['fair']*100:.1f}% | **{c['ev']:+.1%}**{'' if c['ev'] >= 0.01 else ' ᵃ'} | ${c['depth_usd']:,.0f} | {when} |")
        L.append("")
    if not any(top.values()):
        L.append("_No contract cleared the EV, depth and volume filters right now._")
    L += ["---", "ᵃ under 1% EV: within pricing noise, treat as roughly fair.", "", "**How to read this.** Fair prices come from Kalshi's own internal consistency (outcomes in one event must sum to 100%; "
          "'above X' must fall as X rises), not from a second venue. These are *relative-value* signals: a price out of line "
          "with its siblings. They aren't forecasts. Spreads move fast, so re-check before trading. Research tool, not betting advice."]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--order", type=float, default=100.0, help="order size in $ used for fee rounding / depth")
    ap.add_argument("--min-ev", type=float, default=0.0, help="minimum EV to list (default: any positive)")
    ap.add_argument("--min-depth", type=float, default=5.0)
    ap.add_argument("--tz", default="America/New_York", help="time zone that defines 'this weekend'")
    ap.add_argument("--cache", help="read/write raw events JSON here (skip refetch if it exists)")
    ap.add_argument("--out", default="open_ev_report.md")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    import os
    if a.cache and os.path.exists(a.cache):
        events = json.load(open(a.cache))
    else:
        events = fetch_events()
        if a.cache:
            json.dump(events, open(a.cache, "w"))
    top, arbs, window, cover = scan(events, now, a.tz, a.order, max(a.min_ev, 1e-9), min_depth=a.min_depth)
    md = report(top, arbs, window, now, a.top, a.order, len(events), cover)
    with open(a.out, "w") as fh:
        fh.write(md)
    if a.json:
        json.dump({"generated": now.isoformat(), "top": {k: sorted(v, key=lambda c: -c["ev"])[:a.top] for k, v in top.items()},
                   "arbitrage": arbs}, open(a.json, "w"), indent=2, default=str)
    print(md)


if __name__ == "__main__":
    main()
