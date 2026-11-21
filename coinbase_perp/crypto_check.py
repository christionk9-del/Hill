"""Check the Donchian rules on BTC and ETH before trading them as Coinbase perps.

Uses the same 5-minute 9:30-16:00 New York bars and the same trade logic as donchian_lab.py.
Results are in R (multiples of the initial stop risk), so they translate directly to a bot
that risks a fixed % of the account per trade. Costs are round-trip, in basis points of price.

    python coinbase_perp/crypto_check.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import intraday as it        # noqa: E402
import donchian_lab as L     # noqa: E402

VARIANTS = {
    "forward-test rules (n24, 1 ATR stop, 2 ATR trail, entries to 13:25)":
        dict(n=24, exit_n=24, stop_atr=1.0, max_trades=3, last_bar=48, trail_atr=2.0),
    "lab baseline (n78, 3 ATR stop, exit channel 24, entries to 15:00)":
        dict(n=78, exit_n=24, stop_atr=3.0, max_trades=3),
}
COSTS = (0.0, 4.0, 8.0, 12.0)


def r_trades(days, p, bps):
    """Per-trade results in R and the trade dates."""
    out = []
    for d in days:
        d["cost"] = bps / 1e4 * d["o"][0]
        n = p.get("n", 78)
        i = max(p.get("start_bar", 1), 1)
        trades = 0
        while i < p.get("last_bar", 66) and trades < p.get("max_trades", 3):
            hi, lo = d["dh"][n][i], d["dl"][n][i]
            up, dn = d["h"][i] > hi, d["l"][i] < lo
            if up != dn:
                s = 1 if up else -1
                entry = max(hi, d["o"][i]) if up else min(lo, d["o"][i])
                R = p.get("stop_atr", 3.0) * d["atr"][i]
                pnl, j = L.trade(d, i, entry, s, R, p.get("exit_n", 24), trail_atr=p.get("trail_atr"))
                out.append((d["date"], pnl / R))
                trades += 1
                i = j + 1
                continue
            i += 1
    return out


def report(sym):
    days = it.load(f"data/5min/{sym}.csv.gz", cost_bps=0.0, risk_usd=300.0)
    print(f"\n=== {sym}: {days[0]['date']} -> {days[-1]['date']}, {len(days)} sessions")
    for name, p in VARIANTS.items():
        print(f"  {name}")
        for bps in COSTS:
            t = r_trades(days, p, bps)
            r = np.array([x[1] for x in t])
            yrs = {}
            for dt, x in t:
                yrs[dt.year] = yrs.get(dt.year, 0) + x
            eq = np.cumsum(r)
            dd = (np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()
            tt = r.mean() / r.std() * np.sqrt(len(r))
            print(f"    cost {bps:>4.0f} bps: {len(r):>4} trades, win {np.mean(r > 0)*100:>3.0f}%, "
                  f"avg {r.mean():+.3f}R, total {r.sum():+7.1f}R, t {tt:+.2f}, max DD {dd:5.1f}R | "
                  + " ".join(f"{y}:{v:+.0f}" for y, v in sorted(yrs.items())))


if __name__ == "__main__":
    for s in ("BTC", "ETH"):
        report(s)
