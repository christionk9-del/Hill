from datetime import datetime, timedelta, timezone

from kalshi_bot import ev_finder as evf
from kalshi_bot.combo_builder import build_combo, leg_pool

T0 = datetime(2026, 1, 20, 0, 30, tzinfo=timezone.utc)


def test_devig_sums_to_one_and_removes_vig():
    a, b = evf.devig_two_way(1.91, 1.91)
    assert abs(a + b - 1) < 1e-9 and abs(a - 0.5) < 1e-9
    fav, dog = evf.devig_two_way(1.40, 3.10)
    assert abs(fav + dog - 1) < 1e-9 and fav < 1 / 1.40     # fair < raw implied


def odds_api_payload():
    return [{"commence_time": "2026-01-20T00:30:00Z", "home_team": "Minnesota Timberwolves",
             "away_team": "New York Knicks", "bookmakers": [{"key": "pinnacle", "markets": [
                 {"key": "h2h", "outcomes": [{"name": "Minnesota Timberwolves", "price": 1.70},
                                             {"name": "New York Knicks", "price": 2.25}]},
                 {"key": "spreads", "outcomes": [{"name": "Minnesota Timberwolves", "price": 1.95, "point": -3.5},
                                                 {"name": "New York Knicks", "price": 1.95, "point": 3.5}]},
                 {"key": "totals", "outcomes": [{"name": "Over", "price": 1.80, "point": 221.5},
                                                {"name": "Under", "price": 2.05, "point": 221.5}]}]}]}]


def kalshi_events(game_ask=50):
    close = (T0 + timedelta(days=3)).isoformat().replace("+00:00", "Z")   # Kalshi closes days later
    mk = lambda t, sub, ask, bid: {"ticker": t, "status": "active", "close_time": close, "yes_sub_title": sub,
                                   "title": "", "yes_ask": ask, "yes_bid": bid}
    return [
        {"event_ticker": "KXNBAGAME-26JAN19NYKMIN", "series_ticker": "KXNBAGAME",
         "title": "New York vs Minnesota Winner?", "markets": [
             mk("KXNBAGAME-26JAN19NYKMIN-MIN", "Minnesota", game_ask, game_ask - 2),
             mk("KXNBAGAME-26JAN19NYKMIN-NYK", "New York", 44, 42)]},
        {"event_ticker": "KXNBASPREAD-26JAN19NYKMIN", "series_ticker": "KXNBASPREAD",
         "title": "New York vs Minnesota: Spread", "markets": [
             mk("KXNBASPREAD-26JAN19NYKMIN-MIN3", "Minnesota wins by over 3.5 Points", 47, 45)]},
        {"event_ticker": "KXNBATOTAL-26JAN19NYKMIN", "series_ticker": "KXNBATOTAL",
         "title": "New York vs Minnesota: Total Points", "markets": [
             mk("KXNBATOTAL-26JAN19NYKMIN-221", "Over 221.5 points scored", 55, 53)]},
    ]


def test_matching_game_spread_total():
    lines = evf.parse_odds_api(odds_api_payload(), "basketball_nba")
    evs = kalshi_events()
    look = evf.sharp_prob_lookup(evs, lines)
    p_min = look[("KXNBAGAME-26JAN19NYKMIN-MIN", "yes")]
    assert 0.55 < p_min < 0.58                                  # devigged 1.70 vs 2.25
    assert abs(look[("KXNBASPREAD-26JAN19NYKMIN-MIN3", "yes")] - 0.5) < 1e-6
    assert 0.52 < look[("KXNBATOTAL-26JAN19NYKMIN-221", "yes")] < 0.55


def test_find_ev_legs_flags_only_real_gaps():
    lines = evf.parse_odds_api(odds_api_payload(), "basketball_nba")
    legs = evf.find_ev_legs(kalshi_events(game_ask=50), lines, min_edge=0.02)
    assert [l.market_ticker for l in legs] == ["KXNBAGAME-26JAN19NYKMIN-MIN"]   # 56.5% fair vs 50c ask
    assert evf.find_ev_legs(kalshi_events(game_ask=56), lines, min_edge=0.02) == []


def test_game_date_from_ticker():
    from datetime import date
    assert evf.game_date("KXNBAGAME-26NOV20CHAATL") == date(2026, 11, 20)
    assert evf._local_date(T0) == date(2026, 1, 19)       # 00:30 UTC = previous evening ET


def test_team_match_requires_unique_team():
    assert evf.team_match("New York", "Minnesota Timberwolves", "New York Knicks") == "away"
    assert evf.team_match("Los Angeles", "Los Angeles Lakers", "Los Angeles Clippers") is None


def test_combo_pool_uses_sharp_lookup():
    from dataclasses import replace
    from kalshi_bot.config import CONFIG
    cfg = replace(CONFIG, combo_min_legs=1, combo_leg_min_edge=0.02)
    lines = evf.parse_odds_api(odds_api_payload(), "basketball_nba")
    evs = kalshi_events(game_ask=50)
    look = evf.sharp_prob_lookup(evs, lines)
    coll = {"collection_ticker": "C", "associated_events": [{"ticker": e["event_ticker"]} for e in evs]}
    pool = leg_pool(coll, {e["event_ticker"]: e for e in evs}, {}, cfg,
                    now=T0 - timedelta(hours=1), prob_lookup=look)
    assert {l.market_ticker for l in pool} == {"KXNBAGAME-26JAN19NYKMIN-MIN", "KXNBASPREAD-26JAN19NYKMIN-MIN3"}
    plan = build_combo(coll, pool, cfg)
    assert len(plan.legs) == 1          # same game -> correlated -> only one leg allowed
