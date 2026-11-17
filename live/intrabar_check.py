"""How much of the variant's backtest edge survives real intrabar order?

For sampled days with 1-minute Nasdaq-100 bars, each 5-minute bar is replayed minute by minute:
entries trigger on the minute that breaks the channel, and the stop is live from the next
minute on. Indicators (channel, ATR, trailing updates) still come from the 5-minute bars, as
they will live. The entry minute itself is ambiguous; it is reported both ways.
"""
import glob
import json
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from live.engine import DonchianEngine, Params  # noqa: E402

COST_PTS, USD = 1.12, 2.0
T = "/root/.claude/projects/-home-user-Hill/803afca5-f875-54ad-bfda-380b3ec237b9/tool-results/"


def load_minutes(pattern, symbol="NDX"):
    rows = []
    for f in glob.glob(T + pattern):
        j = json.load(open(f))
        for r in j["data"]["results"]:
            if r["symbol"] != symbol:
                continue
            for b in r["bars"]:
                if b.get("interpolated"):
                    continue
                rows.append((b["begins_at"], *(float(b[k]) for k in ("open_value", "high_value", "low_value", "close_value"))))
    m = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c"])
    m["ts"] = pd.to_datetime(m.ts, utc=True).dt.tz_convert("America/New_York")
    return m.drop_duplicates("ts").sort_values("ts").set_index("ts")


def run(five, minutes, sample_days, entry_minute_stop):
    """Return {date: [trade pnl $]} for the sampled days, replaying minutes on those days."""
    eng = DonchianEngine(Params())
    counts = five.groupby(five.index.date).size()
    out = {}
    for date, g in five.groupby(five.index.date, sort=True):
        tradable = counts[date] == 78
        use_min = date in sample_days
        day_trades = []
        for k, (ts, b) in enumerate(g.iterrows()):
            eng.begin_bar(date, k, b.open, tradable)
            if use_min:
                mins = minutes.loc[ts: ts + pd.Timedelta(minutes=4)]
                for mt, mb in mins.iterrows():
                    if eng.pos is None:
                        lv = eng.entry_levels()
                        if lv is None:
                            continue
                        up, dn = mb.h > eng.hi, mb.l < eng.lo
                        if up and not dn:
                            e = eng.enter(1, max(eng.hi, mb.o))
                        elif dn and not up:
                            e = eng.enter(-1, min(eng.lo, mb.o))
                        else:
                            continue
                        day_trades.append([e.price, e.side, None])
                        ps = eng.pos
                        hit = (mb.l <= ps.stop) if ps.side > 0 else (mb.h >= ps.stop)
                        closed_beyond = (mb.c <= ps.stop) if ps.side > 0 else (mb.c >= ps.stop)
                        if (entry_minute_stop and hit) or closed_beyond:
                            x = eng.force_exit(ps.stop, "stop")
                            day_trades[-1][2] = x.price
                    else:
                        ps = eng.pos
                        if (ps.side > 0 and mb.l <= ps.stop) or (ps.side < 0 and mb.h >= ps.stop):
                            px = min(ps.stop, mb.o) if ps.side > 0 else max(ps.stop, mb.o)
                            x = eng.force_exit(px, "stop")
                            day_trades[-1][2] = x.price
            else:
                if eng.pos is None:
                    lv = eng.entry_levels()
                    if lv is not None:
                        up, dn = b.high > eng.hi, b.low < eng.lo
                        if up != dn:
                            e = eng.enter(1 if up else -1, max(eng.hi, b.open) if up else min(eng.lo, b.open))
                            day_trades.append([e.price, e.side, None])
                else:
                    e = eng.on_price(b.high, b.low)
                    if e and e.kind == "exit":
                        day_trades[-1][2] = e.price
            for e in eng.end_bar(b.open, b.high, b.low, b.close, entry_bar_close_rule=not use_min):
                if e.kind == "exit":
                    day_trades[-1][2] = e.price
        if date in sample_days:
            out[date] = [((x - p) * s - COST_PTS) * USD for p, s, x in day_trades]
    return out


if __name__ == "__main__":
    five = pd.read_csv("data/5min/NDX.csv.gz")
    five["ts"] = pd.to_datetime(five.ts, utc=True).dt.tz_convert("America/New_York")
    five = five.set_index("ts")
    minutes = pd.read_csv("data/ndx_1min_sample.csv.gz")
    minutes["ts"] = pd.to_datetime(minutes.ts, utc=True).dt.tz_convert("America/New_York")
    minutes = minutes.set_index("ts")
    days = sorted(set(minutes.index.date))
    # sanity: minute bars aggregate to the 5-minute bars
    agg = minutes.resample("5min").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    j = agg.join(five, how="inner")
    print(f"{len(days)} sampled days, {len(minutes)} minute bars; 5-min match: max |high diff| {np.abs(j.h - j.high).max():.2f}, "
          f"|close diff| {np.abs(j.c - j.close).max():.2f} points over {len(j)} bars")
    sd = set(days)
    import donchian_lab as L
    BEST = dict(n=24, exit_n=24, stop_atr=1.0, max_trades=3, last_bar=48, trail_atr=2.0)
    ref = {x["date"]: [r[0] * USD for r in L.dx(x, **BEST)] for x in L.prepare("data/5min/NDX.csv.gz", usd=USD, cost_pts=COST_PTS)}
    opt = run(five, minutes, sd, entry_minute_stop=False)
    pes = run(five, minutes, sd, entry_minute_stop=True)
    rows = []
    for d in days:
        if d not in ref:
            continue
        rows.append(dict(date=d, bt=sum(ref[d]), n_bt=len(ref[d]), opt=sum(opt.get(d, [])), pes=sum(pes.get(d, [])), n_min=len(pes.get(d, []))))
    r = pd.DataFrame(rows)
    print(r.to_string(index=False, float_format=lambda v: f"{v:,.0f}"))
    print(f"\nPer sampled day (n={len(r)}): backtest ${r.bt.mean():,.1f}, minute-resolved ${r.opt.mean():,.1f} (entry minute optimistic) "
          f"/ ${r.pes.mean():,.1f} (entry minute pessimistic)")
    print(f"Totals: backtest ${r.bt.sum():,.0f}, minute-resolved ${r.opt.sum():,.0f} / ${r.pes.sum():,.0f}; "
          f"trades {r.n_bt.sum()} vs {r.n_min.sum()}")
    r.to_csv("live/intrabar_sample.csv", index=False)
