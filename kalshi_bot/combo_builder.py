"""Build combos only from legs the calibration table says are underpriced,
then accept an RFQ quote only if the model's EV beats it by a safety margin.

Flow (live):  pick legs -> create combo market -> RFQ -> wait for quotes ->
              accept best quote if EV >= 1 + combo_min_edge, else cancel.
Flow (paper): same leg selection; the quote is simulated as fair*(1+vig).
"""
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .client import cents, dollars
from .config import CONFIG
from .strategy import estimate_prob


@dataclass
class Leg:
    event_ticker: str
    market_ticker: str
    side: str
    price_c: int
    est_prob: float
    game: str
    title: str = ""

    @property
    def edge(self):
        return self.est_prob - self.price_c / 100

    def api(self):
        return {"event_ticker": self.event_ticker, "market_ticker": self.market_ticker, "side": self.side}


@dataclass
class ComboPlan:
    collection: str
    legs: list = field(default_factory=list)

    @property
    def fair_price(self):           # product of leg asks = what the legs cost separately
        return math.prod(l.price_c / 100 for l in self.legs)

    @property
    def model_prob(self):           # assumes legs independent (enforced: 1 leg per game)
        return math.prod(l.est_prob for l in self.legs)

    def summary(self):
        return {"collection": self.collection, "legs": len(self.legs),
                "fair_price_$": round(self.fair_price, 6), "model_prob": round(self.model_prob, 6),
                "payout_multiple": round(1 / self.fair_price, 1),
                "legs_detail": [f"{l.side.upper()} {l.market_ticker} @{l.price_c}c est {l.est_prob:.1%}"
                                for l in self.legs]}


def game_key(event_ticker: str) -> str:
    """'KXNBAPTS-25NOV20NYKMIN' -> '25NOV20NYKMIN' so props of one game group together."""
    return event_ticker.split("-", 1)[1] if "-" in event_ticker else event_ticker


def cost_per_contract(price, fee_rate=CONFIG.combo_fee_rate):
    return price * (1 + fee_rate * (1 - price))


def quote_yes_price(quote):
    """Price the requester pays per YES contract. Field meaning is taken
    conservatively: the worse of `yes_bid` and `1 - no_bid`."""
    opts = []
    yb, nb = dollars(quote, "yes_bid"), dollars(quote, "no_bid")
    if yb:
        opts.append(yb)
    if nb:
        opts.append(1 - nb)
    return max(opts) if opts else None


def ev_multiple(model_prob, price, fee_rate=CONFIG.combo_fee_rate):
    return model_prob / cost_per_contract(price, fee_rate)


def leg_pool(collection, events_by_ticker, table, cfg=CONFIG, now=None, prob_lookup=None):
    """prob_lookup: optional {(market_ticker, side): fair prob} from ev_finder (sharp lines).
    When given it replaces the historical calibration table."""
    now = now or datetime.now(timezone.utc)
    pool = []
    for ae in collection.get("associated_events", []):
        et = ae.get("ticker") or ae.get("event_ticker")
        ev = events_by_ticker.get(et)
        if not ev:
            continue
        yes_only = ae.get("is_yes_only", False)
        for m in ev.get("markets", []):
            if m.get("status") not in ("active", "open"):
                continue
            close = m.get("close_time")
            if close and datetime.fromisoformat(close.replace("Z", "+00:00")) <= now:
                continue
            ya, yb = cents(m, "yes_ask"), cents(m, "yes_bid")
            sides = [("yes", ya)] + ([] if yes_only or yb is None else [("no", 100 - yb)])
            for side, px in sides:
                if not px or not (cfg.combo_leg_min_price_c <= px <= cfg.combo_leg_max_price_c):
                    continue
                if prob_lookup is not None:
                    p = prob_lookup.get((m["ticker"], side))
                else:
                    p = estimate_prob(table, ev.get("category", "unknown"), px, cfg, ev.get("series_ticker"))
                if p is None:
                    continue
                leg = Leg(et, m["ticker"], side, px, p, game_key(et), m.get("title", ""))
                if leg.edge >= cfg.combo_leg_min_edge:
                    pool.append(leg)
    return pool


def build_combo(collection, pool, cfg=CONFIG):
    """Greedy: best edge-ratio legs first, one per market, capped per game."""
    chosen, per_game, used = [], {}, set()
    for leg in sorted(pool, key=lambda l: l.est_prob / (l.price_c / 100), reverse=True):
        if leg.market_ticker in used or per_game.get(leg.game, 0) >= cfg.combo_max_legs_per_game:
            continue
        chosen.append(leg)
        used.add(leg.market_ticker)
        per_game[leg.game] = per_game.get(leg.game, 0) + 1
        if len(chosen) >= cfg.combo_max_legs:
            break
    if len(chosen) < cfg.combo_min_legs:
        return None
    return ComboPlan(collection.get("collection_ticker", ""), chosen)


def plan_combos(client, table, cfg=CONFIG, prob_lookup_fn=None):
    """prob_lookup_fn(list_of_events) -> {(ticker, side): prob}; None = use calibration."""
    events = {e["event_ticker"]: e for e in client.events(status="open")}
    lookup = prob_lookup_fn(list(events.values())) if prob_lookup_fn else None
    plans = []
    for coll in client.collections():
        plan = build_combo(coll, leg_pool(coll, events, table, cfg, prob_lookup=lookup), cfg)
        if plan:
            plans.append(plan)
    return sorted(plans, key=lambda p: p.model_prob / p.fair_price, reverse=True)


def execute(plan, client, ledger, mode, cfg=CONFIG, log=print):
    """mode: 'paper' | 'quote-only' | 'live'. Returns True if a combo was bought."""
    stake = min(cfg.combo_stake, ledger.remaining_c(mode) / 100)
    est_price = plan.fair_price * (1 + cfg.combo_paper_vig)
    contracts = int(stake // cost_per_contract(est_price))
    if contracts < 1:
        log("Budget exhausted for today/week.")
        return False

    if mode == "paper":
        price = est_price
    else:
        combo_ticker = client.create_combo_market(plan.collection, [l.api() for l in plan.legs])
        rfq_id = client.create_rfq(combo_ticker, contracts)
        time.sleep(cfg.rfq_wait_seconds)
        quotes = [(quote_yes_price(q), q) for q in client.rfq_quotes(rfq_id)]
        quotes = sorted([x for x in quotes if x[0]], key=lambda x: x[0])
        if not quotes:
            client.cancel_rfq(rfq_id)
            log("No quotes received.")
            return False
        price, best = quotes[0]
        log(f"Best quote {price:.5f} vs fair {plan.fair_price:.5f} "
            f"(market maker markup {price / plan.fair_price - 1:+.0%})")

    ev = ev_multiple(plan.model_prob, price, cfg.combo_fee_rate)
    cost = contracts * cost_per_contract(price)
    ok = ev >= 1 + cfg.combo_min_edge and cost <= stake + 1e-9
    log(f"[{mode}] {len(plan.legs)} legs, {contracts} contracts @ ${price:.5f} = ${cost:.2f}, "
        f"payout ${contracts:.0f} if all hit, model EV x{ev:.2f} -> {'BUY' if ok else 'PASS'}")

    if mode != "paper":
        if not ok or mode == "quote-only":
            client.cancel_rfq(rfq_id)
            return False
        client.accept_quote(rfq_id, best["id"], "yes")
    if ok:
        ledger.record_combo(plan, contracts, price, cost, mode,
                            combo_ticker=None if mode == "paper" else combo_ticker)
    return ok


def settle_paper(row_legs_json, market_lookup):
    """Return 'yes'/'no'/None given each leg's settled result."""
    legs = json.loads(row_legs_json)
    pending = False
    for l in legs:
        res = market_lookup(l["market_ticker"]).get("result")
        if res in ("yes", "no"):
            if res != l["side"]:
                return "no"
        else:
            pending = True
    return None if pending else "yes"
