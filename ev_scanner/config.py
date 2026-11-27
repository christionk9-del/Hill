"""Configuration loading. Everything is overridable from config.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    # Alert when a bet is at or above this EV (20% = 0.20).
    "ev_threshold": 0.20,
    # Also list (but don't alert on) bets above this, for context in the report.
    "report_threshold": 0.03,
    # Bonus-bet conversion rate that counts as "alert-worthy" (70% = 0.70).
    "bonus_conversion_threshold": 0.70,
    "timezone": "America/New_York",
    "sports": [
        "americanfootball_nfl",
        "americanfootball_ncaaf",
        "basketball_nba",
        "basketball_ncaab",
        "icehockey_nhl",
        "baseball_mlb",
        "soccer_epl",
        "mma_mixed_martial_arts",
    ],
    # Game-line markets (cost: 1 credit per market per region per sport).
    "markets": ["h2h", "spreads", "totals"],
    # Odds API regions. us_ex = exchanges (Kalshi, Novig, ProphetX...),
    # us_dfs = pick'em apps (PrizePicks, Underdog, Pick6, Betr), eu = Pinnacle.
    "regions": ["us", "us2", "us_ex", "eu"],
    # Books whose de-vigged line is treated as the "true" price, in priority order.
    "sharp_books": ["pinnacle", "circasports", "novig", "prophetx", "betonlineag", "lowvig"],
    # If no sharp book has the market, fall back to the median of all books'
    # de-vigged prices, but only when at least this many books have it.
    "min_books_for_consensus": 4,
    # Books you actually have accounts with. Empty = all books.
    "my_books": [],
    # Ignore anything starting more than this many hours from now.
    "max_hours_ahead": 48,
    # Ignore prices longer than this (huge longshots produce noisy EV).
    "max_decimal_odds": 6.0,
    "kalshi": {"enabled": True, "fee_rate": 0.07,
               "series": ["KXNFLGAME", "KXNBAGAME", "KXNHLGAME", "KXNCAAFGAME",
                          "KXNCAAMBGAME", "KXMLBGAME"]},
    # Pick'em / DFS player props (PrizePicks, Underdog...). Props are billed
    # per event so this is capped by max_events_per_sport.
    "dfs": {
        "enabled": True,
        "sports": ["basketball_nba", "americanfootball_nfl", "icehockey_nhl"],
        "markets": ["player_points", "player_rebounds", "player_assists",
                    "player_pass_yds", "player_rush_yds", "player_reception_yds",
                    "player_receptions", "player_shots_on_goal"],
        "max_events_per_sport": 6,
        # Per-leg break-even win probability for the payout you play.
        # PrizePicks 5/6-pick flex ~= 0.542, 2-pick power (3x) = 0.577,
        # Underdog 3-pick (6x) = 0.550.
        "breakeven": {"prizepicks": 0.542, "underdog": 0.550, "pick6": 0.550,
                      "betr_us_dfs": 0.550, "default": 0.550},
    },
    "promo_feeds": [],
}


@dataclass
class Config:
    raw: dict = field(default_factory=dict)

    def __getattr__(self, item):
        try:
            return self.raw[item]
        except KeyError as e:
            raise AttributeError(item) from e


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.json"
    data = DEFAULTS
    if path.exists():
        data = _merge(DEFAULTS, json.loads(path.read_text()))
    return Config(data)


def load_promos(path: str | os.PathLike | None = None) -> list[dict]:
    path = Path(path) if path else ROOT / "promos.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [p for p in data.get("promos", []) if p.get("active", True)]
