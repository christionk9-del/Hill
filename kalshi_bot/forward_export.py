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




def export_speedbot(db="data/speedbot.sqlite", out="data/speedbot_export.json"):
    """Speed bot bets plus model-calibration snapshots (only windows that have settled)."""
    con = sqlite3.connect(db)
    bets = []
    for (ts, mode, tk, side, ask, n, fee, fair, edge, tau, sa, ba, held, res, payout, filled) in con.execute(
            "SELECT ts, mode, ticker, side, ask, contracts, fee, fair, edge, tau, spot_age, book_age, quote_held, "
            "result, payout, filled FROM bets WHERE filled > 0 ORDER BY ts"):
        cost = round(ask * n + fee, 4)
        done = res in ("yes", "no")
        bets.append({"time": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": mode,
                     "ticker": tk, "side": side.upper(), "price": ask, "contracts": n, "cost": cost,
                     "model_fair": round(fair, 4), "edge": round(edge, 4), "minutes_left": round(tau, 2),
                     "btc_price_age_s": round(sa, 3), "book_age_s": round(ba, 3), "quote_held": held,
                     "status": "settled" if done else "open", "won": (1 if payout else 0) if done else None,
                     "pnl": round(payout - cost, 4) if done else None})
    cal = [{"ticker": tk, "minutes_left": round(tau, 2), "model_yes": round(f, 4),
            "kalshi_yes_ask": ya, "yes_won": int(r == "yes")}
           for tk, tau, f, ya, r in con.execute(
               "SELECT s.ticker, s.tau, s.fair_yes, s.yes_ask, w.result FROM snaps s JOIN windows w USING(ticker) "
               "WHERE s.tau >= 1 AND w.result IN ('yes','no')")]
    snaps = con.execute("SELECT count(*), count(DISTINCT ticker), min(ts), max(ts) FROM snaps").fetchone()
    with open(__file__) as fh:
        code = fh.read()
    meta = [{"label": "forward_export.py", "value": code, "type": "code", "lang": "python"},
            {"label": "Input", "value": "data/speedbot.sqlite (kalshi_bot/speedbot.py, paper mode)"},
            {"label": "Windows watched", "value": str(snaps[1])}]
    json.dump({"rows": bets, "meta": meta}, open(out, "w"))
    json.dump({"rows": cal, "meta": meta[:2]}, open(out.replace(".json", "_calibration.json"), "w"))
    return len(bets), len(cal), snaps[1]


if __name__ == "__main__":
    print(export(), export_speedbot())
