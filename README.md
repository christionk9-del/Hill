# EV alert scanner

Runs every morning at **7:30 AM** (Eastern by default). It scans sportsbooks, exchanges and pick'em apps for bets at **20%+ expected value** and sends you an alert.

## What it scans

| Source | How | Notes |
|---|---|---|
| DraftKings, FanDuel, BetMGM, Caesars, ESPN Bet, Fanatics, BetRivers, Hard Rock, etc. | The Odds API (`us`, `us2`) | Moneylines, spreads, totals, player props |
| **Kalshi**, Novig, ProphetX | The Odds API (`us_ex`) **and** Kalshi's public API directly | Kalshi prices include its trading fee (7% × P × (1−P)) |
| **PrizePicks**, Underdog, Pick6, Betr | The Odds API (`us_dfs`) | Each leg is compared with your payout's break-even (PrizePicks 6-pick flex ≈ 54.2%) |
| Pinnacle, Circa, Novig, ProphetX | Used as the **sharp "true" line** | It removes the vig with the power method. If no sharp book posts a market, it uses the median of 4 or more books |

**EV** = fair win probability × decimal odds − 1. For example, a +145 bet that Pinnacle prices at 50% is +22.5% EV.

## Promos (EV stacking)

Straight bets above 20% EV are rare. Most 20%+ spots come from adding a promo on top of a bet that's already close to fair. Add today's promos to `promos.json` and the scanner finds the best bet to use each one on:

- `profit_boost`: for example, a 50% boost on a bet the sharp line prices at even money gives +21% EV.
- `bonus_bet`: shows the bonus-bet conversion rate. It alerts at 70% or higher, and usually points you to longshots.
- `no_sweat`: values the refund at `refund_conversion`, which defaults to 70%.
- `dfs_discount`: a PrizePicks or Underdog discounted line, checked against the sharp prop at the same line.

**Automatic promo discovery:** promo pages at PrizePicks and the sportsbooks need a login, a location check and JavaScript, so a script can't read them reliably. List public pages or RSS feeds in `config.json` under `"promo_feeds"` and the scanner pulls out anything that looks like a promo (boost %, bonus bets, no-sweat, Taco Tuesday and so on). Those show up under "Promos spotted"; confirm each one in your app, then add it to `promos.json`.

## Setup

1. Get a key at https://the-odds-api.com. The full daily scan uses several hundred credits, so the 20K plan is comfortable. To use fewer, cut `sports`, `regions` or `dfs.max_events_per_sport`.
2. In GitHub, go to **Settings → Secrets and variables → Actions** and add:
   - `ODDS_API_KEY` (required)
   - At least one alert channel:
     - `NTFY_TOPIC`: the easiest phone push. Install the ntfy app and subscribe to a long random topic name.
     - `DISCORD_WEBHOOK_URL`
     - `SLACK_WEBHOOK_URL`
     - `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` and `ALERT_EMAIL_TO`
3. The workflow `.github/workflows/ev-scan.yml` runs daily at 7:30 AM local time and switches correctly for daylight saving. **Actions → Daily EV scan → Run workflow** runs it on demand. The report is saved as a run artifact.

Not on Eastern time? Change `"timezone"` in `config.json` and the two cron lines in the workflow.

## Run locally

```bash
export ODDS_API_KEY=...        # optional: NTFY_TOPIC=..., DISCORD_WEBHOOK_URL=...
python -m ev_scanner           # full scan + alerts
python -m ev_scanner --no-send # report only (reports/latest.md, reports/latest.json)
python -m unittest discover -s tests -t .
```

It only needs Python 3.10 or newer, with no packages to install.

## Tuning (`config.json`)

- `ev_threshold` (0.20): the EV level that triggers an alert.
- `report_threshold` (0.03): bets below the alert level but above this go on the watchlist.
- `my_books`: for example `["draftkings","fanduel","prizepicks","kalshi"]` to show only books you have accounts with.
- `max_hours_ahead`, `max_decimal_odds`, `sharp_books`, `dfs.breakeven`: see `ev_scanner/config.py` for every option.

> A 20%+ EV reading on a straight bet is usually a stale line or a data mismatch, not free money. Check the sharp price and the rules before betting. Bet responsibly.
