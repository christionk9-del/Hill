# EdgeLab CLI

```bash
python3 -m edgelab ev --fair 0.46 0.56 --bet 0.41 --fee-rate 0.07   # contract prices; also accepts -120 +110 / 1.91 2.05
python3 -m edgelab demo [--days 45 --seed 7]        # synthetic world with known truth → dashboard_demo.html
python3 -m edgelab scan [--every 900]               # live: needs edgelab.toml (see edgelab.example.toml)
python3 -m edgelab report                           # verdict table + devig tournament
python3 -m edgelab dashboard --db edgelab.db --out dashboard.html
python3 -m edgelab match [--threshold 0.6]          # -> mappings.candidates.json; review, then copy into mappings.json
```

Strategy file (`experiments/NAME.json`):
```json
{"name": "my_arm", "hypothesis": "...", "domains": ["politics", "geopolitics", "economics"],
 "devig": "power", "exclude_self": false, "min_sources": 2,
 "min_ev": 0.03, "max_ev": 0.30, "min_prob": 0.04, "max_price": 20,
 "max_days": 30, "min_apr": 0.5, "max_hours_to_start": 1440,
 "staking": "kelly", "kelly_fraction": 0.2, "max_stake_pct": 0.02, "bankroll": 1000,
 "use_calibration": false, "bet_venues": ["kalshi", "polymarket"], "weights": {"polymarket": 0.9}}
```
Retire an arm with `"active": false` (allowed: it doesn't change the hash). Changing any rule needs a new name.
Mapping entry (`mappings.json`):
```json
{"id": "fed-cut-dec", "name": "Fed cuts in December", "domain": "economics",
 "legs": {"kalshi": "TICKER", "polymarket": "market id", "manifold": "id"}, "invert": {"polymarket": false}}
```

Ledger tables (SQLite): `bets` (each paper bet, with CLV and P&L), `fair` (opening and closing prediction per devig method), `markets`, `results`, `quotes` (raw snapshots; `fair` also holds `venue:<name>` rows for the scorecard), `strategies` (hash-locked), `calibrations`.
