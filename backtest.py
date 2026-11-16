"""MA cross + ATR trailing stop backtest, 1 MES contract, vs buy & hold.

Data: S&P 500 cash index daily bars (proxy for MES; MES = $5 x index).
Rules (same as ma_cross_atr_trail.pine):
  - Long when fast EMA crosses above slow EMA, short when it crosses below
    (shorts optional). Signals on bar close, filled at next bar's open.
  - Trailing stop: close -/+ ATR_MULT * ATR, ratchets only in the trade's favor,
    updated on each close and live on the following bar (gap-through fills at open).
  - After a stop-out, wait for a fresh cross to re-enter.
"""
import sys
import itertools
import pandas as pd
import numpy as np

POINT_VALUE = 5.0      # MES $ per index point
COST_PER_SIDE = 0.62 + 1.25  # commission+fees + 1 tick slippage, $ per contract per side


def indicators(df, fast, slow, atr_len):
    d = df.copy()
    d["fast"] = d.close.ewm(span=fast, adjust=False).mean()
    d["slow"] = d.close.ewm(span=slow, adjust=False).mean()
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / atr_len, adjust=False).mean()  # Wilder RMA, as in TradingView
    up = d.fast > d.slow
    d["xup"] = up & ~up.shift(fill_value=False)
    d["xdn"] = ~up & up.shift(fill_value=True)
    return d


def run(d, start, end, atr_mult, allow_short):
    w = d[(d.index >= start) & (d.index <= end)]
    pos, entry, stop = 0, 0.0, 0.0
    pending = 0            # order to fill at this bar's open: +1 long, -1 short, 0 none
    pending_exit = False
    realized, trades, eq, eq_worst = 0.0, [], [], []
    edate = None

    def close_trade(px, date, why):
        nonlocal pos, realized
        pnl = (px - entry) * pos * POINT_VALUE - 2 * COST_PER_SIDE
        realized += pnl
        trades.append(dict(entry_date=edate, exit_date=date, side="L" if pos > 0 else "S",
                           entry=entry, exit=px, pnl=pnl, why=why))
        pos = 0

    for date, b in w.iterrows():
        if pos and (pending_exit or pending == -pos):
            close_trade(b.open, date, "cross")
        pending_exit = False
        if pending and not pos:
            pos, entry, edate = pending, b.open, date
            stop = b.close_prev - pos * atr_mult * b.atr_prev  # set on the signal bar's close
        pending = 0
        worst = b.close
        if pos:
            worst = b.low if pos > 0 else b.high
            if (pos > 0 and b.low <= stop) or (pos < 0 and b.high >= stop):
                px = min(b.open, stop) if pos > 0 else max(b.open, stop)
                close_trade(px, date, "atr_stop")
        if pos:
            stop = max(stop, b.close - atr_mult * b.atr) if pos > 0 else min(stop, b.close + atr_mult * b.atr)
        # signals on close
        if b.xup:
            pending = 1
        elif b.xdn:
            if allow_short:
                pending = -1
            elif pos > 0:
                pending_exit = True
        if pos == pending:
            pending = 0
        open_pnl = (b.close - entry) * pos * POINT_VALUE if pos else 0.0
        worst_pnl = (worst - entry) * pos * POINT_VALUE if pos else 0.0
        eq.append(realized + open_pnl)
        eq_worst.append(realized + worst_pnl)
    if pos:
        close_trade(w.close.iloc[-1], w.index[-1], "end")
        eq[-1] = realized
    return stats(pd.Series(eq, index=w.index), pd.Series(eq_worst, index=w.index), trades)


def stats(eq, eq_worst, trades):
    peak = eq.cummax().clip(lower=0)
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return dict(
        net=eq.iloc[-1],
        max_dd_close=(peak - eq).max(),            # end-of-day trailing drawdown
        max_dd_intraday=(peak - eq_worst).max(),   # using intrabar adverse extreme
        trades=len(pnls),
        win_rate=len(wins) / len(pnls) if pnls else float("nan"),
        profit_factor=sum(wins) / -sum(losses) if losses and sum(losses) else float("inf"),
        worst_trade=min(pnls) if pnls else 0.0,
        trade_list=trades, eq=eq,
    )


def buy_hold(d, start, end):
    w = d[(d.index >= start) & (d.index <= end)]
    entry = w.open.iloc[0]
    eq = (w.close - entry) * POINT_VALUE - COST_PER_SIDE
    eqw = (w.low - entry) * POINT_VALUE - COST_PER_SIDE
    eq.iloc[-1] -= COST_PER_SIDE
    peak = eq.cummax().clip(lower=0)
    return dict(net=eq.iloc[-1], max_dd_close=(peak - eq).max(), max_dd_intraday=(peak - eqw).max(),
                trades=1, win_rate=float(eq.iloc[-1] > 0), profit_factor=float("nan"),
                worst_trade=eq.iloc[-1], trade_list=[], eq=eq)


def fmt(name, s):
    pf = "  n/a" if np.isnan(s["profit_factor"]) else f"{s['profit_factor']:5.2f}"
    return (f"{name:<26}{s['net']:>9,.0f}{s['max_dd_close']:>9,.0f}{s['max_dd_intraday']:>9,.0f}"
            f"{s['trades']:>5}{s['win_rate']*100:>6.0f}%{pf:>7}{s['worst_trade']:>9,.0f}")


if __name__ == "__main__":
    df = pd.read_csv("data/spx_daily.csv", parse_dates=["date"], index_col="date")
    FAST, SLOW, ATR_LEN, ATR_MULT = 10, 30, 14, 3.0
    periods = {"2026 YTD": ("2026-01-01", "2026-12-31"), "2025": ("2025-01-01", "2025-12-31")}
    hdr = f"{'':<26}{'Net $':>9}{'DD eod':>9}{'DD intra':>9}{'Trd':>5}{'Win':>7}{'PF':>7}{'Worst':>9}"
    d = indicators(df, FAST, SLOW, ATR_LEN)
    d["atr_prev"], d["close_prev"] = d.atr.shift(), d.close.shift()
    for name, (s, e) in periods.items():
        w = d[(d.index >= s) & (d.index <= e)]
        print(f"\n=== {name}: {w.index[0].date()} -> {w.index[-1].date()}  "
              f"(EMA {FAST}/{SLOW}, ATR {ATR_LEN} x {ATR_MULT}, 1 MES) ===")
        print(hdr)
        lo = run(d, s, e, ATR_MULT, False)
        ls = run(d, s, e, ATR_MULT, True)
        print(fmt("MA cross long-only", lo))
        print(fmt("MA cross long+short", ls))
        print(fmt("Buy & hold", buy_hold(d, s, e)))
        if "-v" in sys.argv:
            for label, r in (("long-only", lo), ("long+short", ls)):
                print(f"  trades ({label}):")
                for t in r["trade_list"]:
                    print(f"    {t['side']} {t['entry_date'].date()} {t['entry']:8.2f} -> "
                          f"{t['exit_date'].date()} {t['exit']:8.2f}  {t['pnl']:>8,.0f}  {t['why']}")

    print("\n=== Parameter sweep, long-only net $ (max intraday DD $) ===")
    print(f"{'fast/slow':<10}{'ATRx':>5}{'2026 YTD':>18}{'2025':>18}")
    for (f, sl), m in itertools.product([(5, 20), (9, 21), (10, 30), (20, 50)], [2.0, 3.0, 4.0]):
        dd = indicators(df, f, sl, ATR_LEN)
        dd["atr_prev"], dd["close_prev"] = dd.atr.shift(), dd.close.shift()
        cells = []
        for s, e in periods.values():
            r = run(dd, s, e, m, False)
            cells.append(f"{r['net']:>9,.0f} ({r['max_dd_intraday']:>5,.0f})")
        print(f"{f}/{sl:<8}{m:>5}{cells[0]:>18}{cells[1]:>18}")
