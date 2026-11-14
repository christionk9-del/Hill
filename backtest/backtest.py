"""Short BTC when EMA9 crosses below EMA20 (1h). Cover when EMA9 crosses back above.
Signals on bar close; fills at next bar open. Size = 5% of equity at entry."""
import sys
import pandas as pd

def run(df, fee=0.0, start="2026-01-01", capital=10_000, pct=0.05):
    df = df.copy()
    df["ema9"] = df.close.ewm(span=9, adjust=False).mean()
    df["ema20"] = df.close.ewm(span=20, adjust=False).mean()
    below = df.ema9 < df.ema20
    df["xdown"] = below & ~below.shift(1, fill_value=False)
    df["xup"] = ~below & below.shift(1, fill_value=False)
    df = df[df.index >= start]
    eq, pos, trades, curve = capital, None, [], []
    idx = df.index
    for i in range(len(df) - 1):
        r, nxt = df.iloc[i], df.iloc[i + 1]
        if pos is None and r.xdown:
            notional = eq * pct
            pos = dict(entry_time=idx[i + 1], entry=nxt.open, qty=notional / nxt.open, notional=notional)
            eq -= notional * fee
        elif pos is not None and r.xup:
            exitp = nxt.open
            pnl = pos["qty"] * (pos["entry"] - exitp) - pos["qty"] * exitp * fee
            eq += pnl
            trades.append({**pos, "exit_time": idx[i + 1], "exit": exitp, "pnl": pnl,
                           "ret_pct": (pos["entry"] / exitp - 1) * 100})
            pos = None
        mtm = eq + (pos["qty"] * (pos["entry"] - r.close) if pos else 0)
        curve.append((idx[i], mtm))
    if pos:  # mark open trade at last close
        last = df.iloc[-1]
        pnl = pos["qty"] * (pos["entry"] - last.close)
        trades.append({**pos, "exit_time": df.index[-1], "exit": last.close, "pnl": pnl,
                       "ret_pct": (pos["entry"] / last.close - 1) * 100, "open": True})
        eq += pnl
    curve.append((df.index[-1], eq))
    return pd.DataFrame(trades), pd.Series(dict(curve)), eq, df

def stats(trades, curve, eq, capital=10_000):
    wins = trades[trades.pnl > 0]; losses = trades[trades.pnl <= 0]
    dd = (curve / curve.cummax() - 1).min() * 100
    return {
        "Final equity": f"${eq:,.2f}", "Net P&L": f"${eq-capital:,.2f} ({(eq/capital-1)*100:.2f}%)",
        "Trades": len(trades), "Win rate": f"{len(wins)/len(trades)*100:.1f}%",
        "Avg win": f"${wins.pnl.mean():.2f}", "Avg loss": f"${losses.pnl.mean():.2f}",
        "Largest win": f"${trades.pnl.max():.2f}", "Largest loss": f"${trades.pnl.min():.2f}",
        "Profit factor": f"{wins.pnl.sum()/abs(losses.pnl.sum()):.2f}",
        "Max drawdown": f"{dd:.2f}%",
        "Avg hold (h)": f"{((trades.exit_time-trades.entry_time).dt.total_seconds()/3600).mean():.1f}",
    }

if __name__ == "__main__":
    df = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else "btc_1h.csv")
    df.index = pd.to_datetime(df.ts, unit="s", utc=True)
    for fee in (0.0, 0.001):
        t, c, eq, d = run(df, fee=fee)
        print(f"\n=== fee {fee*100:.1f}% per side ===")
        for k, v in stats(t, c, eq).items(): print(f"{k:15s} {v}")
        if fee == 0:
            t.to_csv("trades.csv", index=False); c.rename("equity").to_csv("equity.csv")
            print("BTC period:", f"{d.open.iloc[0]:,.0f} -> {d.close.iloc[-1]:,.0f}",
                  f"({(d.close.iloc[-1]/d.open.iloc[0]-1)*100:.1f}%)", d.index[0], "->", d.index[-1])
            m = t.set_index("exit_time").pnl.resample("ME").agg(["sum", "count"])
            print(m.round(2).to_string())
