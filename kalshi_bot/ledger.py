"""SQLite ledger + hard budget enforcement. Paper and live orders both go here."""
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
        r = self.db.execute("SELECT COALESCE(SUM(cost_c),0), COUNT(*) FROM orders WHERE day>=? AND mode=?",
                            (day.isoformat(), mode)).fetchone()
        return r[0], r[1]

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
