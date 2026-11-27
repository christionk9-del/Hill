#!/usr/bin/env python3
"""Bet ledger: record each bet with the probability you assigned, settle it later, and
check whether your probabilities were right (calibration), whether you made money, and
whether your sizing followed the plan.

  add     --desc "Tatum 19.5+ / Embiid 21.5+ power" --stake 25 --payout 3.0 --p 0.40 [--venue PrizePicks] [--kelly-frac 0.33]
  settle  --id 3 --result win|loss|push|void [--returned 75]
  report  [--min 20]

Data lives in a CSV (default ./bets_log.csv, or --file / $THORP_LEDGER).
"""
import argparse
import csv
import math
import os
import sys
from datetime import datetime, timezone

FIELDS = ["id", "time", "venue", "desc", "stake", "payout", "p", "ev", "kelly_frac", "result", "returned"]


def path(a):
    return a.file or os.environ.get("THORP_LEDGER", "bets_log.csv")


def load(p):
    if not os.path.exists(p):
        return []
    with open(p) as fh:
        return list(csv.DictReader(fh))


def save(p, rows):
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file")
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("add")
    a.add_argument("--desc", required=True); a.add_argument("--stake", type=float, required=True)
    a.add_argument("--payout", type=float, required=True, help="total return multiple if it wins")
    a.add_argument("--p", type=float, required=True, help="your probability it wins")
    a.add_argument("--venue", default=""); a.add_argument("--kelly-frac", type=float, default=None)
    s = sp.add_parser("settle")
    s.add_argument("--id", required=True); s.add_argument("--result", choices=["win", "loss", "push", "void"], required=True)
    s.add_argument("--returned", type=float, default=None, help="actual amount returned (for partial payouts)")
    r = sp.add_parser("report"); r.add_argument("--min", type=int, default=20)
    args = ap.parse_args()
    p = path(args)
    rows = load(p)

    if args.cmd == "add":
        nid = str(max([int(x["id"]) for x in rows], default=0) + 1)
        rows.append({"id": nid, "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "venue": args.venue,
                     "desc": args.desc, "stake": args.stake, "payout": args.payout, "p": args.p,
                     "ev": round(args.p * args.payout - 1, 4), "kelly_frac": args.kelly_frac or "", "result": "", "returned": ""})
        save(p, rows)
        print(f"added bet {nid}: EV {args.p * args.payout - 1:+.1%} on ${args.stake:.2f}")
    elif args.cmd == "settle":
        for x in rows:
            if x["id"] == args.id:
                x["result"] = args.result
                st, po = float(x["stake"]), float(x["payout"])
                x["returned"] = args.returned if args.returned is not None else \
                    {"win": st * po, "loss": 0.0, "push": st, "void": st}[args.result]
                save(p, rows)
                print(f"settled bet {args.id}: {args.result}, returned ${float(x['returned']):.2f}")
                return
        sys.exit(f"no bet with id {args.id}")
    else:
        done = [x for x in rows if x["result"] in ("win", "loss")]
        if not done:
            print("no settled win/loss bets yet"); return
        n = len(done)
        staked = sum(float(x["stake"]) for x in done)
        back = sum(float(x["returned"]) for x in done)
        exp_wins = sum(float(x["p"]) for x in done)
        wins = sum(x["result"] == "win" for x in done)
        exp_back = sum(float(x["stake"]) * float(x["p"]) * float(x["payout"]) for x in done)
        sd = math.sqrt(sum(float(x["p"]) * (1 - float(x["p"])) for x in done))
        z = (wins - exp_wins) / sd if sd > 0 else 0.0
        print(f"settled bets: {n}   staked ${staked:,.2f}   returned ${back:,.2f}   P&L ${back - staked:+,.2f}")
        print(f"actual return per $1: {back / staked:.3f}   your predicted return per $1: {exp_back / staked:.3f}")
        print(f"wins: {wins} vs {exp_wins:.1f} predicted (z = {z:+.2f}; beyond about ±2 means your probabilities are off)")
        for lo, hi in ((0, .3), (.3, .5), (.5, .7), (.7, 1.01)):
            b = [x for x in done if lo <= float(x["p"]) < hi]
            if b:
                print(f"  you said {lo:.0%}-{min(hi, 1):.0%}: {len(b)} bets, won {sum(x['result'] == 'win' for x in b) / len(b):.0%}, "
                      f"predicted {sum(float(x['p']) for x in b) / len(b):.0%}")
        if n < args.min:
            print(f"only {n} settled bets: too few to judge your edge (aim for {args.min}+; 100+ to size up)")


if __name__ == "__main__":
    main()
