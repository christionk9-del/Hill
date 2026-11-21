---
name: open-ev-finder
description: Open EV Finder. Scan every open Kalshi market and list the top positive-EV contracts in each Kalshi category (Politics, Elections, Economics, Financials, Crypto, Climate and Weather, Entertainment, Sports and more), plus any true arbitrage, including cross-venue arbs and "sure bets" between Kalshi (which also covers Robinhood event contracts), Polymarket and US sportsbooks. Sports is limited to games happening this weekend. Uses public Kalshi data only (no API key) and real Kalshi taker fees rounded up per order, capped by order-book depth. Use when the user asks for "the best +EV bets on Kalshi", "open EV", "top 5 per category", "what's mispriced on Kalshi today/this weekend", "arbs", "sure bets", "discrepancies between Kalshi / Robinhood / Polymarket / sportsbooks", or wants to refresh either scan.
---

# Open EV Finder

Finds the highest positive-EV contracts open on Kalshi right now, **top 5 per category**, with sports restricted to **this weekend's games**.

## Run it

```bash
python3 .claude/skills/open-ev-finder/scripts/open_ev_finder.py --out open_ev_report.md --json open_ev.json
```
Useful flags: `--top 5` (rows per category), `--order 100` (order size in $ used for fee rounding and depth), `--min-ev 0.01` (only list ≥ 1%), `--min-depth 5` (minimum $ at the best price), `--tz America/New_York` (which time zone defines "this weekend"), `--cache events.json` (reuse a fetch). A full scan reads ~23,000 events (~115 API pages) and takes about 1–2 minutes. The script backs off automatically on Kalshi rate limits.

Then show the user the report, category by category, and call out the caveats below. Keep the ᵃ marker on rows under 1% EV (noise).

## How fair prices are computed (no second venue needed)

1. **Exactly-one-winner events** (game winner, "who will…", range buckets such as "CPI exactly 3.2%"): the outcomes must sum to 100%. The excess of the midpoints over 100% is removed **in proportion to each outcome's spread²**, so liquid, tight prices barely move and wide, illiquid ones absorb the overround. Events whose midpoints sum to **less than 100%** are skipped for single bets, because unlisted outcomes may hold the missing probability.
2. **Strike ladders** ("above $X", grouped per underlying, e.g. one player's 1+/2+/3+ touchdowns): P(above X) must not rise with X. A spread-weighted isotonic fit gives each rung's fair price.
3. **EV** = fair ÷ (ask + Kalshi taker fee) − 1. The fee is 0.07·C·P·(1−P), **rounded up to the cent per order**, which is brutal on cheap contracts and small orders. NO is bought at 1 − YES bid. APR = EV × 365 ÷ days to resolution.
4. **Arbitrage**: buying every NO in an exclusive set for less than N−1 (always valid), or every YES for less than $1 (only when the listed outcomes look complete). Each leg needs ≥ $5 of depth.

## Caveats to always state
- These are **relative-value** signals: a price out of line with its own siblings on Kalshi. They aren't forecasts. They're strongest on liquid sets (mid sum near 1.0) and weakest on 50–150-outcome sets with mid sum > 1.3.
- Kalshi-only fair prices can't detect a market that's wrong *as a whole*. For that, compare venues (Polymarket, sportsbooks via The Odds API) with the EdgeLab model (`python3 -m edgelab ...`, `forwards/`).
- Prices and depth change by the minute. Re-run before acting. Respect long lock-ups: a 2% edge resolving in 2029 is ~1%/yr.
- Research tool, not betting advice. Check the market's rules (resolution source, edge cases) before trading. Kalshi access depends on jurisdiction. Problem gambling help (US): 1-800-GAMBLER.

## Arb Scanner (cross-venue "sure bets")

```bash
python3 .claude/skills/open-ev-finder/scripts/arb_scanner.py --out arb_report.md --json arbs.json [--cache kalshi_events.json]
```
Checks three places for guaranteed profit after fees:
1. **Kalshi vs US sportsbooks**: full-game moneylines from The Odds API (`regions=us,us2`). Every way to cover all outcomes once (Kalshi YES, Kalshi NO = "anyone but", book moneyline) is priced, using at least two venues. It never mixes a Kalshi 3-way (with a tie) with a 2-way book line, and it flags tie or overtime rule risks. Needs an Odds API key: `ODDS_API_KEY`, or a proxy that injects the `x-api-key` header.
2. **Kalshi vs Polymarket**: questions auto-matched by wording and deadline. Opposite wording ("at least" vs. "less than") flips the legs. Pairs are **rejected** when one side has a question-changing word (finalist, VP, Popcornmeter, coding, next, primary, …) or the deadlines differ by more than 3 days. Arbs need at least $5 available on both legs (Polymarket depth comes from its order book). Polymarket taker fees are included where enabled.
3. **Inside Kalshi**: the complete-set check from `open_ev_finder.py`.

**Robinhood**: Robinhood event contracts are Kalshi contracts on the same order book, so there is no Robinhood-vs-Kalshi arb. The Kalshi legs cover it, and Robinhood adds its own per-contract fee.

When presenting results, lead with whether any **tradable** arb exists. For each candidate, tell the user to open both markets and confirm the resolution rules match, and show the profit **per year** (APR). Most pairs resolving in 2028 earn ~0%/yr. Auto-matching is a lead generator, not proof the questions are identical.
