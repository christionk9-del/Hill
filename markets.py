"""Run the intraday Donchian breakout across futures markets (via the proxies available).

Every market is tested on the 9:30-16:00 New York session with 5-minute bars and sized so
its typical daily high-low range is worth RISK_USD (about one MES contract), so dollar
results are comparable across markets. Costs are round-trip, in basis points of price:
one tick of slippage plus fees per side on the matching futures contract (approximate).
"""
import itertools
import os
import sys
import numpy as np
import intraday as it

RISK_USD = 300.0
PRIMARY = dict(n=78, exit_n=24, stop_atr=3.0, max_trades=3)   # chosen earlier on SPY Feb-Jul 2026
#        name                group       file     futures  cost bps  data note
MARKETS = [
    ("S&P 500",             "Equity",   "SPX",   "MES",   1.0, "cash index + SPY"),
    ("Nasdaq-100",          "Equity",   "NDX",   "MNQ",   0.5, "cash index + QQQ"),
    ("Russell 2000",        "Equity",   "IWM",   "M2K",   1.7, "ETF"),
    ("Dow",                 "Equity",   "DIA",   "MYM",   0.9, "ETF"),
    ("Corn",                "Grains",   "CORN",  "ZC",   12.0, "ETF, some thin bars"),
    ("Wheat",               "Grains",   "WEAT",  "ZW",   10.0, "ETF"),
    ("Soybeans",            "Grains",   "SOYB",  "ZS",    6.0, "ETF, half the bars empty"),
    ("Crude oil",           "Energy",   "USO",   "MCL",   5.0, "ETF"),
    ("Natural gas",         "Energy",   "UNG",   "NG",    8.0, "ETF"),
    ("Euro",                "Currency", "FXE",   "M6E",   2.0, "ETF, half the bars empty"),
    ("Yen",                 "Currency", "FXY",   "6J",    2.5, "ETF, a quarter of bars empty"),
    ("Bitcoin",             "Crypto",   "BTC",   "MBT",  10.0, "Coinbase spot"),
    ("Ether",               "Crypto",   "ETH",   "ETH",   8.0, "Coinbase spot"),
]
GRID = it.GRIDS["Donchian breakout"][1]
COMBOS = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]


def net(rows):
    return sum(r[0] for r in rows)


def run_market(name, group, sym, fut, bps, note, verbose=False):
    path = f"data/5min/{sym}.csv.gz"
    if not os.path.exists(path):
        return None
    days = it.load(path, cost_bps=bps, risk_usd=RISK_USD)
    gross_days = it.load(path, cost_bps=0.0, risk_usd=RISK_USD)
    rows = it.daily(days, it.donchian, **PRIMARY)
    s = it.summary(rows)
    half = len(rows) // 2
    allnet = [net(it.daily(days, it.donchian, **c)) for c in COMBOS]
    ev = it.prop_eval(rows, 2, mode="eod")
    years = {}
    for d, r in zip(days, rows):
        years[d["date"].year] = years.get(d["date"].year, 0) + r[0]
    return dict(name=name, group=group, fut=fut, note=note, days=len(days), start=days[0]["date"], end=days[-1]["date"],
                net=s["net"], gross=net(it.daily(gross_days, it.donchian, **PRIMARY)), n=s["n"], win=s["win"], pf=s["pf"],
                dd=s["dd"], h1=net(rows[:half]), h2=net(rows[half:]), worst=min(r[0] for r in rows),
                pos=sum(x > 0 for x in allnet), med=float(np.median(allnet)), ev=ev, years=years, rows=rows,
                dates=[d["date"] for d in days])


if __name__ == "__main__":
    res = [r for r in (run_market(*m) for m in MARKETS) if r]
    print(f"Donchian breakout {PRIMARY}; each market sized to ${RISK_USD:.0f} typical daily range (about 1 MES)\n")
    print(f"{'market':<14}{'fut':<5}{'from':<12}{'days':>5}{'gross $':>9}{'net $':>9}{'1st half':>9}{'2nd half':>9}"
          f"{'trd':>6}{'win':>5}{'PF':>6}{'maxDD':>7}{'worst day':>10}{'  settings >0':>13}{'median $':>9}")
    for r in res:
        print(f"{r['name']:<14}{r['fut']:<5}{str(r['start']):<12}{r['days']:>5}{r['gross']:>9,.0f}{r['net']:>9,.0f}{r['h1']:>9,.0f}{r['h2']:>9,.0f}"
              f"{r['n']:>6}{r['win']*100:>4.0f}%{r['pf']:>6.2f}{r['dd']:>7,.0f}{r['worst']:>10,.0f}{r['pos']:>9}/{len(COMBOS)}{r['med']:>9,.0f}")
    print("\nBy calendar year, net $ (primary settings):")
    yrs = sorted({y for r in res for y in r["years"]})
    print(f"{'market':<14}" + "".join(f"{y:>9}" for y in yrs))
    for r in res:
        print(f"{r['name']:<14}" + "".join(f"{r['years'].get(y, float('nan')):>9,.0f}" for y in yrs))
    print("\nEvaluation simulation ($3,000 target, $2,000 end-of-day trailing drawdown, 2 risk units):")
    print(f"{'market':<14}{'pass':>6}{'fail':>6}{'open':>6}{'median days':>12}")
    for r in res:
        e = r["ev"]
        print(f"{r['name']:<14}{e['p']*100:>5.0f}%{e['f']*100:>5.0f}%{e['o']*100:>5.0f}%{e['days']:>12.0f}")
    # same-window comparison and a simple basket over the common recent window
    common = max(r["start"] for r in res)
    print(f"\nCommon window from {common} (all markets), net $ primary settings:")
    basket = {}
    for r in res:
        sub = [(d, x) for d, x in zip(r["dates"], r["rows"]) if d >= common]
        print(f"  {r['name']:<14}{sum(x[0] for _, x in sub):>9,.0f}")
        for d, x in sub:
            b = basket.setdefault(d, [0.0, 0.0]); b[0] += x[0]; b[1] += x[1]
    for label, keep in (("all markets", None), ("equity + crypto only", {"Equity", "Crypto"})):
        bk = {}
        for r in res:
            if keep and r["group"] not in keep:
                continue
            for d, x in zip(r["dates"], r["rows"]):
                if d >= common:
                    b = bk.setdefault(d, [0.0, 0.0, []]); b[0] += x[0]; b[1] += x[1]
        k = sum(1 for r in res if not keep or r["group"] in keep)
        rows = [(v[0] / k, v[1] / k, [v[0] / k]) for _, v in sorted(bk.items())]
        s = it.summary(rows)
        print(f"  basket, {label} (1/{k} unit each): net ${s['net']:,.0f}, max drawdown ${s['dd']:,.0f}, "
              + ", ".join(f"{c}x: pass {e['p']*100:.0f}% fail {e['f']*100:.0f}%" for c in (5, 10)
                          for e in [it.prop_eval(rows, c, mode='eod')]))
