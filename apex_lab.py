"""Apex-style instant funded 50K (as reported: $1,500 intraday trailing drawdown, $1,000 daily loss
limit that closes the account, 55% consistency, 5 qualifying days, $500 minimum payout). We take
$1,000 payouts. Intraday trailing is modelled from each day's worst open loss against the peak
balance; open-profit peaks inside a day are not tracked, so this is optimistic about the trail."""
import numpy as np, intraday as it, donchian_lab as L, fleet as f

BASE = dict(n=24, exit_n=24, stop_atr=1.0, max_trades=3, last_bar=48, trail_atr=2.0)
VARIANTS = {"Donchian (current)": BASE, "1 trade a day": dict(BASE, max_trades=1),
            "2 trades a day": dict(BASE, max_trades=2), "With daily trend bias": dict(BASE, trend=True),
            "Bias + 1 trade": dict(BASE, trend=True, max_trades=1), "Stop after a winner": dict(BASE, stop_after_win=True)}

def rows(days, p):
    return [(r[0], r[1]) for r in it.daily(days, L.dx, **p)]

def apex(R, idx, n=20, dd=1500, dll=1000, cons=0.55, qual_days=5, qual_amt=0.0, payout=1000, buf=100):
    pnl = np.array([r[0] for r in R])[idx]; worst = np.array([r[1] for r in R])[idx]
    paths, T = pnl.shape; months = T // 21
    pay = np.zeros((paths, months)); buy = np.zeros((paths, months))
    for p in range(paths):
        st = [None] * n
        for t in range(T):
            m = min(t // 21, months - 1)
            for k in range(n):
                if k > t: continue
                if st[k] is None:
                    st[k] = dict(bal=0.0, peak=0.0, base=0.0, best=0.0, days=0); buy[p, m] += 1
                a = st[k]
                floor = min(a["peak"] - dd, buf)
                if worst[p, t] <= -dll or a["bal"] + worst[p, t] <= floor:
                    st[k] = None; continue
                a["bal"] += pnl[p, t]
                if a["bal"] <= floor: st[k] = None; continue
                a["peak"] = max(a["peak"], a["bal"]); a["best"] = max(a["best"], pnl[p, t])
                a["days"] += pnl[p, t] > qual_amt
                cyc = a["bal"] - a["base"]
                if a["days"] >= qual_days and cyc >= payout and a["best"] <= cons * cyc and a["bal"] - payout >= buf:
                    a["bal"] -= payout; a["base"] = a["bal"]; a["best"] = 0.0; a["days"] = 0; pay[p, m] += 1
    return pay[:, 6:], buy[:, 6:]

if __name__ == "__main__":
    nq = L.prepare("data/5min/NDX.csv.gz", usd=2.0, cost_pts=1.12); half = len(nq) // 2
    import glob
    bear = [x for p in sorted(glob.glob("bear/work/y*.csv.gz"))[:15] for x in L.prepare(p, usd=2.0, cost_pts=1.12) if x["date"].year == 2022]
    print(f"{'strategy, 1 MNQ':<24}{'unseen $':>10}{'worst day':>10}{'days <= -$1,000':>16}  | Apex fleet of 20, unseen year: pay/mo, new accts/mo, max fee to break even | mock 2022")
    for lab, p in VARIANTS.items():
        Ru, Rb = rows(nq[half:], p), rows(bear, p)
        x = np.array([r[0] for r in Ru]); w = np.array([r[1] for r in Ru])
        out = []
        for R in (Ru, Rb):
            idx = f.bootstrap(len(R), 300, 24 * f.MONTH)
            pay, buy = apex(R, idx)
            out.append((pay.mean(), buy.mean(), pay.mean() * 1000 / buy.mean() if buy.mean() else np.inf))
        print(f"{lab:<24}{x.sum():>10,.0f}{x.min():>10,.0f}{(w <= -1000).sum():>16}  | {out[0][0]:>5.1f} {out[0][1]:>6.1f} ${out[0][2]:>7,.0f} | {out[1][0]:>4.1f} {out[1][1]:>5.1f} ${out[1][2]:>6,.0f}")
