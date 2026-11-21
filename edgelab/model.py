"""The EdgeLab EV model.

fair(outcome) = calibrate( consensus over venues( devig(venue prices) ) )

1. devig      : each venue's complete outcome set -> vig-free probabilities
2. consensus  : weighted average in log-odds space across information sources
                (sharp books, exchanges, prediction markets). Soft books get 0 weight.
3. calibrate  : optional per-domain logistic recalibration  logit(q') = a + b*logit(q)
                fitted walk-forward on OUR settled data (fixes e.g. favourite-longshot bias)

EV of buying outcome i at decimal price d:   EV = q_i * d - 1
Every component is a testable hypothesis; strategies pre-register their choices.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

from .devig import devig
from .odds import kelly, logit, sigmoid
from .sources.base import Quote, parse_iso

DEFAULT_WEIGHTS = {
    # sportsbooks — the sharp end of the market
    "pinnacle": 1.0, "betfair_ex_eu": 0.8, "betfair_ex_uk": 0.8, "matchbook": 0.6,
    "circasports": 0.6, "lowvig": 0.4, "betonlineag": 0.3,
    # prediction markets / exchanges
    "polymarket": 0.8, "kalshi": 0.7, "manifold": 0.3, "predictit": 0.4,
    # synthetic demo venues
    "demo_sharp": 1.0, "demo_exchange_1": 0.7, "demo_exchange_2": 0.8, "demo_crowd": 0.3,
    "*": 0.0,  # everyone else (soft books) contributes nothing to "truth"
}


def group_quotes(quotes: list[Quote]) -> dict[str, list[Quote]]:
    g: dict[str, list[Quote]] = defaultdict(list)
    for q in quotes:
        g[q.group_id].append(q)
    return g


def venue_books(qs: list[Quote]) -> tuple[list[str], dict[str, dict[str, float]]]:
    """-> (outcomes, {venue: {outcome: price}}) keeping only venues quoting every outcome."""
    outcomes = sorted({q.outcome for q in qs})
    books: dict[str, dict[str, float]] = defaultdict(dict)
    for q in qs:
        books[q.venue][q.outcome] = q.price
    return outcomes, {v: b for v, b in books.items() if len(b) == len(outcomes)}


def consensus(qs: list[Quote], method: str = "power", weights: dict | None = None,
              exclude_venue: str | None = None, hold_decay: float = 0.0,
              calibration: dict | None = None) -> tuple[dict[str, float], int] | None:
    """Fair probabilities for a group, or None if no weighted source has the full market."""
    weights = weights or DEFAULT_WEIGHTS
    outcomes, books = venue_books(qs)
    if len(outcomes) < 2:
        return None
    acc = {o: 0.0 for o in outcomes}
    wsum, n = 0.0, 0
    for venue, book in books.items():
        if venue == exclude_venue:
            continue
        w = weights.get(venue, weights.get("*", 0.0))
        if w <= 0:
            continue
        prices = [book[o] for o in outcomes]
        if hold_decay > 0:
            hold = sum(1 / p for p in prices) - 1
            w *= math.exp(-max(hold, 0) / hold_decay)
        probs = devig(prices, method)
        for o, p in zip(outcomes, probs):
            acc[o] += w * logit(p)
        wsum += w
        n += 1
    if wsum <= 0:
        return None
    q = {o: sigmoid(acc[o] / wsum) for o in outcomes}
    if calibration:
        q = {o: sigmoid(calibration.get("a", 0) + calibration.get("b", 1) * logit(p)) for o, p in q.items()}
    s = sum(q.values())
    return {o: p / s for o, p in q.items()}, n


def find_edges(groups: dict[str, list[Quote]], strat: dict, calibrations: dict | None = None,
               now: datetime | None = None) -> list[dict]:
    """Return the best +EV candidate per (group, outcome) under a strategy config.

    Event contracts can lock capital for months, so each candidate also carries
    `days` to resolution and `apr` = EV annualised over that lock-up."""
    calibrations = calibrations or {}
    now = now or datetime.now(timezone.utc)
    method = strat.get("devig", "power")
    weights = {**DEFAULT_WEIGHTS, **strat.get("weights", {})}
    domains = set(strat.get("domains", []))
    bet_venues = set(strat.get("bet_venues", []))
    out = []
    for gid, qs in groups.items():
        dom = qs[0].domain
        if domains and dom not in domains:
            continue
        cal = calibrations.get(dom) if strat.get("use_calibration") else None
        cache: dict = {}
        best: dict[str, dict] = {}
        for q in qs:
            if not q.bettable or (bet_venues and q.venue not in bet_venues):
                continue
            ex = q.venue if strat.get("exclude_self", True) else None
            if ex not in cache:
                cache[ex] = consensus(qs, method, weights, ex, strat.get("hold_decay", 0.0), cal)
            fr = cache[ex]
            if not fr or fr[1] < strat.get("min_sources", 1):
                continue
            p = fr[0].get(q.outcome)
            if p is None or p < strat.get("min_prob", 0.03) or q.price > strat.get("max_price", 15):
                continue
            ev = p * q.price - 1
            if not (strat.get("min_ev", 0.02) <= ev <= strat.get("max_ev", 0.30)):
                continue
            days = max((parse_iso(q.start_time) - now).total_seconds() / 86400, 0.5)
            apr = ev * 365 / days
            if days > strat.get("max_days", 1e9) or apr < strat.get("min_apr", -1e9):
                continue
            cand = {"group_id": gid, "outcome": q.outcome, "venue": q.venue, "price": q.price,
                    "q": p, "ev": ev, "kelly": kelly(p, q.price), "n_sources": fr[1],
                    "domain": dom, "event_name": q.event_name, "market_key": q.market_key,
                    "start_time": q.start_time, "days": round(days, 2), "apr": apr, "size": q.size}
            if q.outcome not in best or ev > best[q.outcome]["ev"]:
                best[q.outcome] = cand
        out.extend(best.values())
    return sorted(out, key=lambda c: -c["ev"])


def stake_for(cand: dict, strat: dict, bankroll: float) -> float:
    mode = strat.get("staking", "kelly")
    cap = strat.get("max_stake_pct", 0.02) * bankroll
    if mode == "flat":
        return round(min(strat.get("flat_stake", 10.0), cap), 2)
    return round(min(strat.get("kelly_fraction", 0.25) * cand["kelly"] * bankroll, cap), 2)


def fit_calibration(pairs: list[tuple[float, int]], l2: float = 1.0, intercept: bool = False) -> dict | None:
    """Logistic recalibration via Newton-Raphson with a weak ridge prior toward
    identity (a=0, b=1). pairs = [(q_model, y)]. Returns {a, b, se_b, n}.

    Default is SLOPE-ONLY (a = 0): the favourite-longshot bias is a slope effect,
    and an intercept is meaningless on symmetric Yes/No pairs."""
    if len(pairs) < 30:
        return None
    a, b = 0.0, 1.0
    xs = [logit(q) for q, _ in pairs]
    ys = [y for _, y in pairs]
    for _ in range(50):
        ga, gb = -l2 * a, -l2 * (b - 1)
        haa, hab, hbb = l2, 0.0, l2
        for x, y in zip(xs, ys):
            p = sigmoid(a + b * x)
            r = y - p
            w = p * (1 - p)
            ga += r
            gb += r * x
            haa += w
            hab += w * x
            hbb += w * x * x
        if not intercept:
            det = hbb
            da, db = 0.0, gb / hbb
        else:
            det = haa * hbb - hab * hab
            if det <= 0:
                break
            da = (hbb * ga - hab * gb) / det
            db = (haa * gb - hab * ga) / det
        a, b = a + da, b + db
        if abs(da) + abs(db) < 1e-9:
            break
    se_b = math.sqrt((haa if intercept else 1.0) / det) if det > 0 else float("nan")
    return {"a": a, "b": b, "se_b": se_b, "n": len(pairs)}
