# Coinbase perp bot: intraday Donchian breakout

The same rules as the MNQ forward test (`live/engine.py`, unchanged logic), traded on Coinbase's US
perpetual-style futures (nano BTC `BIP-20DEC30-CDE`, nano ETH `ETP-20DEC30-CDE`).

- 5-minute bars, 9:30-16:00 New York, weekdays only (the session every backtest used).
- Enter when price trades through the channel high/low; max 3 trades a day; flat after the 15:55 bar.
- Two rule sets, chosen with `preset` in the config:

| preset | entry channel | first stop | trailing | last entry |
|---|---|---|---|---|
| `forward_test` (what the MNQ paper test runs) | 24 bars | 1 x ATR | 24-bar channel or 2 x ATR | 13:25 |
| `lab_baseline` (the earlier SPY choice) | 78 bars (1 day) | 3 x ATR | 24-bar channel | 14:55 |

## Files

| file | what it does |
|---|---|
| `bot.py` | the bot: `check` (read-only) and `run` |
| `coinbase_api.py` | Coinbase Advanced Trade REST client (CDP key JWT auth) |
| `sim_exchange.py` | simulated exchange on 1-minute prices; runs the real bot for a year |
| `forward_paper.py` | paper forward test on live Coinbase prices, one finished session at a time |
| `crypto_check.py` | the 5-year, 5-minute backtest on BTC/ETH at several cost levels |
| `fetch_minutes.py` | downloads 1-minute session bars from Coinbase |
| `tests/` | engine vs backtest parity, bot safety on the simulator, request signing and order format |

## Results

See `RESULTS.md`.

## Setup (on your own computer)

1. In Coinbase, open a futures (Coinbase Financial Markets) account and move the margin you want to use there.
2. Create a CDP API key at portal.cdp.coinbase.com with **View** and **Trade** only. Never enable Transfer.
3. Install and configure:

```
pip install -r coinbase_perp/requirements.txt
cp coinbase_perp/config.example.json coinbase_perp/config.json
export COINBASE_API_KEY_NAME="organizations/.../apiKeys/..."
export COINBASE_API_SECRET="$(cat cdp_private_key.pem)"        # never put it in the config file
python -m coinbase_perp.bot check coinbase_perp/config.json     # read-only: balance, products, sizing, order preview
python -m coinbase_perp.bot run   coinbase_perp/config.json     # dry_run: true by default
```

4. `check` confirms the product IDs, contract sizes and tick sizes from Coinbase itself, and
   previews (does not send) a 1-contract order. Fix anything it reports before `run`.
5. Run with `"dry_run": true` for at least two weeks next to `forward_paper.py`. Then set it to
   `false` with a small `risk_pct` and `max_contracts`.

Start the bot before 9:30 New York. Started mid-session, it rebuilds the day; an open position that
matches the model's trade is adopted, anything else is flattened.

## Config

| key | meaning |
|---|---|
| `dry_run` | `true`: live prices, orders only logged |
| `preset` / `strategy` | rule set; `strategy` overrides single values |
| `entry_mode` | `resting` (stop-limit orders at the channel) or `market` |
| `markets[].signal_product` | bars used for signals (spot, as in the backtest); order prices are shifted by the perp - spot basis |
| `risk_pct` | account % lost if the first stop is hit |
| `max_contracts`, `max_leverage` | hard caps per market |
| `daily_loss_limit_pct` | flatten everything and stop for the day |
| `stop_limit_buffer_pct` | how far past a protective stop its limit sits; if price skips the limit the bot closes at market |
| `max_chase_R` | if a resting entry missed and price is this far past the entry, skip the trade |

## Safety

- `STOP` file next to the config: flattens everything and exits. Ctrl+C exits but leaves positions
  and their exchange stops in place.
- Protective stop at the exchange right after each fill; stops only tighten.
- Every 5 minutes the exchange is reconciled with the model: missing stop placed, wrong-size stop
  fixed, unexpected / wrong-side / oversize positions flattened. A position that will not flatten
  switches that market off with a CRITICAL log line.
- Hard flatten at 16:02 New York. Daily loss limit on the account value.
- Orders are never retried blindly (a timeout could otherwise double an order); the next
  reconcile checks what actually happened.
- The bot only cancels orders it created (client order ID starts with `dv-`), but it **will close any
  position in these products** that the model does not hold. Do not trade them by hand while it runs.

## Not verified here

This session could not reach `api.coinbase.com`, so the client was tested against a simulator and
against Coinbase's documented request formats, not against the real exchange. `check` and a dry
run are the first real tests. In particular, confirm: the product IDs, that the edit-order call works
on stop-limit futures orders (the bot falls back to cancel-and-replace if not), and the fee tier on
your account (the results assume 2 bps per side).
