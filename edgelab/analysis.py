"""Turns the ledger into the evidence: per-strategy stats, the devig-method
tournament, calibration curves. Feeds both the CLI report and the dashboard."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone

from .ledger import Ledger
from .stats import calibration_table, log_loss, mean_ci, summarize


def _clean(x):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def tournament(L: Ledger) -> list[dict]:
    rows = L.q("""SELECT f.method, f.group_id, f.outcome, f.first_q, f.last_q, r.result, m.domain FROM fair f
                  JOIN results r ON r.group_id=f.group_id AND r.outcome=f.outcome
                  JOIN markets m ON m.group_id=f.group_id AND m.outcome=f.outcome WHERE r.result IS NOT NULL""")
    by = defaultdict(lambda: {"open": [], "close": [], "brier": [], "groups": set(), "dom": defaultdict(list), "pg": {}})
    for r in rows:
        d = by[r["method"]]
        d["brier"].append((r["last_q"] - r["result"]) ** 2)
        if r["result"] >= 1:  # log loss is scored on the probability given to what happened
            d["open"].append(-math.log(max(r["first_q"], 1e-9)))
            d["close"].append(-math.log(max(r["last_q"], 1e-9)))
            d["groups"].add(r["group_id"])
            d["pg"][r["group_id"]] = -math.log(max(r["last_q"], 1e-9))
            d["dom"][r["domain"]].append(-math.log(max(r["last_q"], 1e-9)))
    out = []
    for m, d in by.items():
        if not d["close"]:
            continue
        out.append({"method": m, "n_groups": len(d["groups"]),
                    "logloss_open": sum(d["open"]) / len(d["open"]), "logloss_close": sum(d["close"]) / len(d["close"]),
                    "brier": sum(d["brier"]) / len(d["brier"]),
                    "by_domain": {k: sum(v) / len(v) for k, v in d["dom"].items()}})
    out.sort(key=lambda x: x["logloss_close"])
    if out:
        best = out[0]["logloss_close"]
        bpg = by[out[0]["method"]]["pg"]
        for o in out:
            o["delta_vs_best"] = o["logloss_close"] - best
            pg = by[o["method"]]["pg"]
            diffs = [pg[g] - bpg[g] for g in pg if g in bpg]
            o["t_vs_best"] = mean_ci(diffs)[3] if len(diffs) > 2 and o is not out[0] else None
    return out


def calibration(L: Ledger, method: str = "power") -> dict:
    rows = L.q("""SELECT m.domain, f.group_id, f.outcome, f.last_q, r.result FROM fair f
                  JOIN results r ON r.group_id=f.group_id AND r.outcome=f.outcome
                  JOIN markets m ON m.group_id=f.group_id AND m.outcome=f.outcome
                  WHERE f.method=? AND r.result IS NOT NULL ORDER BY f.group_id, f.outcome""", (method,))
    by, used = defaultdict(list), set()
    for r in rows:
        if r["group_id"] in used:
            continue
        used.add(r["group_id"])
        by[r["domain"]].append((r["last_q"], r["result"]))
        by["all"].append((r["last_q"], r["result"]))
    return {d: {"n": len(p), "logloss": log_loss(p), "table": calibration_table(p)} for d, p in by.items()}


def strategy_report(L: Ledger, s: dict) -> dict:
    bets = [dict(b) for b in L.q("SELECT * FROM bets WHERE strategy=? ORDER BY ts", (s["name"],))]
    summ = summarize(bets)
    curve, run = [], 0.0
    for b in sorted([b for b in bets if b["status"] == "settled"], key=lambda b: (b["settled_ts"], b["id"])):
        run += b["pnl"]
        curve.append([b["settled_ts"], round(run, 2)])
    doms = defaultdict(list)
    for b in bets:
        doms[b["domain"]].append(b)
    by_domain = {d: {k: summarize(v)[k] for k in ("n_bets", "n_settled", "roi", "clv", "pnl", "clv_t")} for d, v in doms.items()}
    clvs = [b["clv"] for b in bets if b["clv"] is not None]
    edges = [-0.10 + 0.01 * i for i in range(21)]
    hist = [0] * 20
    for c in clvs:
        hist[min(19, max(0, int((c + 0.10) / 0.01)))] += 1
    cfg = s["config"]
    return {"name": s["name"], "hypothesis": s["hypothesis"], "hash": s["hash"], "registered": s["registered_ts"],
            "config": cfg, "summary": summ, "curve": curve, "by_domain": by_domain,
            "clv_hist": {"edges": edges, "counts": hist},
            "bankroll_start": cfg.get("bankroll", 1000.0), "control": bool(cfg.get("control"))}


def build_report(L: Ledger, mode: str = "live") -> dict:
    strategies = [strategy_report(L, s) for s in L.strategies()]
    opps = []
    for r in L.q("SELECT * FROM opps"):
        for c in json.loads(r["data"]):
            opps.append({**c, "strategy": r["strategy"]})
    opps.sort(key=lambda c: -c["ev"])
    recent = [dict(b) for b in L.q("""SELECT b.*, m.event_name FROM bets b JOIN markets m ON m.group_id=b.group_id
                                      AND m.outcome=b.outcome ORDER BY b.ts DESC, b.id DESC LIMIT 60""")]
    sc = L.q("SELECT COUNT(*) n, MIN(ts) a, MAX(ts) b FROM scans")[0]
    fits = [dict(r) for r in L.q("SELECT * FROM calibrations ORDER BY ts DESC LIMIT 20")]
    return _clean({
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "mode": mode,
        "scans": {"n": sc["n"], "first": sc["a"], "last": sc["b"]},
        "strategies": strategies, "tournament": tournament(L), "calibration": calibration(L),
        "calibration_fits": fits, "opportunities": opps[:80], "recent_bets": recent,
    })
