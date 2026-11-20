"""P&L replay of the $5/day plan on real historical Kalshi trades.

Each day, each simulated bettor puts $5 into ONE real trade that actually
happened that day (random draw), at the real price paid, settled by the real
result. Repeated over many paths to get a distribution, not one lucky run.
Requires `python -m kalshi_bot backtest` first (builds data/bt.duckdb).
"""
import numpy as np

FEE = 0.07
BUCKETS = {"2 legs": (2, 2), "3-4 legs": (3, 4), "5-7 legs": (5, 7),
           "8-11 legs": (8, 11), "12+ legs (screenshot style)": (12, 999)}


def _load_combo(con, lo, hi):
    return con.sql(f"""SELECT date_diff('day', TIMESTAMP '2025-09-01', created_time) d, py, win
        FROM combo_trades WHERE taker_side='yes' AND legs BETWEEN {lo} AND {hi}""").fetchnumpy()


def _load_bot_legs(con):
    return con.sql("""SELECT date_diff('day', TIMESTAMP '2025-09-01', ts) d, p py, win
        FROM L JOIN picks USING(series, bucket) WHERE NOT train""").fetchnumpy()


def replay(arr, stake=5.0, paths=2000, seed=0, target=350.0):
    rng = np.random.default_rng(seed)
    d, py, win = arr["d"], arr["py"].astype(float), arr["win"].astype(float)
    order = np.argsort(d, kind="stable")
    d, py, win = d[order], py[order], win[order]
    days, starts, counts = np.unique(d, return_index=True, return_counts=True)
    # one random trade per day per path
    idx = starts[None, :] + (rng.random((paths, len(days))) * counts[None, :]).astype(int)
    price = py[idx] * (1 + FEE * (1 - py[idx]))
    payout = np.where(win[idx] > 0, stake / price, 0.0)
    daily = payout - stake
    weeks = (days - days[0]) // 7
    n_weeks = weeks.max() + 1
    wk_payout = np.zeros((paths, n_weeks))
    np.add.at(wk_payout.T, weeks, payout.T)
    wk_spent = np.bincount(weeks, minlength=n_weeks) * stake
    total = daily.sum(1)
    equity = np.cumsum(daily, 1)
    drawdown = (np.maximum.accumulate(np.maximum(equity, 0), 1) - equity).max(1)
    return {
        "days": len(days), "weeks": int(n_weeks), "spent_$": len(days) * stake,
        "median_pnl_$": float(np.median(total)), "mean_pnl_$": float(total.mean()),
        "p10_pnl_$": float(np.percentile(total, 10)), "p90_pnl_$": float(np.percentile(total, 90)),
        "p_profitable": float((total > 0).mean()),
        "hit_rate": float((payout > 0).mean()),
        "weeks_paying_350+_per_path": float((wk_payout >= target).sum(1).mean()),
        "p_any_350_week": float(((wk_payout >= target).sum(1) > 0).mean()),
        "median_max_drawdown_$": float(np.median(drawdown)),
        "return_per_$": float(payout.sum() / (stake * payout.size)),
        "_weekly_spent": float(wk_spent.mean()),
    }


def run(db="data/bt.duckdb", stake=5.0):
    import duckdb
    con = duckdb.connect(db, read_only=True)
    rows = [(name, replay(_load_combo(con, lo, hi), stake)) for name, (lo, hi) in BUCKETS.items()]
    rows.append(("Bot's picked single legs (BTC/weather)", replay(_load_bot_legs(con), stake)))
    hdr = (f"{'Strategy ($5/day)':40} {'days':>4} {'spent':>6} {'median P&L':>10} {'mean P&L':>9} "
           f"{'10th-90th pct':>17} {'P(profit)':>9} {'hit%':>6} {'P(any $350 wk)':>14} {'med maxDD':>9}")
    print(hdr); print("-" * len(hdr))
    for name, r in rows:
        print(f"{name:40} {r['days']:>4} {r['spent_$']:>6.0f} {r['median_pnl_$']:>10.0f} {r['mean_pnl_$']:>9.0f} "
              f"{r['p10_pnl_$']:>8.0f} to {r['p90_pnl_$']:>5.0f} {r['p_profitable']:>9.0%} {r['hit_rate']:>6.1%} "
              f"{r['p_any_350_week']:>14.0%} {r['median_max_drawdown_$']:>9.0f}")
    return rows
