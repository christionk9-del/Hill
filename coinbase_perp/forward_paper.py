"""Paper forward test on live Coinbase prices: runs every finished New York session through the real bot.

For each session not yet recorded (from --start, default the day the rules were frozen), it downloads
that day's 1-minute bars and the prior 5-minute bars from Coinbase's public API, runs the bot against
the simulated exchange (fees, slippage, stop-limit fills), and books the day on a paper account for
each market and rule set. Accounts compound; nothing is ever re-run or edited.

    python coinbase_perp/forward_paper.py                 # catch up to the last finished session
    python coinbase_perp/forward_paper.py --start 2026-11-17 --fee-bps 2 --slip-bps 1

Writes forward/coinbase/days.csv, trades.jsonl and state.json.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from coinbase_perp.bot import NY, PRESETS                 # noqa: E402
from coinbase_perp.fetch_minutes import session_minutes   # noqa: E402
from coinbase_perp.sim_exchange import run_day            # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "forward", "coinbase")
SPOT = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
URL = "https://api.exchange.coinbase.com/products/{}/candles"


def five_minute(product, end_day, days=10, s=None):
    s = s or requests.Session()
    end = pd.Timestamp(f"{end_day} 00:00", tz=NY)
    t, rows = end - pd.Timedelta(days=days), []
    while t < end:
        hi = min(t + pd.Timedelta(minutes=5 * 299), end)
        r = s.get(URL.format(product), timeout=30, params=dict(granularity=300, start=t.tz_convert("UTC").isoformat(),
                                                               end=hi.tz_convert("UTC").isoformat()))
        r.raise_for_status()
        rows += r.json()
        t = hi
    d = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"]).drop_duplicates("t")
    d["ts"] = pd.to_datetime(d.t, unit="s", utc=True).dt.tz_convert(NY)
    d = d.sort_values("ts").set_index("ts")[["open", "high", "low", "close"]]
    m = d.index.hour * 60 + d.index.minute
    return d[(m >= 570) & (m < 960) & (d.index.dayofweek < 5) & (d.index < end)]


def last_finished_session(now=None):
    now = now or datetime.now(NY)
    d = now.date() if now.hour * 60 + now.minute >= 16 * 60 + 5 else now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-11-17")
    ap.add_argument("--fee-bps", type=float, default=2.0, help="exchange fee per side, bps of notional")
    ap.add_argument("--slip-bps", type=float, default=1.0, help="slippage per fill, bps")
    ap.add_argument("--equity", type=float, default=10000.0)
    ap.add_argument("--risk-pct", type=float, default=0.5)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    sp = os.path.join(OUT, "state.json")
    st = json.load(open(sp)) if os.path.exists(sp) else dict(
        settings=dict(fee_bps=a.fee_bps, slip_bps=a.slip_bps, risk_pct=a.risk_pct, start=a.start),
        accounts={f"{sym}/{p}": a.equity for sym in SPOT for p in PRESETS}, done=[])
    cfgs = st["settings"]
    end = last_finished_session()
    days = [d.date() for d in pd.bdate_range(cfgs["start"], end) if d.date().isoformat() not in st["done"]]
    if not days:
        print(f"nothing to do; last recorded {st['done'][-1] if st['done'] else 'none'}")
        return
    s = requests.Session()
    s.headers["User-Agent"] = "forward-test-script"
    for day in days:
        rows = []
        for sym, prod in SPOT.items():
            minutes = session_minutes(prod, day, s)
            if len(minutes) < 385:
                print(f"{day} {sym}: only {len(minutes)} minute bars; skipping the day")
                rows = None
                break
            five = five_minute(prod, day, s=s)
            for p in PRESETS:
                key = f"{sym}/{p}"
                eq = st["accounts"][key]
                r = run_day(sym, day, five, minutes, preset=p, fee_bps=cfgs["fee_bps"], slip_bps=cfgs["slip_bps"],
                            equity=eq, risk_pct=cfgs["risk_pct"])
                st["accounts"][key] = round(eq + r["pnl"], 2)
                rows.append(dict(date=day.isoformat(), market=sym, rules=p, pnl=r["pnl"], fees=r["fees"], trades=r["model_trades"],
                                 model_R=r["model_R"], equity=st["accounts"][key], leftover=r["leftover"]))
                with open(os.path.join(OUT, "trades.jsonl"), "a") as f:
                    for t in r["trades"]:
                        f.write(json.dumps(dict(t, rules=p, market=sym), default=float) + "\n")
        if rows is None:
            continue
        dp = os.path.join(OUT, "days.csv")
        pd.DataFrame(rows).to_csv(dp, mode="a", header=not os.path.exists(dp), index=False)
        st["done"].append(day.isoformat())
        json.dump(st, open(sp, "w"), indent=1)
        for x in rows:
            print(f"{x['date']} {x['market']} {x['rules']:<13} {x['trades']} trades {x['model_R']:+.2f}R  ${x['pnl']:+8.2f}  equity ${x['equity']:,.2f}")
    d = pd.read_csv(os.path.join(OUT, "days.csv"))
    print("\nTotals since", cfgs["start"])
    print(d.groupby(["market", "rules"]).agg(days=("date", "count"), pnl=("pnl", "sum"), R=("model_R", "sum"), equity=("equity", "last")).to_string())


if __name__ == "__main__":
    main()
