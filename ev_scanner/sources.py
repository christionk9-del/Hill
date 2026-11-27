"""Data sources: The Odds API (most US books, exchanges, DFS apps, Pinnacle)
and Kalshi's public trade API (no key needed)."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ODDS_API = "https://api.the-odds-api.com/v4"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
UA = "ev-scanner/1.0"


class SourceError(RuntimeError):
    pass


def _get_json(url: str, params: dict | None = None, retries: int = 3):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode()), dict(r.headers)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in (401, 403, 422):
                raise SourceError(f"HTTP {e.code} from {url.split('?')[0]}: {body}") from e
            last = SourceError(f"HTTP {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError) as e:
            last = SourceError(str(e))
        time.sleep(2 ** attempt)
    raise last


# --------------------------------------------------------------------------- #
# The Odds API
# --------------------------------------------------------------------------- #
class OddsAPI:
    def __init__(self, api_key: str | None = None):
        # When running in an environment whose proxy injects the key, any
        # placeholder works; otherwise set ODDS_API_KEY.
        self.key = api_key or os.environ.get("ODDS_API_KEY") or "proxy-injected"
        self.remaining = None
        self.used = None

    def _call(self, path: str, **params):
        params["apiKey"] = self.key
        data, headers = _get_json(f"{ODDS_API}{path}", params)
        h = {k.lower(): v for k, v in headers.items()}
        self.remaining = h.get("x-requests-remaining", self.remaining)
        self.used = h.get("x-requests-used", self.used)
        return data

    def active_sports(self) -> set[str]:
        return {s["key"] for s in self._call("/sports") if s.get("active")}

    def odds(self, sport: str, regions: list[str], markets: list[str]) -> list[dict]:
        return self._call(f"/sports/{sport}/odds", regions=",".join(regions),
                          markets=",".join(markets), oddsFormat="decimal",
                          dateFormat="iso")

    def events(self, sport: str) -> list[dict]:
        return self._call(f"/sports/{sport}/events", dateFormat="iso")

    def event_odds(self, sport: str, event_id: str, regions: list[str],
                   markets: list[str]) -> dict:
        return self._call(f"/sports/{sport}/events/{event_id}/odds",
                          regions=",".join(regions), markets=",".join(markets),
                          oddsFormat="decimal", dateFormat="iso")


# --------------------------------------------------------------------------- #
# Kalshi (public, unauthenticated market data)
# --------------------------------------------------------------------------- #
def kalshi_game_markets(series: list[str]) -> list[dict]:
    """Returns Kalshi game-winner markets grouped per event:
    [{event_ticker, expires, sides: [{team, ticker, yes_ask, yes_bid}]}]"""
    events: dict[str, dict] = {}
    for s in series:
        cursor = None
        for _ in range(10):
            params = {"series_ticker": s, "status": "open", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            try:
                data, _ = _get_json(f"{KALSHI_API}/markets", params)
            except SourceError as e:
                print(f"[kalshi] {s}: {e}", file=sys.stderr)
                break
            for m in data.get("markets", []):
                ask = _f(m.get("yes_ask_dollars"))
                if ask is None and m.get("yes_ask") is not None:
                    ask = m["yes_ask"] / 100
                if not ask or ask <= 0 or ask >= 1:
                    continue
                ev = events.setdefault(m["event_ticker"], {
                    "event_ticker": m["event_ticker"], "series": s,
                    "expires": m.get("expected_expiration_time") or m.get("close_time"),
                    "sides": []})
                ev["sides"].append({"team": m.get("yes_sub_title") or m.get("title", ""),
                                    "ticker": m["ticker"], "yes_ask": ask,
                                    "yes_bid": _f(m.get("yes_bid_dollars"))})
            cursor = data.get("cursor")
            if not cursor:
                break
    return [e for e in events.values() if len(e["sides"]) >= 2]


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def kalshi_team_matches(kalshi_name: str, full_name: str) -> bool:
    """Kalshi uses city names ('Sacramento', 'Los Angeles L', 'New York J')
    while sportsbooks use 'Sacramento Kings'. Match on prefix + nickname initial."""
    k, f = _norm(kalshi_name), _norm(full_name)
    if not k or not f:
        return False
    if k == f or f.startswith(k + " ") and len(k) > 3:
        return True
    parts = k.split()
    if len(parts) >= 2 and len(parts[-1]) == 1:
        city, initial = " ".join(parts[:-1]), parts[-1]
        if f.startswith(city + " "):
            nickname = f[len(city) + 1:]
            return nickname.startswith(initial)
    return k in f and len(k) >= 5


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
