from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Quote:
    """One venue's price to BUY one outcome of a market.

    A *group* (event_id + market_key) is a complete set of mutually exclusive
    outcomes: e.g. h2h {Home, Away}, totals:220.5 {Over, Under}, or a
    prediction market {Yes, No}. Prices are decimal odds NET of fees.
    """
    source: str
    venue: str
    domain: str            # sports | politics | geopolitics | economics | other
    event_id: str
    event_name: str
    market_key: str
    outcome: str
    price: float
    start_time: str        # ISO-8601 UTC; kickoff for sports, close time for events
    bettable: bool = True
    ref: dict = field(default_factory=dict)  # source-specific ids for settlement

    @property
    def group_id(self) -> str:
        return f"{self.event_id}|{self.market_key}"


class Source:
    name = "base"

    def fetch(self, now: datetime) -> list[Quote]:
        raise NotImplementedError

    def results(self, pending: list[dict], now: datetime) -> dict[tuple[str, str], float | None]:
        """pending: [{group_id, outcome, ref}] -> {(group_id, outcome): 1.0 win | 0.0 loss | None void}.
        Only include markets that have actually resolved."""
        return {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    if not s:
        return datetime(2100, 1, 1, tzinfo=timezone.utc)
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def http_json(url: str, params: dict | None = None, timeout: int = 20, retries: int = 3):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers={"User-Agent": "edgelab/0.1", "Accept": "application/json"})
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # network hiccup / rate-limit: back off
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET {url.split('?')[0]} failed: {last}")


def env_or(value: str | None, env_name: str) -> str | None:
    return value or os.environ.get(env_name)


DOMAIN_KEYWORDS = {
    "politics": ["politic", "election", "congress", "senate", "president", "governor", "primary", "parliament", "trump", "democrat", "republican"],
    "geopolitics": ["geopolit", "world", "war", "ukraine", "russia", "china", "israel", "iran", "nato", "taiwan", "ceasefire", "middle east", "global"],
    "economics": ["econom", "fed", "inflation", "cpi", "rates", "gdp", "recession", "financ", "crypto", "bitcoin", "stock", "compan", "jobs"],
    "sports": ["sport", "nfl", "nba", "mlb", "nhl", "soccer", "football", "tennis", "ufc", "golf", "f1"],
}


def classify_domain(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for dom in ("sports", "geopolitics", "politics", "economics"):
        if any(k in blob for k in DOMAIN_KEYWORDS[dom]):
            return dom
    return "other"
