"""Replay history through the live engine and compare with the backtest (donchian_lab.dx).

Mode "backtest" fills exactly like the backtest (bar-complete breakout check, entry-bar stop
only on a close beyond it) and must match trade for trade. Mode "live" fills the way a broker
would: the first side touched wins and the stop works from the moment of entry, assuming the
worst case on the entry bar.
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from live.engine import DonchianEngine, Params  # noqa: E402

COST_PTS, USD = 1.12, 2.0      # 1 MNQ round trip, $ per point


def bars(path):
    d = pd.read_csv(path)
    d["ts"] = pd.to_datetime(d.ts, utc=True).dt.tz_convert("America/New_York")
    return d


def replay(d, mode="backtest"):
    eng = DonchianEngine(Params())
    counts = d.groupby(d.ts.dt.date).size()
    trades = []
    for date, g in d.groupby(d.ts.dt.date, sort=True):
        tradable = counts[date] == 78
        for k, b in enumerate(g.itertuples()):
            eng.begin_bar(date, k, b.open, tradable)
            ev = None
            if mode == "backtest":
                if eng.pos is None:
                    lv = eng.entry_levels()
                    if lv is not None:
                        up, dn = b.high > eng.hi, b.low < eng.lo
                        if up != dn:
                            ev = eng.enter(1 if up else -1, max(eng.hi, b.open) if up else min(eng.lo, b.open))
                            trades.append(dict(date=date, side=ev.side, entry=ev.price, bar=k))
                else:
                    ev = eng.on_price(b.high, b.low)
                    if ev and ev.kind == "exit":
                        trades[-1].update(exit=ev.price, why=ev.reason, xbar=k)
                for e in eng.end_bar(b.open, b.high, b.low, b.close, entry_bar_close_rule=True):
                    if e.kind == "exit":
                        trades[-1].update(exit=e.price, why=e.reason, xbar=k)
            else:
                ev = eng.on_price(b.high, b.low)
                if ev and ev.kind == "entry":
                    trades.append(dict(date=date, side=ev.side, entry=ev.price, bar=k))
                    ev2 = eng.on_price(b.high, b.low)          # worst case: stop touched later in the entry bar
                    if ev2 and ev2.kind == "exit":
                        trades[-1].update(exit=ev2.price, why="stop", xbar=k)
                elif ev and ev.kind == "exit":
                    trades[-1].update(exit=ev.price, why=ev.reason, xbar=k)
                for e in eng.end_bar(b.open, b.high, b.low, b.close):
                    if e.kind == "exit":
                        trades[-1].update(exit=e.price, why=e.reason, xbar=k)
    t = pd.DataFrame(trades)
    t["pnl"] = ((t.exit - t.entry) * t.side - COST_PTS) * USD
    return t


if __name__ == "__main__":
    import intraday as it
    import donchian_lab as L
    BEST = dict(n=24, exit_n=24, stop_atr=1.0, max_trades=3, last_bar=48, trail_atr=2.0)
    d = bars("data/5min/NDX.csv.gz")
    days = L.prepare("data/5min/NDX.csv.gz", usd=USD, cost_pts=COST_PTS)
    ref = {x["date"]: [r[0] for r in L.dx(x, **BEST)] for x in days}
    t = replay(d, "backtest")
    t = t[t.date.isin(ref.keys())]
    mine = t.groupby("date").pnl.apply(list).to_dict()
    mism = [k for k in ref if [round(v * USD, 6) for v in ref[k]] != [round(v, 6) for v in mine.get(k, [])]]
    total_ref = sum(sum(v) for v in ref.values()) * USD
    print(f"backtest-mode parity: {len(ref)} days, {sum(len(v) for v in ref.values())} backtest trades vs {len(t)} engine trades, "
          f"days that differ: {len(mism)}; net ${total_ref:,.0f} backtest vs ${t.pnl.sum():,.0f} engine")
    if mism:
        print("first differing days:", mism[:5])
    lv = replay(d, "live")
    lv = lv[lv.date.isin(ref.keys())]
    print(f"live-fill mode (worst case on entry bars): {len(lv)} trades, net ${lv.pnl.sum():,.0f}, "
          f"win {np.mean(lv.pnl > 0):.0%}, stopped on entry bar {np.mean(lv.bar == lv.xbar):.0%}")
