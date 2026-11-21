"""Download 1-minute Coinbase spot candles for the 9:30-16:00 New York session (public API, no key).

    python coinbase_perp/fetch_minutes.py BTC-USD 2025-11-17 2026-11-21 data/1min/BTC.csv.gz
"""
import os
import sys
import time

import pandas as pd
import requests

URL = "https://api.exchange.coinbase.com/products/{}/candles"
NY = "America/New_York"


def session_minutes(product, day, s=None):
    """One weekday session of 1-minute bars (two requests of up to 300 candles)."""
    s = s or requests.Session()
    t0 = pd.Timestamp(f"{day} 09:30", tz=NY)
    rows = []
    for a, b in ((t0, t0 + pd.Timedelta(minutes=299)), (t0 + pd.Timedelta(minutes=300), t0 + pd.Timedelta(minutes=389))):
        for attempt in range(6):
            try:
                r = s.get(URL.format(product), timeout=30, params=dict(granularity=60, start=a.tz_convert("UTC").isoformat(),
                                                                       end=b.tz_convert("UTC").isoformat()))
                if r.status_code == 200:
                    rows += r.json()
                    break
            except requests.RequestException:
                pass
            time.sleep(2 * (attempt + 1))
        time.sleep(0.12)
    d = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"]).drop_duplicates("t")
    d["ts"] = pd.to_datetime(d.t, unit="s", utc=True).dt.tz_convert(NY)
    d = d.sort_values("ts").set_index("ts")[["open", "high", "low", "close", "volume"]]
    m = d.index.hour * 60 + d.index.minute
    return d[(m >= 570) & (m < 960)]


if __name__ == "__main__":
    product, start, end, out = sys.argv[1:5]
    s = requests.Session()
    s.headers["User-Agent"] = "backtest-script"
    parts = []
    for day in pd.bdate_range(start, end):
        parts.append(session_minutes(product, day.date(), s))
    d = pd.concat(parts)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    d.to_csv(out)
    print(f"{product}: {len(d)} minute bars, {d.index[0]} -> {d.index[-1]} -> {out}")
