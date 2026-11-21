"""Semi-automatic arbitrage: YOU place the sportsbook leg, the bot hedges on Kalshi.

Why this order: the sportsbook is the leg that can be refused, limited or re-priced, so it goes first.
Kalshi is the liquid leg: the bot reads the live order book, computes the WORST price that still locks
your minimum profit, and sends an immediate-or-cancel limit order at that price. It cannot fill worse.

  locked profit = min(book payout, Kalshi contracts) - book stake - Kalshi cost (incl. fees)
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .odds import american_to_decimal


def to_decimal(odds: str | float) -> float:
    s = str(odds).strip()
    if s.startswith(("+", "-")):
        return american_to_decimal(float(s))
    v = float(s)
    if v >= 100 or v <= -100:
        return american_to_decimal(v)
    if v <= 1:
        raise ValueError("odds must be American (+160) or decimal (2.60)")
    return v


def fee(n: float, q: float, rate: float = 0.07) -> float:
    """Kalshi taker fee for n contracts at cost q (direct members are rounded to $0.0001)."""
    return math.ceil(rate * n * q * (1 - q) * 10000 - 1e-9) / 10000


def asks_for(orderbook: dict, outcome: str) -> list[tuple[float, float]]:
    """Cost levels to BUY `outcome`, cheapest first. Buying NO hits resting YES bids at 1 - price."""
    other = "yes" if outcome == "no" else "no"
    return sorted(((round(1 - p, 4), c) for p, c in orderbook[other]), key=lambda x: x[0])


@dataclass
class Plan:
    outcome: str
    contracts: float
    max_cost: float | None          # limit: worst acceptable price per contract
    fill: list = field(default_factory=list)  # [(cost, qty)] expected from the current book
    kalshi_cost: float = 0.0        # incl. fees
    book_stake: float = 0.0
    book_payout: float = 0.0
    locked_profit: float = 0.0
    locked_pct: float = 0.0
    fillable: float = 0.0
    note: str = ""

    def lines(self) -> list[str]:
        L = [f"Book leg   : stake ${self.book_stake:,.2f} -> payout ${self.book_payout:,.2f}",
             f"Kalshi leg : buy {self.contracts:g} {self.outcome.upper()} contracts, limit {self.max_cost*100:.1f}¢ each" if self.max_cost
             else "Kalshi leg : no price on the book locks your minimum profit"]
        if self.fill:
            lv = ", ".join(f"{q:g} @ {c*100:.0f}¢" for c, q in self.fill)
            L.append(f"Expected   : {lv}  = ${self.kalshi_cost:,.2f} incl. fees")
            L.append(f"Locked     : {'+' if self.locked_profit >= 0 else ''}${self.locked_profit:,.2f} "
                     f"({self.locked_pct:+.2%} of ${self.book_stake + self.kalshi_cost:,.2f} outlay) whichever side wins")
        if self.note:
            L.append(f"Note       : {self.note}")
        return L


def max_cost_for(book_stake: float, payout: float, n: float, min_profit_pct: float, fee_rate: float) -> float | None:
    """Worst per-contract Kalshi price (whole cents) that still locks >= min_profit_pct if all n fill there."""
    def ok(q):
        k = n * q + fee(n, q, fee_rate)
        return min(payout, n) - book_stake - k >= min_profit_pct * (book_stake + k)
    best = None
    for cents in range(1, 100):
        if ok(cents / 100):
            best = cents / 100
        else:
            break
    return best


def plan_hedge(book_odds, book_stake: float, outcome: str, orderbook: dict, min_profit_pct: float = 0.005,
               fee_rate: float = 0.07, whole_contracts: bool = True) -> Plan:
    d = to_decimal(book_odds)
    payout = book_stake * d
    n = float(round(payout)) if whole_contracts else round(payout, 2)
    p = Plan(outcome, n, max_cost_for(book_stake, payout, n, min_profit_pct, fee_rate),
             book_stake=book_stake, book_payout=payout)
    if p.max_cost is None:
        p.note = "no Kalshi price can lock your minimum profit at these book odds"
        return p
    need, fill = n, []
    for c, q in asks_for(orderbook, outcome):
        if c > p.max_cost + 1e-9 or need <= 1e-9:
            break
        take = round(min(q, need), 2)
        fill.append((c, take))
        need -= take
    p.fill, p.fillable = fill, round(sum(q for _, q in fill), 2)
    p.kalshi_cost = sum(q * c + fee(q, c, fee_rate) for c, q in fill)
    if p.fillable >= n - 1e-9:
        p.locked_profit = min(payout, n) - book_stake - p.kalshi_cost
        p.locked_pct = p.locked_profit / (book_stake + p.kalshi_cost)
    else:
        if_book = payout - book_stake - p.kalshi_cost
        if_kalshi = p.fillable - book_stake - p.kalshi_cost
        p.locked_profit = min(if_book, if_kalshi)
        p.locked_pct = p.locked_profit / (book_stake + p.kalshi_cost) if (book_stake + p.kalshi_cost) else 0
        p.note = (f"only {p.fillable:g}/{n:g} contracts available at <= {p.max_cost*100:.0f}¢ right now; "
                  f"if fully filled you'd lock in profit, as-is worst case is ${p.locked_profit:,.2f}"
                  if fill else f"no Kalshi offers at <= {p.max_cost*100:.0f}¢ right now: the arb is gone (or wait)")
    return p


# ------------------------------------------------------------------ ledger
SCHEMA = """CREATE TABLE IF NOT EXISTS arbs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, env TEXT, description TEXT,
  book TEXT, book_odds TEXT, book_stake REAL, book_payout REAL, ticker TEXT, outcome TEXT, target REAL, filled REAL,
  kalshi_cost REAL, kalshi_fees REAL, locked_profit REAL, status TEXT, order_ids TEXT, notes TEXT)"""


def ledger(path: str) -> sqlite3.Connection:
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute(SCHEMA)
    return db


def spent_today(db, env) -> float:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = db.execute("SELECT COALESCE(SUM(kalshi_cost),0) s FROM arbs WHERE env=? AND ts LIKE ?", (env, day + "%")).fetchone()
    return r["s"]


def execute_hedge(client, plan: Plan, ticker: str, cfg: dict, db, description: str = "", book: str = "",
                  book_odds: str = "", retries: int = 3, log=print) -> dict:
    """Send IOC orders (never above plan.max_cost) until hedged, the price is gone, or retries run out."""
    if not plan.max_cost:
        raise RuntimeError("nothing to execute: " + (plan.note or "no profitable price"))
    est = plan.kalshi_cost
    if est > cfg["max_order_usd"]:
        raise RuntimeError(f"order ~${est:,.2f} exceeds max_order_usd ${cfg['max_order_usd']:,.2f}")
    if spent_today(db, client.env) + est > cfg["max_daily_usd"]:
        raise RuntimeError(f"would exceed max_daily_usd ${cfg['max_daily_usd']:,.2f}")
    bal = client.balance()
    if bal < est:
        raise RuntimeError(f"Kalshi balance ${bal:,.2f} < required ~${est:,.2f}")
    target, filled, cost, fees, ids, unknown = plan.contracts, 0.0, 0.0, 0.0, [], False
    for attempt in range(retries):
        need = round(target - filled, 2)
        if need <= 0:
            break
        r = client.buy_ioc(ticker, plan.outcome, need, plan.max_cost)
        ids.append(r.get("order_id") or r["client_order_id"])
        if r.get("unknown_outcome"):
            unknown = True
            log(f"  ! order outcome UNKNOWN ({r.get('error')}). Not resubmitting. Check Kalshi positions now.")
            break
        if r["filled"]:
            filled += r["filled"]
            cost += r["filled"] * (r["avg_cost"] or plan.max_cost)
            fees += r["filled"] * (r["avg_fee"] or 0)
            log(f"  filled {r['filled']:g} @ {(r['avg_cost'] or 0)*100:.1f}¢ (fee {(r['avg_fee'] or 0)*100:.2f}¢)")
        if filled >= target - 1e-9:
            break
        time.sleep(1.0)  # let the book refill, then try again at the SAME limit
    total = cost + fees
    locked = min(plan.book_payout, filled) - plan.book_stake - total if filled >= target - 1e-9 else None
    status = "unknown" if unknown else ("hedged" if locked is not None else ("partial" if filled else "unhedged"))
    db.execute("""INSERT INTO arbs(ts, env, description, book, book_odds, book_stake, book_payout, ticker, outcome, target,
                  filled, kalshi_cost, kalshi_fees, locked_profit, status, order_ids, notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (datetime.now(timezone.utc).isoformat(timespec="seconds"), client.env, description, book, str(book_odds),
                plan.book_stake, plan.book_payout, ticker, plan.outcome, target, filled, total, fees, locked, status,
                ",".join(map(str, ids)), ""))
    db.commit()
    return {"status": status, "filled": filled, "target": target, "kalshi_cost": total, "locked_profit": locked,
            "unhedged_contracts": max(target - filled, 0.0)}
