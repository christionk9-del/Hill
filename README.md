# EdgeLab: open +EV model & forward-testing lab

Find positive-expected-value bets in **sportsbooks _and_ prediction markets** (politics, geopolitics, economics) with your own transparent model instead of a $100–$300/month odds-screen subscription. Then **prove** whether the edge is real with a pre-registered, forward-only paper-trading test.

* **Zero dependencies.** Pure Python 3.11+ standard library. Clone and run.
* **Free data.** The Odds API (free tier, includes Pinnacle/Betfair), Kalshi, Polymarket and Manifold public APIs.
* **Scientific method built in.** Hypotheses are pre-registered and hash-locked. Every bet is timestamped before the outcome. There's a negative control, A/B arms, CLV with t-stats, bootstrap CIs and a verdict engine.
* **Dashboard.** A single self-contained HTML file. Share it, host it, or embed it in a lead-magnet page.

```bash
python3 -m edgelab demo                      # 45-day synthetic forward test, ~1 min, writes dashboard_demo.html
python3 -m edgelab ev --fair -110 -110 --bet +105     # quick EV calculator
cp edgelab.example.toml edgelab.toml && export ODDS_API_KEY=...   # free key: the-odds-api.com
python3 -m edgelab scan --every 900          # live paper trading every 15 min (+ dashboard.html)
python3 -m edgelab report                    # verdicts + devig tournament
python3 -m unittest discover -s tests -t .   # 12 tests
```

## The model

```
fair(outcome) = calibrate( consensus_venues( devig(venue prices) ) )
EV            = fair × decimal_price − 1          stake = ¼-Kelly, capped at 2% bankroll
```

1. **Devig.** Each venue's full outcome set → vig-free probabilities. Five methods are available (multiplicative, additive, power, Shin, odds-ratio), plus `worst_case`, which takes the minimum of all five. Which one is right is *measured*: the **devig tournament** scores every method by log loss on settled markets.
2. **Consensus.** A weighted average in log-odds space over information sources: Pinnacle, Betfair, Polymarket, Kalshi, Manifold. Soft books get weight 0 and are what you *bet into*. `exclude_self` removes the venue you're betting from its own fair line.
3. **Calibration** (optional). Per-domain logistic slope `logit q' = b·logit q`, fitted **walk-forward** only on your own settled outcomes. It targets the favourite-longshot bias of prediction markets.

Prediction-market contracts are treated like a two-way book: YES ask and NO ask, net of fees. Kalshi's taker fee `0.07·P·(1−P)` is applied, so spreads and fees are never mistaken for edge.

## The scientific method, enforced

| Step | In EdgeLab |
|---|---|
| Hypothesis | `experiments/*.json`. Each file has a `hypothesis` and fixed rules. |
| Pre-registration | Config is SHA-hashed on first scan. Editing rules under the same name is refused ("that's p-hacking, use a new name"). |
| Controlled experiment | A/B arms differ by one variable (e.g. `power` vs `multiplicative`). `control_negative_ev` bets −EV on purpose and must lose. If it doesn't, the pipeline is broken. |
| Measurement | **CLV** against one common benchmark (power devig, default weights, bet venue excluded). It converges ~10× faster than ROI. Also ROI with bootstrap CI, Brier, calibration curve, and a z-score of P&L vs the model's own expected P&L. |
| Conclusion | Verdicts: `COLLECTING → INCONCLUSIVE → PROMISING (CLV t>3) → CONFIRMED (CLV + ROI significant, n ≥ required)` or `REJECT`. |
| Ground truth | `demo` runs a synthetic world with *known* probabilities and planted inefficiencies, so you can grade the model itself (`trueEV` column). |

### What the demo teaches (seed 7, 45 days)

* Sports arms bet into soft books against the sharp consensus. True EV is **+2.2…+2.9%** and CLV is **+3.8…+4.7% (t ≈ 25)**, yet realised ROI is **−6.5%** after ~730 bets. That is pure variance (z ≈ −1.7). ROI alone would have killed a winning strategy. CLV did not.
* Event-market arms show model EV ≈ 10% but **true EV ≈ +1%**. When you pick the bets with the biggest gaps, you also pick the ones where the benchmark is most wrong (winner's curse). **CLV measured against the same venues inherits their errors.** Use `edge_capture` (CLV ÷ model EV) and long forward tests before trusting event-market EV.
* The negative control loses on every metric. The measurement works.

## Data sources

| Source | Domain | Key | Notes |
|---|---|---|---|
| The Odds API | sports | free tier (500 credits/mo) | `eu` region needed for Pinnacle/Betfair sharp lines |
| Kalshi | politics, world, economics | none for data | taker fees modelled |
| Polymarket (Gamma) | politics, geopolitics, economics | none | best bid/ask |
| Manifold | everything | none | play money: signal only, never bettable |

Cross-venue pooling for event markets needs `mappings.json`. Run `python3 -m edgelab match` to get fuzzy-matched suggestions and review them by hand. This uses official public APIs only; it does not scrape OddsJam or other paid screens, which their terms forbid.

## Layout

```
edgelab/  odds.py devig.py model.py simulator.py stats.py analysis.py ledger.py cli.py dashboard_template.html
          sources/{oddsapi,kalshi,polymarket,manifold,demo}.py
experiments/   pre-registered strategies (JSON)
skill/edgelab-ev/   Claude skill (give-away)
docs/          monetization roadmap PDF + generator
```

## Responsible use
Research and education tool. Paper results ≠ future returns. Sportsbooks limit winning accounts. Bet only where legal and only what you can afford to lose. US problem-gambling help: 1-800-GAMBLER.
