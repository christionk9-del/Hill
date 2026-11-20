# Hill — Kalshi tail-bet bot

A small, budget-capped bot that buys cheap ("tail") Kalshi contracts **only where
historical data shows they win more often than their price plus fees**.

## Read this first: the 10x-a-week math

The original goal was $5/day ($35/week) turned into $350/week. Two facts matter:

1. **Many small bets make a 10x week *less* likely.** Lots of independent bets
   average out toward expected value. `python -m kalshi_bot simulate` shows that
   35 bets at 5c with a 4% true win rate reach $350 in ~0% of weeks. One
   concentrated $35 bet reaches it in ~4% of weeks.
2. **No strategy can beat the Markov bound:** P(end ≥ 10× stake) ≤ E[return] / 10.
   If expected return is below 1.0× (typical for longshots), a 10x week happens
   less than 10% of the time, however the bets are arranged.

Cheap contracts are often *overpriced* (the favorite-longshot bias), and fee
rounding can cost 25–33% on a single 3c contract. The bot therefore treats
"is there an edge?" as an empirical question and refuses to bet when the answer is no.

## Backtest results (public data, Sep 2025 – Jan 2026)

`python -m kalshi_bot backtest --download` pulls the free
[TrevorJS/kalshi-trades](https://huggingface.co/datasets/TrevorJS/kalshi-trades) dataset
(154M trades, CC-BY-4.0), runs everything below, and writes the bot's `data/calibration.json`.

**Real Kalshi combos, YES buyers, $ back per $1 after fees** (4.8M trades):

| Legs | Before Dec 15 | After Dec 15 |
|---|---|---|
| 2 | 0.85 | 0.95 |
| 3–4 | 0.73 | 0.93 |
| 5–7 | 0.64 | 0.94 |
| 8–11 | 0.44 | 0.78 |
| 12+ | 0.30 | 0.63 |

- Sports single legs at pre-game prices returned 0.84–0.97 per $1 at every price.
- The only leg types that were +EV before Oct 2025 *and* after: BTC daily price
  (`KXBTCD`, 76–85c, 1.10) and some city daily-high temperatures (`KXHIGHDEN` 1.06,
  `KXHIGHPHIL` 1.03, `KXHIGHCHI` 1.03). These are small samples and not sports.
- The NO side of combos (being the house) returned about 1.01–1.03 for market makers,
  and about 0.99 for takers in the later period.

**"Only bet the +EV ones"** (`python -m kalshi_bot pnl --ev-only`): combo types picked
because they were +EV before Dec 15 returned about $1.15 per $1 afterwards, but only in
3–5c combos, and **removing the single best day (Jan 25 upsets) drops it to $1.00**. Picking
the +EV ones in hindsight always looks good; picked in advance, there's no reliable edge.
The dataset floors sub-cent prices (1c recorded = ~1.6c real), so cheap combos look
cheaper than they were; the report corrects for this.

## Combo (parlay) system — the main strategy

A combo multiplies its legs, so a small edge per leg becomes a big edge on the
ticket, and a small markup per leg becomes a big loss. The engine:

1. **Calibrates every leg type** (by series, category and price bucket, 1–99c) from settled markets.
2. **Builds combos only from underpriced legs**: estimated win rate ≥ price + 1 point
   (shrunk toward the market price), legs priced 40–92c, **max 1 leg per game** so
   multiplying probabilities is honest. Same-game legs move together.
3. **Requests a real quote (RFQ)** and accepts only if model EV ≥ 1.15× the quoted
   price after fees; otherwise it cancels the request.
4. **Sizes to the shared budget**: $5 a combo, at most $5 a day and $35 a week.

```bash
python -m kalshi_bot calibrate                 # once a week or so
python -m kalshi_bot combo-scan                # what it would build right now
python -m kalshi_bot combo-trade               # paper: quote simulated as fair x 1.20
python -m kalshi_bot combo-trade --quote-only  # real quotes from market makers, never accepts
python -m kalshi_bot combo-settle              # settle + P&L vs model
python -m kalshi_bot combo-trade --live        # only after paper results justify it
python -m kalshi_bot combo --cost 9.86 --payout 1562.50 --legs 16   # evaluate any ticket
```

Run `--quote-only` for a week or two. It records how much market makers mark up
combos versus the leg prices, which is the number that decides whether this can win.
Test the live flow on the demo exchange first:
`KALSHI_BASE_URL=https://demo-api.kalshi.co/trade-api/v2`.

The combo/RFQ endpoints in `client.py` follow Kalshi's published docs but were not
exercised against the real API from the build environment. Verify field names there.

## Workflow (single tail bets)

```bash
pip install -r requirements.txt

# 1. Measure reality: how often do 2-15c contracts actually win? (public data, no key)
python -m kalshi_bot calibrate --max-events 3000

# 2. See current positive-edge candidates (often: none)
python -m kalshi_bot scan

# 3. Paper trade for several weeks (daily cron), settle, compare to the model
python -m kalshi_bot trade
python -m kalshi_bot settle
python -m kalshi_bot stats

# 4. Only if paper profit is real and sizable: go live
export KALSHI_KEY_ID=...  KALSHI_PRIVATE_KEY_PATH=~/kalshi.pem
python -m kalshi_bot trade --live
```

## Hard limits (env-overridable, see `kalshi_bot/config.py`)

| Setting | Default |
|---|---|
| Daily budget | $5 |
| Weekly budget | $35 |
| Max per bet | $1 |
| Max bets per day | 5 |
| Tail price range | 2–15c |
| One open bet per event | yes (outcomes in one event are correlated) |
| Required edge after fees | ≥ 1c per contract, on a shrunk, lower-bounded estimate |

## Layout

- `kalshi_bot/client.py` — signed Kalshi v2 API client
- `kalshi_bot/fees.py` — taker-fee model (verify against Kalshi's current fee schedule)
- `kalshi_bot/calibration.py` — win rate by price bucket and category from settled markets
- `kalshi_bot/strategy.py` — candidate selection with shrinkage toward market price
- `kalshi_bot/ledger.py` — SQLite ledger, budget enforcement, settlement, P&L
- `kalshi_bot/simulate.py` — weekly Monte Carlo for the 10x target
