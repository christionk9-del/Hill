#!/bin/bash
# Keeps the BTC speed bot running (paper by default). Pass extra flags, e.g. --live.
# Start:  nohup ./run_speedbot.sh &      Stop: pkill -f run_speedbot.sh; pkill -f "kalshi_bot speedbot"
cd "$(dirname "$0")"
mkdir -p data
while true; do
  python -u -m kalshi_bot speedbot "$@" >> data/speedbot.log 2>&1
  echo "$(date -u) speedbot exited with $?, restarting in 10s" >> data/speedbot.log
  sleep 10
done
