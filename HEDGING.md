# Semi-auto arbs: you place the sportsbook leg, EdgeLab hedges on Kalshi

The sportsbook goes **first**: it's the leg that can refuse, limit or re-price you. Kalshi is the liquid leg. The bot reads the live order book, works out the **worst price that still locks your minimum profit**, and sends an **immediate-or-cancel limit order** at that price. It can fill cheaper, never worse. Anything it can't fill is reported as unhedged and never chased.

> Run this on **your own computer**. Kalshi requires every request to be signed with your private key, so the key must never be uploaded, pasted into chat or committed.

## One-time setup
1. `pip install cryptography` (the only extra dependency; used for request signing).
2. On Kalshi: **Account & security → API Keys → Create Key**. Save the private key file as `~/.edgelab/kalshi.pem` and note the **Key ID**. Make a **demo** key too at demo.kalshi.co, and practise there first.
3. Create `~/.edgelab/kalshi.toml` (see `kalshi.example.toml`):
   ```toml
   key_id = "your-key-id"
   private_key_path = "~/.edgelab/kalshi.pem"
   env = "demo"            # switch to "prod" only after a few demo runs (or pass --prod)
   max_order_usd = 200     # refuses any single hedge above this
   max_daily_usd = 1000    # refuses once today's hedges exceed this
   fee_rate = 0.07         # Kalshi taker fee coefficient (some series differ)
   ```
4. `chmod 600 ~/.edgelab/kalshi.pem ~/.edgelab/kalshi.toml`

## Every arb (about a minute)
```bash
# 1. find the Kalshi market that WINS when your sportsbook bet LOSES
python3 -m edgelab hedge find philadelphia montreal --contains "6.5"
#   -> Over 6.5 goals scored | YES 44¢ NO 58¢ | KXNHLTOTAL-26NOV21PHIMTL-7

# 2. preview with the odds you were offered (sends nothing)
python3 -m edgelab hedge plan --ticker KXNHLTOTAL-26NOV21PHIMTL-7 --buy no --book-odds +160 --stake 38.46
#   Kalshi leg : buy 100 NO contracts, limit 59.0¢ each
#   Locked     : +$1.83 (+1.87% of $98.17 outlay) whichever side wins

# 3. place the sportsbook bet by hand and wait until it's CONFIRMED at those odds

# 4. hedge (type HEDGE to confirm)
python3 -m edgelab hedge place --ticker KXNHLTOTAL-26NOV21PHIMTL-7 --buy no --book-odds +160 --stake 38.46 \
    --book "SportsbookName" --note "PHI-MTL o6.5" --live --prod

python3 -m edgelab hedge log        # every hedge, fill and locked profit
python3 -m edgelab hedge balance --prod
```
**Which side to buy:** the opposite of your sportsbook bet. Book *Over 6.5* → Kalshi **NO** on "Over 6.5". Book *Portland ML* → Kalshi **YES** on "New York" (or **NO** on "Portland"). Book *UAB +4.5* → Kalshi **YES** on "UTSA wins by over 4.5".

## Safety rails
- **Demo by default.** Real money needs `--live`, plus `--prod` (or `env = "prod"`), plus typing `HEDGE`.
- **Price-limited IOC orders**, so it never pays more than your minimum profit allows (`--min-profit`, default 0.5%). To hedge at a small loss after the price moved, pass e.g. `--min-profit -0.01` deliberately.
- **Caps:** per-order and per-day limits, plus a balance check before sending.
- **Partial fills:** it retries up to 3 times at the same limit, then reports exactly how many contracts are unhedged.
- **Timeouts:** if Kalshi times out, the order's fate is unknown. It looks the order up by its unique client ID instead of resubmitting (per Kalshi's docs), so it never double-buys.
- **Rules:** `plan` prints the Kalshi market's resolution rules. Check that they match your sportsbook's (overtime, ties, player not playing, postponement).

## Where arbs come from
- The **arb scanner** (`.claude/skills/open-ev-finder/scripts/arb_scanner.py`) once your Odds API key works.
- Odds apps that show +EV or "sharp" lines: check each pick with `hedge plan`. If "Locked" is positive, it's an arb.

Not financial advice. Sportsbooks limit arbitrage bettors, and access to Kalshi and sportsbooks depends on your state.
