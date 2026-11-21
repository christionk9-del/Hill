"""De-vigging: turning a venue's prices on a complete set of mutually exclusive
outcomes into fair probabilities that sum to 1.

Each method encodes a different hypothesis about HOW the bookmaker/market
spreads its margin across outcomes. Which one is right is an empirical
question; `edgelab tournament` scores them on settled outcomes.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Callable


def _bisect(f: Callable[[float], float], lo: float, hi: float, iters: int = 55) -> float:
    flo = f(lo)
    for _ in range(iters):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def multiplicative(r: list[float]) -> list[float]:
    """Margin proportional to probability (a.k.a. basic normalisation)."""
    s = sum(r)
    return [x / s for x in r]


def additive(r: list[float]) -> list[float]:
    """Equal margin subtracted from every outcome."""
    n = len(r)
    m = (sum(r) - 1) / n
    p = [max(x - m, 1e-6) for x in r]
    s = sum(p)
    return [x / s for x in p]


def power(r: list[float]) -> list[float]:
    """p_i = r_i^k with k chosen so sum p = 1. Puts more margin on longshots."""
    if abs(sum(r) - 1) < 1e-12:
        return list(r)
    k = _bisect(lambda k: sum(x ** k for x in r) - 1, 0.01, 50)
    p = [x ** k for x in r]
    s = sum(p)
    return [x / s for x in p]


def shin(r: list[float]) -> list[float]:
    """Shin (1993) model: margin comes from a fraction z of insider money."""
    s = sum(r)
    if s <= 1 + 1e-12:
        return multiplicative(r)

    def probs(z: float) -> list[float]:
        return [(math.sqrt(z * z + 4 * (1 - z) * x * x / s) - z) / (2 * (1 - z)) for x in r]

    z = _bisect(lambda z: sum(probs(z)) - 1, 0.0, 0.999)
    p = probs(z)
    t = sum(p)
    return [x / t for x in p]


def odds_ratio(r: list[float]) -> list[float]:
    """Cheung (2015): constant odds ratio between fair and implied odds."""
    if abs(sum(r) - 1) < 1e-12:
        return list(r)

    def probs(c: float) -> list[float]:
        return [x / (c + x - c * x) for x in r]

    c = _bisect(lambda c: sum(probs(c)) - 1, 0.01, 100)
    p = probs(c)
    t = sum(p)
    return [x / t for x in p]


METHODS: dict[str, Callable[[list[float]], list[float]]] = {
    "multiplicative": multiplicative,
    "additive": additive,
    "power": power,
    "shin": shin,
    "odds_ratio": odds_ratio,
}


def devig(prices, method: str = "power") -> list[float]:
    """prices: decimal odds for a COMPLETE set of mutually exclusive outcomes."""
    return list(_devig(tuple(prices), method))


@lru_cache(maxsize=200_000)
def _devig(prices: tuple, method: str) -> tuple:
    r = [1 / d for d in prices]
    if method == "worst_case":
        # Conservative: per outcome, the lowest fair prob any method gives.
        allp = [f(r) for f in METHODS.values()]
        return tuple(min(ps[i] for ps in allp) for i in range(len(r)))
    return tuple(METHODS[method](r))
