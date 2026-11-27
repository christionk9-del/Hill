"""CLI: python -m ev_scanner [--config config.json] [--promos promos.json]
                            [--at-local 07:30] [--no-send]"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .alerts import fmt_line, render_markdown, send_alerts, write_reports
from .config import ROOT, load_config, load_promos
from .promo_feed import scan_feeds
from .scanner import run_scan


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ev_scanner")
    ap.add_argument("--config")
    ap.add_argument("--promos")
    ap.add_argument("--out", default=str(ROOT / "reports"))
    ap.add_argument("--no-send", action="store_true", help="don't push alerts")
    ap.add_argument("--at-local", help="only run if local time is this HH:MM hour "
                    "(lets a UTC cron fire twice and still hit 7:30 local across DST)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.at_local:
        now = datetime.now(ZoneInfo(cfg.timezone))
        hh = int(args.at_local.split(":")[0])
        if now.hour != hh:
            print(f"Local time is {now:%H:%M} {cfg.timezone}; scheduled for {args.at_local}. Skipping.")
            return 0

    log = lambda m: print(m, file=sys.stderr)
    promos = load_promos(args.promos)
    log(f"[promos] {len(promos)} active promos loaded from promos.json")
    detected, feed_errors = scan_feeds(cfg.promo_feeds, log) if cfg.promo_feeds else ([], [])

    result = run_scan(cfg, promos, log=log)
    result["errors"] += feed_errors
    md = render_markdown(result, cfg, detected)
    write_reports(result, md, Path(args.out))

    print(md)
    if not args.no_send:
        send_alerts(result, cfg, md, log)
    # Non-zero exit only if we got no data at all, so CI surfaces outages.
    return 2 if result["offers_priced"] == 0 and result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
