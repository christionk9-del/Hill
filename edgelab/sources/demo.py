"""A synthetic 'world' with KNOWN true probabilities, used to validate the whole
pipeline offline. If the model can't find edge here, where we planted it, it
won't find it in the wild. Planted effects:

* sharp book  : small error that shrinks toward the close, margin applied with a power curve
* soft books  : larger, sticky errors + 4.5-7% margin  -> +EV vs the sharp consensus
* exchanges   : favourite-longshot bias (logit m = b * logit p, b<1) + spread + fees.
                exchange_1 is THIN (big idiosyncratic errors), exchange_2 is DEEP (accurate)
* crowd       : play-money, unbiased but noisy, not bettable
"""
from __future__ import annotations

import math
import random
import zlib
from datetime import datetime, timedelta

from ..odds import logit, sigmoid
from .base import Quote, Source, iso

TEAMS = ["Hawks", "Wolves", "Bears", "Sharks", "Comets", "Rangers", "Titans", "Pilots", "Kings", "Storm",
         "Lynx", "Owls", "Vipers", "Giants", "Falcons", "Mariners"]
PM_TEMPLATES = {
    "politics": ["Will {x} win the {y} primary?", "Will the {y} bill pass the Senate?", "Will {x} lead the {y} poll on Friday?"],
    "geopolitics": ["Will a {y} ceasefire be announced?", "Will {x} visit {y} this month?", "Will {y} hold talks before the deadline?"],
    "economics": ["Will {y} CPI print above consensus?", "Will the Fed cut at the {y} meeting?", "Will {x} beat {y} earnings estimates?"],
}
NAMES = ["Alvarez", "Brooks", "Chen", "Duarte", "Evans", "Fischer", "Garcia", "Hale", "Ibsen", "Jones"]
PLACES = ["Northland", "Eastvale", "Ostria", "Meridia", "Calder", "Varkos", "Q3", "Q4", "March", "June"]


def _rng(*parts) -> random.Random:
    return random.Random(zlib.crc32("|".join(map(str, parts)).encode()))


def _margin_power(probs: list[float], margin: float) -> list[float]:
    """Inverse of power devig: implied r_i = p_i^k with sum r = 1 + margin."""
    lo, hi = 0.3, 1.0
    for _ in range(60):
        k = (lo + hi) / 2
        if sum(p ** k for p in probs) > 1 + margin:
            lo = k
        else:
            hi = k
    return [p ** ((lo + hi) / 2) for p in probs]


class DemoWorld(Source):
    name = "demo"

    def __init__(self, start: datetime, seed: int = 7, sports_per_batch=4, pm_per_batch=2, batch_hours=6, **_):
        self.start, self.seed = start, seed
        self.sports_n, self.pm_n, self.batch_h = sports_per_batch, pm_per_batch, batch_hours
        self.events: dict[str, dict] = {}
        self._next = start

    # ----- world generation -------------------------------------------------
    def _spawn(self, now: datetime):
        while self._next <= now:
            t, b = self._next, int((self._next - self.start).total_seconds() // 3600)
            for i in range(self.sports_n):
                r = _rng(self.seed, "s", b, i)
                p = sigmoid(r.gauss(0.25, 0.8))
                h, a = r.sample(TEAMS, 2)
                self._add(f"s{b}_{i}", "sports", f"{a} @ {h}", [h, a], [p, 1 - p], t, t + timedelta(hours=r.uniform(8, 30)), r)
            for dom, tmpls in PM_TEMPLATES.items():
                for i in range(self.pm_n):
                    r = _rng(self.seed, dom, b, i)
                    p = sigmoid(r.gauss(0, 1.6))
                    name = r.choice(tmpls).format(x=r.choice(NAMES), y=r.choice(PLACES))
                    self._add(f"{dom[:3]}{b}_{i}", dom, name, ["Yes", "No"], [p, 1 - p], t,
                              t + timedelta(days=r.uniform(2, 14)), r)
            self._next += timedelta(hours=self.batch_h)

    def _add(self, eid, dom, name, outcomes, probs, created, start, r):
        winner = 0 if r.random() < probs[0] else 1
        venues = (["demo_sharp", "demo_book_a", "demo_book_b", "demo_book_c"] if dom == "sports"
                  else ["demo_exchange_1", "demo_exchange_2", "demo_crowd"])
        offs = {v: r.gauss(0, 1) for v in venues}
        self.events[eid] = dict(id=eid, domain=dom, name=name, outcomes=outcomes, p=probs[0], created=created,
                                start=start, winner=winner, offs=offs)

    # ----- pricing ----------------------------------------------------------
    def _belief(self, e, venue, now) -> float:
        frac = max(0.0, (e["start"] - now).total_seconds() / max(1, (e["start"] - e["created"]).total_seconds()))
        noise = _rng(self.seed, e["id"], venue, int(now.timestamp()) // 3600).gauss(0, 1)
        x = logit(e["p"])
        o = e["offs"][venue]
        if venue == "demo_sharp":
            return sigmoid(x + o * (0.05 + 0.10 * frac) + 0.02 * noise)
        if venue.startswith("demo_book"):
            return sigmoid(x + 0.13 * o + 0.05 * noise)
        b = {"demo_exchange_1": 0.85, "demo_exchange_2": 0.90, "demo_crowd": 1.0}[venue]
        sd = {"demo_exchange_1": 0.30, "demo_exchange_2": 0.07, "demo_crowd": 0.25}[venue]
        return sigmoid(b * x + sd * (0.3 + 0.7 * frac) * o + 0.04 * noise)

    def fetch(self, now: datetime) -> list[Quote]:
        self._spawn(now)
        out = []
        for e in self.events.values():
            if not (e["created"] <= now < e["start"]):
                continue
            venues = e["offs"].keys()
            for v in venues:
                m = self._belief(e, v, now)
                if e["domain"] == "sports":
                    margin = 0.025 if v == "demo_sharp" else {"demo_book_a": 0.045, "demo_book_b": 0.055, "demo_book_c": 0.07}[v]
                    r = _margin_power([m, 1 - m], margin)
                    prices = [round(1 / x, 2) for x in r]
                else:
                    spread = {"demo_exchange_1": 0.02, "demo_exchange_2": 0.02, "demo_crowd": 0.0}[v]
                    ya = min(0.99, max(0.01, round(m + spread / 2, 2)))
                    na = min(0.99, max(0.01, round(1 - m + spread / 2, 2)))
                    if v == "demo_crowd":
                        ya, na = m, 1 - m
                    fee = (lambda p: 0.07 * p * (1 - p)) if v == "demo_exchange_1" else (lambda p: 0.0)
                    prices = [1 / (ya + fee(ya)), 1 / (na + fee(na))]
                depth_r = _rng(self.seed, e["id"], v, "depth")
                lo, hi = {"demo_exchange_1": (40, 400), "demo_exchange_2": (500, 5000)}.get(v, (300, 3000))
                for o, pr in zip(e["outcomes"], prices):
                    out.append(Quote(source=self.name, venue=v, domain=e["domain"], event_id=f"demo:{e['id']}",
                                     event_name=e["name"], market_key="h2h" if e["domain"] == "sports" else "binary",
                                     outcome=o, price=pr, start_time=iso(e["start"]), bettable=(v != "demo_crowd"),
                                     ref={"id": e["id"]}, size=round(depth_r.uniform(lo, hi), 2)))
        return out

    def results(self, pending, now):
        out = {}
        for p in pending:
            e = self.events.get(p["ref"].get("id"))
            if e and now >= e["start"] + timedelta(hours=3):
                out[(p["group_id"], p["outcome"])] = 1.0 if e["outcomes"][e["winner"]] == p["outcome"] else 0.0
        return out

    def true_prob(self, eid: str, outcome: str) -> float | None:
        e = self.events.get(eid)
        if not e:
            return None
        return e["p"] if e["outcomes"][0] == outcome else 1 - e["p"]
