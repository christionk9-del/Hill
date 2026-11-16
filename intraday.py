"""Intraday strategy screen for prop-firm evaluations (MES, regular hours only).

Data: SPY 5-minute RTH bars (proxy: 1 SPY dollar ~ 10 S&P points ~ $50 per MES contract).
Every strategy is flat by the 15:55 bar close. Stops are checked before targets inside a bar.
Parameters are chosen on the first part of the data (in-sample) and then judged on the
rest (out-of-sample), which the selection never saw.
"""
import itertools
import numpy as np
import pandas as pd

USD_PER_PT = 50.0            # $ per 1.00 SPY move per MES contract
COST_PTS = 3.74 / USD_PER_PT  # round trip: 2 x ($0.62 fees + 1 tick slippage)
IS_DAYS = 110                # in-sample length (trading days)

# ---------------------------------------------------------------- data
def load(path="data/spy_5min.csv"):
    d = pd.read_csv(path)
    d["ts"] = pd.to_datetime(d.ts, utc=True).dt.tz_convert("America/New_York")
    d = d.set_index("ts")
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean().shift()   # known at bar open
    for n in (9, 20, 50):
        d[f"ema{n}"] = d.close.ewm(span=n, adjust=False).mean()
    days, prev_close = [], None
    for date, g in d.groupby(d.index.date):
        if len(g) != 78:
            prev_close = g.close.iloc[-1]; continue
        tp = (g.high + g.low + g.close) / 3
        cv = g.volume.cumsum().replace(0, np.nan)
        vwap = (tp * g.volume).cumsum() / cv
        sd = np.sqrt(((tp - vwap) ** 2 * g.volume).cumsum() / cv)
        days.append(dict(date=date, o=g.open.values, h=g.high.values, l=g.low.values, c=g.close.values,
                         atr=g.atr.values, vwap=vwap.ffill().values, sd=sd.ffill().values,
                         ema={n: g[f"ema{n}"].values for n in (9, 20, 50)}, prev_close=prev_close))
        prev_close = g.close.iloc[-1]
    return days

# ---------------------------------------------------------------- trade walker
def walk(day, i, entry, side, stop, target=None, trail=None, exit_fn=None, check_entry_bar=True):
    """Follow one trade from bar i. Returns (pnl_pts after costs, worst open pnl pts, exit bar)."""
    h, l, c, o = day["h"], day["l"], day["c"], day["o"]
    mae = 0.0
    for j in range(i, 78):
        if j > i or check_entry_bar:
            adverse = (l[j] - entry) if side > 0 else (entry - h[j])
            hit = l[j] <= stop if side > 0 else h[j] >= stop
            if hit:
                px = stop
                if j > i:  # gap through the stop fills at the open
                    px = min(stop, o[j]) if side > 0 else max(stop, o[j])
                pnl = (px - entry) * side
                return pnl - COST_PTS, min(mae, pnl) - COST_PTS, j
            mae = min(mae, adverse)
        if target is not None and j > i:
            if (side > 0 and h[j] >= target) or (side < 0 and l[j] <= target):
                return (target - entry) * side - COST_PTS, mae - COST_PTS, j
        if exit_fn is not None and j > i and j < 77 and exit_fn(j):
            return (c[j] - entry) * side - COST_PTS, mae - COST_PTS, j
        if trail is not None and j + 1 < 78:
            a = day["atr"][j + 1]
            stop = max(stop, c[j] - trail * a) if side > 0 else min(stop, c[j] + trail * a)
    return (c[77] - entry) * side - COST_PTS, mae - COST_PTS, 77

# ---------------------------------------------------------------- strategies (return trades for one day)
def orb(day, n=6, rr=None, stop_frac=1.0, last_entry=42):
    """Opening-range breakout: range of first n bars, stop-entry either side, one trade a day."""
    hi, lo = day["h"][:n].max(), day["l"][:n].min()
    rng = hi - lo
    for i in range(n, last_entry):
        up, dn = day["h"][i] > hi, day["l"][i] < lo
        if up and dn:
            return []
        if up or dn:
            side = 1 if up else -1
            entry = max(hi, day["o"][i]) if up else min(lo, day["o"][i])
            stop = entry - side * rng * stop_frac
            tgt = entry + side * rr * rng * stop_frac if rr else None
            return [walk(day, i, entry, side, stop, tgt)[:2]]
    return []

def vwap_fade(day, k=2.0, stop_k=1.5, max_trades=2):
    """Fade a close k sigma away from session VWAP, exit on return to VWAP."""
    out, i = [], 6
    while i < 66 and len(out) < max_trades:
        sd, vw, c = day["sd"][i], day["vwap"][i], day["c"][i]
        side = 1 if c < vw - k * sd else -1 if c > vw + k * sd else 0
        if side and sd > 0:
            entry = day["o"][i + 1]
            stop = entry - side * stop_k * sd
            fn = (lambda j, s=side: (day["c"][j] - day["vwap"][j]) * s >= 0)
            pnl, mae, j = walk(day, i + 1, entry, side, stop, exit_fn=fn)
            out.append((pnl, mae)); i = j + 1
        else:
            i += 1
    return out

def momentum(day, t=6, thr=0.002, trail=4.0):
    """If price has moved more than thr from the open by bar t, follow it with an ATR trail."""
    move = day["c"][t - 1] / day["o"][0] - 1
    if abs(move) < thr:
        return []
    side = 1 if move > 0 else -1
    entry = day["o"][t]
    stop = entry - side * trail * day["atr"][t]
    return [walk(day, t, entry, side, stop, trail=trail)[:2]]

def ma_cross(day, fast=9, slow=20, trail=3.0, max_trades=3):
    """Intraday EMA cross with ATR trailing stop, long and short, 9:45-15:00 entries."""
    f, s = day["ema"][fast], day["ema"][slow]
    out, i = [], 3
    while i < 66 and len(out) < max_trades:
        up = f[i] > s[i] and f[i - 1] <= s[i - 1]
        dn = f[i] < s[i] and f[i - 1] >= s[i - 1]
        if up or dn:
            side = 1 if up else -1
            entry = day["o"][i + 1]
            stop = entry - side * trail * day["atr"][i + 1]
            fn = (lambda j, sd=side: (f[j] - s[j]) * sd < 0)
            pnl, mae, j = walk(day, i + 1, entry, side, stop, trail=trail, exit_fn=fn)
            out.append((pnl, mae)); i = j
        else:
            i += 1
    return out

def gap_fade(day, min_gap=0.002, max_gap=0.01, stop_mult=1.0):
    """Fade the opening gap back toward yesterday's close."""
    pc = day["prev_close"]
    if pc is None:
        return []
    gap = day["o"][0] / pc - 1
    if not (min_gap <= abs(gap) <= max_gap):
        return []
    side = -1 if gap > 0 else 1
    entry = day["o"][0]
    stop = entry - side * abs(entry - pc) * stop_mult
    return [walk(day, 0, entry, side, stop, target=pc, check_entry_bar=True)[:2]]

GRIDS = {
    "Opening range breakout": (orb, dict(n=[3, 6, 12], rr=[None, 1.0, 2.0], stop_frac=[0.5, 1.0])),
    "VWAP fade": (vwap_fade, dict(k=[1.5, 2.0, 2.5], stop_k=[1.0, 2.0])),
    "Open-drive momentum": (momentum, dict(t=[6, 12], thr=[0.001, 0.002, 0.004], trail=[3.0, 5.0])),
    "EMA cross + ATR trail": (ma_cross, dict(fast=[9, 20], slow=[20, 50], trail=[2.0, 3.0, 5.0])),
    "Gap fade": (gap_fade, dict(min_gap=[0.001, 0.002, 0.004], stop_mult=[0.5, 1.0, 2.0])),
}

# ---------------------------------------------------------------- evaluation
def daily(days, fn, **p):
    """Per-day ($ pnl, $ worst intraday point, trade pnls) for one contract."""
    rows = []
    for d in days:
        tr = fn(d, **p)
        run, worst = 0.0, 0.0
        for pnl, mae in tr:
            worst = min(worst, run + mae); run += pnl
        rows.append((run * USD_PER_PT, worst * USD_PER_PT, [t[0] * USD_PER_PT for t in tr]))
    return rows

def summary(rows):
    pnl = np.array([r[0] for r in rows])
    trades = [t for r in rows for t in r[2]]
    if not trades:
        return dict(net=0, n=0, win=0, pf=0, dd=0, sharpe=0, avg=0)
    eq = pnl.cumsum()
    w = sum(t for t in trades if t > 0); ls = -sum(t for t in trades if t <= 0)
    return dict(net=pnl.sum(), n=len(trades), win=np.mean([t > 0 for t in trades]),
                pf=w / ls if ls else np.inf, dd=(np.maximum.accumulate(np.maximum(eq, 0)) - eq).max(),
                sharpe=pnl.mean() / pnl.std() * np.sqrt(252) if pnl.std() else 0, avg=np.mean(trades))

def prop_eval(rows, contracts, target=3000, max_loss=2000, consistency=0.5):
    """Start an evaluation on every day; trailing max loss follows the end-of-day high
    (stops trailing at break-even) and is enforced intraday. Returns pass/fail/open rates."""
    res, days_to = [], []
    for s in range(len(rows)):
        bal, peak, best, out = 0.0, 0.0, 0.0, "open"
        for k, (pnl, worst, _) in enumerate(rows[s:]):
            floor = min(peak - max_loss, 0.0)
            if bal + worst * contracts <= floor:
                out = "fail"; break
            bal += pnl * contracts
            best = max(best, pnl * contracts); peak = max(peak, bal)
            if bal >= target and best <= consistency * bal:
                out = "pass"; days_to.append(k + 1); break
        res.append(out)
    n = len(res)
    return dict(p=res.count("pass") / n, f=res.count("fail") / n, o=res.count("open") / n,
                days=np.median(days_to) if days_to else float("nan"))

if __name__ == "__main__":
    days = load()
    IS, OOS = days[:IS_DAYS], days[IS_DAYS:]
    print(f"{len(days)} days {days[0]['date']} -> {days[-1]['date']}; "
          f"in-sample to {IS[-1]['date']}, out-of-sample from {OOS[0]['date']} ({len(OOS)} days)")
    bh = sum(d["c"][-1] - d["o"][0] for d in days) * USD_PER_PT
    print(f"Reference, long open->close every day, 1 MES: ${bh:,.0f} "
          f"(in-sample ${sum(d['c'][-1]-d['o'][0] for d in IS)*USD_PER_PT:,.0f}, "
          f"out-of-sample ${sum(d['c'][-1]-d['o'][0] for d in OOS)*USD_PER_PT:,.0f})\n")
    print(f"{'strategy (best in-sample settings)':<52}{'':>4}{'net $':>8}{'trd':>5}{'win':>5}{'PF':>6}{'maxDD':>7}{'Sharpe':>7}{'$/trd':>7}")
    chosen = {}
    for name, (fn, grid) in GRIDS.items():
        combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
        if fn is ma_cross:
            combos = [c for c in combos if c["fast"] < c["slow"]]
        scored = [(summary(daily(IS, fn, **c)), c) for c in combos]
        n_pos = sum(s["net"] > 0 for s, _ in scored)
        best_s, best = max(scored, key=lambda x: x[0]["sharpe"])
        chosen[name] = (fn, best)
        label = f"{name} {','.join(f'{k}={v}' for k, v in best.items())}"
        for tag, part in (("IS", IS), ("OOS", OOS)):
            s = summary(daily(part, fn, **best))
            print(f"{label if tag == 'IS' else f'   ({n_pos}/{len(combos)} settings profitable in-sample)':<52}{tag:>4}"
                  f"{s['net']:>8,.0f}{s['n']:>5}{s['win']*100:>4.0f}%{s['pf']:>6.2f}{s['dd']:>7,.0f}{s['sharpe']:>7.2f}{s['avg']:>7.1f}")
        oos_all = [summary(daily(OOS, fn, **c))["net"] for c in combos]
        print(f"   all settings out-of-sample: {sum(x > 0 for x in oos_all)}/{len(combos)} profitable, "
              f"median ${np.median(oos_all):,.0f}, range ${min(oos_all):,.0f} to ${max(oos_all):,.0f}")

    print("\nEvaluation simulation, 50K-style rules ($3,000 target, $2,000 trailing max loss,")
    print("best day <= 50% of profit), started on every day of the full period:")
    print(f"{'strategy':<26}{'MES':>4}{'pass':>7}{'fail':>7}{'open':>7}{'median days':>13}")
    for name, (fn, best) in chosen.items():
        rows = daily(days, fn, **best)
        for c in (2, 5, 10):
            r = prop_eval(rows, c)
            print(f"{name if c == 2 else '':<26}{c:>4}{r['p']*100:>6.0f}%{r['f']*100:>6.0f}%{r['o']*100:>6.0f}%{r['days']:>13.0f}")
