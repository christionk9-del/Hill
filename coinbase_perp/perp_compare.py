"""Same Donchian trades, different instruments: MNQ vs a US100 (Nasdaq-100) perp, gold, crypto; with funding.

Every market uses its 5-minute 9:30-16:00 New York bars and the rules from live/engine.py. Trades are
found once (before costs); then each instrument's costs are applied to the same trades:

  MNQ          $0.62 fee per side + 1 tick slippage per side, $2 per point (no funding)
  perp         taker fee per side + slippage per side, % of notional, plus funding while the trade is open
               (funding: long pays and short receives when the rate is positive; the strategy is flat
               every night, so it only pays for the hours a trade is open)

Sizing: on Nasdaq the perp is sized to the same notional as 1 MNQ ($2 x index), so dollars are directly
comparable. On gold / crypto, each trade risks 0.5% of a $10,000 account (max 5x leverage).

    python coinbase_perp/perp_compare.py
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import donchian_lab as L                    # noqa: E402
from coinbase_perp.bot import PRESETS       # noqa: E402

PERP_FEES = {"2 bps (Coinbase-like)": 2.0, "3.5 bps": 3.5, "4.5 bps (Hyperliquid base)": 4.5}
SLIP_BPS = 1.0
FUNDING_APR = {"10% APR (typical)": 0.10, "30% APR (hot market)": 0.30}
MNQ_COST_PTS = 1.12


def lab(p):
    return {k: v for k, v in p.items() if k not in ("first_bar", "atr_len")}


def find_trades(days, p, start=None):
    """Before-cost trades: date, side, entry price, points, R (stop distance), hours open."""
    p = lab(p)
    out = []
    for d in days:
        if start and d["date"] < start:
            continue
        d["cost"] = 0.0
        i, n = max(p.get("start_bar", 1), 1), 0
        while i < p.get("last_bar", 66) and n < p.get("max_trades", 3):
            hi, lo = d["dh"][p["n"]][i], d["dl"][p["n"]][i]
            up, dn = d["h"][i] > hi, d["l"][i] < lo
            if up != dn:
                s = 1 if up else -1
                e = max(hi, d["o"][i]) if up else min(lo, d["o"][i])
                R = p["stop_atr"] * d["atr"][i]
                pts, j = L.trade(d, i, e, s, R, p["exit_n"], trail_atr=p.get("trail_atr"))
                out.append(dict(date=d["date"], side=s, entry=e, pts=pts, R=R, hours=(j - i + 1) * 5 / 60))
                n += 1
                i = j + 1
                continue
            i += 1
    return out


def perp_cost(t, notional, fee_bps, apr, slip_bps=SLIP_BPS):
    fees = notional * 2 * (fee_bps + slip_bps) / 1e4
    funding = t["side"] * notional * apr * t["hours"] / (365 * 24)    # + = paid
    return fees, funding


def nasdaq(trades, label):
    gross = sum(t["pts"] for t in trades) * 2
    mnq = gross - len(trades) * MNQ_COST_PTS * 2
    hrs = sum(t["hours"] for t in trades)
    longs = sum(t["side"] > 0 for t in trades)
    print(f"\n--- {label}: {len(trades)} trades ({longs} long), {hrs:,.0f} hours in a trade")
    print(f"  before costs             ${gross:>10,.0f}")
    print(f"  1 MNQ                    ${mnq:>10,.0f}   costs ${gross - mnq:,.0f}")
    for fl, fee in PERP_FEES.items():
        f = sum(perp_cost(t, 2 * t["entry"], fee, 0)[0] for t in trades)
        line = f"  US100 perp, fee {fl:<27}${gross - f:>10,.0f}   fees ${f:,.0f}"
        for al, apr in FUNDING_APR.items():
            fu = sum(perp_cost(t, 2 * t["entry"], fee, apr)[1] for t in trades)
            worst = sum(abs(perp_cost(t, 2 * t["entry"], fee, apr)[1]) for t in trades)
            line += f" | funding {al}: net ${fu:+,.0f} (if every trade paid: ${worst:,.0f})"
        print(line)
    return dict(gross=gross, mnq=mnq)


def risk_sized(trades, fee_bps, apr, equity=10000.0, risk=0.005, lev=5.0, slip_bps=SLIP_BPS):
    """$ result on a fixed $10k account (no compounding) with fees and funding split out."""
    gross = fees = fund = 0.0
    for t in trades:
        notional = min(equity * risk / (t["R"] / t["entry"]), equity * lev)
        q = notional / t["entry"]
        gross += t["pts"] * q
        f, fu = perp_cost(t, notional, fee_bps, apr, slip_bps)
        fees += f
        fund += fu
    return gross, fees, fund


def main():
    import datetime as dt
    rules = {"forward_test": PRESETS["forward_test"], "lab_baseline": PRESETS["lab_baseline"]}

    print("=" * 100 + "\nA. Nasdaq-100: 1 MNQ vs a US100 perp of the same notional (NDX 5-minute data, Dec 2024 - Nov 2026)")
    ndx = L.prepare("data/5min/NDX.csv.gz", usd=2.0, cost_pts=0.0)
    for name, p in rules.items():
        nasdaq(find_trades(ndx, p), f"{name}, two-year backtest")
    fw = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mnq_forward_trades.json")))
    ft = [dict(side=1 if t["side"] == "long" else -1, entry=t["entry"], pts=t["model_pts"],
               hours=(int(t["exit_time"][:2]) * 60 + int(t["exit_time"][3:]) - int(t["entry_time"][:2]) * 60 - int(t["entry_time"][3:]) + 5) / 60)
          for d in fw for t in d["trades"]]
    print(f"\nMNQ paper account booked ${sum(d['pnl'] for d in fw):,.2f} on these days (bot on minute prices, real fills)")
    nasdaq(ft, "forward_test, paper forward test Nov 17-20 (the actual 12 trades)")

    print("\n" + "=" * 100 + "\nB. Gold (Coinbase PAXG as the gold price), crypto: $10,000 account, 0.5% risk per trade, fixed size")
    print("   fee + 1 bps slippage per side; funding at 10% APR (30% in brackets)")
    one_year = dt.date(2025, 11, 17)
    for sym in ("PAXG", "BTC", "ETH"):
        path = f"data/5min/{sym}.csv.gz"
        if not os.path.exists(path):
            continue
        days = L.prepare(path, usd=1.0, cost_pts=0.0)
        print(f"\n  {sym}: {days[0]['date']} -> {days[-1]['date']}")
        for name, p in rules.items():
            for period, start in (("full history", None), ("last 12 months", one_year)):
                tr = find_trades(days, p, start)
                if not tr:
                    continue
                g, f2, fu = risk_sized(tr, 2.0, 0.10)
                _, f45, _ = risk_sized(tr, 4.5, 0.10)
                _, _, fu30 = risk_sized(tr, 2.0, 0.30)
                fut = ""
                if sym == "PAXG":   # micro gold future: $0.62 fee + 1 tick ($1) per side on 10 oz
                    _, fm, _ = risk_sized(tr, 1.62 / (10 * tr[-1]["entry"]) * 1e4, 0.0, slip_bps=0.0)
                    fut = f" | as MGC futures: costs ${fm:,.0f} -> ${g - fm:,.0f}"
                print(f"    {name:<13}{period:<15}{len(tr):>5} trades  before costs ${g:>8,.0f} | fees @2bps ${f2:>7,.0f} -> ${g - f2:>8,.0f}"
                      f" | @4.5bps ${f45:>7,.0f} -> ${g - f45:>8,.0f} | funding ${fu:>+5,.0f} (${fu30:>+5,.0f})" + fut)


if __name__ == "__main__":
    main()
