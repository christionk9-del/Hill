"""Download 5-minute spot candles from Coinbase (public API, no key) and keep the
weekday 9:30-16:00 New York window, the same session the other markets are tested on.
Usage: python3 fetch_coinbase.py BTC-USD 2021-11-17 2026-11-17
"""
import sys
import time
import os
import pandas as pd
import requests

URL = "https://api.exchange.coinbase.com/products/{}/candles"


def fetch(product, start, end):
    rows, t = [], pd.Timestamp(start, tz="UTC")
    end = pd.Timestamp(end, tz="UTC")
    step = pd.Timedelta(minutes=5 * 300)
    s = requests.Session()
    s.headers["User-Agent"] = "backtest-script"
    while t < end:
        for attempt in range(6):
            try:
                r = s.get(URL.format(product), timeout=30, params=dict(
                    granularity=300, start=t.isoformat(), end=(t + step - pd.Timedelta(minutes=5)).isoformat()))
                if r.status_code == 200:
                    rows += r.json(); break
            except requests.RequestException:
                pass
            time.sleep(2 * (attempt + 1))
        else:
            print("gave up at", t, flush=True)
        t += step
        time.sleep(0.12)
    d = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"]).drop_duplicates("t")
    d["ts"] = pd.to_datetime(d.t, unit="s", utc=True).dt.tz_convert("America/New_York")
    return d.sort_values("ts").set_index("ts")[["open", "high", "low", "close", "volume"]]


if __name__ == "__main__":
    product, start, end = sys.argv[1:4]
    d = fetch(product, start, end)
    mins = d.index.hour * 60 + d.index.minute
    rth = d[(d.index.dayofweek < 5) & (mins >= 570) & (mins < 960)]
    os.makedirs("data/5min", exist_ok=True)
    out = f"data/5min/{product.split('-')[0]}.csv.gz"
    rth.to_csv(out)
    print(f"{product}: {len(d)} bars fetched, {len(rth)} kept, {rth.index[0]} -> {rth.index[-1]} -> {out}")
