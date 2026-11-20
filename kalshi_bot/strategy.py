"""Pick tail bets ONLY where calibration says cheap contracts are underpriced."""
from dataclasses import dataclass
from datetime import datetime, timezone

from .calibration import bucket_of
from .client import cents
from .config import CONFIG
from .fees import breakeven_prob, max_contracts_for_budget


@dataclass
class Candidate:
    ticker: str
    event_ticker: str
    category: str
    side: str
    price_c: int
    count: int
    est_win_prob: float
    breakeven: float
    hours_to_close: float

    @property
    def edge_c(self):  # expected profit per contract, cents
        return (self.est_win_prob - self.breakeven) * 100

    @property
    def cost_c(self):
        return round(self.breakeven * 100 * self.count)


def estimate_prob(table, category, price_c, cfg=CONFIG):
    """Shrink empirical win rate toward the market price. Uses the
    category bucket if well sampled, else the ALL bucket, else None."""
    b = bucket_of(price_c)
    if not b:
        return None
    for key in (f"{category}|{b}", f"ALL|{b}"):
        s = table.get(key)
        if s and s["n"] >= cfg.min_bucket_samples:
            k = cfg.prior_strength
            prior = price_c / 100
            shrunk = (s["win_rate"] * s["n"] + prior * k) / (s["n"] + k)
            # be conservative: never exceed the 90% lower confidence bound by much
            return min(shrunk, max(s["win_rate_lower90"], prior))
    return None


def find_candidates(events, table, now=None, cfg=CONFIG):
    now = now or datetime.now(timezone.utc)
    out = []
    for ev in events:
        cat = ev.get("category", "unknown")
        for m in ev.get("markets", []):
            if m.get("status") not in ("active", "open"):
                continue
            if (m.get("volume_24h") or 0) < cfg.min_volume_24h:
                continue
            close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            hrs = (close - now).total_seconds() / 3600
            if hrs <= 0 or hrs > cfg.max_hours_to_close:
                continue
            ya, yb = cents(m, "yes_ask"), cents(m, "yes_bid")
            sides = []
            if ya:
                sides.append(("yes", ya))
            if yb:
                sides.append(("no", 100 - yb))
            for side, px in sides:
                if not (cfg.min_tail_price_c <= px <= cfg.max_tail_price_c):
                    continue
                count = max_contracts_for_budget(int(cfg.max_per_bet * 100), px)
                if count == 0:
                    continue
                p = estimate_prob(table, cat, px, cfg)
                if p is None:
                    continue
                c = Candidate(m["ticker"], ev["event_ticker"], cat, side, px, count, p,
                              breakeven_prob(count, px), hrs)
                if c.edge_c >= cfg.min_edge_c:
                    out.append(c)
    # one bet per event: outcomes inside one event are correlated
    best = {}
    for c in sorted(out, key=lambda c: c.edge_c, reverse=True):
        best.setdefault(c.event_ticker, c)
    return sorted(best.values(), key=lambda c: c.edge_c, reverse=True)
