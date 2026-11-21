"""The Odds API (the-odds-api.com): 40+ sportsbooks incl. Pinnacle & Betfair.
Free tier = 500 credits/month. Cost per call = (#markets) x (#regions).
This is the same raw material paid screens like OddsJam use."""
from __future__ import annotations

from datetime import datetime

from .base import Quote, Source, env_or, http_json, iso, parse_iso

BASE = "https://api.the-odds-api.com/v4"


class OddsAPI(Source):
    name = "oddsapi"

    def __init__(self, api_key: str | None = None, sports=("basketball_nba", "americanfootball_nfl", "icehockey_nhl", "baseball_mlb", "soccer_epl"),
                 regions=("us", "eu"), markets=("h2h", "spreads", "totals"), **_):
        self.key = env_or(api_key, "ODDS_API_KEY")
        self.sports, self.regions, self.markets = list(sports), list(regions), list(markets)

    def fetch(self, now: datetime) -> list[Quote]:
        if not self.key:
            raise RuntimeError("oddsapi: set ODDS_API_KEY (free key at the-odds-api.com)")
        out: list[Quote] = []
        for sport in self.sports:
            events = http_json(f"{BASE}/sports/{sport}/odds", {
                "apiKey": self.key, "regions": ",".join(self.regions),
                "markets": ",".join(self.markets), "oddsFormat": "decimal"})
            for ev in events:
                if parse_iso(ev["commence_time"]) <= now:
                    continue  # pre-match only
                name = f"{ev['away_team']} @ {ev['home_team']}"
                for bk in ev.get("bookmakers", []):
                    for mk in bk.get("markets", []):
                        out.extend(self._quotes(sport, ev, name, bk["key"], mk))
        return out

    def _quotes(self, sport, ev, name, book, mk):
        kind = mk["key"]
        outs = mk.get("outcomes", [])
        if kind == "h2h":
            mkey = "h2h"
        elif kind == "totals":
            pts = {o.get("point") for o in outs}
            if len(pts) != 1:
                return []
            mkey = f"totals:{pts.pop()}"
        elif kind == "spreads":
            home = [o for o in outs if o["name"] == ev["home_team"]]
            if not home:
                return []
            mkey = f"spreads:{home[0].get('point')}"
        else:
            return []
        res = []
        for o in outs:
            label = o["name"] if kind != "spreads" else f"{o['name']} {o.get('point', 0):+g}"
            res.append(Quote(
                source=self.name, venue=book, domain="sports", event_id=f"oa:{ev['id']}",
                event_name=name, market_key=mkey, outcome=label, price=float(o["price"]),
                start_time=ev["commence_time"], bettable=True,
                ref={"sport": sport, "id": ev["id"], "kind": kind, "name": o["name"], "point": o.get("point")}))
        return res

    def results(self, pending, now):
        if not self.key:
            return {}
        sports = {p["ref"]["sport"] for p in pending}
        scores = {}
        for sport in sports:
            for g in http_json(f"{BASE}/sports/{sport}/scores", {"apiKey": self.key, "daysFrom": 3}):
                if g.get("completed") and g.get("scores"):
                    scores[g["id"]] = {s["name"]: float(s["score"]) for s in g["scores"]}
        out = {}
        for p in pending:
            r = p["ref"]
            sc = scores.get(r["id"])
            if not sc or len(sc) != 2:
                continue
            mine = sc.get(r["name"])
            other = [v for k, v in sc.items() if k != r["name"]]
            if r["kind"] == "h2h":
                if r["name"] == "Draw":
                    a, b = list(sc.values())
                    out[(p["group_id"], p["outcome"])] = 1.0 if a == b else 0.0
                elif mine is not None:
                    out[(p["group_id"], p["outcome"])] = 1.0 if mine > other[0] else 0.0
            elif r["kind"] == "totals":
                tot = sum(sc.values())
                pt = float(r["point"])
                if tot == pt:
                    out[(p["group_id"], p["outcome"])] = None
                else:
                    over = tot > pt
                    out[(p["group_id"], p["outcome"])] = 1.0 if (r["name"] == "Over") == over else 0.0
            elif r["kind"] == "spreads" and mine is not None:
                margin = mine + float(r["point"]) - other[0]
                out[(p["group_id"], p["outcome"])] = None if margin == 0 else (1.0 if margin > 0 else 0.0)
        return out
