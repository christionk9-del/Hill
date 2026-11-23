"""Kalshi mirror of the NDX 0DTE dip-put strategy (from the 'auto trade sims' forward test).

Options rule: checks at 9:35 ET and hourly at :45 (9:45-15:45; entries until 15:30). The first
time today NDX is >= threshold below TODAY's 9:30 open (or, with the gap rule, below the
PREVIOUS close), buy one at-the-money 0DTE NDXP put at the ask; sell at the bid at 15:55.

Kalshi mirror: at the same check, buy NO on the 4pm `KXNASDAQ100U` "above X" strike that
Kalshi prices closest to 50c (the binary version of an at-the-money put), at the real ask
(first taker NO buy on that strike in the 10 minutes after the check). Hold to 4pm settlement.

Index path: Robinhood 30-minute NDX bars; at a check, only bars that have ENDED are used,
so the signal can lag by up to 30 minutes (conservative timing, no look-ahead).
"""
import csv
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FEE = 0.07
CHECKS = [(9, 35)] + [(h, 45) for h in range(9, 16)]   # 9:35, 9:45, ..., 15:45


def load_bars(path="data/ndx/ndx_30m.csv"):
    days = defaultdict(list)
    for r in csv.DictReader(open(path)):
        t = datetime.fromisoformat(r["t_et"])
        days[t.date()].append((t, float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"])))
    return {d: sorted(v) for d, v in days.items()}


def signals(days, threshold=0.0075, use_gap=True, latest=(15, 30)):
    """-> list of {date, check_time, spot, trigger, day_open, prev_close, close_4pm}"""
    out, prev_close = [], None
    for d in sorted(days):
        bars = days[d]
        if len(bars) < 12 or bars[0][0].hour != 9 or bars[0][0].minute != 30:
            prev_close = bars[-1][4] if bars else prev_close
            continue
        day_open, close = bars[0][1], bars[-1][4]
        for hh, mm in CHECKS:
            if (hh, mm) > latest:
                break
            now = datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)
            done = [b for b in bars if b[0] + timedelta(minutes=30) <= now]
            spot = done[-1][4] if done else day_open
            low = min([b[3] for b in done] + [spot])
            trig = None
            if low <= day_open * (1 - threshold):
                trig = "open"
            elif use_gap and prev_close and low <= prev_close * (1 - threshold):
                trig = "gap"
            if trig:
                out.append({"date": d, "check": now, "spot": spot, "trigger": trig, "day_open": day_open,
                            "prev_close": prev_close, "close": close})
                break
        prev_close = close
    return out


def kalshi_entries(sigs, con, wait_min=10):
    """Attach the Kalshi ATM-NO entry for each signal from the trade table `idx_tr`."""
    res = []
    for s in sigs:
        ev = f"KXNASDAQ100U-{s['date']:%y%b%d}".upper() + "H1600"
        t0 = s["check"]
        rows = con.execute(
            """SELECT ticker, created_time, taker_side, px, result FROM idx_tr
               WHERE event_ticker = ? AND created_time BETWEEN ? AND ?
               ORDER BY created_time""", [ev, t0 - timedelta(minutes=30), t0 + timedelta(minutes=wait_min)]).fetchall()
        if not rows:
            res.append({**s, "status": "no_kalshi_trades", "event": ev})
            continue
        # strike Kalshi prices closest to 50c, from the last YES price seen per strike up to the check
        last_yes = {}
        for tk, ct, side, px, _ in rows:
            if ct <= t0:
                last_yes[tk] = px / 100 if side == "yes" else 1 - px / 100
        if not last_yes:
            res.append({**s, "status": "no_quotes_before_check", "event": ev})
            continue
        atm = min(last_yes, key=lambda k: abs(last_yes[k] - 0.5))
        entry = next(((ct, px / 100, r) for tk, ct, side, px, r in rows
                      if tk == atm and side == "no" and ct >= t0), None)
        if not entry:
            res.append({**s, "status": "no_no_ask_in_window", "event": ev, "strike_ticker": atm})
            continue
        ct, p, result = entry
        if not (0.05 <= p <= 0.95):
            res.append({**s, "status": "atm_price_out_of_range", "event": ev})
            continue
        cost = p * (1 + FEE * (1 - p))
        res.append({**s, "status": "entry", "event": ev, "strike_ticker": atm, "no_price": p, "cost": cost,
                    "win": int(result == "no"), "entry_time": ct})
    return res
