"""15-minute opening range breakout (ORB) on MNQ, tested the same way as the Donchian variant,
then checked for how much it diversifies a fleet that also runs the Donchian variant."""
import glob, itertools, os
import numpy as np
import intraday as it, donchian_lab as L, fleet as f

DON = dict(n=24, exit_n=24, stop_atr=1.0, max_trades=3, last_bar=48, trail_atr=2.0)
GRID = dict(n=[3], rr=[None, 1.0, 2.0, 3.0], stop_frac=[0.5, 1.0], last_entry=[24, 42, 66])

def series(days, fn, **p):
    return np.array([r[0] for r in it.daily(days, fn, **p)])

def tstat(x):
    return x.mean() / x.std() * np.sqrt(len(x)) if x.std() else 0

if __name__ == "__main__":
    nq = L.prepare("data/5min/NDX.csv.gz", usd=2.0, cost_pts=1.12); half = len(nq) // 2
    others = [("S&P", L.prepare("data/5min/SPX.csv.gz", usd=5.0, cost_pts=0.748)),
              ("ETH", L.prepare("data/5min/ETH.csv.gz", cost_bps=8.0)), ("BTC", L.prepare("data/5min/BTC.csv.gz", cost_bps=10.0))]
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    ranked = sorted(combos, key=lambda p: -tstat(series(nq[:half], it.orb, **p)))
    print("15-minute ORB settings ranked on the first Nasdaq year only (stop_frac = stop as share of the range; rr = target in R, None = hold to close; last_entry bar 24 = 11:30, 42 = 13:00, 66 = 15:00)")
    print(f"{'settings':<52}{'NQ chosen-on':>14}{'NQ unseen':>14}" + "".join(f"{n:>14}" for n, _ in others))
    for p in ranked[:5]:
        cells = "".join(f"{s.sum():>8,.0f} ({tstat(s):.1f})" for s in (series(nq[:half], it.orb, **p), series(nq[half:], it.orb, **p)))
        cells += "".join(f"{s.sum():>8,.0f} ({tstat(s):.1f})" for s in (series(d, it.orb, **p) for _, d in others))
        print(f"{str(p):<52}{cells}")
    unseen = [series(nq[half:], it.orb, **p).sum() for p in combos]
    print(f"All {len(combos)} settings on the unseen Nasdaq year: {sum(u > 0 for u in unseen)} profitable, median ${np.median(unseen):,.0f}")
    best = ranked[0]
    o, d = series(nq, it.orb, **best), series(nq, L.dx, **DON)
    print(f"\nChosen ORB {best}: 2 yrs ${o.sum():,.0f}, worst day ${o.min():,.0f}; Donchian 2 yrs ${d.sum():,.0f}")
    print(f"Daily correlation with the Donchian variant: {np.corrcoef(o, d)[0, 1]:.2f}; both lose on {np.mean((o < 0) & (d < 0)):.0%} of days, "
          f"ORB wins when Donchian loses on {np.mean((o > 0) & (d < 0)) / max(np.mean(d < 0), 1e-9):.0%} of Donchian's losing days")
    for lab, s in (("Donchian only", d), ("ORB only", o), ("Half and half", (d + o) / 2)):
        cum = np.cumsum(s); dd = (np.maximum.accumulate(np.maximum(cum, 0)) - cum).max()
        print(f"  {lab:<15} per contract: net ${s.sum():,.0f}, max drawdown ${dd:,.0f}, worst 20-day stretch ${min(s[i:i+20].sum() for i in range(len(s)-20)):,.0f}")
    # mock 2022 paths built earlier
    bd, bo = [], []
    for p in sorted(glob.glob("bear/work/y*.csv.gz")):
        days = [x for x in L.prepare(p, usd=2.0, cost_pts=1.12) if x["date"].year == 2022]
        bd.append(series(days, L.dx, **DON)); bo.append(series(days, it.orb, **best))
    bd, bo = np.concatenate(bd), np.concatenate(bo)
    print(f"Mock 2022: Donchian ${bd.sum() / 40:,.0f}/yr, ORB ${bo.sum() / 40:,.0f}/yr, correlation {np.corrcoef(bd, bo)[0, 1]:.2f}")
    # fleets: 20 Donchian vs 10 + 10 split, plan A at $95 ($3k DD, 5 days of $200)
    rules = dict(f.FREE, dd=3000, win_days=5, win_amt=200, min_days=5)
    print(f"\nFleet of 20, $95 evals, $3k DD, 5 days of $200 (both strategies resampled on the same days):")
    print(f"{'market':<16}{'fleet':<22}{'pay/mo':>7}{'evals/mo':>9}{'net/mo':>9}{'yr2 median':>11}{'bad yr':>9}{'losing yrs':>11}{'zero mo':>8}")
    for lab, (A, B) in (("Unseen year", (d[half:], o[half:])), ("Full two years", (d, o)), ("Mock 2022", (bd, bo))):
        idx = f.bootstrap(len(A), 2000, 24 * f.MONTH)
        for flab, groups in (("20 Donchian", [(A, 20, 0)]), ("20 ORB", [(B, 20, 0)]), ("10 Donchian + 10 ORB", [(A, 10, 0), (B, 10, 10)])):
            outs = [f.simulate(S[idx], n, 1, rules, offset=off) for S, n, off in groups]
            out = {k: sum(x[k] for x in outs) for k in outs[0]}
            net = out["payouts"] * 1000 - out["evals"] * 95; y2 = net[:, 12:24].sum(1); s6 = {k: v[:, 6:] for k, v in out.items()}
            print(f"{lab:<16}{flab:<22}{s6['payouts'].mean():>7.1f}{s6['evals'].mean():>9.1f}{net[:, 6:].mean():>9,.0f}{np.median(y2):>11,.0f}{np.percentile(y2, 10):>9,.0f}{np.mean(y2 < 0):>10.0%}{np.mean(s6['payouts'] == 0):>8.0%}")
