"""+EV finder: compare Kalshi prices to devigged SHARP sportsbook lines.

Fair probability comes from a sharp book (Pinnacle by default) via The Odds API
(https://the-odds-api.com, set ODDS_API_KEY). The bookmaker margin is removed
("devig"), then each Kalshi game-winner / spread / total market is matched to the
same line. A leg is +EV when fair_prob - (Kalshi ask + fee) >= min_edge.

The +EV legs feed the combo builder (`--source sharp`): combo fair value is the
product of fair probs of legs from DIFFERENT games, compared to the RFQ quote.

Only exact line matches are used (Kalshi "over 7.5" vs sharp -7.5). Props are not
matched yet: sharp books post few, and US-book prop lines are not sharp.
"""
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

from .client import cents

ODDS_API = "https://api.the-odds-api.com/v4"
SPORTS = {"KXNBA": "basketball_nba", "KXNFL": "americanfootball_nfl", "KXNHL": "icehockey_nhl",
          "KXMLB": "baseball_mlb", "KXNCAAF": "americanfootball_ncaaf", "KXNCAAMB": "basketball_ncaab"}


# ---------------- devig ----------------
def devig_two_way(dec_a: float, dec_b: float, method: str = "power"):
    """Decimal odds -> fair probabilities summing to 1."""
    ia, ib = 1 / dec_a, 1 / dec_b
    if method == "multiplicative":
        s = ia + ib
        return ia / s, ib / s
    # power method: find k with ia^k + ib^k = 1 (handles favourite-longshot skew better)
    lo, hi = 1.0, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        if ia ** k + ib ** k > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    return ia ** k, ib ** k


# ---------------- sharp lines ----------------
@dataclass
class SharpLine:
    sport: str
    home: str
    away: str
    commence: datetime
    kind: str          # h2h | spreads | totals
    outcome: str       # team name, or Over/Under
    point: float | None
    fair: float


def parse_odds_api(events, sport, book="pinnacle", method="power"):
    out = []
    for ev in events:
        commence = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        bm = next((b for b in ev.get("bookmakers", []) if b["key"] == book), None)
        if not bm:
            continue
        for mk in bm.get("markets", []):
            oc = mk.get("outcomes", [])
            if len(oc) != 2:
                continue
            fa, fb = devig_two_way(oc[0]["price"], oc[1]["price"], method)
            for o, f in ((oc[0], fa), (oc[1], fb)):
                out.append(SharpLine(sport, ev["home_team"], ev["away_team"], commence, mk["key"],
                                     o["name"], o.get("point"), f))
    return out


def fetch_sharp(sport_key, api_key=None, book="pinnacle", session=None):
    api_key = api_key or os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        raise RuntimeError("Set ODDS_API_KEY (https://the-odds-api.com).")
    s = session or requests
    r = s.get(f"{ODDS_API}/sports/{sport_key}/odds", timeout=20, params={
        "apiKey": api_key, "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal", "bookmakers": book})
    r.raise_for_status()
    return parse_odds_api(r.json(), sport_key, book)


# ---------------- matching Kalshi markets to sharp lines ----------------
def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def team_match(label, home, away):
    """Return 'home'/'away' if Kalshi label ('New York', 'Knicks', 'NY Knicks') names exactly one team."""
    l = _norm(label)
    if not l:
        return None
    hits = []
    for side, name in (("home", home), ("away", away)):
        n = _norm(name)
        words = set(n.split())
        if l in n or n in l or (words & set(l.split()) - {"new", "los", "san", "st", "city", "state"}):
            hits.append(side)
    return hits[0] if len(hits) == 1 else None


SPREAD_RE = re.compile(r"^(.*?)\s+wins?\s+by\s+over\s+([\d.]+)", re.I)
TOTAL_RE = re.compile(r"over\s+([\d.]+)", re.I)


MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
TICKER_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")


def game_date(event_ticker):
    """'KXNBAGAME-26NOV20CHAATL' -> date(2026, 11, 20). Kalshi markets close days after the
    game, so the ticker date (US local game date) is the reliable key."""
    m = TICKER_DATE.search(event_ticker)
    if not m or m.group(2) not in MONTHS:
        return None
    from datetime import date
    return date(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)))


def _local_date(dt):
    from zoneinfo import ZoneInfo
    return dt.astimezone(ZoneInfo("America/New_York")).date()


def match_market(market, event, lines):
    """Return fair P(YES) for a Kalshi market, or None if no exact sharp match."""
    gd = game_date(event.get("event_ticker", market["ticker"]))
    if gd is None:
        return None
    title = f"{event.get('title', '')} {market.get('title', '')}"
    games = {}
    for ln in lines:
        if _local_date(ln.commence) != gd:
            continue
        if team_match_in_title(title, ln.home) and team_match_in_title(title, ln.away):
            games.setdefault((ln.home, ln.away, ln.commence), []).append(ln)
    if len(games) != 1:
        return None
    (home, away, _), glines = next(iter(games.items()))
    series = event.get("series_ticker", market["ticker"].split("-")[0])
    sub = market.get("yes_sub_title") or market.get("subtitle") or ""

    if series.endswith("GAME"):
        side = team_match(sub, home, away)
        team = {"home": home, "away": away}.get(side)
        return next((l.fair for l in glines if l.kind == "h2h" and l.outcome == team), None)
    if series.endswith("SPREAD"):
        m = SPREAD_RE.search(sub) or SPREAD_RE.search(market.get("title", ""))
        if not m:
            return None
        side = team_match(m.group(1), home, away)
        team, pt = {"home": home, "away": away}.get(side), float(m.group(2))
        return next((l.fair for l in glines if l.kind == "spreads" and l.outcome == team
                     and l.point is not None and abs(l.point + pt) < 1e-9), None)
    if series.endswith("TOTAL"):
        m = TOTAL_RE.search(sub) or TOTAL_RE.search(market.get("title", ""))
        if not m:
            return None
        pt = float(m.group(1))
        return next((l.fair for l in glines if l.kind == "totals" and l.outcome == "Over"
                     and l.point is not None and abs(l.point - pt) < 1e-9), None)
    return None


def team_match_in_title(title, team):
    t, words = _norm(title), _norm(team).split()
    nickname = words[-1] if words else ""
    city = " ".join(words[:-1])
    return (nickname and nickname in t) or (city and city in t)


# ---------------- EV legs ----------------
@dataclass
class EVLeg:
    event_ticker: str
    market_ticker: str
    side: str
    price_c: int
    fair: float

    @property
    def cost(self):
        p = self.price_c / 100
        return p * (1 + 0.07 * (1 - p))

    @property
    def edge(self):
        return self.fair - self.cost


def find_ev_legs(kalshi_events, lines, min_edge=0.02, min_price_c=5, max_price_c=95):
    out = []
    for ev in kalshi_events:
        for m in ev.get("markets", []):
            if m.get("status") not in ("active", "open"):
                continue
            fair_yes = match_market(m, ev, lines)
            if fair_yes is None:
                continue
            ya, yb = cents(m, "yes_ask"), cents(m, "yes_bid")
            for side, px, fair in (("yes", ya, fair_yes), ("no", None if yb is None else 100 - yb, 1 - fair_yes)):
                if px and min_price_c <= px <= max_price_c:
                    leg = EVLeg(ev["event_ticker"], m["ticker"], side, px, fair)
                    if leg.edge >= min_edge:
                        out.append(leg)
    return sorted(out, key=lambda l: l.edge, reverse=True)


def sharp_prob_lookup(kalshi_events, lines):
    """{(market_ticker, side): fair prob} for every matched market (for the combo builder)."""
    table = {}
    for ev in kalshi_events:
        for m in ev.get("markets", []):
            f = match_market(m, ev, lines)
            if f is not None:
                table[(m["ticker"], "yes")] = f
                table[(m["ticker"], "no")] = 1 - f
    return table


def sports_in(events):
    keys = set()
    for ev in events:
        s = ev.get("series_ticker") or ev["event_ticker"].split("-")[0]
        for prefix, key in SPORTS.items():
            if s.startswith(prefix):
                keys.add(key)
    return keys
