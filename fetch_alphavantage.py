"""Download SPY 5-minute regular-hours bars from Alpha Vantage, one month per request.

Reads the key from the ALPHAVANTAGE_API_KEY environment variable (never stored in the repo).
Months already saved under data/av/ are skipped, so it can be rerun after a daily limit.
Usage: python3 fetch_alphavantage.py 2021-11 2026-02   -> then merges into data/spy_5min.csv
"""
import io
import os
import sys
import time
import pandas as pd
import requests

URL = "https://www.alphavantage.co/query"
OUT = "data/av"


def fetch_month(month, key):
    r = requests.get(URL, timeout=60, params=dict(
        function="TIME_SERIES_INTRADAY", symbol="SPY", interval="5min", month=month,
        outputsize="full", extended_hours="false", adjusted="false", datatype="csv", apikey=key))
    if not r.text.startswith("timestamp"):
        raise RuntimeError(r.text.replace(key, "***")[:300])
    return r.text


def merge():
    parts = [pd.read_csv(os.path.join(OUT, f)) for f in sorted(os.listdir(OUT)) if f.endswith(".csv")]
    av = pd.concat(parts)
    av["ts"] = pd.to_datetime(av.timestamp).dt.tz_localize("America/New_York")
    av = av.drop(columns="timestamp").set_index("ts")
    old = pd.read_csv("data/spy_5min.csv")
    old["ts"] = pd.to_datetime(old.ts, utc=True).dt.tz_convert("America/New_York")
    old = old.set_index("ts")
    both = pd.concat([av[av.index < old.index[0]], old]).sort_index()
    both = both[~both.index.duplicated()][["open", "high", "low", "close", "volume"]]
    both.to_csv("data/spy_5min.csv")
    print(f"merged: {len(both)} bars, {both.index[0]} -> {both.index[-1]}")


if __name__ == "__main__":
    key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key:
        sys.exit("ALPHAVANTAGE_API_KEY is not set")
    os.makedirs(OUT, exist_ok=True)
    for m in pd.period_range(sys.argv[1], sys.argv[2], freq="M").astype(str):
        path = os.path.join(OUT, f"{m}.csv")
        if os.path.exists(path):
            continue
        try:
            text = fetch_month(m, key)
        except RuntimeError as e:
            print(f"{m}: stopped - {e}")
            break
        open(path, "w").write(text)
        print(f"{m}: {text.count(chr(10)) - 1} bars")
        time.sleep(13)
    if os.listdir(OUT):
        merge()
