"""Odds conversion, de-vigging and EV math."""
from __future__ import annotations

from typing import Iterable


def american_to_decimal(american: float) -> float:
    if american >= 100:
        return 1 + american / 100
    if american <= -100:
        return 1 + 100 / abs(american)
    raise ValueError(f"invalid american odds: {american}")


def decimal_to_american(dec: float) -> int:
    if dec <= 1:
        raise ValueError(f"invalid decimal odds: {dec}")
    if dec >= 2:
        return round((dec - 1) * 100)
    return round(-100 / (dec - 1))


def implied_prob(dec: float) -> float:
    return 1 / dec


def devig_multiplicative(decimals: Iterable[float]) -> list[float]:
    """Remove the vig proportionally. Returns fair probabilities summing to 1."""
    raw = [1 / d for d in decimals]
    total = sum(raw)
    return [p / total for p in raw]


def devig_power(decimals: Iterable[float], tol: float = 1e-10) -> list[float]:
    """Power de-vig: find k so sum(p_i^k) == 1. Handles favourite/longshot bias
    better than multiplicative; this is the default."""
    raw = [1 / d for d in decimals]
    if abs(sum(raw) - 1) < 1e-9:
        return raw
    lo, hi = 0.5, 3.0
    for _ in range(200):
        k = (lo + hi) / 2
        s = sum(p ** k for p in raw)
        if abs(s - 1) < tol:
            break
        if s > 1:
            lo = k
        else:
            hi = k
    fair = [p ** k for p in raw]
    t = sum(fair)
    return [p / t for p in fair]


def ev(fair_prob: float, dec: float) -> float:
    """Expected profit per 1 unit staked."""
    return fair_prob * dec - 1


def boosted_decimal(dec: float, boost_pct: float) -> float:
    """Profit boost: winnings (dec-1) multiplied by (1 + boost)."""
    return 1 + (dec - 1) * (1 + boost_pct / 100)


def kalshi_fee_per_contract(price: float, rate: float = 0.07) -> float:
    """Kalshi taker fee per $1 contract: rate * P * (1-P), rounded up to the cent."""
    import math
    fee = rate * price * (1 - price)
    return math.ceil(fee * 100 - 1e-9) / 100


def kalshi_effective_decimal(dec: float, rate: float = 0.07) -> float:
    """Decimal odds after Kalshi's trading fee. Contract costs P, pays $1."""
    price = 1 / dec
    cost = price + kalshi_fee_per_contract(price, rate)
    return 1 / cost if cost < 1 else 1.0


def kelly_fraction(fair_prob: float, dec: float) -> float:
    b = dec - 1
    if b <= 0:
        return 0.0
    return max(0.0, (fair_prob * b - (1 - fair_prob)) / b)
