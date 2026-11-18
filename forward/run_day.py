"""Forward test: run one finished trading day through the live bot on paper.

Inputs are Robinhood index-historicals JSON results for the Nasdaq-100 (NDX): 5-minute bars for
the previous days (warm-up) and 1-minute bars for the day being tested. The bot from
live/topstep_bot.py trades that day against the simulated broker (live/sim_broker.py), so fills
come from real minute prices. The day's profit per contract is then applied to 20 paper prop
accounts (15 with a $3,000 end-of-day drawdown, 5 with $2,500), one new account starting each
trading day, under the same rules as the fleet simulation.

    python forward/run_day.py --state prev_snapshot.json --out out_dir FILE.json [FILE.json ...]

Writes out_dir/writes-N.json files (lists of ArtifactData batch writes, all new documents) and
prints a summary. Exits without writing if the minute bars do not cover a full session.
"""
import argparse
import glob
import json
import logging
import os
import sys
from datetime import timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live.sim_broker import SimClock, SimProjectX, NY   # noqa: E402
from live.topstep_bot import Bot                       # noqa: E402

NDX_ID = "50c298f7-27a8-44a1-b049-ec153cf2892f"
FEE, TARGET, PAYOUT, LOCK, EVAL_CONSISTENCY = 130.0, 3000.0, 1000.0, 100.0, 0.5
ENTRY_MODE = os.environ.get("FORWARD_ENTRY_MODE", "market")
ROSTER = ([dict(id=f"tv{i:02d}", name=f"Paper Tradovate {i:02d}", platform="TradingView → Tradovate", drawdown=3000.0) for i in range(1, 16)]
          + [dict(id=f"ts{i:02d}", name=f"Paper Topstep {i:02d}", platform="Topstep API", drawdown=2500.0) for i in range(1, 6)])


def read_bars(files):
    five, mins = [], []
    for f in files:
        j = json.load(open(f))
        for r in j.get("data", {}).get("results", []):
            if r.get("instrument_id") != NDX_ID and r.get("symbol") != "NDX":
                continue
            for b in r["bars"]:
                if b.get("interpolated"):
                    continue
                row = (b["begins_at"], float(b["open_value"]), float(b["high_value"]), float(b["low_value"]), float(b["close_value"]))
                (mins if r.get("interval") == "minute" else five if r.get("interval") == "5minute" else []).append(row)
    out = []
    for rows in (five, mins):
        d = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
        d["ts"] = pd.to_datetime(d.ts, utc=True).dt.tz_convert(NY)
        d = d.drop_duplicates("ts").sort_values("ts").set_index("ts")
        m = d.index.hour * 60 + d.index.minute
        out.append(d[(m >= 570) & (m < 960) & (d.index.dayofweek < 5)])
    return out


def trade_day(five, minutes, day):
    """Run the bot on one simulated account; return (net $ for 1 contract, trades list)."""
    m = minutes[minutes.index.date == day].rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})
    hist = five[five.index.date < day]
    clock = SimClock(pd.Timestamp(f"{day} 09:24", tz=NY).to_pydatetime())
    api = SimProjectX(hist, m, clock, n_accounts=1)
    clock.broker = api
    log = logging.getLogger("forward")
    log.setLevel(logging.WARNING)
    cfg = dict(accounts=["SIM-1"], dry_run=False, poll_seconds=20, entry_mode=ENTRY_MODE, contracts_per_account=1,
               daily_loss_limit=10**9, log_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"), stop_file="/nonexistent/STOP")
    bot = Bot(cfg, api, clock, log)
    bot.start()
    bot.run(until=pd.Timestamp(f"{day} 16:06", tz=NY).to_pydatetime())
    trades = [dict(side="long" if t["side"] > 0 else "short", entry_time=t["time"][11:16], exit_time=t.get("exit_time", "")[11:16],
                   entry=round(t["entry"], 2), exit=round(t.get("exit", 0), 2), reason=t.get("reason", ""),
                   model_pts=round(t.get("pnl_pts", 0), 2)) for t in bot.closed_trades]
    left = bool(api.pos[1]) or bool(api.orders[1])
    return round(api.accts[1]["balance"] - 50000.0, 2), trades, left


def fresh_state():
    return dict(days_run=0, accounts=[dict(a, order=i + 1, phase="Not started", start=50000.0, balance=50000.0, peak=50000.0,
                                           best_day=0.0, cycle_base=50000.0, restart=False) for i, a in enumerate(ROSTER)])


def apply_day(state, day, pnl):
    """Advance every paper account by one day. Returns (state, ledger entries)."""
    ledger = []
    k = state["days_run"]
    for i, a in enumerate(state["accounts"]):
        if a["phase"] == "Not started" and i <= k:
            a.update(phase="Evaluation", balance=a["start"], peak=a["start"], best_day=0.0, restart=False)
            ledger.append(dict(date=day, account=a["name"], kind="Evaluation fee", amount=FEE))
        elif a["phase"] == "Failed" and a.get("restart"):
            a.update(phase="Evaluation", balance=a["start"], peak=a["start"], best_day=0.0, restart=False)
            ledger.append(dict(date=day, account=a["name"], kind="Evaluation fee", amount=FEE))
        if a["phase"] == "Evaluation":
            floor = min(a["peak"] - a["drawdown"], a["start"])
            a["balance"] = round(a["balance"] + pnl, 2)
            a["best_day"] = max(a["best_day"], pnl)
            profit = a["balance"] - a["start"]
            if a["balance"] <= floor:
                a.update(phase="Failed", restart=True)
            else:
                a["peak"] = max(a["peak"], a["balance"])
                if profit >= TARGET and a["best_day"] <= EVAL_CONSISTENCY * profit:
                    a.update(phase="Funded", balance=a["start"], peak=a["start"], cycle_base=a["start"], best_day=0.0)
        elif a["phase"] == "Funded":
            floor = min(a["peak"] - a["drawdown"], a["start"] + LOCK)
            a["balance"] = round(a["balance"] + pnl, 2)
            if a["balance"] <= floor:
                a.update(phase="Failed", restart=True)
            else:
                a["peak"] = max(a["peak"], a["balance"])
                if a["balance"] - a["cycle_base"] >= PAYOUT and a["balance"] - PAYOUT >= a["start"] + LOCK:
                    a["balance"] = round(a["balance"] - PAYOUT, 2)
                    a["cycle_base"] = a["balance"]
                    ledger.append(dict(date=day, account=a["name"], kind="Payout", amount=PAYOUT))
    state["days_run"] = k + 1
    return state, ledger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", help="latest snapshot JSON (ArtifactData read output or the document itself)")
    ap.add_argument("--out", required=True)
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    files = [p for f in a.files for p in glob.glob(f)]
    five, minutes = read_bars(files)
    if minutes.empty:
        sys.exit("no minute bars found")
    day = max(minutes.index.date)
    n = (minutes.index.date == day).sum()
    if n < 380:
        sys.exit(f"{day}: only {n} minute bars; not a full session, nothing written")
    if (five.index.date < day).sum() < 150:
        sys.exit("not enough 5-minute warm-up bars before the day")
    state = None
    if a.state and os.path.exists(a.state):
        doc = json.load(open(a.state))
        doc = doc.get("data", doc)
        state = doc.get("state")
        if isinstance(state, str):
            state = json.loads(state)
        if doc.get("date") == day.isoformat():
            sys.exit(f"{day} already recorded; nothing written")
    state = state or fresh_state()
    pnl, trades, left = trade_day(five, minutes, day)
    state, ledger = apply_day(state, day.isoformat(), pnl)
    ds = day.isoformat()
    writes = [dict(op="set", collection="data/paper/days", doc_id=ds,
                   data=dict(date=ds, pnl=pnl, trades=len(trades), detail=json.dumps(trades), model_pts=round(sum(t["model_pts"] for t in trades), 2),
                             entry_mode=ENTRY_MODE, leftover=left)),
              dict(op="set", collection="data/paper/snapshots", doc_id=ds,
                   data=dict(date=ds, day_number=state["days_run"], accounts=json.dumps(state["accounts"]), state=json.dumps(state)))]
    for i, e in enumerate(ledger):
        writes.append(dict(op="set", collection="data/paper/ledger", doc_id=f"{ds}-{i:02d}", data=e))
    os.makedirs(a.out, exist_ok=True)
    for w in writes:                                   # each document's data in its own file
        f = os.path.abspath(os.path.join(a.out, f"{w['collection'].replace('/', '_')}__{w['doc_id']}.json"))
        json.dump(w.pop("data"), open(f, "w"))
        w["file_path"] = f
    paths = []
    for i in range(0, len(writes), 50):
        p = os.path.join(a.out, f"writes-{i // 50}.json")
        json.dump(writes[i:i + 50], open(p, "w"), indent=1)
        paths.append(os.path.abspath(p))
    phases = pd.Series([x["phase"] for x in state["accounts"]]).value_counts().to_dict()
    print(json.dumps(dict(date=ds, day_number=state["days_run"], pnl_per_contract=pnl, trades=len(trades), leftover=left,
                          ledger=len(ledger), phases=phases, write_files=paths)))


if __name__ == "__main__":
    main()
