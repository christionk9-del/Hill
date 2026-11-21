# Results: Donchian breakout on Coinbase BTC / ETH perps

All tests use the 9:30-16:00 New York session, 5-minute signals, and the rules exactly as in
`live/engine.py` (parity with `donchian_lab.py` is checked trade for trade in `tests/`).

## 1. Five-year backtest, 5-minute bars (`crypto_check.py`)

Dec 2021 - Nov 2026, results in R (1R = the first stop). Round-trip cost in basis points of price.

| rules | market | 0 bps | 4 bps | 8 bps | 12 bps |
|---|---|---|---|---|---|
| forward_test | BTC | +888R (t 6.3) | +462R (t 3.3) | +36R (t 0.3) | -391R (t -2.8) |
| forward_test | ETH | +772R (t 5.4) | +425R (t 3.0) | +77R (t 0.5) | -270R (t -1.9) |
| lab_baseline | BTC | +287R (t 5.0) | +218R (t 3.8) | +149R (t 2.6) | +80R (t 1.4) |
| lab_baseline | ETH | +285R (t 4.9) | +231R (t 4.0) | +177R (t 3.1) | +122R (t 2.1) |

The forward-test rules have the bigger raw edge, but the 1-ATR stop on 5-minute bars is only about
0.3% of price, so each trade's costs are a large share of its risk. Break-even is about 8 bps round trip.

## 2. Last 12 months, the real bot on 1-minute prices (`sim_exchange.py`)

Nov 2025 - Nov 2026, 258 sessions. $10,000 account, 0.5% risk per trade, fee 2 bps per side
(Coinbase's reported US perp taker rate) plus slippage per fill. Stop-limit orders, entry-bar stops,
and fees are all modelled; the backtest above ignores a stop touched inside the entry bar.

| rules | market | slip 1 bps: net | max DD | fees | slip 3 bps: net |
|---|---|---|---|---|---|
| forward_test | BTC | **-$898** | $3,269 | $4,408 | -$5,302 |
| forward_test | ETH | **-$1,346** | $5,791 | $3,582 | -$4,923 |
| lab_baseline | BTC | +$36 | $1,656 | $693 | -$657 |
| lab_baseline | ETH | **+$1,270** | $655 | $517 | +$753 |

- The forward-test rules **lost money on both coins** after costs. Fees alone were 36-44% of the
  account over the year, because tight stops mean large positions and up to 3 trades a day.
- The lab baseline trades half as often with stops 3x wider: fees fell about 85%. ETH made +12.7%
  with a small drawdown; BTC was flat.
- One year and one market is thin evidence. The baseline was chosen on SPY long before any crypto
  test, but preferring it now is still a choice made after seeing these numbers.
- With 3% of API calls failing at random, the bot finished every day flat with no orders left.

## 3. Paper forward test (`forward_paper.py`)

Starts 2026-11-17, the day the rules were frozen. Same settings as section 2 (2 + 1 bps).

| market | rules | Nov 17-20 |
|---|---|---|
| BTC | forward_test | +$22 |
| BTC | lab_baseline | +$152 |
| ETH | forward_test | +$701 (one +19R trade on Nov 20) |
| ETH | lab_baseline | +$266 |

Four days says nothing yet. Run it after each session; give it at least 40 sessions before deciding.

## What the example config does

`config.example.json` uses `lab_baseline`, ETH enabled and BTC disabled, `dry_run: true`. Change any
of that deliberately; the forward-test rules are one line (`"preset": "forward_test"`) if the paper
test changes the picture.

## 4. Perp vs futures, gold, and funding (`perp_compare.py`, output in `perp_compare_output.txt`)

Same trades found once on 5-minute bars, then each instrument's costs applied. Perp costs: taker fee
per side + 1 bps slippage per side, plus funding for the hours a trade is open.

### US100 perp vs MNQ (no US100 perp history available: estimated from the Nasdaq-100 index itself,
perp sized to the same notional as 1 MNQ, about $50k)

| forward_test rules | before costs | 1 MNQ | US100 perp @ 2 bps | @ 3.5 bps | @ 4.5 bps |
|---|---|---|---|---|---|
| two-year backtest (1,078 trades) | +$22,225 | **+$19,811** | **-$10,015** | -$26,135 | -$36,882 |
| paper forward test Nov 17-20 (12 trades) | -$138 | -$165 | -$526 | -$720 | -$849 |

One MNQ round trip costs about $2.24. The same notional in a perp costs about $30 at 2 bps (6 bps
round trip with slippage), so the perp pays roughly 13x more per trade. MNQ is the right vehicle.

### Gold (Coinbase PAXG used as the gold price; Coinbase only has it from May 2025, and it is thin)

| rules, last 12 months | before costs | gold perp @ 2 bps | as micro gold futures (MGC) |
|---|---|---|---|
| forward_test | +$5,963 | **-$8,290** | **+$3,777** |
| lab_baseline | +$1,160 | -$1,726 | +$723 |

Gold's 5-minute moves are small relative to its price, so the stops are tight and positions large:
costs are even worse than on crypto. As a perp it fails; with futures costs the forward-test rules
were positive over the last year, but the 6 months before that were negative (full 18 months: +$260),
and PAXG's empty bars shrink the ATR, so treat this as weak. Real gold futures data is the next test.

### Silver

Not run. No silver price history was reachable here (Coinbase has none; Yahoo, Stooq and others are
blocked; Dukascopy was allowed but rate-limited the request).

### Funding

Not the problem. The strategy is flat every night and the average trade is open about an hour, so
funding is a rounding error next to trading fees:

| | fees per year | funding at 10% APR | at 30% APR |
|---|---|---|---|
| BTC forward_test, $10k account, last 12 months | $6,718 | ~$0 | ~$0 |
| US100 perp, two-year backtest | $32,240 | $137 | $411 (worst case, every trade paying: $1,859) |

Trades are a mix of longs and shorts, so funding is partly received as well as paid. The cost that
kills the perp versions is the fee (plus slippage) on every entry and exit, multiplied by tight
stops that make positions large.
