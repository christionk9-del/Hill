"""Write the dashboard's plan files from the backtest and the fleet simulation.

Fleet: 15 accounts with a $3,000 end-of-day drawdown plus 5 with $2,500, all on the Donchian
variant at 1 MNQ, $3,000 evaluation target, $1,000 payouts with no consistency or minimum-day
rule, $130 per evaluation. 2,000 simulated two-year paths built from 20-day blocks of history.
"Unseen year" resamples only Dec 2025 - Nov 2026 (data the variant was not chosen on);
"Full two years" resamples Dec 2024 - Nov 2026.
Outputs dashboard/plan.json, dashboard/months.json and dashboard/backtest.json.
"""
import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import intraday as it          # noqa: E402
import fleet as f              # noqa: E402
import donchian_lab as L       # noqa: E402

BEST = dict(n=24, exit_n=24, stop_atr=1.0, max_trades=3, last_bar=48, trail_atr=2.0)
FEE = 130
HERE = os.path.dirname(os.path.abspath(__file__))
CODE = open(os.path.abspath(__file__)).read()


def mixed(P):
    a = f.simulate(P, 15, 1, dict(f.FREE, dd=3000))
    b = f.simulate(P, 5, 1, dict(f.FREE, dd=2500), offset=15)
    return {k: a[k] + b[k] for k in a}


def metrics(o):
    s = {k: v[:, 6:] for k, v in o.items()}
    net = o["payouts"] * 1000 - o["evals"] * FEE
    sn = net[:, 6:]
    y2 = net[:, 12:24].sum(1)
    fees2 = (o["evals"][:, 12:24] * FEE).sum(1)
    cum = np.cumsum(net, 1)
    cash = np.maximum(-np.minimum.accumulate(cum, 1)[:, -1], FEE)
    runs = []
    for row in (s["payouts"] == 0):
        b = c = 0
        for z in row:
            c = c + 1 if z else 0
            b = max(b, c)
        runs.append(b)
    roi = np.where(fees2 > 0, y2 / np.maximum(fees2, 1), np.nan)
    return {
        "payouts_per_month": float(s["payouts"].mean()),
        "months_no_payout": float((s["payouts"] == 0).mean()),
        "months_8_plus": float((s["payouts"] >= 8).mean()),
        "evals_per_month": float(s["evals"].mean()),
        "funded_churn": float(s["funded_blown"].sum() / s["funded"].sum()),
        "funded_held": float(s["funded"].mean()),
        "net_per_month": float(sn.mean()),
        "typical_year": float(np.median(y2)),
        "bad_year": float(np.percentile(y2, 10)),
        "good_year": float(np.percentile(y2, 90)),
        "losing_years": float((y2 < 0).mean()),
        "roi_year2": float(np.nanmedian(roi)),
        "cocr_year2": float(np.median(y2 / cash)),
        "cash_needed": float(np.median(cash)),
        "dry_spell": float(np.median(runs)),
        "dry_spell_bad": float(np.percentile(runs, 90)),
    }, s["payouts"].ravel()


LABELS = [
    ("payouts_per_month", "Payouts per month", "count"),
    ("months_no_payout", "Months with no payout", "share"),
    ("months_8_plus", "Months with 8+ payouts", "share"),
    ("funded_held", "Funded accounts held", "count"),
    ("evals_per_month", "Evaluations bought per month", "count"),
    ("funded_churn", "Funded accounts lost per month", "share"),
    ("net_per_month", "Average net per month", "usd"),
    ("typical_year", "Typical year (median)", "usd"),
    ("bad_year", "Bad year (1 in 10)", "usd"),
    ("good_year", "Good year (1 in 10)", "usd"),
    ("losing_years", "Losing years", "share"),
    ("roi_year2", "Return on fees, year 2", "share"),
    ("cocr_year2", "Cash-on-cash, year 2", "share"),
    ("cash_needed", "Most cash out of pocket", "usd"),
    ("dry_spell", "Longest run of months with no payout (typical)", "months"),
    ("dry_spell_bad", "Longest run of months with no payout (1 in 10)", "months"),
]

if __name__ == "__main__":
    nq = L.prepare(os.path.join(HERE, "..", "data/5min/NDX.csv.gz"), usd=2.0, cost_pts=1.12)
    half = len(nq) // 2
    x_all = np.array([r[0] for r in it.daily(nq, L.dx, **BEST)])
    x_oos = x_all[half:]
    res, dist = {}, {}
    for key, x in (("unseen_year", x_oos), ("full_two_years", x_all)):
        P = x[f.bootstrap(len(x), 2000, 24 * f.MONTH)]
        res[key], dist[key] = metrics(mixed(P))
    rows = [dict(metric=lab, key=k, unit=u, unseen_year=round(res["unseen_year"][k], 4),
                 full_two_years=round(res["full_two_years"][k], 4)) for k, lab, u in LABELS]
    meta = [dict(label="make_plan_data.py", value=CODE, type="code", lang="python"),
            dict(label="Inputs", value="data/5min/NDX.csv.gz (Nasdaq-100 5-minute bars, Dec 2024 - Nov 2026); fleet.py; donchian_lab.py"),
            dict(label="Fleet", value="15 x $3,000 + 5 x $2,500 end-of-day drawdown, 1 MNQ each, $3,000 target, $1,000 payouts, $130 per evaluation")]
    json.dump({"rows": rows, "meta": meta}, open(os.path.join(HERE, "plan.json"), "w"), indent=1)
    buckets = [(0, 0, "0"), (1, 4, "1-4"), (5, 9, "5-9"), (10, 14, "10-14"), (15, 19, "15-19"), (20, 29, "20-29"), (30, 10**6, "30+")]
    mrows = [dict(payouts=lab, order=i, unseen_year=round(float(((dist["unseen_year"] >= lo) & (dist["unseen_year"] <= hi)).mean()), 4),
                  full_two_years=round(float(((dist["full_two_years"] >= lo) & (dist["full_two_years"] <= hi)).mean()), 4))
             for i, (lo, hi, lab) in enumerate(buckets)]
    json.dump({"rows": mrows, "meta": meta[:2]}, open(os.path.join(HERE, "months.json"), "w"), indent=1)
    cum = np.cumsum(x_all)
    brows = [dict(date=d["date"].isoformat(), pnl=round(float(p), 2), cumulative=round(float(c), 2),
                  period="chosen on" if i < half else "unseen")
             for i, (d, p, c) in enumerate(zip(nq, x_all, cum))]
    json.dump({"rows": brows, "meta": [meta[0], dict(label="Inputs", value="data/5min/NDX.csv.gz; strategy: 24-bar channel, 1 ATR stop, 2 ATR trail, entries before 13:30, max 3 trades, 1 MNQ, $2.24 round-trip costs")]},
              open(os.path.join(HERE, "backtest.json"), "w"))
    for r in rows:
        print(f"{r['metric']:<52}{r['unseen_year']:>12,.3f}{r['full_two_years']:>12,.3f}")
    print(mrows)
