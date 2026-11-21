"""Odds conversions. Internally every price is DECIMAL odds (total return per 1 staked)."""
from __future__ import annotations

import math


def american_to_decimal(a: float) -> float:
    a = float(a)
    if a == 0:
        raise ValueError("American odds cannot be 0")
    return 1 + (a / 100 if a > 0 else 100 / -a)


def decimal_to_american(d: float) -> float:
    if d <= 1:
        raise ValueError("decimal odds must be > 1")
    return (d - 1) * 100 if d >= 2 else -100 / (d - 1)


def prob_to_decimal(p: float) -> float:
    return 1.0 / p


def decimal_to_prob(d: float) -> float:
    return 1.0 / d


def overround(prices: list[float]) -> float:
    """Sum of implied probabilities minus 1 (the 'hold' / 'vig')."""
    return sum(1 / d for d in prices) - 1


def logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def ev(prob: float, decimal_price: float) -> float:
    """Expected profit per 1 unit staked."""
    return prob * decimal_price - 1


def kelly(prob: float, decimal_price: float) -> float:
    """Full-Kelly fraction of bankroll (0 if no edge)."""
    b = decimal_price - 1
    if b <= 0:
        return 0.0
    return max(0.0, (prob * b - (1 - prob)) / b)
