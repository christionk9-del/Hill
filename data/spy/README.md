# SPY 5-minute bars (RTH), 2016-01-04 → 2026-10-23

Source: SetupAlpha export formatted for RealTest (`SetupAlpha_SPY_5min_RTH_RealTest.xlsx`).

| File | Contents |
|---|---|
| `SPY_5min_RTH_2016-2026.xlsx` | Original upload, untouched. Columns: Symbol, Date, Time, Open, High, Low, Close, Volume |
| `SPY_5min_RTH_2016-2026.csv.gz` | Same data as gzipped CSV: `datetime,open,high,low,close,volume` (datetime = `YYYY-MM-DD HH:MM`, US/Eastern, bar start time) |

- 211,234 bars across 2,718 trading days
- Regular trading hours only: 09:30–15:55 bar starts (78 bars/day)
- 21 half-days with 42 bars (close at 13:00); a handful of days have 73–77 bars (missing bars)
- Prices appear split/dividend-unadjusted (first bar opens at 200.49)

## Loading

```python
import pandas as pd
df = pd.read_csv("data/spy/SPY_5min_RTH_2016-2026.csv.gz", parse_dates=["datetime"], index_col="datetime")
```
