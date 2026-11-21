"""SQLite ledger: every snapshot-derived prediction and every paper bet is
written down BEFORE the outcome is known. That timestamped record is what makes
the forward test trustworthy."""
from __future__ import annotations

import hashlib
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies(name TEXT PRIMARY KEY, hash TEXT, config TEXT, hypothesis TEXT, registered_ts TEXT);
CREATE TABLE IF NOT EXISTS markets(group_id TEXT, outcome TEXT, domain TEXT, event_name TEXT, market_key TEXT,
    start_time TEXT, refs TEXT, PRIMARY KEY(group_id, outcome));
CREATE TABLE IF NOT EXISTS quotes(ts TEXT, group_id TEXT, outcome TEXT, venue TEXT, price REAL);
CREATE TABLE IF NOT EXISTS fair(group_id TEXT, outcome TEXT, method TEXT, first_ts TEXT, first_q REAL,
    last_ts TEXT, last_q REAL, n_src INTEGER, PRIMARY KEY(group_id, outcome, method));
CREATE TABLE IF NOT EXISTS bets(id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT, ts TEXT, group_id TEXT, outcome TEXT,
    venue TEXT, price REAL, q REAL, ev REAL, stake REAL, domain TEXT, close_q REAL, clv REAL,
    status TEXT DEFAULT 'open', result REAL, pnl REAL, settled_ts TEXT, true_ev REAL, UNIQUE(strategy, group_id, outcome));
CREATE TABLE IF NOT EXISTS results(group_id TEXT, outcome TEXT, result REAL, ts TEXT, PRIMARY KEY(group_id, outcome));
CREATE TABLE IF NOT EXISTS calibrations(ts TEXT, domain TEXT, a REAL, b REAL, se_b REAL, n INTEGER);
CREATE TABLE IF NOT EXISTS opps(ts TEXT, strategy TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS scans(ts TEXT, n_quotes INTEGER, n_groups INTEGER, n_bets INTEGER, errors TEXT);
CREATE INDEX IF NOT EXISTS bets_status ON bets(status);
"""


def config_hash(cfg: dict) -> str:
    core = {k: v for k, v in cfg.items() if k not in ("hypothesis", "notes", "active", "retired")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:12]


class Ledger:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def q(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def commit(self):
        self.db.commit()

    # --- pre-registration -------------------------------------------------------
    def register(self, cfg: dict, ts: str) -> str:
        h = config_hash(cfg)
        row = self.q("SELECT hash FROM strategies WHERE name=?", (cfg["name"],))
        if row and row[0]["hash"] != h:
            raise ValueError(
                f"Strategy '{cfg['name']}' was pre-registered with a different config ({row[0]['hash']} != {h}).\n"
                "Changing rules mid-test is p-hacking. Give the new variant a new name instead.")
        if not row:
            self.db.execute("INSERT INTO strategies VALUES(?,?,?,?,?)",
                            (cfg["name"], h, json.dumps(cfg), cfg.get("hypothesis", ""), ts))
        return h

    def strategies(self) -> list[dict]:
        return [dict(r) | {"config": json.loads(r["config"])} for r in self.q("SELECT * FROM strategies ORDER BY registered_ts")]

    # --- snapshots ----------------------------------------------------------------
    def upsert_market(self, q, refs_by_source: dict):
        row = self.q("SELECT refs FROM markets WHERE group_id=? AND outcome=?", (q.group_id, q.outcome))
        refs = json.loads(row[0]["refs"]) if row else {}
        refs.update(refs_by_source)
        self.db.execute("INSERT OR REPLACE INTO markets VALUES(?,?,?,?,?,?,?)",
                        (q.group_id, q.outcome, q.domain, q.event_name, q.market_key, q.start_time, json.dumps(refs)))

    def upsert_fair(self, gid, outcome, method, ts, qv, n):
        self.db.execute("""INSERT INTO fair VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(group_id,outcome,method) DO UPDATE SET last_ts=excluded.last_ts, last_q=excluded.last_q, n_src=excluded.n_src""",
                        (gid, outcome, method, ts, qv, ts, qv, n))

    def add_bet(self, strategy, ts, c, stake) -> bool:
        cur = self.db.execute("""INSERT OR IGNORE INTO bets(strategy,ts,group_id,outcome,venue,price,q,ev,stake,domain,close_q,clv)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (strategy, ts, c["group_id"], c["outcome"], c["venue"], c["price"], c["q"], c["ev"], stake,
                               c["domain"], None, None))
        return cur.rowcount > 0

    def bankroll(self, strategy: str, start: float) -> float:
        r = self.q("SELECT COALESCE(SUM(pnl),0) s FROM bets WHERE strategy=? AND status='settled'", (strategy,))
        return start + r[0]["s"]
