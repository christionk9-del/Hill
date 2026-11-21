"""edgelab command line.  `python -m edgelab --help`"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import tomllib
from datetime import timedelta

from . import __version__
from .analysis import build_report, tournament
from .devig import devig
from .ledger import Ledger
from .odds import american_to_decimal, kelly
from .simulator import Engine
from .sources.base import utcnow


def load_config(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "rb") as f:
            return tomllib.load(f)
    return {}


def load_strategies(d: str) -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        with open(p) as f:
            cfg = json.load(f)
        if cfg.get("active", True):
            out.append(cfg)
    return out


def build_sources(cfg: dict):
    from .sources.kalshi import Kalshi
    from .sources.manifold import Manifold
    from .sources.oddsapi import OddsAPI
    from .sources.polymarket import Polymarket
    reg = {"oddsapi": OddsAPI, "kalshi": Kalshi, "polymarket": Polymarket, "manifold": Manifold}
    out = []
    for name, sc in cfg.get("sources", {}).items():
        if sc.get("enabled", True) and name in reg:
            src = reg[name](**{k: v for k, v in sc.items() if k not in ("enabled", "min_interval_minutes")})
            src.min_interval = sc.get("min_interval_minutes", 0)
            out.append(src)
    return out


def parse_price(s: str, fee_rate: float = 0.0) -> float:
    """American (+150/-120), decimal (2.5) or contract price (0.42 -> cost incl. fee rate*P*(1-P))."""
    s = s.strip()
    if s.startswith(("+", "-")) or (s.lstrip("-").isdigit() and abs(float(s)) >= 100):
        return american_to_decimal(float(s))
    v = float(s)
    if 0 < v < 1:  # contract price
        return 1 / (v + fee_rate * v * (1 - v))
    return v


def cmd_ev(a):
    fair_prices = [parse_price(x) for x in a.fair]
    probs = devig(fair_prices, a.method)
    bet = parse_price(a.bet, a.fee_rate)
    p = probs[a.side]
    ev = p * bet - 1
    print(f"Sharp market : {['%.3f' % x for x in fair_prices]}  hold={sum(1/x for x in fair_prices)-1:.2%}")
    print(f"Fair probs   : {['%.4f' % x for x in probs]}  ({a.method})")
    cost = f"  = {100/bet:.1f}c all-in" if a.bet.strip().startswith("0.") else ""
    print(f"Your price   : {bet:.3f} on outcome #{a.side}{cost}  (fair {1/p:.3f} = {100*p:.1f}c)")
    print(f"EV           : {ev:+.2%} per unit   |  full Kelly {kelly(p, bet):.2%}  quarter Kelly {kelly(p, bet)/4:.2%}")


def _engine(cfg, L, sources):
    maps = []
    mp = cfg.get("mappings", "mappings.json")
    if os.path.exists(mp):
        with open(mp) as f:
            maps = json.load(f)
    return Engine(L, sources, load_strategies(cfg.get("strategies_dir", "experiments")), maps,
                  store_quotes=cfg.get("store_quotes", True))


def cmd_scan(a, cfg):
    os.makedirs(os.path.dirname(cfg.get("db", "edgelab.db")) or ".", exist_ok=True)
    L = Ledger(cfg.get("db", "edgelab.db"))
    a.dashboard = a.dashboard or cfg.get("dashboard", "dashboard.html")
    a.title = cfg.get("title", "")
    eng = _engine(cfg, L, build_sources(cfg))
    while True:
        r = eng.scan(utcnow())
        print(f"[{utcnow():%H:%M:%S}] quotes={r['quotes']} markets={r['groups']} new paper bets={r['new_bets']}")
        if a.dashboard:
            _write_dash(L, a.dashboard, "live", a.title)
        if not a.every:
            break
        time.sleep(a.every)


def _write_dash(L, out, mode, title=""):
    from .dashboard import render
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    rep = build_report(L, mode)
    rep["title"] = title
    with open(out, "w") as f:
        f.write(render(rep))
    print(f"dashboard -> {out}")


def cmd_demo(a, cfg):
    from .sources.demo import DemoWorld
    if os.path.exists(a.db):
        os.remove(a.db)
    L = Ledger(a.db)
    end = utcnow().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=a.days)
    world = DemoWorld(start, seed=a.seed, sports_per_batch=a.sports)
    eng = Engine(L, [world], load_strategies(cfg.get("strategies_dir", a.strategies)), store_quotes=False, log=lambda *x: None)
    t, i = start, 0
    while t <= end:
        eng.scan(t)
        i += 1
        if i % 40 == 0:
            print(f"  simulated {t:%Y-%m-%d %H:%M} ...", file=sys.stderr)
        t += timedelta(hours=a.step)
    # Ground truth: only the demo knows real probabilities -> grade the model itself
    for b in L.q("SELECT id, group_id, outcome, price FROM bets"):
        tp = world.true_prob(b["group_id"].split("|")[0].removeprefix("demo:"), b["outcome"])
        if tp is not None:
            L.db.execute("UPDATE bets SET true_ev=? WHERE id=?", (tp * b["price"] - 1, b["id"]))
    L.commit()
    print_report(L)
    _write_dash(L, a.out, "demo")


def print_report(L):
    rep = build_report(L)
    truth = any(s["summary"].get("true_ev") is not None for s in rep["strategies"])
    hdr = f"\n{'strategy':34} {'bets':>5} {'settled':>7} {'ROI':>8} {'CLV':>7} {'CLV t':>6} {'modelEV':>8}"
    print(hdr + (f" {'trueEV':>8}" if truth else "") + "  verdict")
    for s in rep["strategies"]:
        m = s["summary"]
        f = lambda v, p=True: "—" if v is None else (f"{v:+.2%}" if p else f"{v:+.1f}")
        row = f"{s['name'][:34]:34} {m['n_bets']:5d} {m['n_settled']:7d} {f(m['roi']):>8} {f(m['clv']):>7} {f(m['clv_t'], False):>6} {f(m['avg_ev']):>8}"
        print(row + (f" {f(m['true_ev']):>8}" if truth else "") + f"  {m['verdict']}")
    v = rep.get("venues") or []
    if v:
        print("\nVenue scorecard (each venue's own closing price, log loss, lower = more accurate):")
        for r in v:
            tt = "" if r.get("t_vs_best") is None else f"  paired t={r['t_vs_best']:+.1f}"
            print(f"  {r['method'][6:]:18} {r['logloss_close']:.5f}  n={r['n_groups']}{tt}")
    t = rep["tournament"]
    if t:
        print("\nDevig tournament (closing log loss, lower = better):")
        for r in t:
            tt = "" if r.get("t_vs_best") in (None,) else f"  paired t={r['t_vs_best']:+.1f}"
            print(f"  {r['method']:15} {r['logloss_close']:.5f}  (+{r['delta_vs_best']:.5f})  n={r['n_groups']}{tt}")


def cmd_report(a, cfg):
    if cfg.get("title"):
        print(f"== {cfg['title']} ==")
    print_report(Ledger(cfg.get("db", "edgelab.db")))


def cmd_export(a, cfg):
    """Write every paper bet (with market name, CLV, result) to CSV for spreadsheets / notebooks."""
    import csv
    L = Ledger(a.db or cfg.get("db", "edgelab.db"))
    rows = [dict(r) for r in L.q("""SELECT b.*, m.event_name, m.start_time FROM bets b
                                    LEFT JOIN markets m ON m.group_id=b.group_id AND m.outcome=b.outcome ORDER BY b.ts""")]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="") as f:
        if rows:
            wr = csv.DictWriter(f, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)
    print(f"{len(rows)} bets -> {a.out}")


def cmd_dashboard(a, cfg):
    _write_dash(Ledger(a.db or cfg.get("db", "edgelab.db")), a.out, a.mode, cfg.get("title", ""))


def cmd_match(a, cfg):
    """Suggest cross-venue pairs (same question on Kalshi/Polymarket/Manifold) for mappings.json.

    Score = 0.6 x token overlap (Jaccard, stop-words removed, numbers must agree)
          + 0.4 x character similarity, minus a penalty when close dates differ by > 7 days.
    Output is a REVIEW file: resolution rules must be checked by a human before use."""
    import re
    from difflib import SequenceMatcher
    from .sources.base import parse_iso
    stop = {"will", "the", "a", "an", "of", "in", "on", "by", "to", "be", "before", "after", "at", "for", "and", "or", "who", "what", "next"}
    tok = lambda t: {w for w in re.findall(r"[a-z0-9%.]+", t.lower()) if w not in stop}
    nums = lambda t: set(re.findall(r"\d+(?:\.\d+)?", t))
    qs = [q for src in build_sources(cfg) if src.name != "oddsapi" for q in _safe_fetch(src)]
    by: dict = {}
    for q in qs:
        if q.outcome == "Yes":
            by.setdefault(q.source, {})[str(q.ref.get("ticker") or q.ref.get("id"))] = (q.event_name, q.start_time, q.domain)
    out, srcs = [], list(by)
    for i, s1 in enumerate(srcs):
        for s2 in srcs[i + 1:]:
            for id1, (n1, t1, d1) in by[s1].items():
                k1, best = tok(n1), None
                for id2, (n2, t2, _) in by[s2].items():
                    k2 = tok(n2)
                    if not k1 & k2 or nums(n1) != nums(n2):
                        continue
                    sc = 0.6 * len(k1 & k2) / len(k1 | k2) + 0.4 * SequenceMatcher(None, n1.lower(), n2.lower()).ratio()
                    if abs((parse_iso(t1) - parse_iso(t2)).days) > 7:
                        sc -= 0.15
                    if not best or sc > best[0]:
                        best = (sc, id2, n2, t2)
                if best and best[0] >= a.threshold:
                    out.append({"score": round(best[0], 3), "id": re.sub(r"[^a-z0-9]+", "-", n1.lower())[:60].strip("-"),
                                "name": n1, "domain": d1, "legs": {s1: id1, s2: best[1]}, "invert": {},
                                "_check": f"{s2}: {best[2]} (closes {best[3][:10]} vs {t1[:10]}). Confirm identical resolution rules."})
    out.sort(key=lambda r: -r["score"])
    with open(a.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"{len(out)} candidate pairs -> {a.out}. Review each, delete wrong ones, set invert if YES means NO, then merge into mappings.json")


def _safe_fetch(src):
    try:
        return src.fetch(utcnow())
    except Exception as e:
        print(f"! {src.name}: {e}", file=sys.stderr)
        return []


def main(argv=None):
    p = argparse.ArgumentParser(prog="edgelab", description="Empirical +EV model & forward-testing simulator")
    p.add_argument("--config", default="edgelab.toml")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("ev", help="quick EV calculator: devig a sharp line, price your bet")
    e.add_argument("--fair", nargs="+", required=True, help="sharp prices for ALL outcomes, e.g. -110 -110 or 1.91 2.05 or 0.42 0.60")
    e.add_argument("--bet", required=True, help="the price you can get")
    e.add_argument("--side", type=int, default=0, help="index of the outcome you are betting")
    e.add_argument("--fee-rate", type=float, default=0.0, help="contract fee rate on --bet, e.g. 0.07 for Kalshi taker")
    e.add_argument("--method", default="power", choices=["multiplicative", "additive", "power", "shin", "odds_ratio", "worst_case"])

    s = sub.add_parser("scan", help="live: fetch odds, paper-bet +EV, track CLV, settle")
    s.add_argument("--every", type=int, default=0, help="repeat every N seconds")
    s.add_argument("--dashboard", default=None, help="defaults to the config's `dashboard` or dashboard.html")

    d = sub.add_parser("demo", help="run a synthetic forward test with planted edges (offline)")
    d.add_argument("--days", type=int, default=45)
    d.add_argument("--step", type=float, default=3, help="hours between scans")
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--db", default="demo.db")
    d.add_argument("--out", default="dashboard_demo.html")
    d.add_argument("--strategies", default="experiments")
    d.add_argument("--sports", type=int, default=0, help="sports events per 6h batch (0 = events-only world)")

    sub.add_parser("report", help="print strategy verdicts + devig tournament")
    db = sub.add_parser("dashboard", help="render the HTML dashboard from a ledger")
    db.add_argument("--db")
    db.add_argument("--out", default="dashboard.html")
    db.add_argument("--mode", default="live")
    ex = sub.add_parser("export", help="export paper bets to CSV")
    ex.add_argument("--db")
    ex.add_argument("--out", default="bets.csv")
    m = sub.add_parser("match", help="suggest cross-venue market mappings")
    m.add_argument("--threshold", type=float, default=0.6)
    m.add_argument("--out", default="mappings.candidates.json")

    a = p.parse_args(argv)
    cfg = load_config(a.config)
    if a.cmd == "ev":
        return cmd_ev(a)
    return {"scan": cmd_scan, "demo": cmd_demo, "report": cmd_report, "dashboard": cmd_dashboard, "match": cmd_match, "export": cmd_export}[a.cmd](a, cfg)
