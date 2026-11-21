#!/usr/bin/env python3
"""Arb Scanner: guaranteed-profit (arbitrage) and near-arb checks across venues.

  1. Kalshi vs sportsbooks: full-game moneylines (The Odds API, US books) vs Kalshi game contracts.
     Every way to cover all outcomes exactly once (Kalshi YES, Kalshi NO = "anyone but", book
     moneyline) is priced; total cost < $1 per $1 payout = arb.
  2. Kalshi vs Polymarket: same question on both. Kalshi YES + Polymarket NO (or the reverse) < $1.
  3. Inside Kalshi: complete outcome sets cheaper than their guaranteed payout (from open_ev_finder).

All costs include fees: Kalshi taker fee rounded up per order; Polymarket taker fee (conservative
estimate rate*min(p,1-p) per share where fees are enabled); exchange commission on Betfair-style books.
Robinhood event contracts are Kalshi contracts (same order book), so they are covered by the Kalshi legs.

Stdlib only. The Odds API key is read from ODDS_API_KEY, or injected by a proxy (x-api-key header).
Usage: python3 arb_scanner.py [--out arb_report.md] [--json arbs.json] [--cache kalshi_events.json]
                              [--sports-days 3] [--no-sports] [--no-polymarket] [--order 100]
"""
from __future__ import annotations

import argparse
import gzip
import itertools
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import open_ev_finder as oef  # noqa: E402

ODDS = "https://api.the-odds-api.com/v4"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

# Kalshi full-game series -> The Odds API sport key
SPORT_MAP = {
    "KXNFLGAME": "americanfootball_nfl", "KXNCAAFGAME": "americanfootball_ncaaf",
    "KXNBAGAME": "basketball_nba", "KXNCAAMBGAME": "basketball_ncaab", "KXNCAAWBGAME": "basketball_wncaab",
    "KXWNBAGAME": "basketball_wnba", "KXNHLGAME": "icehockey_nhl", "KXAHLGAME": "icehockey_ahl",
    "KXMLBGAME": "baseball_mlb", "KXEPLGAME": "soccer_epl", "KXLALIGAGAME": "soccer_spain_la_liga",
    "KXSERIEAGAME": "soccer_italy_serie_a", "KXBUNDESLIGAGAME": "soccer_germany_bundesliga",
    "KXLIGUE1GAME": "soccer_france_ligue_one", "KXUCLGAME": "soccer_uefa_champs_league",
    "KXUELGAME": "soccer_uefa_europa_league", "KXMLSGAME": "soccer_usa_mls", "KXUFCFIGHT": "mma_mixed_martial_arts",
}
# Leagues where a 2-way moneyline can still end level: books usually refund, Kalshi may resolve differently
TIE_RISK = {"americanfootball_nfl": "NFL games can end in a tie: check both venues' tie rules",
            "americanfootball_ncaaf": "", "mma_mixed_martial_arts": "MMA draws/no-contests: check both rule sets"}
EXCHANGE_COMMISSION = {"betfair_ex_eu": 0.05, "betfair_ex_uk": 0.05, "betfair_ex_au": 0.05,
                       "matchbook": 0.02, "smarkets": 0.02, "betopenly": 0.01, "novig": 0.0, "prophetx": 0.0}


def http(url, params=None, headers=None, retries=4):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    h = {"User-Agent": "edgelab-arb/1.0", "Accept": "application/json", "Accept-Encoding": "gzip", **(headers or {})}
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < retries - 1:
                time.sleep(2 * (i + 1))
                continue
            body = e.read().decode(errors="replace")[:200]
            raise RuntimeError(f"HTTP {e.code} from {url.split('?')[0]}: {body}") from None


# ------------------------------------------------------------------ helpers
def norm_tokens(name: str) -> list[str]:
    s = name.lower().replace("&", " and ").replace("reg time:", " ")
    s = re.sub(r"\bst\.?(?=\s|$)", "state", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return [t for t in s.split() if t not in {"the", "fc", "cf", "afc", "sc", "vs", "at"}]


def team_match(kalshi_name: str, book_name: str) -> bool:
    """Every Kalshi token must prefix-match a book token ('Los Angeles L' ~ 'Los Angeles Lakers')."""
    kt, bt = norm_tokens(kalshi_name), norm_tokens(book_name)
    return bool(kt) and all(any(b.startswith(k) for b in bt) for k in kt)


def poly_fee(p: float, market: dict) -> float:
    if not market.get("feesEnabled"):
        return 0.0
    rate = (market.get("feeSchedule") or {}).get("rate", 0.01)
    return rate * min(p, 1 - p)  # conservative per-share estimate


def partitions(items):
    items = list(items)
    if not items:
        yield []
        return
    first, rest = items[0], items[1:]
    for part in partitions(rest):
        for i in range(len(part)):
            yield part[:i] + [[first] + part[i]] + part[i + 1:]
        yield [[first]] + part


def best_cover(outcomes: list[str], instruments: list[dict]):
    """instruments: {covers: frozenset, cost: $ per $1 payout, venue, ...}. Cheapest exact cover of all
    outcomes that uses at least two venues (a single venue's own set is just its margin, or is handled by
    the inside-Kalshi scan)."""
    best = None
    for part in partitions(outcomes):
        if len(part) < 2:
            continue
        options = []
        for block in part:
            fs = frozenset(block)
            cands = sorted((i for i in instruments if i["covers"] == fs), key=lambda i: i["cost"])
            if not cands:
                break
            options.append(cands[:6])  # cheapest few per block is enough
        else:
            for legs in itertools.product(*options):
                if len({l["venue"] for l in legs}) < 2:
                    continue
                tot = sum(l["cost"] for l in legs)
                if best is None or tot < best[0]:
                    best = (tot, list(legs))
    return best


# ------------------------------------------------------------------ 1. Kalshi vs sportsbooks
def kalshi_games(events, now, days):
    out = []
    for ev in events:
        sport = SPORT_MAP.get(ev.get("series_ticker", ""))
        if not sport or not ev.get("mutually_exclusive"):
            continue
        ms = [m for m in ev.get("markets", []) if m.get("status") in ("active", "open")]
        if len(ms) not in (2, 3):
            continue
        t = oef.parse_t(ms[0].get("expected_expiration_time"))
        if not t or not (now < t < now + timedelta(days=days)):
            continue
        out.append({"event": ev, "sport": sport, "markets": ms, "time": t})
    return out


def fetch_odds(sports, regions="us,us2"):
    key = os.environ.get("ODDS_API_KEY", "")
    games, remaining, errors = {}, None, []
    for sp in sorted(sports):
        params = {"regions": regions, "markets": "h2h", "oddsFormat": "decimal"}
        if key and key != "proxy-injected":
            params["apiKey"] = key
        try:
            data, hdr = http(f"{ODDS}/sports/{sp}/odds", params)
            games[sp] = data
            remaining = hdr.get("x-requests-remaining", remaining)
        except Exception as e:
            errors.append(f"{sp}: {e}")
    return games, remaining, errors


def sports_arbs(events, now, order_usd, days, odds=None):
    kg = kalshi_games(events, now, days)
    if odds is None:
        odds, remaining, errors = fetch_odds({g["sport"] for g in kg})
    else:
        remaining, errors = None, []
    results, matched = [], 0
    for g in kg:
        ms, ev = g["markets"], g["event"]
        names = [re.sub(r"^Reg Time:\s*", "", m.get("yes_sub_title") or "") for m in ms]
        is_tie = [n.lower() in ("tie", "draw") for n in names]
        game = None
        for bg in odds.get(g["sport"], []):
            ct = oef.parse_t(bg.get("commence_time"))
            if not ct or not (g["time"] - timedelta(hours=30) <= ct <= g["time"] + timedelta(hours=2)):
                continue
            teams = [n for n, t in zip(names, is_tie) if not t]
            if len(teams) == 2 and ((team_match(teams[0], bg["home_team"]) and team_match(teams[1], bg["away_team"])) or
                                    (team_match(teams[0], bg["away_team"]) and team_match(teams[1], bg["home_team"]))):
                game = bg
                break
        if not game:
            continue
        matched += 1
        # outcome keys = Kalshi names; map book outcome names onto them
        def key_for(book_name):
            if book_name.lower() == "draw":
                return next((n for n, t in zip(names, is_tie) if t), None)
            return next((n for n, t in zip(names, is_tie) if not t and team_match(n, book_name)), None)
        outcomes = names
        inst = []
        for m, n in zip(ms, names):
            ya, yb = oef.f(m.get("yes_ask_dollars")), oef.f(m.get("yes_bid_dollars"))
            if 0 < ya < 1:
                inst.append({"covers": frozenset([n]), "cost": oef.all_in_cost(ya, order_usd), "venue": "Kalshi",
                             "label": f"Kalshi YES {n} @ {ya*100:.0f}¢", "depth": oef.f(m.get("yes_ask_size_fp")) * ya,
                             "ticker": m["ticker"]})
            if 0 < yb < 1:
                na = 1 - yb
                inst.append({"covers": frozenset(x for x in names if x != n), "cost": oef.all_in_cost(na, order_usd),
                             "venue": "Kalshi", "label": f"Kalshi NO {n} @ {na*100:.0f}¢",
                             "depth": oef.f(m.get("yes_bid_size_fp")) * na, "ticker": m["ticker"]})
        n_book_outcomes = None
        for bk in game.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") != "h2h":
                    continue
                n_book_outcomes = len(mk["outcomes"])
                comm = EXCHANGE_COMMISSION.get(bk["key"], 0.0)
                for o in mk["outcomes"]:
                    k = key_for(o["name"])
                    if not k:
                        continue
                    d = 1 + (float(o["price"]) - 1) * (1 - comm)
                    inst.append({"covers": frozenset([k]), "cost": 1 / d, "venue": bk["title"],
                                 "label": f"{bk['title']} {o['name']} @ {float(o['price']):.2f}", "depth": None,
                                 "book_key": bk["key"]})
        if n_book_outcomes and n_book_outcomes != len(outcomes):
            continue  # e.g. Kalshi 3-way regulation vs a 2-way book line: not the same bet
        bc = best_cover(outcomes, inst)
        if not bc:
            continue
        tot, legs = bc
        if not any(l["venue"] == "Kalshi" for l in legs):
            kind = "book vs book"
        else:
            kind = "Kalshi vs book" if any(l["venue"] != "Kalshi" for l in legs) else "Kalshi only"
        flags = []
        if TIE_RISK.get(g["sport"]) and len(outcomes) == 2:
            flags.append(TIE_RISK[g["sport"]])
        if len(outcomes) == 2:
            flags.append("Check overtime/extra-time is included on both sides")
        kd = [l["depth"] for l in legs if l["depth"] is not None]
        results.append({"type": kind, "sport": g["sport"], "event": ev.get("title"),
                        "game_time": oef.parse_t(game["commence_time"]).strftime("%a %b %d %H:%M UTC"),
                        "total_cost": tot, "profit_pct": 1 / tot - 1, "legs": [
                            {k: v for k, v in l.items() if k != "covers"} | {"covers": sorted(l["covers"]),
                                                                            "stake_per_100": round(100 * l["cost"], 2)}
                            for l in legs],
                        "kalshi_max_payout": min(kd) / min(l["cost"] for l in legs if l["depth"] is not None) if kd else None,
                        "flags": flags})
    results.sort(key=lambda r: r["total_cost"])
    return results, {"kalshi_games": len(kg), "matched": matched, "credits_remaining": remaining, "errors": errors}


# ------------------------------------------------------------------ 2. Kalshi vs Polymarket
STOP = {"will", "the", "a", "an", "of", "in", "on", "by", "to", "be", "before", "after", "at", "for", "and", "or",
        "who", "what", "which", "win", "winner", "yes", "no", "than", "more", "less", "this", "is"}


SYNONYM = {"nominee": "nomination", "nominated": "nomination", "elected": "election", "wins": "win", "won": "win",
           "us": "u.s.", "usa": "u.s.", "president": "presidential", "pres": "presidential", "tomatometer": "tomatometer"}
# words that change WHAT is being asked; if only one side has one, it's a different question
HARD = {"finalist", "finalists", "vice", "vp", "vice-presidential", "popcornmeter", "audience", "critics", "tomatometer",
        "coding", "direction", "narrative", "software", "configuration", "human", "primary", "runoff", "caucus", "first",
        "round", "half", "quarter", "period", "map", "set", "seat", "seats", "margin", "turnout", "senate", "house",
        "governor", "mayor", "nomination", "election", "approval", "cut", "hike", "pause", "hold", "top", "relegated",
        "relegation", "advance", "champion", "championship", "mvp", "rookie", "coach", "finals", "next"}
POS = re.compile(r"\b(at least|or more|or higher|above|over|more than|greater than|exceed|exceeds)\b")
NEG = re.compile(r"\b(less than|fewer than|below|under|or less|or lower|not)\b")


def toks(s):
    out = set()
    for w in re.findall(r"[a-z0-9%$.\-]+", (s or "").lower().replace(",", "")):
        w = SYNONYM.get(w.strip(".-"), w.strip(".-"))
        if w and w not in STOP and len(w) > 1:
            out.add(w)
    return out


def polarity(s):
    s = (s or "").lower()
    return -1 if NEG.search(s) else (1 if POS.search(s) else 0)


def nums(s):
    return set(re.findall(r"\d+(?:\.\d+)?", (s or "").replace(",", "")))


def fetch_polymarket(max_pages=12, min_liq=1000):
    out, cursor = [], None
    for page in range(max_pages):
        q = {"active": "true", "closed": "false", "limit": 200, "order": "volume24hr", "ascending": "false"}
        if cursor:
            q["after_cursor"] = cursor
        resp, _ = http(f"{GAMMA}/events/keyset", q)
        data, cursor = resp.get("events") or [], resp.get("next_cursor")
        if not data:
            break
        for e in data:
            for m in e.get("markets") or []:
                try:
                    if json.loads(m.get("outcomes") or "[]") != ["Yes", "No"]:
                        continue
                    b, a = float(m.get("bestBid") or 0), float(m.get("bestAsk") or 0)
                except (ValueError, TypeError):
                    continue
                if m.get("closed") or not (0 < b < a < 1) or float(m.get("liquidityNum") or 0) < min_liq:
                    continue
                m["_event_slug"], m["_event_title"] = e.get("slug"), e.get("title")
                out.append(m)
        if not cursor:
            break
        time.sleep(0.2)
    return out


def kalshi_binaries(events, now, min_volume=100):
    out = []
    for ev in events:
        for m in ev.get("markets") or []:
            if m.get("status") not in ("active", "open") or oef.f(m.get("volume_fp") or m.get("volume")) < min_volume:
                continue
            ya, yb = oef.f(m.get("yes_ask_dollars")), oef.f(m.get("yes_bid_dollars"))
            if not (0 < yb < ya < 1):
                continue
            sub = m.get("yes_sub_title") or ""
            title = m.get("title") or ev.get("title", "")
            name = f"{ev.get('title', '')} {sub}" if sub and sub.lower() not in title.lower() else title
            out.append({"m": m, "ev": ev, "name": name})
    return out


def poly_arbs(events, now, order_usd, pm=None, threshold=0.8, max_date_gap_days=3):
    pm = pm if pm is not None else fetch_polymarket()
    index = {}
    for i, m in enumerate(pm):
        m["_toks"] = toks(m.get("question"))
        for t in m["_toks"]:
            index.setdefault(t, []).append(i)
    df = {t: len(v) for t, v in index.items()}
    res, pairs, rejected = [], 0, []
    for k in kalshi_binaries(events, now):
        kt = toks(k["name"])
        if len(kt) < 2:
            continue
        rare = sorted((t for t in kt if t in index), key=lambda t: df[t])[:4]
        cand = {i for t in rare for i in index[t]}
        best = None
        for i in cand:
            m = pm[i]
            pt = m["_toks"]
            if nums(k["name"]) != nums(m.get("question")):
                continue
            sc = len(kt & pt) / len(kt | pt)
            if sc >= 0.6 and (best is None or sc > best[0]):
                best = (sc, m)
        if not best:
            continue
        sc, m = best
        kc = oef.parse_t(k["m"].get("expected_expiration_time")) or oef.parse_t(k["m"].get("close_time"))
        pc = oef.parse_t(m.get("endDate"))
        pt = m["_toks"]
        why = None
        hard_diff = (kt ^ pt) & HARD
        if hard_diff:
            why = "different question: " + ", ".join(sorted(hard_diff))
        elif sc < threshold:
            why = f"wording too different (similarity {sc:.2f})"
        elif kc and pc and abs((kc - pc).total_seconds()) > max_date_gap_days * 86400:
            why = f"different deadlines ({kc:%Y-%m-%d} vs {pc:%Y-%m-%d})"
        if why:
            rejected.append({"kalshi": k["name"], "polymarket": m.get("question"), "why": why})
            continue
        pairs += 1
        inverted = polarity(k["name"]) * polarity(m.get("question")) == -1
        ya, yb = oef.f(k["m"].get("yes_ask_dollars")), oef.f(k["m"].get("yes_bid_dollars"))
        pa, pb = float(m["bestAsk"]), float(m["bestBid"])
        k_yes = (oef.all_in_cost(ya, order_usd), oef.f(k["m"].get("yes_ask_size_fp")) * ya)
        k_no = (oef.all_in_cost(1 - yb, order_usd), oef.f(k["m"].get("yes_bid_size_fp")) * (1 - yb))
        p_yes, p_no = pa + poly_fee(pa, m), (1 - pb) + poly_fee(1 - pb, m)
        if not inverted:   # opposite sides of the same question
            combos = [("Kalshi YES + Polymarket NO", k_yes[0], p_no, k_yes[1], 1),
                      ("Kalshi NO + Polymarket YES", k_no[0], p_yes, k_no[1], 0)]
        else:              # Polymarket asks the opposite question ("less than" vs "at least")
            combos = [("Kalshi YES + Polymarket YES (inverted wording)", k_yes[0], p_yes, k_yes[1], 0),
                      ("Kalshi NO + Polymarket NO (inverted wording)", k_no[0], p_no, k_no[1], 1)]
        for label, kc_cost, pc_cost, kdepth, pm_token_idx in combos:
            tot = kc_cost + pc_cost
            res.append({"type": label, "kalshi": k["name"], "kalshi_ticker": k["m"]["ticker"],
                        "polymarket": m.get("question"), "polymarket_url": f"https://polymarket.com/event/{m.get('_event_slug')}",
                        "match_score": round(sc, 2), "kalshi_cost": kc_cost, "poly_cost": pc_cost, "total_cost": tot,
                        "profit_pct": 1 / tot - 1, "kalshi_depth": kdepth, "poly_token": _token(m, pm_token_idx),
                        "kalshi_close": kc.strftime("%Y-%m-%d") if kc else "", "poly_close": pc.strftime("%Y-%m-%d") if pc else "",
                        "days": max(((kc or pc) - now).total_seconds() / 86400, 0.5) if (kc or pc) else None,
                        "poly_fees": bool(m.get("feesEnabled"))})
    res.sort(key=lambda r: r["total_cost"])
    for r in [x for x in res if x["total_cost"] < 1][:40] + [x for x in res if x["total_cost"] >= 1][:8]:  # real PM depth
        if r["poly_token"]:
            try:
                book, _ = http(f"{CLOB}/book", {"token_id": r["poly_token"]})
                asks = sorted(((float(x["price"]), float(x["size"])) for x in book.get("asks", [])), key=lambda x: x[0])
                if asks:
                    r["poly_depth"] = asks[0][0] * asks[0][1]
            except Exception:
                pass
    return res, {"polymarket_markets": len(pm), "matched_pairs": pairs, "rejected": rejected}


def _token(m, idx):
    try:
        return json.loads(m.get("clobTokenIds") or "[]")[idx]
    except (ValueError, IndexError):
        return None


# ------------------------------------------------------------------ report
def report(now, sp, sp_meta, pmr, pm_meta, kal_arbs, n_events, order_usd) -> str:
    L = [f"# Arb Scanner: {now:%A %b %d %Y %H:%M} UTC", "",
         f"Kalshi: {n_events:,} open events · order size ${order_usd:.0f} for fee rounding. "
         "Robinhood event contracts are Kalshi contracts (same order book), so they are covered by the Kalshi legs.", ""]
    # sports
    L += ["## 1. Kalshi vs sportsbooks (full-game moneylines)", ""]
    if sp_meta.get("errors"):
        L += [f"> Sportsbook data unavailable: {sp_meta['errors'][0]}", ""]
    L.append(f"Kalshi games in window: {sp_meta['kalshi_games']} · matched to sportsbook lines: {sp_meta['matched']}"
             + (f" · Odds API credits left: {sp_meta['credits_remaining']}" if sp_meta.get("credits_remaining") else ""))
    L.append("")
    arbs = [r for r in sp if r["total_cost"] < 1]
    L += _sports_table("Arbitrage (total cost < $1 per $1 payout)", arbs) if arbs else ["**No sportsbook arbitrage right now.**", ""]
    L += _sports_table("Closest to an arb", [r for r in sp if r["total_cost"] >= 1][:8])
    # polymarket
    L += ["## 2. Kalshi vs Polymarket (same question on both)", "",
          f"Polymarket markets scanned: {pm_meta.get('polymarket_markets', 0):,} · auto-matched pairs: {pm_meta.get('matched_pairs', 0)}", ""]
    if pm_meta.get("error"):
        L += [f"> Polymarket unavailable: {pm_meta['error']}", ""]
    parbs = [r for r in pmr if r["total_cost"] < 1]
    tradable = [r for r in parbs if r["kalshi_depth"] >= 5 and (r.get("poly_depth") or 0) >= 5]
    thin = [r for r in parbs if r not in tradable]
    L += (_poly_table("Arbitrage, both legs tradable (≥ $5 each side). Verify the rules match first", tradable[:15]) if tradable
          else ["**No tradable Kalshi–Polymarket arbitrage** (≥ $5 available on both legs).", ""])
    L += _poly_table("Price gaps too thin to trade (< $5 on a leg)", thin[:8])
    L += _poly_table("Closest to an arb", [r for r in pmr if r["total_cost"] >= 1][:8])
    rej = pm_meta.get("rejected") or []
    if rej:
        L += [f"<details><summary>{len(rej)} look-alike pairs rejected as different questions (sample)</summary>", "",
              "| Kalshi | Polymarket | Why rejected |", "|---|---|---|"]
        for r in rej[:12]:
            L.append(f"| {r['kalshi']} | {r['polymarket']} | {r['why']} |")
        L += ["", "</details>", ""]
    # inside kalshi
    L += ["## 3. Inside Kalshi (complete outcome sets)", ""]
    if kal_arbs:
        L += ["| Event | Trade | Cost | Payout | Profit | Thinnest leg |", "|---|---|---|---|---|---|"]
        for a in kal_arbs[:10]:
            L.append(f"| {a['event']} | {a['type']} ({a['legs']} legs) | ${a['cost']:.3f} | ${a['payout']:.2f} | {a['profit_pct']:+.1%} | ${a['min_leg_depth']:,.0f} |")
        L.append("")
    else:
        L += ["**None open.**", ""]
    L += ["---", "**Before calling anything a sure bet**",
          "1. **Same event, same rules.** Resolution source, deadline, overtime, ties, cancellations and voids must match on every leg. A mismatch turns an arb into a loss.",
          "2. **Fill both legs.** Prices move between your orders. Place the thinner or more volatile leg first, and size to the smallest depth shown.",
          "3. **Fees are included**, but check your own fee tier, and that Polymarket fees apply where flagged.",
          "4. **Access & limits.** Sportsbooks limit arbitrage bettors. Polymarket and Kalshi access depend on your jurisdiction.",
          "5. **Lock-up.** Profit % is over the holding period. A 1% arb resolving in a year is ~1%/yr.",
          "", "Research tool, not financial or betting advice."]
    return "\n".join(L)


def _sports_table(title, rows):
    if not rows:
        return []
    L = [f"### {title}", "", "| Game | Legs (stake per $100 payout) | Total cost | Profit | Kalshi size | Flags |", "|---|---|---|---|---|---|"]
    for r in rows:
        legs = "<br>".join(f"{l['label']} (${l['stake_per_100']:.2f})" for l in r["legs"])
        size = f"${r['kalshi_max_payout']:,.0f} payout" if r.get("kalshi_max_payout") else "n/a"
        L.append(f"| {r['event']}<br><sub>{r['sport']} · {r['game_time']} · {r['type']}</sub> | {legs} | ${r['total_cost']:.4f} | "
                 f"**{r['profit_pct']:+.2%}** | {size} | {'; '.join(r['flags'])} |")
    return L + [""]


def _poly_table(title, rows):
    if not rows:
        return []
    L = [f"### {title}", "", "| Kalshi | Polymarket | Trade | Cost | Profit | APR | Size (K / PM) | Closes (K / PM) |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        pd = f"${r['poly_depth']:,.0f}" if r.get("poly_depth") is not None else "?"
        L.append(f"| {r['kalshi']}<br><sub>{r['kalshi_ticker']}</sub> | [{r['polymarket']}]({r['polymarket_url']})<br><sub>match {r['match_score']}"
                 f"{' · PM fees' if r['poly_fees'] else ''}</sub> | {r['type']} | ${r['total_cost']:.3f} | **{r['profit_pct']:+.1%}** | "
                 f"{(r['profit_pct'] * 365 / r['days']) if r.get('days') else 0:+.0%} | "
                 f"${r['kalshi_depth']:,.0f} / {pd} | {r['kalshi_close']} / {r['poly_close']} |")
    return L + [""]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="arb_report.md")
    ap.add_argument("--json")
    ap.add_argument("--cache", help="Kalshi events JSON cache (shared with open_ev_finder)")
    ap.add_argument("--order", type=float, default=100.0)
    ap.add_argument("--sports-days", type=float, default=3)
    ap.add_argument("--no-sports", action="store_true")
    ap.add_argument("--no-polymarket", action="store_true")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    if a.cache and os.path.exists(a.cache):
        events = json.load(open(a.cache))
    else:
        events = oef.fetch_events()
        if a.cache:
            json.dump(events, open(a.cache, "w"))
    sp, sp_meta = ([], {"kalshi_games": 0, "matched": 0, "errors": ["skipped (--no-sports)"]})
    if not a.no_sports:
        sp, sp_meta = sports_arbs(events, now, a.order, a.sports_days)
    pmr, pm_meta = [], {}
    if not a.no_polymarket:
        try:
            pmr, pm_meta = poly_arbs(events, now, a.order)
        except Exception as e:
            pm_meta = {"error": str(e)}
    _, kal_arbs, _, _ = oef.scan(events, now, order_usd=a.order, min_ev=1e-9)
    md = report(now, sp, sp_meta, pmr, pm_meta, kal_arbs, len(events), a.order)
    with open(a.out, "w") as fh:
        fh.write(md)
    if a.json:
        json.dump({"generated": now.isoformat(), "sports": sp, "sports_meta": sp_meta, "polymarket": pmr[:200],
                   "polymarket_meta": pm_meta, "kalshi": kal_arbs}, open(a.json, "w"), indent=2, default=str)
    print(md)


if __name__ == "__main__":
    main()
