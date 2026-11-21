"""Statistics for judging a strategy like a scientist, not a gambler.

Key idea: realised ROI is extremely noisy (you need thousands of bets), while
Closing-Line Value (CLV) converges ~10x faster and is the accepted leading
indicator of a real edge. We report both, with uncertainty, and a verdict.
"""
from __future__ import annotations

import math
import random
import statistics as st


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def mean_ci(xs: list[float], z: float = 1.96):
    if len(xs) < 2:
        return (xs[0] if xs else 0.0), float("nan"), float("nan"), float("nan")
    m, sd = st.fmean(xs), st.stdev(xs)
    se = sd / math.sqrt(len(xs))
    t = m / se if se > 0 else float("inf")
    return m, m - z * se, m + z * se, t


def bootstrap_roi(bets: list[dict], n: int = 1000, seed: int = 1):
    if len(bets) < 5:
        return float("nan"), float("nan")
    r = random.Random(seed)
    rois = []
    for _ in range(n):
        s = [bets[r.randrange(len(bets))] for _ in bets]
        stake = sum(b["stake"] for b in s)
        rois.append(sum(b["pnl"] for b in s) / stake if stake else 0)
    rois.sort()
    return rois[int(0.025 * n)], rois[int(0.975 * n)]


def max_drawdown(curve: list[float]) -> float:
    peak, dd = -1e18, 0.0
    for x in curve:
        peak = max(peak, x)
        dd = max(dd, peak - x)
    return dd


def bets_needed(edge: float, avg_price: float, z: float = 1.96) -> int:
    """Bets needed for a 1-unit flat bettor's ROI to be `z` SEs above zero."""
    if edge <= 0:
        return 0
    p = (1 + edge) / avg_price
    var = p * (avg_price - 1) ** 2 + (1 - p) - edge ** 2
    return int(math.ceil((z ** 2) * var / edge ** 2))


def summarize(bets: list[dict]) -> dict:
    settled = [b for b in bets if b["status"] == "settled"]
    openb = [b for b in bets if b["status"] == "open"]
    stake = sum(b["stake"] for b in settled)
    pnl = sum(b["pnl"] for b in settled)
    exp_pnl = sum(b["stake"] * b["ev"] for b in settled)
    var = sum((b["stake"] ** 2) * (b["price"] ** 2) * b["q"] * (1 - b["q"]) for b in settled)
    luck_z = (pnl - exp_pnl) / math.sqrt(var) if var > 0 else 0.0
    unit_ret = [b["pnl"] / b["stake"] for b in settled if b["stake"] > 0]
    roi_m, _, _, roi_t = mean_ci(unit_ret)
    lo, hi = bootstrap_roi(settled)
    clvs = [b["clv"] for b in bets if b["clv"] is not None and b["status"] != "void"]
    clv_m, clv_lo, clv_hi, clv_t = mean_ci(clvs)
    beat_close = sum(1 for c in clvs if c > 0) / len(clvs) if clvs else float("nan")
    brier = st.fmean([(b["q"] - b["result"]) ** 2 for b in settled]) if settled else float("nan")
    brier_mkt = st.fmean([(1 / b["price"] - b["result"]) ** 2 for b in settled]) if settled else float("nan")
    avg_ev = st.fmean([b["ev"] for b in bets]) if bets else 0.0
    avg_price = st.fmean([b["price"] for b in bets]) if bets else 2.0
    curve, run = [], 0.0
    for b in sorted(settled, key=lambda b: b["settled_ts"] or ""):
        run += b["pnl"]
        curve.append(run)
    need = bets_needed(max(clv_m if not math.isnan(clv_m) else avg_ev, 1e-6), avg_price)
    p_clv = norm_sf(clv_t) if not math.isnan(clv_t) else float("nan")
    p_roi = norm_sf(roi_t) if not math.isnan(roi_t) else float("nan")
    return {
        "n_bets": len(bets), "n_settled": len(settled), "n_open": len(openb),
        "staked": stake, "pnl": pnl, "roi": pnl / stake if stake else 0.0, "roi_ci": [lo, hi],
        "roi_t": roi_t, "p_roi": p_roi, "expected_pnl": exp_pnl, "luck_z": luck_z,
        "avg_ev": avg_ev, "avg_price": avg_price,
        "clv": clv_m, "clv_ci": [clv_lo, clv_hi], "clv_t": clv_t, "p_clv": p_clv, "beat_close": beat_close,
        "brier": brier, "brier_market": brier_mkt, "max_dd": max_drawdown([0] + curve),
        "bets_needed": need,
        "true_ev": st.fmean([b["true_ev"] for b in bets]) if bets and bets[0].get("true_ev") is not None else None,
        "edge_capture": (clv_m / avg_ev) if avg_ev and not math.isnan(clv_m) else None, "verdict": verdict(len(clvs), clv_t, len(settled), roi_t, need),
    }


def verdict(n_clv, clv_t, n_settled, roi_t, need) -> str:
    if n_clv < 50:
        return "COLLECTING — under 50 bets, no conclusion yet"
    if math.isnan(clv_t):
        return "COLLECTING"
    if clv_t < -2:
        return "REJECT — consistently losing to the closing line"
    if clv_t > 3 and n_settled >= need and roi_t > 2:
        return "CONFIRMED — beats the close AND realised ROI is significant"
    if clv_t > 3:
        return f"PROMISING — significant +CLV; ROI needs ~{need} settled bets to confirm"
    return "INCONCLUSIVE — CLV not yet distinguishable from zero"


def calibration_table(pairs: list[tuple[float, float]], bins: int = 10) -> list[dict]:
    rows = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        b = [(q, y) for q, y in pairs if lo <= q < hi or (i == bins - 1 and q == 1)]
        if b:
            rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "pred": st.fmean(q for q, _ in b),
                         "actual": st.fmean(y for _, y in b), "n": len(b)})
    return rows


def log_loss(pairs):
    return st.fmean(-math.log(max(1e-9, q if y else 1 - q)) for q, y in pairs) if pairs else float("nan")
