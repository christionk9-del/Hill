"""Combo (parlay) math: price, true odds, EV, and weekly hit chances.

A combo's payout multiplier is ~ 1 / product(leg prices). Any per-leg mispricing
COMPOUNDS: being 3 points worse per leg across 16 legs roughly halves EV;
being 3 points better roughly doubles it. Combos amplify edge in both directions.

Probabilities are multiplied assuming independent legs. Same-game legs are
correlated (a team blowout moves margin, points and minutes together), and
the combo price from Kalshi's market makers already reflects that.
"""
import math
import random


def implied_prob(cost, max_payout):
    return cost / max_payout


def per_leg_price(cost, max_payout, legs):
    return implied_prob(cost, max_payout) ** (1 / legs)


def combo_true_prob(leg_probs):
    return math.prod(leg_probs)


def combo_ev_multiple(cost, max_payout, leg_true_probs):
    """Expected dollars back per $1 staked."""
    return combo_true_prob(leg_true_probs) * max_payout / cost


def uniform_edge_ev(legs, leg_price, edge_pts):
    """EV multiple if every leg's true prob = price + edge (edge in prob points, e.g. -0.03)."""
    return ((leg_price + edge_pts) / leg_price) ** legs


def weekly_plan(stake, multiplier, true_prob, bets_per_week=7, target=350.0,
                weeks=52, trials=20000, seed=0):
    """$stake per combo, `bets_per_week` combos a week. Returns hit odds and yearly P&L."""
    rng = random.Random(seed)
    p_week_target = 0
    year_profit = []
    for _ in range(trials):
        total = 0.0
        for w in range(weeks):
            wins = sum(rng.random() < true_prob for _ in range(bets_per_week))
            payout = wins * stake * multiplier
            if w == 0 and payout >= target:
                p_week_target += 1
            total += payout - stake * bets_per_week
        year_profit.append(total)
    year_profit.sort()
    return {
        "p_hit_target_in_a_week": p_week_target / trials,
        "p_at_least_one_hit_in_year": 1 - (1 - true_prob) ** (bets_per_week * weeks),
        "spent_per_year_$": stake * bets_per_week * weeks,
        "avg_year_profit_$": sum(year_profit) / trials,
        "median_year_profit_$": year_profit[trials // 2],
        "p_year_profitable": sum(x > 0 for x in year_profit) / trials,
    }
