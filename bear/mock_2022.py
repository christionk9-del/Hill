"""Mock 2022 bear market for the Donchian variant (Monte Carlo).

Real 2022 Nasdaq-100 daily open/high/low/close is known, but its 5-minute bars are not available.
Each 2022 day gets an intraday path borrowed from a real 5-minute day (Dec 2024 - Nov 2026) with a
similar shape (where the open and close sat inside the day's range), rescaled linearly onto that
2022 day's actual low-to-high range. Picking different donor days gives many possible 2022 years.
A control rebuilds the 2025-26 period the same way, to see how far the method itself shifts results.
"""
import json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import intraday as it, donchian_lab as L, fleet as f

BEST = dict(n=24, exit_n=24, stop_atr=1.0, max_trades=3, last_bar=48, trail_atr=2.0)
NY = "America/New_York"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work"); os.makedirs(OUT, exist_ok=True)

def daily_targets(path):
    j = json.load(open(path)); rows = []
    for r in j["data"]["results"]:
        if r["symbol"] != "NDX": continue
        for b in r["bars"]:
            if b.get("interpolated"): continue
            rows.append((b["begins_at"][:10], *(float(b[k]) for k in ("open_value", "high_value", "low_value", "close_value"))))
    return pd.DataFrame(rows, columns=["date", "o", "h", "l", "c"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)

def donors():
    d = pd.read_csv(os.path.join(os.path.dirname(OUT), "..", "data/5min/NDX.csv.gz"))
    d["ts"] = pd.to_datetime(d.ts, utc=True).dt.tz_convert(NY)
    out = []
    for date, g in d.groupby(d.ts.dt.date):
        if len(g) != 78: continue
        lo, hi = g.low.min(), g.high.max()
        if hi - lo <= 0: continue
        x = g[["open", "high", "low", "close"]].values
        out.append(dict(date=date, norm=(x - lo) / (hi - lo), fo=(x[0, 0] - lo) / (hi - lo), fc=(x[-1, 3] - lo) / (hi - lo)))
    return out

def build(targets, pool, rng, k=8, exclude_same=False):
    feats = np.array([[p["fo"], p["fc"]] for p in pool])
    rows = []
    for t in targets.itertuples():
        rng_ = t.h - t.l
        fo, fc = (t.o - t.l) / rng_, (t.c - t.l) / rng_
        dist = np.hypot(feats[:, 0] - fo, feats[:, 1] - fc)
        if exclude_same:
            dist = np.where([str(p["date"]) == t.date for p in pool], np.inf, dist)
        pick = pool[rng.choice(np.argsort(dist)[:k])]
        bars = t.l + pick["norm"] * rng_
        start = pd.Timestamp(f"{t.date} 09:30", tz=NY)
        for i, (o, h, l, c) in enumerate(bars):
            rows.append(((start + pd.Timedelta(minutes=5 * i)).tz_convert("UTC").isoformat(), o, h, l, c))
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])

def run(df, tag, start="2022-01-01", end="2022-12-31"):
    p = os.path.join(OUT, f"{tag}.csv.gz"); df.to_csv(p, index=False)
    days = L.prepare(p, usd=2.0, cost_pts=1.12)
    days = [d for d in days if start <= d["date"].isoformat() <= end]
    return np.array([r[0] for r in it.daily(days, L.dx, **BEST)]), [d["date"] for d in days]

if __name__ == "__main__":
    tgt = daily_targets(sys.argv[1]); pool = donors()
    t22 = tgt[(tgt.date >= "2021-11-01") & (tgt.date <= "2022-12-31")]
    y = t22[t22.date >= "2022-01-01"]
    print(f"2022 NDX: {y.o.iloc[0]:,.0f} -> {y.c.iloc[-1]:,.0f} ({y.c.iloc[-1] / y.o.iloc[0] - 1:+.1%}), {len(y)} days, "
          f"median daily range {((y.h - y.l) / y.o).median():.2%}")
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    rng = np.random.default_rng(2022)
    years, paths = [], []
    for i in range(N):
        x, dates = run(build(t22, pool, rng), f"y{i}")
        years.append(x); paths.append(x)
    tot = np.array([x.sum() for x in years])
    q = lambda a: f"median {np.median(a):,.0f}, 1-in-10 bad {np.percentile(a, 10):,.0f}, good {np.percentile(a, 90):,.0f}"
    print(f"\nMock 2022, {N} paths, 1 MNQ, net $: {q(tot)}; losing paths {np.mean(tot < 0):.0%}")
    half = [(x[:len(x)//2].sum(), x[len(x)//2:].sum()) for x in years]
    print(f"  first half median {np.median([h[0] for h in half]):,.0f}, second half median {np.median([h[1] for h in half]):,.0f}")
    dd = [ (np.maximum.accumulate(np.maximum(np.cumsum(x),0)) - np.cumsum(x)).max() for x in years]
    print(f"  max drawdown median {np.median(dd):,.0f}, worst {max(dd):,.0f}; worst day median {np.median([x.min() for x in years]):,.0f}")
    # control: rebuild the real 2025-26 period with the same method
    real_days = L.prepare("data/5min/NDX.csv.gz", usd=2.0, cost_pts=1.12)
    real = np.array([r[0] for r in it.daily(real_days, L.dx, **BEST)])
    ctrl_t = tgt.iloc[0:0]
    five = pd.read_csv("data/5min/NDX.csv.gz"); five["ts"] = pd.to_datetime(five.ts, utc=True).dt.tz_convert(NY)
    g = five.groupby(five.ts.dt.date)
    ctrl_t = pd.DataFrame({"date": [str(k) for k in g.groups], "o": g.open.first().values, "h": g.high.max().values, "l": g.low.min().values, "c": g.close.last().values})
    ctrl = []
    for i in range(10):
        x, _ = run(build(ctrl_t, pool, rng, exclude_same=True), f"c{i}", start="2024-12-01", end="2026-12-31")
        ctrl.append(x.sum())
    print(f"\nControl (2025-26 rebuilt the same way, 10 paths): median ${np.median(ctrl):,.0f} vs real ${real.sum():,.0f}")
    np.save(os.path.join(OUT, "years.npy"), np.array([np.pad(x, (0, 260 - len(x))) for x in years]))
    # fleet on mock 2022
    pooled = np.concatenate(years)
    P = pooled[f.bootstrap(len(pooled), 2000, 24 * f.MONTH)]
    a = f.simulate(P, 15, 1, dict(f.FREE, dd=3000)); b = f.simulate(P, 5, 1, dict(f.FREE, dd=2500), offset=15)
    o = {k: a[k] + b[k] for k in a}
    s = {k: v[:, 6:] for k, v in o.items()}
    net = o["payouts"] * 1000 - o["evals"] * 130; y2 = net[:, 12:24].sum(1)
    print(f"\nFleet (15 x $3k + 5 x $2.5k, 1 MNQ, $130 evals) living through 2022-like markets: payouts/mo {s['payouts'].mean():.1f}, "
          f"months with none {(s['payouts'] == 0).mean():.0%}, funded churn {s['funded_blown'].sum() / s['funded'].sum():.0%}/mo, "
          f"evals/mo {s['evals'].mean():.1f}, net/mo ${(net[:, 6:]).mean():,.0f}, year: median ${np.median(y2):,.0f}, 1-in-10 bad ${np.percentile(y2, 10):,.0f}, losing {np.mean(y2 < 0):.0%}")
