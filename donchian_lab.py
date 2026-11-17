"""Search variations of the intraday Donchian breakout without fooling ourselves.

Variants are ranked ONLY on the first half of the Nasdaq-100 data (Dec 2024 - Nov 2025).
The second half of Nasdaq, the S&P 500, Ether and Bitcoin are held back to check whether
whatever wins in-sample still works on data it never saw.
"""
import itertools
import numpy as np
import intraday as it

DON = it.DON


def prepare(path, usd=None, cost_pts=None, cost_bps=1.0, risk_usd=300.0):
    days = it.load(path, cost_bps=cost_bps, risk_usd=risk_usd)
    closes = np.array([d["c"][-1] for d in days])
    rng = np.array([d["h"].max() - d["l"].min() for d in days])
    ema = closes.copy()
    for k in range(1, len(ema)):
        ema[k] = ema[k - 1] + (closes[k] - ema[k - 1]) * 2 / 21
    for k, d in enumerate(days):
        if usd is not None:
            d["usd"], d["cost"] = usd, cost_pts
        d["trend"] = 0 if k == 0 else (1 if closes[k - 1] > ema[k - 1] else -1)
        d["nr"] = np.nan if k < 20 else rng[k - 1] / np.median(rng[k - 20:k])   # prior day range vs normal
    return days


def trade(day, i, entry, side, R, chan, be=None, tgt=None, trail_atr=None, tstop=None):
    h, l, c, o, cost = day["h"], day["l"], day["c"], day["o"], day["cost"]
    stop = entry - side * R
    moved = False
    for j in range(i, 78):
        if j > i:
            if (side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop):
                px = min(stop, o[j]) if side > 0 else max(stop, o[j])
                return (px - entry) * side - cost, j
            if tgt is not None:
                t = entry + side * tgt * R
                if (side > 0 and h[j] >= t) or (side < 0 and l[j] <= t):
                    px = max(t, o[j]) if side > 0 else min(t, o[j])
                    return (px - entry) * side - cost, j
        elif (c[j] - stop) * side <= 0:          # entry bar: only its close can stop us out
            return (stop - entry) * side - cost, j
        if tstop is not None and j >= tstop:
            return (c[j] - entry) * side - cost, j
        if j + 1 < 78:
            if chan:
                stop = max(stop, day["dl"][chan][j + 1]) if side > 0 else min(stop, day["dh"][chan][j + 1])
            if trail_atr:
                a = trail_atr * day["atr"][j + 1]
                stop = max(stop, c[j] - a) if side > 0 else min(stop, c[j] + a)
            if be is not None and not moved and (c[j] - entry) * side >= be * R:
                stop = max(stop, entry) if side > 0 else min(stop, entry)
                moved = True
    return (c[77] - entry) * side - cost, 77


def dx(day, n=78, exit_n=24, stop_atr=3.0, max_trades=3, side=0, start_bar=1, last_bar=66,
       trend=False, vwap=False, nr_max=None, nr_min=None, be=None, tgt=None, trail_atr=None,
       stop_after_win=False, tstop=None):
    if nr_max is not None and not (day["nr"] <= nr_max):
        return []
    if nr_min is not None and not (day["nr"] >= nr_min):
        return []
    out, i = [], max(start_bar, 1)
    while i < last_bar and len(out) < max_trades:
        hi, lo = day["dh"][n][i], day["dl"][n][i]
        up, dn = day["h"][i] > hi, day["l"][i] < lo
        if up != dn:
            s = 1 if up else -1
            ok = side in (0, s)
            if trend:
                ok = ok and s == day["trend"]
            if vwap:
                ok = ok and (day["c"][i - 1] - day["vwap"][i - 1]) * s > 0
            if ok:
                entry = max(hi, day["o"][i]) if up else min(lo, day["o"][i])
                pnl, j = trade(day, i, entry, s, stop_atr * day["atr"][i], exit_n, be, tgt, trail_atr, tstop)
                out.append((pnl, min(pnl, 0.0)))
                i = j + 1
                if stop_after_win and pnl > 0:
                    break
                continue
        i += 1
    return out


def stats(days, **p):
    rows = it.daily(days, dx, **p)
    x = np.array([r[0] for r in rows])
    n = sum(len(r[2]) for r in rows)
    sd = x.std()
    return dict(net=x.sum(), t=x.mean() / sd * np.sqrt(len(x)) if sd else 0, sharpe=x.mean() / sd * np.sqrt(252) if sd else 0,
                trades=n, rows=rows, x=x)


BASE = dict(n=78, exit_n=24, stop_atr=3.0, max_trades=3)
ONE_AT_A_TIME = {
    "entry channel": [dict(n=v) for v in (12, 24, 48)],
    "exit channel": [dict(exit_n=v) for v in (6, 12, 48)],
    "initial stop (ATR)": [dict(stop_atr=v) for v in (1.0, 2.0, 4.0)],
    "trades per day": [dict(max_trades=v) for v in (1, 2)],
    "direction": [dict(side=1), dict(side=-1)],
    "with daily trend": [dict(trend=True)],
    "with session VWAP": [dict(vwap=True)],
    "skip first 30 min": [dict(start_bar=6)],
    "no entries after noon / 1:30": [dict(last_bar=30), dict(last_bar=48)],
    "prior day range": [dict(nr_max=0.8), dict(nr_min=1.2)],
    "breakeven after 1R / 2R": [dict(be=1.0), dict(be=2.0)],
    "profit target 2R / 4R": [dict(tgt=2.0), dict(tgt=4.0)],
    "ATR trail 2 / 4": [dict(trail_atr=2.0), dict(trail_atr=4.0)],
    "stop after a winner": [dict(stop_after_win=True)],
    "exit by 2:00 pm": [dict(tstop=54)],
}


def label(p):
    return ", ".join(f"{k}={v}" for k, v in p.items()) or "baseline"


if __name__ == "__main__":
    nq = prepare("data/5min/NDX.csv.gz", usd=2.0, cost_pts=1.12)            # 1 MNQ
    half = len(nq) // 2
    IS, OOS = nq[:half], nq[half:]
    es = prepare("data/5min/SPX.csv.gz", usd=5.0, cost_pts=0.748)          # 1 MES
    eth = prepare("data/5min/ETH.csv.gz", cost_bps=8.0)
    btc = prepare("data/5min/BTC.csv.gz", cost_bps=10.0)
    print(f"In-sample: Nasdaq {IS[0]['date']} -> {IS[-1]['date']} ({len(IS)} days). Held out: Nasdaq {OOS[0]['date']} -> "
          f"{OOS[-1]['date']}, S&P, Ether, Bitcoin.\n")

    def row(name, p, show_all=True):
        a = stats(IS, **p)
        cells = f"{name:<44}{a['net']:>8,.0f}{a['t']:>6.2f}{a['trades']:>6}"
        if show_all:
            for d in (OOS, es, eth, btc):
                b = stats(d, **p)
                cells += f"{b['net']:>9,.0f}{b['t']:>6.2f}"
        print(cells)
        return a

    hdr = (f"{'variant (changes from baseline)':<44}{'IS $':>8}{'t':>6}{'trd':>6}"
           f"{'NQ OOS $':>9}{'t':>6}{'S&P $':>9}{'t':>6}{'ETH $':>9}{'t':>6}{'BTC $':>9}{'t':>6}")
    print("Step 1: change one thing at a time (ETH/BTC sized to a $300 typical day; NQ = 1 MNQ, S&P = 1 MES)")
    print(hdr)
    base = row("baseline", BASE)
    helped = []
    for group, opts in ONE_AT_A_TIME.items():
        for o in opts:
            a = row(f"{group}: {label(o)}", dict(BASE, **o))
            if a["t"] > base["t"] + 0.25:
                helped.append(o)
    print(f"\n{len(helped)} changes improved in-sample t by more than 0.25: {[label(o) for o in helped]}")

    # Step 2: combine the in-sample helpers (only in-sample decides)
    keys = {}
    for o in helped:
        for k, v in o.items():
            keys.setdefault(k, set()).add(v)
    grid = {k: [BASE.get(k, None)] + sorted(v, key=str) for k, v in keys.items()}
    combos = []
    for vals in itertools.product(*grid.values()):
        p = dict(BASE)
        for k, v in zip(grid, vals):
            if v is not None:
                p[k] = v
            else:
                p.pop(k, None)
        combos.append(p)
    scored = sorted(((stats(IS, **p)["t"], i, p) for i, p in enumerate(combos)), key=lambda z: -z[0])
    print(f"\nStep 2: {len(combos)} combinations of those changes, ranked on in-sample t only. Top 5, then held-out results:")
    print(hdr)
    for t, _, p in scored[:5]:
        row(label({k: v for k, v in p.items() if BASE.get(k) != v}), p)
    best = scored[0][2]
    print("\nBEST", best)
