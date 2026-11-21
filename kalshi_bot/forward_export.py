"""Export forward-test bets to JSON for the P&L dashboard: {rows, meta}.

    python -m kalshi_bot.forward_export [db] [out]
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone

from .forward import CRYPTO

LABELS = {"model_edge_5": "Model edge ≥5%", "model_edge_10": "Model edge ≥10%",
          "late_favorite": "Late favorite (85–97¢)", "longshot_10c": "Longshot (≤10¢)",
          "baseline_yes": "Baseline: buy YES", "user_down_2_3m_tp20": "Your rule: DOWN 20–35¢ @2–3m, +20% TP",
          "fade_1st_min_down": "First-minute fade: DOWN 20–35¢", "fade_1st_min_up": "First-minute fade: UP 20–35¢"}


def export(db="data/forward.sqlite", out="data/forward_export.json"):
    con = sqlite3.connect(db)
    rows = []
    for (ts, strat, series, ticker, side, price, n, fee, fair, tau, still, result, payout) in con.execute(
            "SELECT ts, strategy, series, ticker, side, price, contracts, fee, fair, tau, still_there, result, payout "
            "FROM bets ORDER BY ts"):
        cost = round(price * n + fee, 4)
        settled = result in ("yes", "no")
        rows.append({
            "time": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "strategy": LABELS.get(strat, strat), "market_type": "Crypto" if series in CRYPTO else "Index / FX / rates",
            "series": series, "ticker": ticker, "side": side, "price": round(price, 4), "contracts": n,
            "cost": cost, "status": "settled" if settled else "open",
            "won": (1 if payout and payout > 0 else 0) if settled else None,
            "payout": round(payout, 4) if settled else None,
            "pnl": round((payout or 0) - cost, 4) if settled else None,
            "model_fair": None if fair is None else round(fair, 4),
            "minutes_left": round(tau, 2), "quote_held": still,
        })
    hb = con.execute("SELECT min(ts), max(ts) FROM heartbeat").fetchone()
    beats = [r[0] for r in con.execute("SELECT ts FROM heartbeat ORDER BY ts")]
    active_h = sum(b - a for a, b in zip(beats, beats[1:]) if b - a < 60) / 3600   # gaps = test was down
    with open(__file__) as fh:
        code = fh.read()
    meta = [{"label": "forward_export.py", "value": code, "type": "code", "lang": "python"},
            {"label": "Input", "value": "data/forward.sqlite (live paper forward test, kalshi_bot/forward.py)"},
            {"label": "Test window (UTC)", "value": f"{datetime.fromtimestamp(hb[0], timezone.utc):%Y-%m-%d %H:%M} to "
                                                    f"{datetime.fromtimestamp(hb[1], timezone.utc):%Y-%m-%d %H:%M}"},
            {"label": "Hours actually running", "value": f"{active_h:.1f}"}]
    with open(out, "w") as fh:
        json.dump({"rows": rows, "meta": meta}, fh)
    return len(rows), out


if __name__ == "__main__":
    print(export(*sys.argv[1:]))
