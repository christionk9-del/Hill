"""Forward-testing engine: snapshot -> predict -> paper-bet -> track CLV -> settle.

Nothing is ever back-filled. A bet exists only if the engine saw the price live
(or in the demo, at the simulated 'now'), so there is no look-ahead bias.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .devig import METHODS
from .ledger import Ledger
from .model import DEFAULT_WEIGHTS, consensus, find_edges, fit_calibration, group_quotes, stake_for
from .sources.base import Quote, Source, iso, parse_iso

TOURNAMENT = list(METHODS) + ["worst_case"]
CAL_METHOD_DEFAULT = "power"
CLV_BENCHMARK = "power"


def apply_mappings(quotes: list[Quote], mappings: list[dict]) -> list[Quote]:
    """Re-key quotes from different venues that describe the SAME real-world question
    onto one canonical group, so their prices can be pooled (cross-venue consensus)."""
    if not mappings:
        return quotes
    look = {}
    for m in mappings:
        for src, nid in m.get("legs", {}).items():
            look[(src, str(nid))] = m
    for q in quotes:
        nid = str(q.ref.get("ticker") or q.ref.get("id") or "")
        m = look.get((q.source, nid))
        if not m:
            continue
        inv = m.get("invert", {}).get(q.source, False)
        q.ref = {**q.ref, "side": q.outcome}
        if inv:
            q.outcome = "No" if q.outcome == "Yes" else "Yes"
        q.event_id, q.market_key = f"x:{m['id']}", "binary"
        q.event_name, q.domain = m.get("name", q.event_name), m.get("domain", q.domain)
    return quotes


class Engine:
    def __init__(self, ledger: Ledger, sources: list[Source], strategies: list[dict],
                 mappings: list[dict] | None = None, store_quotes: bool = False, recalib_hours: float = 24, log=print):
        self.L, self.sources, self.strats = ledger, sources, strategies
        self.mappings, self.store_quotes, self.recalib_h, self.log = mappings or [], store_quotes, recalib_hours, log
        self._cal: dict = {}
        self._cal_ts: datetime | None = None

    # ------------------------------------------------------------------ scan
    def scan(self, now: datetime) -> dict:
        ts = iso(now)
        for s in self.strats:
            self.L.register(s, ts)
        quotes, errors = [], []
        for src in self.sources:
            try:
                quotes += src.fetch(now)
            except Exception as e:
                errors.append(f"{src.name}: {e}")
                self.log(f"  ! {src.name}: {e}")
        quotes = apply_mappings(quotes, self.mappings)
        groups = group_quotes(quotes)
        seen = set()
        for q in quotes:
            if (q.group_id, q.outcome, q.source) not in seen:
                seen.add((q.group_id, q.outcome, q.source))
                self.L.upsert_market(q, {q.source: q.ref})
            if self.store_quotes:
                self.L.db.execute("INSERT INTO quotes VALUES(?,?,?,?,?)", (ts, q.group_id, q.outcome, q.venue, q.price))
        # model tournament: every devig method's consensus, kept as first (opening) and last (closing) prediction
        for gid, qs in groups.items():
            for m in TOURNAMENT:
                fr = consensus(qs, m)
                if fr:
                    for o, p in fr[0].items():
                        self.L.upsert_fair(gid, o, m, ts, p, fr[1])
        self._refresh_calibration(now)
        n_bets = 0
        self.L.db.execute("DELETE FROM opps")
        for s in self.strats:
            cands = find_edges(groups, s, self._cal_for(s))
            bank = self.L.bankroll(s["name"], s.get("bankroll", 1000.0))
            for c in cands:
                if parse_iso(c["start_time"]) - now > timedelta(hours=s.get("max_hours_to_start", 24 * 30)):
                    continue
                stake = stake_for(c, s, bank)
                if stake >= s.get("min_stake", 1.0) and self.L.add_bet(s["name"], ts, c, stake):
                    n_bets += 1
            self.L.db.execute("INSERT INTO opps VALUES(?,?,?)", (ts, s["name"], json.dumps(cands[:60])))
        self._update_clv(groups, now)
        self.settle(now)
        self.L.db.execute("INSERT INTO scans VALUES(?,?,?,?,?)", (ts, len(quotes), len(groups), n_bets, json.dumps(errors)))
        self.L.commit()
        return {"quotes": len(quotes), "groups": len(groups), "new_bets": n_bets, "errors": errors}

    # ------------------------------------------------------------ calibration
    def _cal_for(self, strat: dict) -> dict:
        m = strat.get("devig", CAL_METHOD_DEFAULT)
        cal = {dom: c for (dom, meth), c in self._cal.items() if meth == m}
        pooled = cal.pop("all", None)
        if pooled:  # domains without enough data of their own fall back to the pooled fit
            for dom in ("sports", "politics", "geopolitics", "economics", "other"):
                cal.setdefault(dom, pooled)
        return cal

    def _refresh_calibration(self, now: datetime):
        """Walk-forward: refit using ONLY outcomes settled before `now`."""
        if not any(s.get("use_calibration") for s in self.strats):
            return
        if self._cal_ts and now - self._cal_ts < timedelta(hours=self.recalib_h):
            return
        self._cal_ts = now
        methods = {s.get("devig", CAL_METHOD_DEFAULT) for s in self.strats if s.get("use_calibration")}
        for m in methods:
            rows = self.L.q("""SELECT mk.domain, f.group_id, f.outcome, f.last_q, r.result FROM fair f
                JOIN results r ON r.group_id=f.group_id AND r.outcome=f.outcome
                JOIN markets mk ON mk.group_id=f.group_id AND mk.outcome=f.outcome
                WHERE f.method=? AND r.result IS NOT NULL AND r.ts<=?""", (m, iso(now)))
            by: dict[str, list] = {}
            used = set()
            for r in rows:
                if r["group_id"] in used:      # one outcome per group -> independent samples
                    continue
                used.add(r["group_id"])
                by.setdefault(r["domain"], []).append((r["last_q"], int(r["result"])))
                by.setdefault("all", []).append((r["last_q"], int(r["result"])))
            for dom, pairs in by.items():
                fit = fit_calibration(pairs)
                if fit and fit["n"] >= 100:
                    self._cal[(dom, m)] = fit
                    self.L.db.execute("INSERT INTO calibrations VALUES(?,?,?,?,?,?)",
                                      (iso(now), f"{dom}:{m}", fit["a"], fit["b"], fit["se_b"], fit["n"]))

    # -------------------------------------------------------------------- CLV
    def _update_clv(self, groups, now):
        rows = self.L.q("""SELECT b.id, b.strategy, b.group_id, b.outcome, b.venue, b.price, m.start_time FROM bets b
                           JOIN markets m ON m.group_id=b.group_id AND m.outcome=b.outcome WHERE b.status='open'""")
        for r in rows:
            qs = groups.get(r["group_id"])
            if not qs or parse_iso(r["start_time"]) <= now:
                continue
            # CLV is measured against ONE common benchmark for every strategy (power devig,
            # default weights, the bet venue itself excluded, no calibration) so arms are comparable
            # and no strategy can grade its own homework.
            fr = consensus(qs, CLV_BENCHMARK, DEFAULT_WEIGHTS, r["venue"])
            if fr and r["outcome"] in fr[0]:
                cq = fr[0][r["outcome"]]
                self.L.db.execute("UPDATE bets SET close_q=?, clv=? WHERE id=?", (cq, cq * r["price"] - 1, r["id"]))

    # ----------------------------------------------------------------- settle
    def settle(self, now: datetime) -> int:
        pend = self.L.q("""SELECT m.* FROM markets m LEFT JOIN results r ON r.group_id=m.group_id AND r.outcome=m.outcome
                           WHERE r.group_id IS NULL AND m.start_time<=?""", (iso(now),))
        if not pend:
            return 0
        by_src: dict[str, list] = {}
        for p in pend:
            for src, ref in json.loads(p["refs"]).items():
                by_src.setdefault(src, []).append({"group_id": p["group_id"], "outcome": p["outcome"], "ref": ref})
        got: dict = {}
        for src in self.sources:
            items = by_src.get(src.name)
            if not items:
                continue
            try:
                for k, v in src.results(items, now).items():
                    got.setdefault(k, v)
            except Exception as e:
                self.log(f"  ! settle {src.name}: {e}")
        ts = iso(now)
        for (gid, o), res in got.items():
            self.L.db.execute("INSERT OR REPLACE INTO results VALUES(?,?,?,?)", (gid, o, res, ts))
        n = 0
        for b in self.L.q("""SELECT b.id, b.price, b.stake, r.result FROM bets b JOIN results r
                             ON r.group_id=b.group_id AND r.outcome=b.outcome WHERE b.status='open'"""):
            if b["result"] is None:
                self.L.db.execute("UPDATE bets SET status='void', pnl=0, settled_ts=? WHERE id=?", (ts, b["id"]))
            else:
                pnl = b["stake"] * (b["price"] - 1) if b["result"] >= 1 else -b["stake"]
                self.L.db.execute("UPDATE bets SET status='settled', result=?, pnl=?, settled_ts=? WHERE id=?",
                                  (b["result"], pnl, ts, b["id"]))
            n += 1
        return n
