"""Core EV engine.

1. Pull odds for every configured sport from every region.
2. Build a "fair" probability for each outcome by de-vigging the sharpest book
   available (Pinnacle first), falling back to a multi-book consensus.
3. Price every soft-book / exchange / DFS offer against that fair line.
4. Apply any active promos (profit boosts, bonus bets, no-sweats, DFS
   discounts) and find the best place to use each one.
"""
from __future__ import annotations

import statistics
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone

from . import odds_math as om
from .sources import (OddsAPI, SourceError, kalshi_game_markets,
                      kalshi_team_matches, parse_iso)

DFS_BOOKS = {"prizepicks", "underdog", "pick6", "betr_us_dfs", "dabble_au",
             "sleeper", "parlayplay"}
EXCHANGES = {"kalshi", "novig", "prophetx", "betopenly", "polymarket", "sporttrade"}


@dataclass
class Opportunity:
    kind: str            # "+EV", "boost", "bonus_bet", "no_sweat", "dfs", "kalshi"
    book: str
    sport: str
    event: str
    start: str
    market: str
    selection: str
    odds_decimal: float
    fair_prob: float
    ev: float            # expected profit per $1 staked (or per $1 of promo)
    sharp_source: str
    notes: str = ""
    stake_hint: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def odds_american(self) -> int:
        return om.decimal_to_american(self.odds_decimal) if self.odds_decimal > 1 else 0

    def to_dict(self):
        d = asdict(self)
        d["odds_american"] = self.odds_american
        return d


# --------------------------------------------------------------------------- #
# Fair-line construction
# --------------------------------------------------------------------------- #
def _outcome_key(market_key: str, o: dict) -> tuple:
    # Totals / props: side + point (+ player description). Spreads: name + point.
    return (o.get("description") or "", o["name"], o.get("point"))


def _group_key(market_key: str, o: dict) -> tuple:
    """Which outcomes belong to the same two-way (or n-way) market."""
    if market_key in ("spreads",) or market_key.endswith("_spread"):
        return (abs(o.get("point") or 0),)
    if o.get("point") is not None:
        return (o.get("description") or "", o["point"])
    return (o.get("description") or "",)


def fair_probs_for_event(event: dict, cfg) -> dict:
    """{(market, group, outcome_key): (fair_prob, source)}"""
    # Collect per book: market -> group -> {outcome_key: decimal}
    per_book: dict[str, dict] = {}
    for b in event.get("bookmakers", []):
        if b["key"] in DFS_BOOKS:
            continue  # DFS prices are fixed payouts, not real odds
        for m in b.get("markets", []):
            for o in m.get("outcomes", []):
                if not o.get("price") or o["price"] <= 1:
                    continue
                g = _group_key(m["key"], o)
                per_book.setdefault(b["key"], {}).setdefault(m["key"], {}) \
                    .setdefault(g, {})[_outcome_key(m["key"], o)] = o["price"]

    def devigged(book):
        out = {}
        for mk, groups in per_book.get(book, {}).items():
            for g, outs in groups.items():
                if len(outs) < 2:
                    continue
                if mk == "spreads" and len(outs) != 2:
                    continue
                keys = list(outs)
                probs = om.devig_power([outs[k] for k in keys])
                for k, p in zip(keys, probs):
                    out[(mk, g, k)] = p
        return out

    fair: dict = {}
    for book in cfg.sharp_books:
        if book in per_book:
            for k, p in devigged(book).items():
                fair.setdefault(k, (p, book))

    # Consensus fallback for anything the sharp books don't cover.
    all_dv = {b: devigged(b) for b in per_book}
    keys = set().union(*[set(v) for v in all_dv.values()]) if all_dv else set()
    for k in keys:
        if k in fair:
            continue
        vals = [dv[k] for dv in all_dv.values() if k in dv]
        if len(vals) >= cfg.min_books_for_consensus:
            fair[k] = (statistics.median(vals), f"consensus({len(vals)})")
    return fair


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
def _event_name(e):
    return f"{e.get('away_team')} @ {e.get('home_team')}"


def _within_window(e, cfg, now):
    start = parse_iso(e["commence_time"])
    return now <= start <= now + timedelta(hours=cfg.max_hours_ahead)


def _selection_label(mk, o):
    s = o["name"]
    if o.get("description"):
        s = f"{o['description']} {s}"
    if o.get("point") is not None:
        p = o["point"]
        s += f" {p:+g}" if mk == "spreads" else f" {p:g}"
    return s


def scan_events(events: list[dict], sport: str, cfg, promos: list[dict],
                now: datetime | None = None) -> tuple[list[Opportunity], list[dict]]:
    """Returns (opportunities, priced_offers). priced_offers is every soft offer
    with a fair price, which promo evaluation reuses."""
    now = now or datetime.now(timezone.utc)
    opps, offers = [], []
    my = set(cfg.my_books)
    for e in events:
        if not _within_window(e, cfg, now):
            continue
        fair = fair_probs_for_event(e, cfg)
        if not fair:
            continue
        for b in e.get("bookmakers", []):
            book = b["key"]
            if book in DFS_BOOKS:
                continue
            for m in b.get("markets", []):
                for o in m.get("outcomes", []):
                    k = (m["key"], _group_key(m["key"], o), _outcome_key(m["key"], o))
                    if k not in fair or not o.get("price"):
                        continue
                    p, src = fair[k]
                    if src == book:
                        continue  # don't price a book against itself
                    dec = o["price"]
                    if book == "kalshi":
                        dec = om.kalshi_effective_decimal(dec, cfg.kalshi["fee_rate"])
                    offer = dict(book=book, sport=sport, event=_event_name(e),
                                 start=e["commence_time"], market=m["key"],
                                 selection=_selection_label(m["key"], o),
                                 dec=dec, p=p, src=src)
                    offers.append(offer)
                    if my and book not in my:
                        continue
                    if dec > cfg.max_decimal_odds:
                        continue
                    val = om.ev(p, dec)
                    if val >= cfg.report_threshold:
                        opps.append(Opportunity(
                            "+EV", book, sport, offer["event"], offer["start"],
                            m["key"], offer["selection"], dec, p, val, src,
                            notes="after Kalshi fees" if book == "kalshi" else "",
                            extra={"kelly": round(om.kelly_fraction(p, dec), 4)}))
    return opps, offers


def scan_dfs(event_odds: list[dict], sport: str, cfg, promos: list[dict],
             now: datetime | None = None) -> list[Opportunity]:
    """PrizePicks / Underdog etc.: every leg pays a fixed multiplier, so a leg
    is +EV when the sharp fair probability beats the per-leg break-even."""
    now = now or datetime.now(timezone.utc)
    be_cfg = cfg.dfs["breakeven"]
    opps = []
    dfs_promos = [p for p in promos if p.get("type") == "dfs_discount"]
    for e in event_odds:
        if not _within_window(e, cfg, now):
            continue
        fair = fair_probs_for_event(e, cfg)
        for b in e.get("bookmakers", []):
            if b["key"] not in DFS_BOOKS:
                continue
            be = be_cfg.get(b["key"], be_cfg["default"])
            for m in b.get("markets", []):
                for o in m.get("outcomes", []):
                    k = (m["key"], _group_key(m["key"], o), _outcome_key(m["key"], o))
                    if k not in fair:
                        continue
                    p, src = fair[k]
                    leg_ev = p / be - 1  # EV of the slip if every leg had this edge
                    if leg_ev >= cfg.report_threshold:
                        opps.append(Opportunity(
                            "dfs", b["key"], sport, _event_name(e), e["commence_time"],
                            m["key"], _selection_label(m["key"], o), 1 / be, p, leg_ev,
                            src, notes=f"fair {p:.1%} vs break-even {be:.1%} per leg"))
    # Manually entered DFS discounted lines ("Taco Tuesday", demons/goblins...)
    for promo in dfs_promos:
        opps.extend(_eval_dfs_discount(promo, event_odds, cfg))
    return opps


def _eval_dfs_discount(promo, event_odds, cfg):
    """promo: {type: dfs_discount, book, player, market, side, line, payout_multiplier?}
    Matches the sharp book at the same line if available."""
    out = []
    player = promo["player"].lower()
    for e in event_odds:
        fair = fair_probs_for_event(e, cfg)
        for (mk, g, ok), (p, src) in fair.items():
            desc, side, point = ok
            if mk != promo["market"] or player not in desc.lower():
                continue
            if side.lower() != promo["side"].lower() or point != promo["line"]:
                continue
            be = promo.get("breakeven") or cfg.dfs["breakeven"].get(
                promo["book"], cfg.dfs["breakeven"]["default"])
            if promo.get("payout_multiplier"):  # single-leg discounted pick
                dec = float(promo["payout_multiplier"])
                val = om.ev(p, dec)
            else:
                dec = 1 / be
                val = p / be - 1
            out.append(Opportunity("dfs_promo", promo["book"], e.get("sport_key", ""),
                                   _event_name(e), e["commence_time"], mk,
                                   f"{desc} {side} {point:g}", dec, p, val, src,
                                   notes=promo.get("name", "DFS discount")))
    return out


def scan_kalshi_direct(kalshi_events: list[dict], sharp_events: list[dict], cfg,
                       now: datetime | None = None) -> list[Opportunity]:
    """Live Kalshi asks (fetched straight from Kalshi) priced against the sharp
    moneyline from the odds feed."""
    now = now or datetime.now(timezone.utc)
    opps = []
    for ke in kalshi_events:
        try:
            exp = parse_iso(ke["expires"])
        except Exception:
            continue
        sides = ke["sides"]
        # Internal Kalshi arbitrage: buying YES on every side for < $1 total.
        cost = sum(s["yes_ask"] + om.kalshi_fee_per_contract(s["yes_ask"], cfg.kalshi["fee_rate"])
                   for s in sides)
        if len(sides) == 2 and cost < 1:
            opps.append(Opportunity("kalshi_arb", "kalshi", ke["series"], ke["event_ticker"],
                                    ke["expires"], "h2h", " + ".join(s["team"] for s in sides),
                                    1 / cost, 1.0, 1 / cost - 1, "arbitrage",
                                    notes="buy YES on both sides"))
        for se in sharp_events:
            start = parse_iso(se["commence_time"])
            if not (timedelta(hours=-2) <= exp - start <= timedelta(hours=30)):
                continue
            names = [se["home_team"], se["away_team"]]
            matched = {}
            for s in sides:
                for n in names:
                    if kalshi_team_matches(s["team"], n):
                        matched[n] = s
            if len(matched) != 2:
                continue
            fair = fair_probs_for_event(se, cfg)
            for team, s in matched.items():
                k = ("h2h", ("",), ("", team, None))
                if k not in fair:
                    continue
                p, src = fair[k]
                dec = om.kalshi_effective_decimal(1 / s["yes_ask"], cfg.kalshi["fee_rate"])
                val = om.ev(p, dec)
                if val >= cfg.report_threshold:
                    opps.append(Opportunity(
                        "kalshi", "kalshi", se["sport_key"], _event_name(se),
                        se["commence_time"], "h2h", f"{team} YES @ ${s['yes_ask']:.2f}",
                        dec, p, val, src, notes=f"{s['ticker']} (live ask, after fees)",
                        extra={"kelly": round(om.kelly_fraction(p, dec), 4)}))
            break
    return opps


# --------------------------------------------------------------------------- #
# Promos
# --------------------------------------------------------------------------- #
def _promo_applies(promo, offer):
    if promo.get("book") and promo["book"] != offer["book"]:
        return False
    if promo.get("sports") and offer["sport"] not in promo["sports"]:
        return False
    if promo.get("markets") and offer["market"] not in promo["markets"]:
        return False
    if promo.get("min_odds") and offer["dec"] < om.american_to_decimal(promo["min_odds"]):
        return False
    if promo.get("max_odds") and offer["dec"] > om.american_to_decimal(promo["max_odds"]):
        return False
    return True


def evaluate_promos(promos: list[dict], offers: list[dict], cfg, top_n=3) -> list[Opportunity]:
    """Find the best bet to use each promo on. EV is per $1 staked (boost /
    no-sweat) or per $1 of bonus bet (bonus_bet)."""
    out = []
    for promo in promos:
        t = promo.get("type")
        if t == "dfs_discount":
            continue
        scored = []
        for off in offers:
            if not _promo_applies(promo, off):
                continue
            p, dec = off["p"], off["dec"]
            if t == "profit_boost":
                bdec = om.boosted_decimal(dec, promo["boost_pct"])
                val = om.ev(p, bdec)
                shown = bdec
            elif t == "bonus_bet":
                # Stake isn't returned: value = p * (dec - 1) per $1 of bonus.
                val = p * (dec - 1)
                shown = dec
            elif t == "no_sweat":
                conv = promo.get("refund_conversion", 0.70)
                val = om.ev(p, dec) + (1 - p) * conv
                shown = dec
            else:
                continue
            scored.append((val, shown, off))
        scored.sort(key=lambda x: -x[0])
        for val, shown, off in scored[:top_n]:
            out.append(Opportunity(
                t if t != "profit_boost" else "boost", off["book"], off["sport"],
                off["event"], off["start"], off["market"], off["selection"], shown,
                off["p"], val, off["src"], notes=promo.get("name", t),
                stake_hint=promo.get("max_stake"),
                extra={"promo": promo.get("name"), "base_odds": off["dec"]}))
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_scan(cfg, promos, api: OddsAPI | None = None, log=print) -> dict:
    api = api or OddsAPI()
    now = datetime.now(timezone.utc)
    opps: list[Opportunity] = []
    offers: list[dict] = []
    errors: list[str] = []
    sharp_events: list[dict] = []

    try:
        active = api.active_sports()
    except SourceError as e:
        errors.append(f"Odds API unavailable: {e}")
        active = set()

    for sport in cfg.sports:
        if sport not in active:
            continue
        try:
            events = api.odds(sport, cfg.regions, cfg.markets)
        except SourceError as e:
            errors.append(f"{sport}: {e}")
            continue
        log(f"[odds] {sport}: {len(events)} events")
        sharp_events.extend(events)
        o, off = scan_events(events, sport, cfg, promos, now)
        opps += o
        offers += off

    if cfg.dfs.get("enabled") and active:
        for sport in cfg.dfs["sports"]:
            if sport not in active:
                continue
            evs = [e for e in sharp_events if e["sport_key"] == sport and _within_window(e, cfg, now)]
            evs.sort(key=lambda e: e["commence_time"])
            detailed = []
            for e in evs[: cfg.dfs["max_events_per_sport"]]:
                try:
                    detailed.append(api.event_odds(sport, e["id"],
                                                   sorted(set(cfg.regions) | {"us_dfs"}),
                                                   cfg.dfs["markets"]))
                except SourceError as ex:
                    errors.append(f"props {sport}/{e['id']}: {ex}")
            log(f"[dfs] {sport}: {len(detailed)} events with props")
            opps += scan_dfs(detailed, sport, cfg, promos, now)
            # Props from regular books are also fair game for +EV and promos.
            for d in detailed:
                o, off = scan_events([d], sport, cfg, promos, now)
                opps += o
                offers += off

    if cfg.kalshi.get("enabled"):
        try:
            kev = kalshi_game_markets(cfg.kalshi["series"])
            log(f"[kalshi] {len(kev)} live game markets")
            opps += scan_kalshi_direct(kev, sharp_events, cfg, now)
        except SourceError as e:
            errors.append(f"Kalshi: {e}")

    opps += evaluate_promos(promos, offers, cfg)

    # De-dupe (same book/selection may come from both feeds) and sort.
    seen, uniq = set(), []
    for o in sorted(opps, key=lambda x: -x.ev):
        key = (o.kind, o.book, o.event, o.market, o.selection)
        if key not in seen:
            seen.add(key)
            uniq.append(o)

    def alert_worthy(o):
        if o.kind == "bonus_bet":
            return o.ev >= cfg.bonus_conversion_threshold
        return o.ev >= cfg.ev_threshold

    return {
        "generated_at": now.isoformat(),
        "alerts": [o for o in uniq if alert_worthy(o)],
        "watchlist": [o for o in uniq if not alert_worthy(o)][:40],
        "errors": errors,
        "api_remaining": api.remaining,
        "offers_priced": len(offers),
    }
