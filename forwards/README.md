# Forward tests: geopolitics, economics, sports

Three independent live paper-trading tests. Each has its own ledger, dashboard and pre-registered strategies, so each can be tuned on its own evidence.

| Forward | Sources | Strategies (v1) |
|---|---|---|
| `geopolitics/` | Kalshi (Politics/Elections/World, re-classified by title), Polymarket, Manifold | `geo_inclusive_v1` (baseline model), `geo_30d_v1` (≤30-day contracts, APR ≥ 25%), `geo_control_v1` |
| `economics/` | Kalshi (Economics/Financials/Companies), Polymarket, Manifold | `econ_inclusive_v1`, `econ_30d_v1`, `econ_control_v1` |
| `sports/` | The Odds API (NBA + NFL moneylines; Pinnacle/Betfair consensus vs US books) | `sports_power_v1`, `sports_worstcase_v1`, `sports_control_v1` |

Every strategy enforces cash (no stake beyond the free bankroll) and **depth** (at most 50% of the $ shown at the best price, where the venue reports it).

## Running it

**Automatically (recommended):** `.github/workflows/forward-tests.yml` scans all three every 30 minutes on GitHub's free runners and pushes ledgers, dashboards (`*.html`) and CSVs (`*-bets.csv`) to the **`forward-data`** branch.
1. Merge this branch into `main`. GitHub only runs scheduled workflows from the default branch.
2. *(Sports)* Add the repository secret `ODDS_API_KEY` (Settings → Secrets and variables → Actions). Use a free key from the-odds-api.com.
3. Actions tab → *forward-tests* → **Run workflow** once to start it.

**By hand / cron on your own machine:**
```bash
for f in geopolitics economics sports; do python3 -m edgelab --config forwards/$f/config.toml scan; done
python3 -m edgelab --config forwards/geopolitics/config.toml report
```

## The one manual job: cross-venue mappings

Event strategies need ≥ 2 venues quoting the *same* question (`min_sources: 2`). **Until `forwards/mappings.json` has entries, geopolitics and economics place no bets.** That's by design: one venue can't grade itself.

1. Once a day the workflow writes `geopolitics-mapping-candidates.json` / `economics-mapping-candidates.json` to `forward-data`. Locally: `python3 -m edgelab --config forwards/geopolitics/config.toml match --out cands.json`.
2. For each candidate, open both markets and confirm the **same resolution source, deadline and edge cases**. Set `"invert": {"polymarket": true}` if one venue's YES is the other's NO.
3. Copy the confirmed entries (drop `score` and `_check`) into `forwards/mappings.json` and commit. Start with the 20–30 most liquid questions.

## Tweaking the model (without fooling yourself)

Rules are hash-locked: if you edit a running strategy, the next scan refuses it.
1. Copy `geo_inclusive_v1.json` to `geo_inclusive_v2.json`, give it the new `name`, change **one** thing (e.g. `min_ev`, `devig`, `weights`, `max_days`) and write the hypothesis ("v2 raises min_ev to 5%; expect higher CLV per bet, fewer bets").
2. Run v1 and v2 side by side. They see the same prices, so it's a clean A/B.
3. Judge on CLV t-stat and the control first, ROI last. When v2 wins, set `"active": false` on v1 (allowed; the history stays).
4. Use the **venue scorecard** on each dashboard to set `weights` from evidence.

## Budgets & limits
- **Odds API free tier (500 credits/mo):** the sports config fetches twice a day (~360 credits/mo), so sports CLV is coarse. On a paid plan, set `min_interval_minutes = 30` and add sports/markets.
- **Kalshi top-of-book depth is often tiny** (a few dollars). The depth cap reflects that, and real capacity is what limits annual returns.
- Polymarket depth isn't fetched yet (`size` unknown, so only the cash cap applies). Treat Polymarket stakes as optimistic.
