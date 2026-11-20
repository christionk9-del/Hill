"""Kalshi trading fee model.

Taker fee = ceil(0.07 * C * P * (1 - P)) rounded UP to the next cent, P in dollars.
Check https://kalshi.com/docs/kalshi-fee-schedule.pdf -- some series use different
multipliers, and resting (maker) orders may be cheaper or free.
"""
import math

TAKER_RATE = 0.07


def taker_fee_cents(count: int, price_c: int, rate: float = TAKER_RATE) -> int:
    if count <= 0:
        return 0
    p = price_c / 100.0
    # tiny epsilon avoids float noise pushing exact cents up
    return math.ceil(rate * count * p * (1 - p) * 100 - 1e-9)


def total_cost_cents(count: int, price_c: int) -> int:
    return count * price_c + taker_fee_cents(count, price_c)


def max_contracts_for_budget(budget_c: int, price_c: int) -> int:
    if price_c <= 0:
        return 0
    n = budget_c // price_c
    while n > 0 and total_cost_cents(n, price_c) > budget_c:
        n -= 1
    return n


def breakeven_prob(count: int, price_c: int) -> float:
    """Win probability needed to break even on this order (fees included)."""
    if count <= 0:
        return 1.0
    return total_cost_cents(count, price_c) / (100.0 * count)
