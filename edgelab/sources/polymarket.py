"""Polymarket via the public Gamma API (no key)."""
from __future__ import annotations

import json
from datetime import datetime

from .base import Quote, Source, classify_domain, http_json, parse_iso

BASE = "https://gamma-api.polymarket.com"


class Polymarket(Source):
    name = "polymarket"

    def __init__(self, limit=200, min_liquidity=5000, fee_rate=0.0, domains=("politics", "geopolitics", "economics"), **_):
        self.limit, self.min_liq, self.fee = limit, min_liquidity, fee_rate
        self.domains = set(domains)

    def fetch(self, now: datetime) -> list[Quote]:
        out = []
        events = http_json(f"{BASE}/events", {"active": "true", "closed": "false", "limit": self.limit,
                                              "order": "volume24hr", "ascending": "false"})
        for ev in events:
            tags = " ".join(t.get("label", "") for t in ev.get("tags", []) or [])
            dom = classify_domain(tags, ev.get("title", ""))
            if self.domains and dom not in self.domains:
                continue
            for m in ev.get("markets", []) or []:
                out.extend(self._quotes(m, dom, now))
        return out

    def _quotes(self, m, dom, now):
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
            bid, ask = float(m.get("bestBid") or 0), float(m.get("bestAsk") or 0)
            liq = float(m.get("liquidityNum") or m.get("liquidity") or 0)
        except (ValueError, TypeError):
            return []
        if outcomes != ["Yes", "No"] or not (0 < bid < ask < 1) or liq < self.min_liq or m.get("closed"):
            return []
        if parse_iso(m.get("endDate", "")) <= now:
            return []
        no_ask = 1 - bid
        base = dict(source=self.name, venue="polymarket", domain=dom, event_id=f"pm:{m['id']}",
                    event_name=m.get("question", ""), market_key="binary", start_time=m.get("endDate", ""),
                    ref={"id": m["id"], "slug": m.get("slug")})
        return [Quote(outcome="Yes", price=1 / (ask * (1 + self.fee)), **base),
                Quote(outcome="No", price=1 / (no_ask * (1 + self.fee)), **base)]

    def results(self, pending, now):
        out = {}
        for mid in {p["ref"]["id"] for p in pending if p["ref"].get("id")}:
            try:
                m = http_json(f"{BASE}/markets/{mid}")
            except Exception:
                continue
            if not m.get("closed"):
                continue
            try:
                prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            except ValueError:
                continue
            if len(prices) != 2 or max(prices) < 0.99:
                continue
            yes_won = prices[0] > prices[1]
            for p in pending:
                if p["ref"].get("id") == mid:
                    side = p["ref"].get("side", p["outcome"])
                    out[(p["group_id"], p["outcome"])] = 1.0 if (side == "Yes") == yes_won else 0.0
        return out
