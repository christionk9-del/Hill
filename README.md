# EdgeLab: open +EV model & forward-testing lab for event markets

Find mispriced **event contracts** (politics, geopolitics, economics) across **Kalshi, Polymarket and Manifold** with your own transparent model instead of a paid odds-screen subscription. Then **prove** whether the edge is real with a pre-registered, forward-only paper-trading test.

* **Zero dependencies.** Pure Python 3.11+ standard library.
* **Free data, no keys.** Kalshi, Polymarket (Gamma) and Manifold public APIs.
* **Scientific method built in.** Hash-locked hypotheses, timestamped paper bets, a negative control, A/B arms, CLV t-stats, bootstrap CIs, a venue accuracy scorecard and a verdict engine.
* **Dashboard.** One self-contained HTML file. Share it or embed it in a lead-magnet page.
* Sports (via The Odds API) is still in the code but **off by default**. Set `[sources.oddsapi] enabled = true` to bring it back.

```bash
python3 -m edgelab demo                                  # 45-day synthetic event-market forward test (~1 min) -> dashboard_demo.html
python3 -m edgelab ev --fair 0.46 0.56 --bet 0.41        # contract EV: deep venue YES/NO asks vs the price you can get
cp edgelab.example.toml edgelab.toml
python3 -m edgelab match                                 # suggest Kalshi/Polymarket/Manifold pairs -> mappings.candidates.json
#   review each pair (same question? same resolution rules & date?) and copy the good ones into mappings.json
python3 -m edgelab scan --every 900                      # live paper trading every 15 min (+ dashboard.html)
python3 -m edgelab report                                # verdicts, venue scorecard, devig tournament
python3 -m unittest discover -s tests -t .               # 32 tests
```

## Open EV Finder (Kalshi scan)

`python3 .claude/skills/open-ev-finder/scripts/open_ev_finder.py` scans every open Kalshi market (~23k events) and lists the **top 5 +EV contracts per category**, with sports limited to this weekend's games, plus any true arbitrage. Fair prices come from Kalshi's own internal consistency, fees and depth included. It's also a Claude skill (`.claude/skills/open-ev-finder`, zipped in `docs/open-ev-finder-skill.zip`). Example output: `reports/`.

`arb_scanner.py` (same folder) looks for **guaranteed-profit arbs** between Kalshi (which also covers Robinhood event contracts), Polymarket and US sportsbooks, fees included. It flags rule mismatches and rejects look-alike questions.

## Live forward tests

Three separate live forward tests (**geopolitics, economics, sports**) run from `forwards/` every 30 minutes via GitHub Actions. Results go to the `forward-data` branch. Setup, mappings and the tweak-by-versioning workflow are in [`forwards/README.md`](forwards/README.md).

## The model

```
fair(outcome) = calibrate( consensus_venues( devig(YES ask, NO ask) ) )
EV  = fair / all-in cost − 1          APR = EV × 365 / days-to-resolution
stake = ⅕–¼ Kelly, capped at 2% of bankroll
```

1. **Each venue is a two-way book.** The YES ask and NO ask (after fees) sum to more than $1, and that overround is devigged like a bookmaker's margin. Kalshi's taker fee `0.07·P·(1−P)` is applied, so spreads and fees are never mistaken for edge.
2. **Cross-venue consensus.** A weighted average in log-odds space: Polymarket 0.8, Kalshi 0.7, Manifold 0.3 (play money, so signal only). Questions only pool once they're linked in `mappings.json`. *Matching resolution rules is the #1 source of fake edges.*
3. **Calibration** (optional). A per-domain logistic slope fitted walk-forward on your settled outcomes, aimed at the favourite-longshot bias.
4. **Venue scorecard.** Every venue's own closing price is scored on outcomes. Use it to set weights from evidence.
5. **Capital lock-up.** Contracts can tie up money for months. `max_days` and `min_apr` let a strategy demand an annualised return.

## The scientific method, enforced

| Step | In EdgeLab |
|---|---|
| Hypothesis | `experiments/*.json`. Each has a `hypothesis` and fixed rules. |
| Pre-registration | The config is SHA-hashed. Editing rules under the same name is refused. Retiring (`"active": false`) is allowed. |
| Control | `events_control_negative_ev` buys −EV contracts on purpose and must lose. |
| Measurement | CLV against one common benchmark, ROI with bootstrap CI, Brier, a calibration curve, edge capture (CLV ÷ model EV) and the venue scorecard. |
| Conclusion | `COLLECTING → INCONCLUSIVE → PROMISING → CONFIRMED`, or `REJECT`. |
| Ground truth | `demo` simulates a world with *known* probabilities, so the model itself can be graded (`trueEV`). |

### What the demo taught (seed 7, 45 days, events only)

| Arm | Model EV | True EV | Lesson |
|---|---|---|---|
| `events_consensus_ev3` (bet venue excluded from its own fair line) | +18.1% | **+2.9%** | Winner's curse: the biggest gaps are partly errors in the *benchmark* |
| `events_inclusive_ev3` (bet venue kept as one weighted signal) | +8.9% | **+6.2%** | Pre-registered fix: fewer, better bets |
| `events_short_dated_apr` (≤14 days, APR ≥ 50%) | +8.8% | **+6.4%** | Capital turns over fast |
| `events_consensus_calibrated` | +19.3% | 0.0% | Calibration on thin data didn't help (yet) |
| `events_control_negative_ev` | −0.3% | −1.2% | The measuring stick works |

The venue scorecard correctly ranked the planted accurate venue first.

**Capital.** ROI is return per dollar *staked*. What you earn on your bankroll ≈ ROI × (365 ÷ hold days) × share of bankroll deployed. The engine enforces cash (you can't stake money already in open positions) and reports `Bankroll / yr` and `Deployed`. The demo's huge annualised figures come from a toy world with ~8-day contracts, unlimited liquidity and planted edges. Don't read them as forecasts.

These are *simulated* results that check the pipeline, not evidence about real markets. Only the live forward test is.

Sports arms (`01–03`, `99`) are retired and kept for the record.

## Layout

```
edgelab/  odds.py devig.py model.py simulator.py stats.py analysis.py ledger.py cli.py dashboard_template.html
          sources/{kalshi,polymarket,manifold,demo,oddsapi}.py
experiments/   pre-registered strategies (JSON)
skill/edgelab-ev/   Claude skill (give-away)
docs/          monetization roadmap PDF + generator, demo dashboard, zipped skill
```

## Responsible use
Research and education tool. Paper results ≠ future returns. Access to prediction markets depends on where you live, so check the rules. Trade only what you can afford to lose. US problem-gambling help: 1-800-GAMBLER.
