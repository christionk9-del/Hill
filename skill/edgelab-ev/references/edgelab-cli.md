# EdgeLab CLI

```bash
python3 -m edgelab ev --fair -120 +110 --bet +125 --side 1 [--method power]
python3 -m edgelab demo [--days 45 --seed 7]        # synthetic world with known truth → dashboard_demo.html
python3 -m edgelab scan [--every 900]               # live: needs edgelab.toml (see edgelab.example.toml)
python3 -m edgelab report                           # verdict table + devig tournament
python3 -m edgelab dashboard --db edgelab.db --out dashboard.html
python3 -m edgelab match                            # suggest cross-venue mappings for event markets
```

Strategy file (`experiments/NAME.json`):
```json
{"name": "my_arm", "hypothesis": "...", "domains": ["sports"], "devig": "power",
 "min_ev": 0.02, "max_ev": 0.25, "min_prob": 0.05, "max_price": 10, "exclude_self": true,
 "min_sources": 1, "staking": "kelly", "kelly_fraction": 0.25, "max_stake_pct": 0.02,
 "bankroll": 1000, "max_hours_to_start": 48, "use_calibration": false,
 "bet_venues": ["draftkings", "fanduel"], "weights": {"pinnacle": 1.0}}
```

Ledger tables (SQLite): `bets` (each paper bet, with CLV and P&L), `fair` (opening and closing prediction per devig method), `markets`, `results`, `quotes` (raw snapshots), `strategies` (hash-locked), `calibrations`.
