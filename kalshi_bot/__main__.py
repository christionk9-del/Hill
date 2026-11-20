"""CLI.  python -m kalshi_bot {calibrate|scan|trade|settle|stats|simulate}"""
import argparse
import json
import sys

from . import calibration
from .config import CONFIG


def cmd_calibrate(a):
    from .client import KalshiClient
    obs = calibration.collect(KalshiClient(), max_events=a.max_events)
    table = calibration.summarize(obs)
    calibration.save(table)
    print(calibration.report(table))
    print(f"\nSaved to {CONFIG.calibration_path}. A '$ back per $1' above ~1.05 in a "
          f"well-sampled bucket is the ONLY thing that justifies live betting.")


def _scan():
    from .client import KalshiClient
    from .strategy import find_candidates
    table = calibration.load()
    if not table:
        sys.exit("No calibration table. Run `python -m kalshi_bot calibrate` first.")
    return find_candidates(KalshiClient().events(status="open"), table)


def cmd_scan(a):
    cands = _scan()
    if not cands:
        print("No positive-edge tail bets right now. (That is the expected, honest answer most days.)")
    for c in cands[:20]:
        print(f"{c.ticker:40} {c.side:3} @{c.price_c:>2}c x{c.count:<3} est win {c.est_win_prob:6.2%} "
              f"breakeven {c.breakeven:6.2%} edge {c.edge_c:+.2f}c/contract  closes {c.hours_to_close:.0f}h")


def cmd_trade(a):
    from .client import KalshiClient
    from .ledger import Ledger
    mode = "live" if a.live else "paper"
    led, client = Ledger(), KalshiClient()
    if a.live and not CONFIG.key_id:
        sys.exit("Live mode needs KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH.")
    placed = 0
    for c in _scan():
        if led.has_event(c.event_ticker, mode):
            continue
        if c.cost_c > led.remaining_c(mode):
            continue
        if a.live:
            if client.balance_cents() < c.cost_c:
                print("Insufficient balance; stopping.")
                break
            resp = client.buy_limit(c.ticker, c.side, c.count, c.price_c)
            print("order:", json.dumps(resp.get("order", resp))[:200])
        led.record(c, mode)
        placed += 1
        print(f"[{mode}] BUY {c.side} {c.ticker} x{c.count} @{c.price_c}c (edge {c.edge_c:+.2f}c)")
    print(f"{placed} orders placed; ${led.remaining_c(mode)/100:.2f} budget left today.")


def cmd_settle(a):
    from .client import KalshiClient
    from .ledger import Ledger
    led, client = Ledger(), KalshiClient()
    for t in led.open_tickers():
        m = client._req("GET", f"/markets/{t}")["market"]
        if m.get("result") in ("yes", "no"):
            print(f"{t}: {m['result']} ({led.settle(t, m['result'])} orders)")


def cmd_stats(a):
    from .ledger import Ledger
    for mode in ("paper", "live"):
        print(mode, Ledger().stats(mode))


def cmd_simulate(a):
    from .simulate import simulate_week
    r = simulate_week(a.price, a.win_prob, a.bets)
    print(json.dumps(r, indent=2))


def cmd_combo(a):
    from . import combo
    legs, mult = a.legs, a.payout / a.cost
    lp = combo.per_leg_price(a.cost, a.payout, legs)
    print(f"Implied combo prob {combo.implied_prob(a.cost, a.payout):.3%} (1 in {mult:.0f}); "
          f"avg leg price {lp:.1%}")
    print("EV per $1 if every leg's true prob differs from its price by:")
    for e in (-0.05, -0.03, -0.01, 0.0, 0.01, 0.03):
        print(f"  {e*100:+.0f} pts/leg -> ${combo.uniform_edge_ev(legs, lp, e):.2f}")
    true_p = (lp + a.edge) ** legs
    print(f"\n${a.stake}/combo x {a.per_week}/week at {a.edge*100:+.0f} pts/leg edge:")
    r = combo.weekly_plan(a.stake, mult, true_p, a.per_week)
    for k, v in r.items():
        print(f"  {k:30} {v:,.3f}" if isinstance(v, float) else f"  {k:30} {v}")


def cmd_combo_scan(a):
    from .client import KalshiClient
    from .combo_builder import plan_combos
    table = calibration.load()
    if not table:
        sys.exit("No calibration table. Run `python -m kalshi_bot calibrate` first.")
    plans = plan_combos(KalshiClient(), table)
    if not plans:
        print("No combo has enough underpriced legs right now.")
    for p in plans[:a.top]:
        print(json.dumps(p.summary(), indent=2))


def cmd_combo_trade(a):
    from .client import KalshiClient
    from .combo_builder import execute, plan_combos
    from .ledger import Ledger
    mode = "live" if a.live else ("quote-only" if a.quote_only else "paper")
    if mode != "paper" and not CONFIG.key_id:
        sys.exit(f"{mode} mode needs KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH.")
    table = calibration.load()
    if not table:
        sys.exit("No calibration table. Run `python -m kalshi_bot calibrate` first.")
    client, led = KalshiClient(), Ledger()
    for plan in plan_combos(client, table)[:a.max_attempts]:
        if execute(plan, client, led, mode):
            break


def cmd_combo_settle(a):
    from .client import KalshiClient
    from .combo_builder import settle_paper
    from .ledger import Ledger
    client, led = KalshiClient(), Ledger()
    for cid, mode, ticker, legs in led.open_combos():
        if ticker:
            res = client.market(ticker).get("result")
            res = res if res in ("yes", "no") else None
        else:
            res = settle_paper(legs, client.market)
        if res:
            led.settle_combo(cid, res)
            print(f"combo {cid} ({mode}): {res}")
    for mode in ("paper", "live"):
        print(mode, led.combo_stats(mode))


def cmd_backtest(a):
    from . import backtest
    if a.download:
        backtest.download(a.data_dir)
    backtest.run(a.data_dir)


def cmd_pnl(a):
    from . import pnl
    pnl.ev_filter_report() if a.ev_only else pnl.run(stake=a.stake)


def main():
    p = argparse.ArgumentParser(prog="kalshi_bot")
    sp = p.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("calibrate"); c.add_argument("--max-events", type=int, default=3000); c.set_defaults(f=cmd_calibrate)
    sp.add_parser("scan").set_defaults(f=cmd_scan)
    t = sp.add_parser("trade"); t.add_argument("--live", action="store_true"); t.set_defaults(f=cmd_trade)
    sp.add_parser("settle").set_defaults(f=cmd_settle)
    sp.add_parser("stats").set_defaults(f=cmd_stats)
    s = sp.add_parser("simulate", help="odds of hitting 10x in a week")
    s.add_argument("--price", type=int, default=5); s.add_argument("--win-prob", type=float, default=0.045)
    s.add_argument("--bets", type=int, default=35); s.set_defaults(f=cmd_simulate)
    cb = sp.add_parser("combo", help="evaluate a parlay/combo and a weekly combo plan")
    cb.add_argument("--cost", type=float, default=9.86); cb.add_argument("--payout", type=float, default=1562.50)
    cb.add_argument("--legs", type=int, default=16); cb.add_argument("--edge", type=float, default=-0.02,
                    help="assumed true-minus-price per leg, e.g. -0.02")
    cb.add_argument("--stake", type=float, default=5.0); cb.add_argument("--per-week", type=int, default=7)
    cb.set_defaults(f=cmd_combo)
    cs = sp.add_parser("combo-scan"); cs.add_argument("--top", type=int, default=3); cs.set_defaults(f=cmd_combo_scan)
    ct = sp.add_parser("combo-trade")
    ct.add_argument("--live", action="store_true")
    ct.add_argument("--quote-only", action="store_true", help="request real quotes, never accept")
    ct.add_argument("--max-attempts", type=int, default=3)
    ct.set_defaults(f=cmd_combo_trade)
    sp.add_parser("combo-settle").set_defaults(f=cmd_combo_settle)
    bt = sp.add_parser("backtest", help="backtest on the public Hugging Face Kalshi dataset")
    bt.add_argument("--download", action="store_true"); bt.add_argument("--data-dir", default="data/hf")
    bt.set_defaults(f=cmd_backtest)
    pl = sp.add_parser("pnl", help="P&L replay of the $5/day plan on real trades")
    pl.add_argument("--stake", type=float, default=5.0); pl.add_argument("--ev-only", action="store_true"); pl.set_defaults(f=cmd_pnl)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
