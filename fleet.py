"""Simulate running many prop-firm accounts on the same strategy at once.

Every account trades the same signals (copy trading), so accounts differ only by when they
started and their own drawdown/payout state. Daily P&L is resampled from history in 20-day
blocks to build many possible 2-year futures. A slot whose account fails or blows up buys a
new evaluation the next day, so the fleet always holds N accounts (evaluations + funded).

Rules modelled (generic 50K, adjust in RULES):
  evaluation: +$3,000 target, $2,000 end-of-day trailing drawdown that stops trailing at the
              starting balance, best day <= 50% of profit when passing.
  funded:     $2,000 end-of-day trailing drawdown that locks at start + $100; a $1,000
              payout when the cycle has >= 5 trading days, cycle profit >= $1,000, best
              day <= 35% of cycle profit, and the balance after paying stays >= the buffer.
              Alternative (win_days > 0): at least win_days days of >= win_amt profit in the
              cycle instead of, or as well as, the consistency rule.
"""
import sys
import numpy as np

RULES = dict(target=3000, dd=2000, eval_consistency=0.5, lock=100, payout=1000,
             min_days=5, consistency=0.35, buffer=100, win_days=0, win_amt=150)
MONTH = 21


CONTRACTS = {"NDX": (2.0, 1.12), "SPX": (5.0, 0.748)}   # MNQ / MES: $ per point, round-trip cost in points


def load_pnl(sym):
    """Daily $ P&L of the Donchian strategy for one micro contract, flat by the close."""
    import intraday as it
    import markets as mk
    usd, cost = CONTRACTS[sym]
    days = it.load(f"data/5min/{sym}.csv.gz", cost_bps=0, risk_usd=300)
    for d in days:
        d["usd"], d["cost"] = usd, cost
    return np.array([r[0] for r in it.daily(days, it.donchian, **mk.PRIMARY)])


def bootstrap(n_days, paths, T, block=20, seed=1):
    rng = np.random.default_rng(seed)
    idx = np.empty((paths, T), dtype=int)
    for p in range(paths):
        out = []
        while len(out) < T:
            s = rng.integers(0, n_days - block)
            out.extend(range(s, s + block))
        idx[p] = out[:T]
    return idx


def simulate(P, N, stagger=1, rules=RULES):
    """P: (paths, T) daily $ P&L per account (already sized). Returns per-path monthly stats."""
    r = rules
    paths, T = P.shape
    start = np.arange(N) * stagger                      # day each slot opens its first evaluation
    phase = np.zeros((paths, N), int)                   # 0 waiting, 1 eval, 2 funded
    bal = np.zeros((paths, N)); peak = np.zeros((paths, N)); best = np.zeros((paths, N))
    base = np.zeros((paths, N)); cdays = np.zeros((paths, N), int); wins = np.zeros((paths, N), int)
    months = T // MONTH
    out = {k: np.zeros((paths, months)) for k in ("payouts", "evals", "passes", "eval_fail", "funded_blown", "funded")}
    for t in range(T):
        m = min(t // MONTH, months - 1)
        opening = (start == t)[None, :] & (phase == 0)
        new = opening.copy()
        x = P[:, t][:, None]
        ev, fu = phase == 1, phase == 2
        # evaluation day
        floor = np.minimum(peak - r["dd"], 0.0)
        b2 = np.where(ev, bal + x, bal)
        fail_e = ev & (b2 <= floor)
        best_e = np.where(ev, np.maximum(best, x), best)
        peak_e = np.where(ev, np.maximum(peak, b2), peak)
        pass_e = ev & ~fail_e & (b2 >= r["target"]) & (best_e <= r["eval_consistency"] * b2)
        # funded day
        floor_f = np.minimum(peak - r["dd"], r["lock"])
        b3 = np.where(fu, bal + x, b2)
        blow = fu & (b3 <= floor_f)
        peak_f = np.where(fu, np.maximum(peak, b3), peak_e)
        cd = np.where(fu, cdays + 1, cdays)
        best_f = np.where(fu, np.maximum(best, x), best_e)
        wd = np.where(fu, wins + (x >= r["win_amt"]), wins)
        cyc = b3 - base
        pay = (fu & ~blow & (cd >= r["min_days"]) & (cyc >= r["payout"]) & (wd >= r["win_days"])
               & (best_f <= r["consistency"] * cyc) & (b3 - r["payout"] >= r["buffer"]))
        b3 = np.where(pay, b3 - r["payout"], b3)
        base = np.where(pay, b3, base)
        cd = np.where(pay, 0, cd)
        wd = np.where(pay, 0, wd)
        best_f = np.where(pay, 0.0, best_f)
        bal, peak, best, cdays, wins = b3, peak_f, best_f, cd, wd
        out["payouts"][:, m] += pay.sum(1)
        out["eval_fail"][:, m] += fail_e.sum(1)
        out["funded_blown"][:, m] += blow.sum(1)
        out["passes"][:, m] += pass_e.sum(1)
        # transitions: passed -> funded (fresh), failed/blown -> new evaluation
        reset = pass_e | fail_e | blow | new
        bal = np.where(reset, 0.0, bal); peak = np.where(reset, 0.0, peak); best = np.where(reset, 0.0, best)
        base = np.where(reset, 0.0, base); cdays = np.where(reset, 0, cdays)
        wins = np.where(reset, 0, wins)
        phase = np.where(pass_e, 2, np.where(fail_e | blow | new, 1, phase))
        out["evals"][:, m] += (fail_e | blow | new).sum(1)
        out["funded"][:, m] += (phase == 2).sum(1) / MONTH
    return out


def report(label, out, skip=6, goal=15):
    s = {k: v[:, skip:] for k, v in out.items()}
    pay = s["payouts"]
    flat = pay.ravel()
    evals = s["evals"].mean()
    funded = s["funded"].mean()
    churn = (s["funded_blown"].sum() / s["funded"].sum()) if s["funded"].sum() else np.nan
    print(f"{label:<44}{flat.mean():>6.1f}{np.percentile(flat, 10):>6.0f}{np.percentile(flat, 90):>6.0f}"
          f"{(flat >= goal).mean()*100:>7.0f}%{(flat == 0).mean()*100:>7.0f}%{funded:>8.1f}{evals:>7.1f}"
          f"{churn*100:>7.0f}%{flat.mean()*1000/evals if evals else 0:>9,.0f}")


HEAD = (f"{'scenario (months 7-24)':<44}{'pay/mo':>6}{'p10':>6}{'p90':>6}{'>=15':>8}{'zero':>8}"
        f"{'funded':>8}{'evals':>7}{'churn':>8}{'$/eval':>9}")

if __name__ == "__main__":
    nq, es = load_pnl("NDX"), load_pnl("SPX")
    paths, T = 2000, 24 * MONTH
    idx = bootstrap(len(nq), paths, T)
    NQ, ES = nq[idx], es[idx]
    print("Payouts per month across the fleet; 'funded' = average funded accounts held; 'evals' = new")
    print("evaluations bought per month; churn = funded accounts blown per funded-account-month;")
    print("$/eval = payout dollars per evaluation bought (your break-even all-in cost per account).\n")
    print(HEAD)
    report("20 accts, 1 MNQ, all start same day", simulate(NQ, 20, stagger=0))
    report("20 accts, 1 MNQ, one new account a day", simulate(NQ, 20, stagger=1))
    report("20 accts, 1 MNQ, one new account a week", simulate(NQ, 20, stagger=5))
    report("20 accts, 2 MNQ, one a day", simulate(2 * NQ, 20, stagger=1))
    report("20 accts, 2 MES, one a day", simulate(2 * ES, 20, stagger=1))
    for n in (15, 25, 30, 40, 60):
        report(f"{n} accts, 1 MNQ, one a day", simulate(NQ, n, stagger=1))
    print("\nNo consistency rule; instead 5 days of $150+ profit per payout period:")
    alt = dict(RULES, consistency=1e9, win_days=5, win_amt=150)
    for n in (15, 20, 25, 30, 40, 60):
        report(f"{n} accts, 1 MNQ, one a day, 5 x $150 days", simulate(NQ, n, stagger=1, rules=alt))
    report("20 accts, 2 MNQ, one a day, 5 x $150 days", simulate(2 * NQ, 20, stagger=1, rules=alt))
    report("20 accts, 1 MNQ, 5 x $150 days, strict buffer", simulate(NQ, 20, stagger=1, rules=dict(alt, buffer=2100)))
    print("\nStricter payout buffer (balance must stay >= start + $2,100 after a payout):")
    strict = dict(RULES, buffer=2100)
    for n in (20, 30, 60):
        report(f"{n} accts, 1 MNQ, one a day, strict buffer", simulate(NQ, n, stagger=1, rules=strict))


FREE = dict(RULES, consistency=1e9, win_days=0, min_days=1)   # no consistency, no minimum days


def returns(out, fee):
    """Per-path dollars: year-1 and year-2 net, fees, and the most cash ever out of pocket.
    ROI = net / fees paid that year. Cash-on-cash = net / peak cash out of pocket."""
    net = out["payouts"] * 1000 - out["evals"] * fee
    fees = out["evals"] * fee
    cum = np.cumsum(net, 1)
    cash = np.maximum(-np.minimum.accumulate(cum, 1)[:, -1], fee)   # never below one evaluation
    y1, y2 = net[:, :12].sum(1), net[:, 12:24].sum(1)
    f1, f2 = fees[:, :12].sum(1), fees[:, 12:24].sum(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        roi1, roi2 = np.where(f1 > 0, y1 / f1, np.nan), np.where(f2 > 0, y2 / f2, np.nan)
    return dict(y1=y1, y2=y2, f1=f1, f2=f2, cash=cash, roi1=roi1, roi2=roi2, cocr1=y1 / cash, cocr2=y2 / cash)


def returns_table(P, fee, scenarios, rules=FREE):
    print(f"{'scenario':<20}{'cash needed':>12}{'yr1 net':>9}{'yr1 ROI':>9}{'yr1 CoCR':>9}{'yr2 net':>9}{'yr2 fees':>9}"
          f"{'yr2 ROI':>9}{'yr2 CoCR':>9}{'  yr2 ROI p10 / p90':>20}")
    for n, size in scenarios:
        r = returns(simulate(size * P, n, 1, rules), fee)
        med = {k: np.nanmedian(v) for k, v in r.items()}
        print(f"{f'{n} accts, {size} MNQ':<20}{med['cash']:>12,.0f}{med['y1']:>9,.0f}{med['roi1']*100:>8.0f}%{med['cocr1']*100:>8.0f}%"
              f"{med['y2']:>9,.0f}{med['f2']:>9,.0f}{med['roi2']*100:>8.0f}%{med['cocr2']*100:>8.0f}%"
              f"{np.nanpercentile(r['roi2'], 10)*100:>10.0f}% / {np.nanpercentile(r['roi2'], 90)*100:.0f}%")
