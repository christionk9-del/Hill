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


# ---------------- "only +EV bets" -----------------------------------------
# A segment = combo product x leg count x price range x hours before close.
# Honest version: segments chosen on data BEFORE the split, bet only after it.
# Hindsight version: segments chosen on the same data they're scored on (cheating).
SEG = """series || '|' ||
  CASE WHEN legs<=2 THEN 'L02' WHEN legs<=4 THEN 'L03-04' WHEN legs<=7 THEN 'L05-07' WHEN legs<=11 THEN 'L08-11' ELSE 'L12+' END || '|' ||
  CASE WHEN py<0.03 THEN 'P01-02' WHEN py<0.06 THEN 'P03-05' WHEN py<0.11 THEN 'P06-10' WHEN py<0.21 THEN 'P11-20'
       WHEN py<0.41 THEN 'P21-40' ELSE 'P41+' END || '|' ||
  CASE WHEN h<1 THEN 'H<1' WHEN h<4 THEN 'H1-4' WHEN h<24 THEN 'H4-24' ELSE 'H24+' END"""


# The dataset FLOORS sub-cent combo prices to whole cents (checked against Kalshi's
# /historical/trades exact prices: "1c" trades really cost ~1.6c, "2c" ~2.7c).
# Adding half a cent removes most of that bias; without it cheap combos look +EV.
FLOOR_FIX = 0.005


def _combo_seg_table(con):
    con.sql(f"""CREATE OR REPLACE TEMP TABLE cs AS
      SELECT date_diff('day', TIMESTAMP '2025-09-01', c.created_time) d, c.py + {FLOOR_FIX} AS py, c.win,
             c.created_time < TIMESTAMP '2025-12-15' AS train,
             {SEG.replace('series', 'c.series').replace('legs', 'c.legs')} seg
      FROM (SELECT ct.*, date_diff('minute', ct.created_time, m.close_time)/60.0 h
            FROM combo_trades ct JOIN mkts m USING(ticker) WHERE ct.taker_side='yes') c""")


def _good_segments(con, table, where, min_n, min_ret):
    return con.sql(f"""SELECT seg FROM {table} WHERE {where} GROUP BY seg
      HAVING count(*) >= {min_n} AND avg(win / (py*(1+{FEE}*(1-py)))) >= {min_ret}""")


def _replay_where(con, table, cond):
    arr = con.sql(f"SELECT d, py, win FROM {table} WHERE {cond}").fetchnumpy()
    return replay(arr) if len(arr["d"]) else None


def ev_filter_report(db="data/bt.duckdb", min_n=300, min_ret=1.05):
    import duckdb
    con = duckdb.connect(db)
    _combo_seg_table(con)
    con.sql("""CREATE OR REPLACE TEMP TABLE ls AS
      SELECT date_diff('day', TIMESTAMP '2025-09-01', ts) d, p py, win, NOT train AND ts >= TIMESTAMP '2025-09-01' AS testp,
             train, series || '|' || bucket seg FROM L""")
    out = []
    # combos
    honest = _good_segments(con, "cs", "train", min_n, min_ret).fetchall()
    con.sql("CREATE OR REPLACE TEMP TABLE g1 AS " + _good_segments(con, "cs", "train", min_n, min_ret).sql_query())
    con.sql("CREATE OR REPLACE TEMP TABLE g2 AS " + _good_segments(con, "cs", "NOT train", min_n, min_ret).sql_query())
    out.append(("Combos, all (test period)", _replay_where(con, "cs", "NOT train")))
    out.append((f"Combos, +EV segments picked in HINDSIGHT ({con.sql('select count(*) from g2').fetchall()[0][0]} segs)",
                _replay_where(con, "cs", "NOT train AND seg IN (SELECT seg FROM g2)")))
    out.append((f"Combos, +EV segments picked from PAST data ({len(honest)} segs)",
                _replay_where(con, "cs", "NOT train AND seg IN (SELECT seg FROM g1)")))
    # single legs
    con.sql("CREATE OR REPLACE TEMP TABLE g3 AS " + _good_segments(con, "ls", "train", min_n // 3, min_ret).sql_query())
    con.sql("CREATE OR REPLACE TEMP TABLE g4 AS " + _good_segments(con, "ls", "testp", min_n // 3, min_ret).sql_query())
    out.append(("Single legs, all (test period)", _replay_where(con, "ls", "testp")))
    out.append((f"Singles, +EV picked in HINDSIGHT ({con.sql('select count(*) from g4').fetchall()[0][0]} segs)",
                _replay_where(con, "ls", "testp AND seg IN (SELECT seg FROM g4)")))
    out.append((f"Singles, +EV picked from PAST data ({con.sql('select count(*) from g3').fetchall()[0][0]} segs)",
                _replay_where(con, "ls", "testp AND seg IN (SELECT seg FROM g3)")))
    hdr = f"{'Strategy ($5/day, test period only)':58} {'days':>4} {'spent':>6} {'median P&L':>10} {'P(profit)':>9} {'$ back/$1':>9} {'P(any $350 wk)':>14}"
    print(hdr); print("-" * len(hdr))
    for name, r in out:
        if r is None:
            print(f"{name:58}  (no bets qualified)"); continue
        print(f"{name:58} {r['days']:>4} {r['spent_$']:>6.0f} {r['median_pnl_$']:>10.0f} {r['p_profitable']:>9.0%} "
              f"{r['return_per_$']:>9.2f} {r['p_any_350_week']:>14.0%}")
    rows = con.sql(f"""SELECT sum(win/(py*(1+{FEE}*(1-py)))) p, count(*) n FROM cs
        WHERE NOT train AND seg IN (SELECT seg FROM g1) GROUP BY d ORDER BY p DESC""").fetchall()
    for k in (0, 1, 3):
        rest = rows[k:]
        if rest:
            print(f"Past-picked combos, next period, without best {k} day(s): "
                  f"${sum(r[0] for r in rest) / sum(r[1] for r in rest):.3f} per $1")
    print("\nCombo segments that looked +EV in the past, and what they did next:")
    print(con.sql("""SELECT seg, count(*) FILTER (WHERE train) n_past,
        round(avg(win/(py*(1+0.07*(1-py)))) FILTER (WHERE train),2) past_ret,
        count(*) FILTER (WHERE NOT train) n_next, round(avg(win/(py*(1+0.07*(1-py)))) FILTER (WHERE NOT train),2) next_ret
        FROM cs WHERE seg IN (SELECT seg FROM g1) GROUP BY seg ORDER BY n_next DESC LIMIT 15"""))
    return out
