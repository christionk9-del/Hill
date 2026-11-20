"""SQLite ledger + hard budget enforcement. Paper and live orders both go here."""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import CONFIG

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  mode TEXT NOT NULL,
  ticker TEXT, event_ticker TEXT, side TEXT,
  price_c INTEGER, count INTEGER, cost_c INTEGER,
  est_win_prob REAL, edge_c REAL,
  result TEXT, payout_c INTEGER
);
CREATE TABLE IF NOT EXISTS combos (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  mode TEXT NOT NULL,
  collection TEXT, combo_ticker TEXT, legs_json TEXT,
  contracts INTEGER, price_d REAL, cost_c INTEGER,
  fair_price_d REAL, model_prob REAL,
  result TEXT, payout_c INTEGER
);
"""


class Ledger:
    def __init__(self, path=CONFIG.db_path, cfg=CONFIG):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.timezone)

    def _today(self, now=None):
        return (now or datetime.now(self.tz)).astimezone(self.tz).date()

    def spent_since(self, day, mode):
        """Spend + bet count across single bets AND combos (one shared budget).
        quote-only mode never spends, so it shares the paper budget for sizing."""
        mode = "paper" if mode == "quote-only" else mode
        total, n = 0, 0
        for table in ("orders", "combos"):
            r = self.db.execute(f"SELECT COALESCE(SUM(cost_c),0), COUNT(*) FROM {table} WHERE day>=? AND mode=?",
                                (day.isoformat(), mode)).fetchone()
            total, n = total + r[0], n + r[1]
        return total, n

    def remaining_c(self, mode, now=None):
        today = self._today(now)
        day_spent, day_n = self.spent_since(today, mode)
        week_spent, _ = self.spent_since(today - timedelta(days=today.weekday()), mode)
        if day_n >= self.cfg.max_bets_per_day:
            return 0
        return max(0, min(int(self.cfg.daily_budget * 100) - day_spent,
                          int(self.cfg.weekly_budget * 100) - week_spent))

    def has_event(self, event_ticker, mode):
        return self.db.execute("SELECT 1 FROM orders WHERE event_ticker=? AND mode=? AND result IS NULL",
                               (event_ticker, mode)).fetchone() is not None

    def record(self, c, mode, now=None):
        now = now or datetime.now(self.tz)
        self.db.execute(
            "INSERT INTO orders (ts,day,mode,ticker,event_ticker,side,price_c,count,cost_c,est_win_prob,edge_c)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now.isoformat(), self._today(now).isoformat(), mode, c.ticker, c.event_ticker, c.side,
             c.price_c, c.count, c.cost_c, c.est_win_prob, c.edge_c))
        self.db.commit()

    def record_combo(self, plan, contracts, price, cost, mode, combo_ticker=None, now=None):
        now = now or datetime.now(self.tz)
        legs = [{"event_ticker": l.event_ticker, "market_ticker": l.market_ticker, "side": l.side,
                 "price_c": l.price_c, "est_prob": l.est_prob} for l in plan.legs]
        self.db.execute(
            "INSERT INTO combos (ts,day,mode,collection,combo_ticker,legs_json,contracts,price_d,cost_c,"
            "fair_price_d,model_prob) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now.isoformat(), self._today(now).isoformat(), mode, plan.collection, combo_ticker,
             json.dumps(legs), contracts, price, round(cost * 100), plan.fair_price, plan.model_prob))
        self.db.commit()

    def open_combos(self):
        return self.db.execute("SELECT id, mode, combo_ticker, legs_json FROM combos WHERE result IS NULL").fetchall()

    def settle_combo(self, combo_id, result):
        n = self.db.execute("SELECT contracts FROM combos WHERE id=?", (combo_id,)).fetchone()[0]
        self.db.execute("UPDATE combos SET result=?, payout_c=? WHERE id=?",
                        (result, n * 100 if result == "yes" else 0, combo_id))
        self.db.commit()

    def combo_stats(self, mode):
        n, cost, payout, wins, exp = self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_c),0), COALESCE(SUM(payout_c),0), "
            "COALESCE(SUM(CASE WHEN result='yes' THEN 1 ELSE 0 END),0), "
            "COALESCE(SUM(model_prob*contracts*100 - cost_c),0) "
            "FROM combos WHERE mode=? AND result IS NOT NULL", (mode,)).fetchone()
        return {"settled_combos": n, "hits": wins, "cost_$": cost / 100, "payout_$": payout / 100,
                "profit_$": (payout - cost) / 100, "model_expected_profit_$": exp / 100}

    def settle(self, ticker, result):
        rows = self.db.execute("SELECT id, side, count FROM orders WHERE ticker=? AND result IS NULL",
                               (ticker,)).fetchall()
        for oid, side, count in rows:
            payout = count * 100 if side == result else 0
            self.db.execute("UPDATE orders SET result=?, payout_c=? WHERE id=?", (result, payout, oid))
        self.db.commit()
        return len(rows)

    def open_tickers(self):
        return [r[0] for r in self.db.execute("SELECT DISTINCT ticker FROM orders WHERE result IS NULL")]

    def stats(self, mode):
        q = ("SELECT COUNT(*), COALESCE(SUM(cost_c),0), COALESCE(SUM(payout_c),0), "
             "COALESCE(SUM(CASE WHEN payout_c>0 THEN 1 ELSE 0 END),0), "
             "COALESCE(SUM(est_win_prob*count*100 - cost_c),0) "
             "FROM orders WHERE mode=? AND result IS NOT NULL")
        n, cost, payout, wins, exp_profit = self.db.execute(q, (mode,)).fetchone()
        return {"settled_bets": n, "cost_$": cost / 100, "payout_$": payout / 100,
                "profit_$": (payout - cost) / 100, "wins": wins,
                "model_expected_profit_$": exp_profit / 100}
