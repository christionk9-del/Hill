"""Manifold Markets (play-money). Used as a *signal only* — never bettable."""
from __future__ import annotations

from datetime import datetime, timezone

from .base import Quote, Source, classify_domain, http_json

BASE = "https://api.manifold.markets/v0"


class Manifold(Source):
    name = "manifold"

    def __init__(self, limit=200, min_liquidity=500, domains=("politics", "geopolitics", "economics"), **_):
        self.limit, self.min_liq, self.domains = limit, min_liquidity, set(domains)

    def fetch(self, now: datetime) -> list[Quote]:
        out = []
        for m in http_json(f"{BASE}/search-markets", {"term": "", "filter": "open", "sort": "liquidity",
                                                      "contractType": "BINARY", "limit": self.limit}):
            p = m.get("probability")
            if p is None or not (0.01 < p < 0.99) or (m.get("totalLiquidity") or 0) < self.min_liq:
                continue
            dom = classify_domain(m.get("question", ""))
            if self.domains and dom not in self.domains:
                continue
            close = datetime.fromtimestamp((m.get("closeTime") or 4102444800000) / 1000, tz=timezone.utc)
            if close <= now:
                continue
            base = dict(source=self.name, venue="manifold", domain=dom, event_id=f"mf:{m['id']}",
                        event_name=m.get("question", ""), market_key="binary",
                        start_time=close.strftime("%Y-%m-%dT%H:%M:%SZ"), bettable=False, ref={"id": m["id"]})
            out += [Quote(outcome="Yes", price=1 / p, **base), Quote(outcome="No", price=1 / (1 - p), **base)]
        return out

    def results(self, pending, now):
        out = {}
        for mid in {p["ref"]["id"] for p in pending}:
            try:
                m = http_json(f"{BASE}/market/{mid}")
            except Exception:
                continue
            if not m.get("isResolved"):
                continue
            res = m.get("resolution")
            for p in pending:
                if p["ref"]["id"] == mid:
                    side = p["ref"].get("side", p["outcome"])
                    out[(p["group_id"], p["outcome"])] = None if res not in ("YES", "NO") else (1.0 if side.upper() == res else 0.0)
        return out
