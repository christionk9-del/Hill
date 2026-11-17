"""Close-confirmed Donchian breakout: the signal needs a 5-minute CLOSE beyond the channel and
the trade enters at the next bar's open. Every stop check then happens after the entry price
is known, so 5-minute bars decide each trade without guessing the order of highs and lows.
"""
import itertools
import numpy as np
import intraday as it
import donchian_lab as L


def trade_from_open(day, k, side, R, chan, trail_atr):
    """Enter at the open of bar k; the stop is live for the whole of bar k."""
    h, l, c, o, cost = day["h"], day["l"], day["c"], day["o"], day["cost"]
    entry = o[k]
    stop = entry - side * R
    for j in range(k, 78):
        if (side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop):
            px = stop if j == k else (min(stop, o[j]) if side > 0 else max(stop, o[j]))
            return (px - entry) * side - cost, j
        if j + 1 < 78:
            if chan:
                stop = max(stop, day["dl"][chan][j + 1]) if side > 0 else min(stop, day["dh"][chan][j + 1])
            if trail_atr:
                a = trail_atr * day["atr"][j + 1]
                stop = max(stop, c[j] - a) if side > 0 else min(stop, c[j] + a)
    return (c[77] - entry) * side - cost, 77


def cx(day, n=24, exit_n=24, stop_atr=1.0, max_trades=3, last_bar=48, trail_atr=2.0):
    out, i = [], 1
    while i < last_bar - 1 and len(out) < max_trades:
        hi, lo = day["dh"][n][i], day["dl"][n][i]
        s = 1 if day["c"][i] > hi else -1 if day["c"][i] < lo else 0
        if s:
            k = i + 1
            pnl, j = trade_from_open(day, k, s, stop_atr * day["atr"][k], exit_n, trail_atr)
            out.append((pnl, min(pnl, 0.0)))
            i = j + 1
            continue
        i += 1
    return out


def stats(days, **p):
    x = np.array([r[0] for r in it.daily(days, cx, **p)])
    sd = x.std()
    return x.sum(), (x.mean() / sd * np.sqrt(len(x)) if sd else 0), x


if __name__ == "__main__":
    nq = L.prepare("data/5min/NDX.csv.gz", usd=2.0, cost_pts=1.12)
    half = len(nq) // 2
    IS, OOS = nq[:half], nq[half:]
    es = L.prepare("data/5min/SPX.csv.gz", usd=5.0, cost_pts=0.748)
    eth = L.prepare("data/5min/ETH.csv.gz", cost_bps=8.0)
    btc = L.prepare("data/5min/BTC.csv.gz", cost_bps=10.0)
    grid = dict(n=[24, 48, 78], exit_n=[12, 24], stop_atr=[1.0, 2.0, 3.0], trail_atr=[None, 2.0], max_trades=[1, 3])
    combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    scored = sorted(((stats(IS, **p)[1], i, p) for i, p in enumerate(combos)), key=lambda z: -z[0])
    print(f"{len(combos)} close-confirmed settings ranked on the first Nasdaq year only. Top 6 and their held-out results:")
    print(f"{'settings':<56}{'IS $':>8}{'t':>6}{'NQ OOS $':>9}{'t':>6}{'S&P $':>8}{'t':>6}{'ETH $':>8}{'t':>6}{'BTC $':>8}{'t':>6}")
    for t, _, p in scored[:6]:
        cells = f"{str(p):<56}"
        for d in (IS, OOS, es, eth, btc):
            net, tt, _ = stats(d, **p)
            cells += f"{net:>8,.0f}{tt:>6.2f}"
        print(cells)
    oos = [stats(OOS, **p)[0] for _, _, p in scored]
    print(f"\nAll {len(combos)} settings on the unseen Nasdaq year: {sum(v > 0 for v in oos)} profitable, median ${np.median(oos):,.0f}")
    same = dict(n=24, exit_n=24, stop_atr=1.0, trail_atr=2.0, max_trades=3)
    print("Same settings as the intrabar variant, close-confirmed:", {k: f"{stats(d, **same)[0]:,.0f}" for k, d in (("NQ IS", IS), ("NQ OOS", OOS), ("S&P", es))})
