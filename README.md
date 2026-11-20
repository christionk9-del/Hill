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

## Workflow

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
