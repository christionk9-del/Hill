"""Backtest on the public TrevorJS/kalshi-trades dataset (Hugging Face, CC-BY-4.0).

Download:  python -m kalshi_bot backtest --download     (~5.7 GB into data/hf/)
Run:       python -m kalshi_bot backtest                (reports + writes data/calibration.json)

Three questions, answered from real trades:
 1. What did people buying Kalshi combos actually get back?          (combo_report)
 2. Are single legs mispriced at PRE-GAME prices?                     (leg_report)
 3. Does the bot's walk-forward leg selection hold up out of sample?  (walk_forward)

Look-ahead guard: a leg's price is the taker VWAP over the 12h ending at
min(close - 6h, first time the outcome was obvious (95c/5c) - 4h), i.e. before
the game started. Averaging over a market's whole life (including in-game
trading) leaks the outcome and makes favorites look ~20% profitable. They aren't.
"""
import os
import subprocess

from .calibration import BUCKETS
from .config import CONFIG

REPO = "https://huggingface.co/datasets/TrevorJS/kalshi-trades/resolve/main"
FILES = [f"markets-{i:04d}" for i in range(4)] + [f"trades-{i:04d}" for i in range(16)]
SPLIT = "2025-10-01"  # walk-forward: learn before, test after
COMBO_SPLIT = "2025-12-15"


def download(data_dir="data/hf"):
    os.makedirs(data_dir, exist_ok=True)
    for f in FILES:
        out = os.path.join(data_dir, f + ".parquet")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            continue
        print("downloading", f)
        subprocess.run(["curl", "-sfL", "--retry", "4", "-o", out, f"{REPO}/{f}.parquet"], check=True)


def _bucket_sql(col="px"):
    whens = " ".join(f"WHEN {col} BETWEEN {lo} AND {hi} THEN '{lo}-{hi}'" for lo, hi in BUCKETS)
    return f"CASE {whens} END"


def build(con, data_dir="data/hf"):
    m, t = f"'{data_dir}/markets-*.parquet'", f"'{data_dir}/trades-*.parquet'"
    con.sql(f"""CREATE OR REPLACE TABLE mkts AS
      SELECT ticker, event_ticker, regexp_extract(event_ticker,'^([A-Z0-9]+)',1) series, title, result,
             close_time, len(title)-len(replace(title,',',''))+1 AS legs
      FROM {m} WHERE result IN ('yes','no')""")
    con.sql(f"""CREATE OR REPLACE TABLE combo_trades AS
      SELECT t.ticker, t.count, t.yes_price/100.0 py, t.no_price/100.0 pn, t.taker_side, t.created_time,
             mk.series, mk.legs, (mk.result='yes')::int win
      FROM {t} t JOIN mkts mk USING(ticker) WHERE mk.series LIKE 'KXMVE%' AND t.yes_price >= 1""")
    con.sql(f"""CREATE OR REPLACE TABLE legs_pre AS
      WITH tr AS (SELECT t.*, mk.series, mk.event_ticker, mk.close_time, mk.result
                  FROM {t} t JOIN mkts mk USING(ticker) WHERE mk.series NOT LIKE 'KXMVE%'),
      cut AS (SELECT ticker, least(any_value(close_time) - INTERVAL 6 HOUR,
                coalesce(min(created_time) FILTER (WHERE (result='yes' AND yes_price>=95) OR (result='no' AND yes_price<=5)),
                         TIMESTAMP '2100-01-01') - INTERVAL 4 HOUR) cutoff FROM tr GROUP BY 1)
      SELECT tr.ticker, any_value(series) series, any_value(event_ticker) event_ticker, taker_side side,
             round(sum(count*CASE WHEN taker_side='yes' THEN yes_price ELSE no_price END)/sum(count))::INT px,
             max(created_time) ts, (any_value(result)=taker_side)::int win
      FROM tr JOIN cut USING(ticker)
      WHERE created_time <= cutoff AND created_time >= cutoff - INTERVAL 12 HOUR
      GROUP BY tr.ticker, taker_side""")
    con.sql(f"""CREATE OR REPLACE VIEW L AS SELECT *, px/100.0 p, px/100.0*(1+0.07*(1-px/100.0)) cost,
      {_bucket_sql()} bucket, ts < TIMESTAMP '{SPLIT}' train FROM legs_pre""")


def combo_report(con):
    fee = "count*py*(1+0.07*(1-py))"
    return con.sql(f"""SELECT CASE WHEN legs<=2 THEN '02' WHEN legs<=4 THEN '03-04' WHEN legs<=7 THEN '05-07'
        WHEN legs<=11 THEN '08-11' ELSE '12+' END legs, count(*) trades,
      round(sum(count*win) FILTER (WHERE created_time <  TIMESTAMP '{COMBO_SPLIT}') /
            sum({fee})      FILTER (WHERE created_time <  TIMESTAMP '{COMBO_SPLIT}'),3) yes_ret_early,
      round(sum(count*win) FILTER (WHERE created_time >= TIMESTAMP '{COMBO_SPLIT}') /
            sum({fee})      FILTER (WHERE created_time >= TIMESTAMP '{COMBO_SPLIT}'),3) yes_ret_late
      FROM combo_trades WHERE taker_side='yes' GROUP BY 1 ORDER BY 1""")


def leg_report(con, series_regex="NBA|NFL|NHL|MLB|NCAA|WNBA|EPL|UCL|MLS|UFC|ATP|WTA|PGA"):
    return con.sql(f"""SELECT bucket, count(*) FILTER (WHERE train) n_train,
      round(sum(win) FILTER (WHERE train)/sum(cost) FILTER (WHERE train),3) ret_train,
      count(*) FILTER (WHERE NOT train) n_test,
      round(sum(win) FILTER (WHERE NOT train)/sum(cost) FILTER (WHERE NOT train),3) ret_test
      FROM L WHERE regexp_matches(series, '{series_regex}') GROUP BY 1 ORDER BY min(px)""")


def walk_forward(con, cfg=CONFIG, min_n=100, min_ret=1.05):
    con.sql(f"""CREATE OR REPLACE TABLE picks AS SELECT series, bucket, count(*) n, sum(win)/sum(cost) ret_train
      FROM L WHERE train AND px BETWEEN {cfg.combo_leg_min_price_c} AND {cfg.combo_leg_max_price_c}
      GROUP BY 1,2 HAVING count(*) >= {min_n} AND sum(win)/sum(cost) >= {min_ret}""")
    return con.sql("""SELECT p.series, p.bucket, p.n n_train, round(p.ret_train,3) ret_train,
      count(l.ticker) n_test, round(sum(l.win)/nullif(sum(l.cost),0),3) ret_test
      FROM picks p LEFT JOIN L l ON l.series=p.series AND l.bucket=p.bucket AND NOT l.train
      GROUP BY ALL ORDER BY n_test DESC""")


def write_calibration(con, path=CONFIG.calibration_path):
    """Calibration table for the live bot, from ALL pre-game legs (train + test)."""
    from .calibration import save, wilson_lower
    table = {}
    for grp, n, wins, avg_px, ret in con.sql("""
        SELECT grp, count(*), sum(win), avg(px), sum(win)*100.0/sum(px) FROM (
          SELECT 'S:'||series||'|'||bucket grp, win, px FROM L WHERE bucket IS NOT NULL
          UNION ALL SELECT 'ALL|'||bucket, win, px FROM L WHERE bucket IS NOT NULL) GROUP BY grp""").fetchall():
        table[grp] = {"n": n, "win_rate": wins / n, "win_rate_lower90": wilson_lower(wins, n),
                      "avg_price_c": avg_px, "return_per_dollar": ret}
    save(table, path)
    return len(table)


def run(data_dir="data/hf", db="data/bt.duckdb"):
    import duckdb
    con = duckdb.connect(db)
    con.sql("SET memory_limit='12GB'")
    build(con, data_dir)
    print("== 1. Real Kalshi combos: $ back per $1 for YES buyers (after taker fee) ==")
    print(combo_report(con))
    print("== 2. Sports single legs at pre-game prices: $ back per $1 ==")
    print(leg_report(con))
    print(f"== 3. Walk-forward: leg types +EV before {SPLIT}, and how they did after ==")
    print(walk_forward(con))
    print(f"Wrote {write_calibration(con)} calibration buckets to {CONFIG.calibration_path}")
