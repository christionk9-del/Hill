"""Fetch BTC-USD 1h candles from Coinbase Exchange public API."""
import csv, json, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

START = datetime(2025, 12, 1, tzinfo=timezone.utc)  # warm-up for EMAs
END = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
out = sys.argv[1] if len(sys.argv) > 1 else "btc_1h.csv"
rows = {}
t = START
while t < END:
    t2 = min(t + timedelta(hours=300), END)
    url = (f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600"
           f"&start={t.isoformat()}&end={t2.isoformat()}")
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "backtest-script"})
            data = json.load(urllib.request.urlopen(req, timeout=20))
            break
        except Exception as e:
            time.sleep(2 ** attempt)
    else:
        raise SystemExit(f"failed {url}")
    for ts, low, high, open_, close, vol in data:
        rows[ts] = (ts, open_, high, low, close, vol)
    t = t2
    time.sleep(0.25)
with open(out, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["ts", "open", "high", "low", "close", "volume"])
    for k in sorted(rows):
        if k < END.timestamp(): w.writerow(rows[k])
print(len(rows), "bars")
