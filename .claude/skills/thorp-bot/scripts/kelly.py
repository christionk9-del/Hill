#!/usr/bin/env python3
"""Thorp Bot calculator: EV, break-even, Kelly sizing, multi-outcome payouts,
correlated parlays and bankroll simulation. Every number the agent reports
should come from here, not from mental arithmetic.

Subcommands (all money in dollars, probabilities as 0-1, payouts as TOTAL return
multiples, e.g. a PrizePicks 2-pick Power Play returns 3.0x the entry):

  binary     --p 0.40 --payout 3.0 [--bankroll 1000] [--p-low 0.35 --p-high 0.45]
  american   --p 0.55 --odds=-110            (sportsbook odds; converts to payout; use "=")
  kalshi     --p 0.62 --price 0.55 [--maker]  (buy a contract at `price`; taker fee applied)
  multi      --outcomes "0.41:2.0,0.49:0.5,0.10:0"   (prob:payout pairs; e.g. Flex Play)
  parlay     --legs "0.80,0.50" --payout 3.0 [--rho 0.0]  (legs' win probs; rho = leg correlation)
  pickem     --legs "0.80,0.50" --type power|flex       (PrizePicks payouts built in)
  simulate   --p 0.40 --payout 3.0 --frac 0.03 --bets 156 [--bankroll 1000] [--cap 25]
  devig      --odds=-130,+110                (fair probabilities from a two-way market; use "=")

Add --json to any command for machine-readable output.
"""
import argparse
import json
import math
import sys

import numpy as np

TAKER = 0.07      # Kalshi taker fee: 0.07 * P * (1 - P) per contract
MAKER = 0.0175    # Kalshi maker fee where charged

# PrizePicks standard payout tables (total return multiples). Verify in the app:
# promos and "goblin/demon" lines change these.
POWER = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5}
FLEX = {3: {3: 3.0, 2: 1.0}, 4: {4: 6.0, 3: 1.5}, 5: {5: 10.0, 4: 2.0, 3: 0.4},
        6: {6: 25.0, 5: 2.0, 4: 0.4}, 2: {2: 2.0, 1: 0.5}}


# ---------------------------------------------------------------- core math
def kelly_binary(p, payout):
    """Full-Kelly fraction for a bet that returns `payout` x stake with prob p, else 0."""
    b = payout - 1.0                      # net odds
    if b <= 0:
        return 0.0
    return max(0.0, (b * p - (1 - p)) / b)


def growth_rate(outcomes, f):
    """Expected log growth per bet: sum p * ln(1 + f*(payout-1)); outcomes = [(p, payout)]."""
    g = 0.0
    for p, x in outcomes:
        w = 1 + f * (x - 1)
        if w <= 0:
            return -math.inf
        g += p * math.log(w)
    return g


def kelly_general(outcomes):
    """Kelly fraction for any discrete payout table, by maximising log growth."""
    lo, hi = 0.0, 0.999
    best_f, best_g = 0.0, 0.0
    for f in np.linspace(lo, hi, 2000):
        g = growth_rate(outcomes, f)
        if g > best_g:
            best_f, best_g = f, g
    return best_f, best_g


def ev(outcomes):
    return sum(p * x for p, x in outcomes) - 1.0


def sizing_table(outcomes, bankroll=None):
    fk, gk = kelly_general(outcomes)
    rows = []
    for label, mult in (("full Kelly", 1.0), ("half Kelly", 0.5), ("third Kelly", 1 / 3), ("quarter Kelly", 0.25)):
        f = fk * mult
        g = growth_rate(outcomes, f) if f > 0 else 0.0
        rows.append({"size": label, "fraction": f, "stake": None if bankroll is None else bankroll * f,
                     "growth_per_bet": g, "share_of_max_growth": (g / gk) if gk > 0 else 0.0})
    return fk, rows


def mvn_orthant(z, corr, n=400_000, seed=0):
    """P(all Z_i < z_i) for standard normals with correlation matrix corr (Monte Carlo)."""
    rng = np.random.default_rng(seed)
    L = np.linalg.cholesky(corr + 1e-12 * np.eye(len(z)))
    s = rng.standard_normal((n, len(z))) @ L.T
    return float(np.mean(np.all(s < np.asarray(z), axis=1)))


def parlay_prob(ps, rho=0.0):
    """Probability every leg wins, with a common pairwise correlation rho (Gaussian copula)."""
    ps = np.clip(np.asarray(ps, float), 1e-6, 1 - 1e-6)
    if abs(rho) < 1e-9:
        return float(np.prod(ps))
    from statistics import NormalDist
    z = [NormalDist().inv_cdf(p) for p in ps]
    k = len(ps)
    corr = np.full((k, k), rho)
    np.fill_diagonal(corr, 1.0)
    return mvn_orthant(z, corr)


def pickem_outcomes(ps, kind):
    """Distribution of the number of hits for independent legs -> payout table."""
    k = len(ps)
    dist = np.zeros(k + 1)
    dist[0] = 1.0
    for p in ps:                       # convolution of Bernoullis
        dist[1:] = dist[1:] * (1 - p) + dist[:-1] * p
        dist[0] *= (1 - p)
    if kind == "power":
        table = {k: POWER[k]}
    else:
        table = FLEX[k]
    return [(float(dist[h]), table.get(h, 0.0)) for h in range(k + 1)]


def american_to_payout(odds):
    odds = float(odds)
    return 1 + (odds / 100 if odds > 0 else 100 / -odds)


def devig(odds_list):
    imp = [1 / american_to_payout(o) for o in odds_list]
    s = sum(imp)
    return [x / s for x in imp], s - 1


def simulate(outcomes, frac, bets, bankroll=1000.0, cap=None, n=50_000, seed=0):
    rng = np.random.default_rng(seed)
    ps = np.array([p for p, _ in outcomes]); xs = np.array([x for _, x in outcomes])
    ps = ps / ps.sum()
    br = np.full(n, float(bankroll)); peak = br.copy(); dd = np.zeros(n)
    for _ in range(bets):
        stake = br * frac
        if cap:
            stake = np.minimum(stake, cap)
        x = xs[rng.choice(len(xs), size=n, p=ps)]
        br = br - stake + stake * x
        peak = np.maximum(peak, br); dd = np.maximum(dd, 1 - br / peak)
    q = np.percentile(br, [5, 25, 50, 75, 95])
    return {"p5": q[0], "p25": q[1], "median": q[2], "p75": q[3], "p95": q[4], "mean": float(br.mean()),
            "p_end_below_start": float((br < bankroll).mean()), "median_max_drawdown": float(np.median(dd)),
            "p_drawdown_50pct": float((dd >= 0.5).mean())}


# ---------------------------------------------------------------- output
def fmt_pct(x):
    return f"{x * 100:+.1f}%"


def report_binary(p, payout, bankroll, p_low=None, p_high=None):
    outs = [(p, payout), (1 - p, 0.0)]
    be = 1 / payout
    fk, rows = sizing_table(outs, bankroll)
    res = {"ev": ev(outs), "break_even_prob": be, "edge_points": p - be, "kelly_full": fk, "sizing": rows}
    if p_low is not None and p_high is not None:
        res["ev_range"] = (p_low * payout - 1, p_high * payout - 1)
        res["kelly_at_low"] = kelly_binary(p_low, payout)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("binary"); b.add_argument("--p", type=float, required=True); b.add_argument("--payout", type=float, required=True)
    b.add_argument("--bankroll", type=float); b.add_argument("--p-low", type=float); b.add_argument("--p-high", type=float)
    a = sp.add_parser("american"); a.add_argument("--p", type=float, required=True); a.add_argument("--odds", type=float, required=True); a.add_argument("--bankroll", type=float)
    k = sp.add_parser("kalshi"); k.add_argument("--p", type=float, required=True); k.add_argument("--price", type=float, required=True)
    k.add_argument("--maker", action="store_true"); k.add_argument("--bankroll", type=float)
    m = sp.add_parser("multi"); m.add_argument("--outcomes", required=True); m.add_argument("--bankroll", type=float)
    pa = sp.add_parser("parlay"); pa.add_argument("--legs", required=True); pa.add_argument("--payout", type=float, required=True)
    pa.add_argument("--rho", type=float, default=0.0); pa.add_argument("--bankroll", type=float)
    pk = sp.add_parser("pickem"); pk.add_argument("--legs", required=True); pk.add_argument("--type", choices=["power", "flex"], required=True)
    pk.add_argument("--bankroll", type=float)
    s = sp.add_parser("simulate"); s.add_argument("--p", type=float); s.add_argument("--payout", type=float)
    s.add_argument("--outcomes"); s.add_argument("--frac", type=float, required=True); s.add_argument("--bets", type=int, required=True)
    s.add_argument("--bankroll", type=float, default=1000.0); s.add_argument("--cap", type=float)
    d = sp.add_parser("devig"); d.add_argument("--odds", required=True)
    args = ap.parse_args()

    if args.cmd == "binary":
        res = report_binary(args.p, args.payout, args.bankroll, args.p_low, args.p_high)
    elif args.cmd == "american":
        x = american_to_payout(args.odds)
        res = {"payout": x, **report_binary(args.p, x, args.bankroll)}
    elif args.cmd == "kalshi":
        fee = (MAKER if args.maker else TAKER) * args.price * (1 - args.price)
        cost = args.price + fee
        x = 1 / cost
        res = {"cost_per_contract": cost, "fee_per_contract": fee, "payout_multiple": x, **report_binary(args.p, x, args.bankroll)}
    elif args.cmd == "multi":
        outs = [tuple(map(float, t.split(":"))) for t in args.outcomes.split(",")]
        tot = sum(p for p, _ in outs)
        fk, rows = sizing_table(outs, args.bankroll)
        res = {"prob_sum": tot, "ev": ev(outs), "kelly_full": fk, "sizing": rows}
    elif args.cmd == "parlay":
        ps = [float(x) for x in args.legs.split(",")]
        pj = parlay_prob(ps, args.rho)
        res = {"joint_prob": pj, "independent_prob": float(np.prod(ps)), **report_binary(pj, args.payout, args.bankroll)}
    elif args.cmd == "pickem":
        ps = [float(x) for x in args.legs.split(",")]
        outs = pickem_outcomes(ps, args.type)
        fk, rows = sizing_table(outs, args.bankroll)
        res = {"outcomes": outs, "ev": ev(outs), "kelly_full": fk, "sizing": rows,
               "note": "assumes independent legs; same-game legs are correlated"}
    elif args.cmd == "simulate":
        outs = [tuple(map(float, t.split(":"))) for t in args.outcomes.split(",")] if args.outcomes else \
            [(args.p, args.payout), (1 - args.p, 0.0)]
        res = {"ev": ev(outs), "kelly_full": kelly_general(outs)[0], "frac": args.frac, "bets": args.bets,
               **simulate(outs, args.frac, args.bets, args.bankroll, args.cap)}
    elif args.cmd == "devig":
        fair, vig = devig([float(x) for x in args.odds.split(",")])
        res = {"fair_probs": fair, "vig": vig}

    if args.json:
        print(json.dumps(res, indent=2, default=float))
        return
    for kx, v in res.items():
        if kx == "sizing":
            print("sizing:")
            for r in v:
                st = f"  stake ${r['stake']:,.2f}" if r["stake"] is not None else ""
                print(f"  {r['size']:14} {r['fraction'] * 100:5.2f}% of bankroll{st}  "
                      f"growth/bet {r['growth_per_bet'] * 100:+.3f}%  ({r['share_of_max_growth']:.0%} of max)")
        elif kx in ("ev", "edge_points"):
            print(f"{kx}: {fmt_pct(v)}")
        elif isinstance(v, float):
            print(f"{kx}: {v:.4f}")
        else:
            print(f"{kx}: {v}")


if __name__ == "__main__":
    sys.exit(main())
