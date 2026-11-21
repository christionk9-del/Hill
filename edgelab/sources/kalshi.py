"""Kalshi (CFTC-regulated event exchange). Public market data needs no key."""
from __future__ import annotations

from datetime import datetime

from .base import Quote, Source, http_json, parse_iso

BASE = "https://api.elections.kalshi.com/trade-api/v2"
CATEGORY_DOMAIN = {
    "Politics": "politics", "Elections": "politics", "World": "geopolitics",
    "Economics": "economics", "Financials": "economics", "Companies": "economics", "Crypto": "economics",
    "Sports": "sports",
}


def taker_fee(p: float, rate: float = 0.07) -> float:
    """Kalshi taker fee per $1 contract: rate * P * (1-P) (rounded up per order in reality)."""
    return rate * p * (1 - p)


class Kalshi(Source):
    name = "kalshi"

    def __init__(self, categories=("Politics", "Elections", "World", "Economics"), max_pages=5,
                 min_volume=1000, fee_rate=0.07, **_):
        self.categories, self.max_pages = set(categories), max_pages
        self.min_volume, self.fee_rate = min_volume, fee_rate

    def fetch(self, now: datetime) -> list[Quote]:
        out, cursor = [], None
        for _ in range(self.max_pages):
            params = {"status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = http_json(f"{BASE}/events", params)
            for ev in data.get("events", []):
                if self.categories and ev.get("category") not in self.categories:
                    continue
                dom = CATEGORY_DOMAIN.get(ev.get("category"), "other")
                for m in ev.get("markets", []):
                    out.extend(self._quotes(ev, m, dom, now))
            cursor = data.get("cursor")
            if not cursor:
                break
        return out

    def _quotes(self, ev, m, dom, now):
        try:
            ya, na = float(m.get("yes_ask_dollars") or 0), float(m.get("no_ask_dollars") or 0)
            vol = float(m.get("volume_fp") or m.get("volume") or 0)
        except ValueError:
            return []
        if m.get("status") not in ("active", "open") or not (0 < ya < 1 and 0 < na < 1) or vol < self.min_volume:
            return []
        if parse_iso(m.get("close_time", "")) <= now:
            return []
        title = ev.get("title", "")
        sub = m.get("yes_sub_title") or ""
        name = f"{title} — {sub}" if sub and sub not in title else title
        base = dict(source=self.name, venue="kalshi", domain=dom, event_id=f"ks:{m['ticker']}",
                    event_name=name, market_key="binary", start_time=m.get("close_time", ""),
                    ref={"ticker": m["ticker"]})
        return [Quote(outcome="Yes", price=1 / (ya + taker_fee(ya, self.fee_rate)), **base),
                Quote(outcome="No", price=1 / (na + taker_fee(na, self.fee_rate)), **base)]

    def results(self, pending, now):
        out = {}
        for t in {p["ref"]["ticker"] for p in pending if p["ref"].get("ticker")}:
            try:
                m = http_json(f"{BASE}/markets/{t}")["market"]
            except Exception:
                continue
            res = (m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            for p in pending:
                if p["ref"].get("ticker") == t:
                    out[(p["group_id"], p["outcome"])] = 1.0 if p["ref"].get("side", p["outcome"]).lower() == res else 0.0
        return out
