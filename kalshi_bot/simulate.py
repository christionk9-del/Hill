"""Monte Carlo of a week: $35 split across N bets at a given price/true win prob."""
import random

from .fees import max_contracts_for_budget, total_cost_cents


def simulate_week(price_c, win_prob, bets, budget_c=3500, target_mult=10, trials=100_000, seed=0):
    rng = random.Random(seed)
    per_bet = budget_c // bets
    n = max_contracts_for_budget(per_bet, price_c)
    cost = total_cost_cents(n, price_c)
    leftover = budget_c - cost * bets
    hits, total = 0, 0
    for _ in range(trials):
        wins = sum(rng.random() < win_prob for _ in range(bets))
        end = leftover + wins * n * 100
        total += end
        hits += end >= budget_c * target_mult
    return {
        "contracts_per_bet": n,
        "p_reach_target": hits / trials,
        "avg_end_balance_$": total / trials / 100,
        "markov_upper_bound": (total / trials) / (budget_c * target_mult),
    }
