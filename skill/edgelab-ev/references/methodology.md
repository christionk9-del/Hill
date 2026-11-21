# Methodology reference

Focus: event / prediction markets. The sportsbook example at the end still applies to generic devigging.

## Conversions
| from | to prob |
|---|---|
| American −X | X/(X+100) |
| American +X | 100/(X+100) |
| Decimal d | 1/d |
| Contract $p (+fee f) | p + f |

Fair American from prob p: p ≥ 0.5 → −100·p/(1−p); else +100·(1−p)/p.

## Event-contract worked example
Polymarket (deep): YES ask 46¢, NO ask 56¢ → r = 0.46, 0.56, overround 2%. Power devig → fair YES ≈ 0.450.
Kalshi YES ask 41¢ + taker fee 0.07·0.41·0.59 ≈ 1.7¢ → all-in cost 0.427.
Consensus with Kalshi included as a signal: Kalshi's own devig (YES 41¢, NO 61¢, fees included) ≈ 0.397. Weighting Polymarket 0.8 and Kalshi 0.7 in log-odds gives fair ≈ 0.425.
→ EV ≈ 0.425/0.427 − 1 ≈ **−0.4%**, so no trade. Exclude Kalshi and you'd get 0.450/0.427 − 1 = **+5.3%**: that's the winner's-curse inflation.
CLI: `edgelab ev --fair 0.46 0.56 --bet 0.41 --fee-rate 0.07` shows the excluded-venue (+5.3%) view.
Resolution in 30 days → APR = EV × 365/30.

## Devig methods (r = implied probs, Σr = 1 + hold)
- **Multiplicative:** pᵢ = rᵢ / Σr
- **Additive:** pᵢ = rᵢ − hold/n
- **Power:** pᵢ = rᵢᵏ with k solving Σ rᵢᵏ = 1. Shades longshots more; the usual default.
- **Shin:** pᵢ = (√(z² + 4(1−z)rᵢ²/Σr) − z) / (2(1−z)), z solved so Σp = 1. Insider-trading model.
- **Odds ratio:** pᵢ = rᵢ / (c + rᵢ − c·rᵢ), c solved so Σp = 1.
- **Worst case:** per outcome, the minimum across methods.

### Sportsbook example (generic devig)
Pinnacle −120 / +110 → r = 0.5455, 0.4762, hold 2.16%.
Multiplicative → 0.5339 / 0.4661. Power → 0.5350 / 0.4650 (fair underdog price +115).
Soft book +105 on the underdog (d = 2.05): EV = 0.4650 × 2.05 − 1 = **−4.7%**, so no bet.
Soft book +125 (d = 2.25): EV = 0.4650 × 2.25 − 1 = **+4.6%**. Full Kelly = 0.046/1.25 = 3.7%, ¼ Kelly ≈ 0.93% of bankroll.

## Consensus
fair = σ( Σ wᵥ·logit(pᵥ) / Σ wᵥ ), then renormalise across outcomes.
Default weights: Pinnacle 1.0, Betfair 0.8, Polymarket 0.8, Kalshi 0.7, Matchbook 0.6, Circa 0.6, Lowvig 0.4, Manifold 0.3, everyone else 0.

## Calibration (walk-forward)
Fit logit(q′) = b·logit(q) on settled outcomes known *before* the bet. b > 1 means the market is too timid (a longshot bias). Require n ≥ 100 and report the SE of b.

## Statistics
- CLV per bet = q_close·d − 1. Mean, SE = sd/√n, t = mean/SE, one-sided p = ½·erfc(t/√2).
- ROI CI: bootstrap 1,000 resamples of settled bets.
- Bets needed for ROI significance: n ≈ 1.96²·Var(return)/edge². Var ≈ p(d−1)² + (1−p) − edge².
- Luck z = (P&L − Σ stake·EV) / √Σ stake²·d²·q(1−q).
- Brier = mean (q − y)². Log loss = mean −ln q(outcome).
