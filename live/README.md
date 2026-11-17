# Running the MNQ Donchian variant

Two ways to trade the same rules:

| Accounts | How | Files |
|---|---|---|
| 15 Tradovate prop accounts | TradingView strategy sends webhook alerts to a bridge that copies them to Tradovate | `pine/mnq_donchian_variant.pine` |
| 5 Topstep accounts | Python bot on your own computer, TopstepX API | `live/topstep_bot.py` + `live/config.json` |

The rules: 5-minute regular-hours Nasdaq bars; enter when price trades through the high or low of
the previous 24 bars (9:35 to 13:25 New York, max 3 trades a day); initial stop 1 x ATR(14); after
each 5-minute close the stop tightens to the 24-bar channel or close -/+ 2 x ATR; flat at 16:00.

## Before any real money

1. Check each firm's rules on automated trading, copy trading across accounts, and how many
   accounts you may hold. Topstep requires API trading to come from your own device, not a VPS or
   VPN, and the API is a paid add-on.
2. Run both setups on simulated or paper accounts for at least two weeks and compare the fills
   with the backtest (`live/replay_test.py` shows what the model would have done).
3. Start with a few accounts, then scale up.

## TradingView -> Tradovate (15 accounts)

1. Open a 5-minute MNQ chart (MNQ1!), Pine Editor, paste `pine/mnq_donchian_variant.pine`, Save,
   Add to chart. It has not been compiled in this session; fix any compile message TradingView shows.
2. In the Strategy Tester, check that trades only start 9:35-13:25 and close by 16:00.
3. Set up your bridge (for example TradersPost or PickMyTrade) with one strategy that fans out to
   your Tradovate accounts, and copy its webhook URL.
4. Create one alert on the strategy: condition "Order fills only", message
   `{{strategy.order.alert_message}}`, webhook URL from step 3.
5. Edit the webhook message templates in the strategy settings if your bridge uses another format.
   The defaults are TradersPost-style JSON: a market entry with a broker-side stop, and an "exit".
6. In a sim account, confirm that after every exit no stop order is left working at Tradovate.

## Topstep bot (5 accounts)

```
pip install -r live/requirements.txt
cp live/config.example.json live/config.json      # put your five account names in it
export TOPSTEPX_USERNAME=your-login               # Windows PowerShell: $env:TOPSTEPX_USERNAME="..."
export TOPSTEPX_API_KEY=your-api-key              # never put the key in the config file
python live/topstep_bot.py live/config.json
```

- `dry_run: true` (the default) runs on live prices and logs every order it would send, without
  sending any. Run it this way for a few sessions first, then set it to false.
- Start it before 9:30 New York time. Started later, it rebuilds the day and does not join a trade
  already in progress.
- To stop everything: create a file named `STOP` next to `config.json`. The bot flattens all
  accounts and exits. Ctrl+C stops the bot but leaves open positions and their stops in place.
- Logs: `logs/bot-YYYY-MM-DD.log`, `logs/daily.csv` (per-account P&L each day, for the dashboard),
  `logs/model_trades.jsonl`.
- Safety built in: protective stop at the broker right after every fill; stops only ever tighten;
  every 5 minutes each account is checked against the model (missing stop placed, unexpected
  position flattened); a flatten is confirmed and an account that cannot be flattened is switched
  off with a CRITICAL log line; per-account daily loss limit; hard flatten at 16:02.

## Checks run here

- `python live/replay_test.py`: the live engine matches the backtest trade for trade.
- `python live/intrabar_check.py`: on 53 random days replayed minute by minute, results match the backtest.
- `python live/sim_broker.py` and `python live/sim_broker.py 0.03`: the bot itself, run end to end against
  a simulated broker on those days, with and without 3% of API calls failing.
