"""Diversifying the Donchian fleet: (1) a failed-breakout fade (mean reversion) for extra accounts,
(2) the same Donchian rules on other micro futures (MES, MCL, MGC) via the data available."""
import itertools
import numpy as np
import intraday as it, donchian_lab as L, fleet as f

DON = dict(n=24, exit_n=24, stop_atr=1.0, max_trades=3, last_bar=48, trail_atr=2.0)

def fade(day, n=24, window=2, target="mid", stop_buf=0.5, max_trades=2, last_bar=60):
    """Price breaks the n-bar channel, then closes back inside within `window` bars: trade back into
    the range at the next open. Stop beyond the breakout extreme (+ stop_buf ATR); target the channel
    middle or a multiple of the risk; flat at the close. Conservative when stop and target share a bar."""
    h, l, c, o, cost = day["h"], day["l"], day["c"], day["o"], day["cost"]
    out, i = [], 1
    while i < last_bar and len(out) < max_trades:
        hi, lo = day["dh"][n][i], day["dl"][n][i]
        side = -1 if h[i] > hi and not l[i] < lo else 1 if l[i] < lo and not h[i] > hi else 0
        if not side:
            i += 1; continue
        level = hi if side < 0 else lo
        trade = None
        for j in range(i, min(i + window, last_bar, 77)):
            ext = max(h[i:j + 1]) if side < 0 else min(l[i:j + 1])
            if (side < 0 and c[j] < level) or (side > 0 and c[j] > level):
                k = j + 1; entry = o[k]; a = day["atr"][k]
                stop = ext - side * stop_buf * a if side < 0 else ext - stop_buf * a
                stop = ext + stop_buf * a if side < 0 else ext - stop_buf * a
                R = abs(entry - stop)
                if R <= 0: break
                tgt = (hi + lo) / 2 if target == "mid" else entry + side * target * R
                if (tgt - entry) * side <= 0: break
                trade = (k, entry, stop, tgt); break
        if not trade:
            i += 1; continue
        k, entry, stop, tgt = trade
        px, end = c[77], 77
        for m in range(k, 78):
            hit_s = (side > 0 and l[m] <= stop) or (side < 0 and h[m] >= stop)
            hit_t = (side > 0 and h[m] >= tgt) or (side < 0 and l[m] <= tgt)
            if hit_s:
                px = (min(stop, o[m]) if side > 0 else max(stop, o[m])) if m > k else stop; end = m; break
            if hit_t:
                px = (max(tgt, o[m]) if side > 0 else min(tgt, o[m])) if m > k else tgt; end = m; break
        pnl = (px - entry) * side - cost
        out.append((pnl, min(pnl, 0.0)))
        i = end + 1
    return out

def series(days, fn, **p):
    return np.array([r[0] for r in it.daily(days, fn, **p)])

def tstat(x):
    return x.mean() / x.std() * np.sqrt(len(x)) if x.std() else 0.0

def fleet(groups, idx, rules, fee=95):
    outs, off = [], 0
    for S, n in groups:
        outs.append(f.simulate(S[idx], n, 1, rules, offset=off)); off += n
    o = {k: sum(x[k] for x in outs) for k in outs[0]}
    net = o["payouts"] * 1000 - o["evals"] * fee; y2 = net[:, 12:24].sum(1); s6 = {k: v[:, 6:] for k, v in o.items()}
    return s6["payouts"].mean(), s6["evals"].mean(), net[:, 6:].mean(), np.median(y2), np.percentile(y2, 10), np.mean(y2 < 0), np.mean(s6["payouts"] == 0)

if __name__ == "__main__":
    nq = L.prepare("data/5min/NDX.csv.gz", usd=2.0, cost_pts=1.12); half = len(nq) // 2
    es = L.prepare("data/5min/SPX.csv.gz", usd=5.0, cost_pts=0.748)
    eth, btc = L.prepare("data/5min/ETH.csv.gz", cost_bps=8.0), L.prepare("data/5min/BTC.csv.gz", cost_bps=10.0)
    grid = dict(n=[12, 24, 48], window=[1, 3], target=["mid", 1.0, 2.0], stop_buf=[0.0, 0.5], max_trades=[2])
    combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    ranked = sorted(combos, key=lambda p: -tstat(series(nq[:half], fade, **p)))
    print("Failed-breakout fade, ranked on the first Nasdaq year only:")
    print(f"{'settings':<74}{'NQ chosen-on':>14}{'NQ unseen':>14}{'S&P':>14}{'ETH':>14}{'BTC':>14}")
    for p in ranked[:5]:
        print(f"{str(p):<74}" + "".join(f"{s.sum():>8,.0f} ({tstat(s):.1f})" for s in (series(d, fade, **p) for d in (nq[:half], nq[half:], es, eth, btc))))
    uns = [series(nq[half:], fade, **p).sum() for p in combos]
    print(f"All {len(combos)} settings on the unseen Nasdaq year: {sum(u > 0 for u in uns)} profitable, median ${np.median(uns):,.0f}")
    best = ranked[0]
    mr, dn = series(nq, fade, **best), series(nq, L.dx, **DON)
    o_ = np.array([x["o"][0] for x in nq]); c_ = np.array([x["c"][-1] for x in nq]); h_ = np.array([x["h"].max() for x in nq]); l_ = np.array([x["l"].min() for x in nq])
    tr = np.abs(c_ - o_) / (h_ - l_)
    print(f"\nChosen fade {best}: 2 yrs ${mr.sum():,.0f}; correlation with Donchian {np.corrcoef(mr, dn)[0, 1]:.2f}")
    print(f"  on choppy days (Donchian ${dn[tr < 0.3].sum():,.0f}): fade ${mr[tr < 0.3].sum():,.0f}; on trend days (Donchian ${dn[tr > 0.6].sum():,.0f}): fade ${mr[tr > 0.6].sum():,.0f}")
    print(f"  fade makes money on {np.mean(mr[dn < 0] > 0):.0%} of Donchian's losing days")

    # other markets with the Donchian rules, common window Feb 23 - Nov 2026
    mk = {"MNQ": nq, "MES": es,
          "MCL (crude, USO)": L.prepare("data/5min/USO.csv.gz", cost_bps=5.0, risk_usd=600),
          "MGC (gold, GLD)": L.prepare("data/5min/GLD.csv.gz", cost_bps=3.0, risk_usd=600)}
    common = sorted(set.intersection(*[set(x["date"] for x in d) for d in mk.values()]))
    S = {}
    for k, d in mk.items():
        dd = [x for x in d if x["date"] in set(common)]
        S[k] = series(dd, L.dx, **DON)
    S["Fade MNQ"] = series([x for x in nq if x["date"] in set(common)], fade, **best)
    print(f"\nCommon window {common[0]} to {common[-1]} ({len(common)} days); MCL and MGC sized to a typical day like 1 MNQ")
    keys = list(S)
    print(f"{'':<18}{'net $':>9}{'t':>6}" + "".join(f"{k[:9]:>11}" for k in keys))
    for a in keys:
        print(f"{a:<18}{S[a].sum():>9,.0f}{tstat(S[a]):>6.1f}" + "".join(f"{np.corrcoef(S[a], S[b])[0, 1]:>11.2f}" for b in keys))

    rules = dict(f.FREE, dd=3000, win_days=5, win_amt=200, min_days=5)
    print(f"\nFleets, $95 evals, $3k DD, 5 days of $200; resampled from the common window (same days for every strategy)")
    print(f"{'fleet':<40}{'pay/mo':>7}{'evals/mo':>9}{'net/mo':>9}{'yr2 median':>11}{'bad yr':>9}{'losing':>8}{'zero mo':>8}")
    idx = f.bootstrap(len(common), 2000, 24 * f.MONTH)
    plans = {"20 Donchian MNQ": [(S["MNQ"], 20)], "25 Donchian MNQ": [(S["MNQ"], 25)],
             "20 Donchian MNQ + 5 fade MNQ": [(S["MNQ"], 20), (S["Fade MNQ"], 5)],
             "15 MNQ + 5 MES": [(S["MNQ"], 15), (S["MES"], 5)],
             "14 MNQ + 3 MGC + 3 MCL": [(S["MNQ"], 14), (S["MGC (gold, GLD)"], 3), (S["MCL (crude, USO)"], 3)],
             "20 MNQ + 5 MGC": [(S["MNQ"], 20), (S["MGC (gold, GLD)"], 5)]}
    for lab, g in plans.items():
        p, e, n, y, b, lo, z = fleet(g, idx, rules)
        print(f"{lab:<40}{p:>7.1f}{e:>9.1f}{n:>9,.0f}{y:>11,.0f}{b:>9,.0f}{lo:>8.0%}{z:>8.0%}")
