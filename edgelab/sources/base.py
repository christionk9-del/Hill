from __future__ import annotations

import json
import os
import re
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
    size: float | None = None  # $ available at this price (top of book); None = unknown

    @property
    def group_id(self) -> str:
        return f"{self.event_id}|{self.market_key}"


class Source:
    name = "base"
    min_interval = 0  # minutes between API calls (protects metered quotas, e.g. The Odds API)

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
    "sports": ["sport", "sports", "nfl", "nba", "mlb", "nhl", "soccer", "football", "tennis", "ufc", "golf", "f1", "world cup",
               "super bowl", "playoff", "playoffs", "championship", "premier league", "olympics", "grand slam"],
    "geopolitics": ["geopolitics", "geopolitical", "war", "wars", "ukraine", "russia", "putin", "zelensky", "china", "xi jinping",
                    "taiwan", "israel", "netanyahu", "gaza", "hamas", "hezbollah", "iran", "north korea", "kim jong un", "nato",
                    "ceasefire", "middle east", "sanctions", "invade", "invasion", "military", "nuclear", "venezuela", "maduro",
                    "syria", "g7", "g20", "un security council", "prime minister", "chancellor", "foreign", "treaty", "pope",
                    "coup", "missile", "strike on", "annex", "eu", "european union", "european parliament", "europe", "european",
                    "india", "pakistan", "brics", "africa", "african", "asia", "latin america", "korea", "south korea", "japan",
                    "germany", "german", "france", "french", "uk", "britain", "british", "united kingdom", "canada", "mexico",
                    "brazil", "argentina", "turkey", "saudi", "egypt", "poland", "hungary", "leave office", "head of state",
                    "world leader", "world leaders", "un", "united nations", "japanese", "korean", "chinese", "russian",
                    "israeli", "iranian", "indian", "canadian", "mexican", "brazilian", "ukrainian", "taiwanese"],
    "politics": ["politics", "political", "election", "elections", "congress", "senate", "house", "president", "presidential",
                 "governor", "primary", "parliament", "trump", "democrat", "democrats", "republican", "republicans", "mayor",
                 "supreme court", "impeach", "cabinet", "poll"],
    "economics": ["economy", "economics", "economic", "fed", "fomc", "inflation", "cpi", "rates", "rate cut", "gdp", "recession",
                  "finance", "financials", "crypto", "bitcoin", "stock", "stocks", "s&p", "nasdaq", "ipo", "earnings", "jobs",
                  "unemployment", "payrolls", "tariff", "tariffs", "treasury", "oil price", "company", "companies"],
}
_KW_RE = {d: re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(k) for k in ks) + r")(?![a-z0-9])") for d, ks in DOMAIN_KEYWORDS.items()}


def classify_domain(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for dom in ("sports", "geopolitics", "politics", "economics"):
        if _KW_RE[dom].search(blob):
            return dom
    return "other"
