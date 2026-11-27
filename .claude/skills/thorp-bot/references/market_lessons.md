# Lessons from the Hill project's backtests (Kalshi, PrizePicks, options)

These are empirical results from this repository (`christionk9-del/Hill`, Nov 2026). Use them
as base rates before believing a claimed edge. Details and code are in the repo README and
`docs/updown_preregistered.txt`.

## Base rate: buying at the ask loses
- Across 103.6M Kalshi BTC 15-minute trades (Mar–Sep 2026), takers got **$0.969 per $1**
  and makers (resting orders) **$1.005**. The money goes to whoever posts resting orders.
- Pre-game sports single legs on Kalshi: **$0.84–0.97 per $1** at every price.
- Index "price at time" markets (S&P, Nasdaq hourly): takers **$0.96–0.98**.
- So treat "+EV" against an exchange price as unproven until shown out of sample.

## Favorite-longshot bias
- Cheap contracts are the most overpriced. In BTC up/down, returns rose steadily with price:
  10–20¢ **$0.80**, 20–35¢ $0.82, 35–50¢ $0.85, 50–65¢ $0.89, 65–80¢ $0.93, 80–92¢ $0.96.
- Real Kalshi sports combo buyers: 2 legs **$0.85–0.95**, 12+ legs **$0.30–0.63**. Long
  parlays are the worst bet type in the data.

## Rules that looked good and failed out of sample
- BTC 15-min "DOWN at 20–35¢ in minutes 2–3, sell at +20%": **$0.79–0.82** in every period.
  Take-profits raise the win rate (70%) but not the return.
- First-minute fade: $1.25 on its test half, then **$0.89** on six months of new data.
- Overnight favorites (85–99¢, 05–08 UTC): +4–9% during the search, **$1.005** (0.98–1.03) on the
  locked holdout.
- Speed rule (fair value vs. Kalshi, 1 s delay): **$1.015** (0.87–1.16) on 8 tick days.
- About 2,400 rule variants: 75 passed three periods. That's roughly what chance alone produces.
- Lesson: an edge found by searching needs a locked holdout decided in advance. If you test
  N rules, expect about N × 5% to look significant by luck.

## Correlation in combos is already priced
- Crypto 15-min coins move together (all 7 down together 23.5% of windows vs 0.8% if
  independent; implied correlation 0.74, BTC–ETH 0.87).
- Kalshi prices that correlation: same-side crypto stacks were worth **$0.86–1.01 per $1**
  against a correlation-fair model; 3–5 coin stacks were slightly overpriced.
- Treat a single "cheap" combo quote as a lead, not proof.

## Options-style signals don't carry over to binaries
- NDX 0DTE dip-put signal: NDX closed below entry only 44–47% of the time after a dip; the
  options edge (if any) comes from large down days. A flat $1 binary loses it. On Kalshi the
  mirror returned **$0.46–0.89**.
- A strategy that wins through payoff size (trend following, long options) belongs in a venue
  whose payoff grows with the move.

## Where real edge showed up
- **Promotional lines.** PrizePicks "Taco Tuesday" lowered Tatum's points line from 26.5 to
  19.5. The fair line ~26.5 implies about 80% for 20+ points, so the promo leg alone carried
  the +20% EV. Promos and boosts are the most reliable edges available to small bettors,
  usually with low stake caps ($25).
- **Sharp-line comparison.** Prices that lag a sharp book (Pinnacle, devigged) can be +EV,
  but the edges are 1–4 points and short-lived.
- **Market making**, which requires order-book data, queue position and inventory risk.

## Common errors to catch
- Confusing EV on stake with return on bankroll: +20% EV at 5% stakes is about +1% of
  bankroll per bet, compounding at about +0.7%.
- Calling 3–5% stakes "full Kelly". On a 3x payout, full Kelly is 10% at 20% EV and 15%
  at 30% EV.
- Treating a single win as proof. A 40% bet that hits proves nothing.
- Look-ahead bias: averaging prices over a market's life (including in-game) made favorites
  look 20% profitable. Use only prices available at decision time.
- Data artifacts: the public Kalshi dataset floors sub-cent prices (a "1¢" combo really
  cost ~1.6¢), which made cheap combos look profitable.
- Survivorship: screenshots of big wins (13x months, 16-leg hits) are selected. Ask for the
  full record.
