"""Offline stand-in for the ProjectX API, driven by historical minute bars.

Lets the real bot code run end to end with no account: market orders fill at the current price
plus one tick of slippage, stop orders trigger on the minute bars, and History/retrieveBars
returns 5-minute bars (with a partial bar) as they would have looked at the simulated time.

    python live/sim_broker.py          # replays the sampled minute days through the bot
"""
import logging
import os
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live.projectx import MARKET, STOP, BUY, LONG, SHORT, ApiError  # noqa: E402
from live.topstep_bot import Bot, NY                        # noqa: E402

TICK, USD_PER_PT, FEE = 0.25, 2.0, 0.62


class SimClock:
    def __init__(self, start, broker=None):
        self.t = start
        self.broker = broker

    def now(self):
        return self.t

    def sleep(self, s):
        self.t = self.t + timedelta(seconds=max(s, 1))
        if self.broker:
            self.broker.advance(self.t)


class SimProjectX:
    def __init__(self, five, minutes, clock, n_accounts=2, fail_rate=0.0, seed=0):
        self.five, self.min, self.clock = five, minutes, clock
        self.fail_rate, self.rng = fail_rate, np.random.default_rng(seed)
        self.cid = "CON.F.US.MNQ.SIM"
        self.accts = {i: dict(id=i, name=f"SIM-{i}", balance=50000.0, canTrade=True) for i in range(1, n_accounts + 1)}
        self.pos = {i: None for i in self.accts}           # (side, size, avg)
        self.orders = {i: {} for i in self.accts}           # id -> dict
        self.fills = {i: [] for i in self.accts}
        self.next_id = 1000
        self.done_min = None                                 # last minute bar already processed
        self.calls = 0

    # ---- market state ----------------------------------------------------------------
    def _minutes_until(self, t):
        return self.min[self.min.index + pd.Timedelta(minutes=1) <= t]

    def price(self):
        m = self._minutes_until(self.clock.now())
        return float(m.c.iloc[-1]) if len(m) else float(self.five.close.iloc[-1])

    def advance(self, t):
        m = self._minutes_until(t)
        if self.done_min is not None:
            m = m[m.index > self.done_min]
        for ts, b in m.iterrows():
            for a in self.accts:
                for oid, o in list(self.orders[a].items()):
                    if o["type"] != STOP:
                        continue
                    buy = o["side"] == BUY
                    if (buy and b.h >= o["stopPrice"]) or (not buy and b.l <= o["stopPrice"]):
                        px = max(o["stopPrice"], b.o) if buy else min(o["stopPrice"], b.o)
                        self._fill(a, o["side"], o["size"], px + (TICK if buy else -TICK))
                        del self.orders[a][oid]
            self.done_min = ts

    def _fill(self, a, side, size, px):
        sgn = 1 if side == BUY else -1
        p = self.pos[a]
        self.accts[a]["balance"] -= FEE * size
        if p is None:
            self.pos[a] = (sgn, size, px)
        else:
            s0, n0, avg = p
            if s0 == sgn:
                self.pos[a] = (s0, n0 + size, (avg * n0 + px * size) / (n0 + size))
            else:
                closed = min(n0, size)
                pnl = (px - avg) * s0 * closed * USD_PER_PT
                self.accts[a]["balance"] += pnl
                self.fills[a].append(dict(time=self.clock.now(), pnl=pnl, price=px))
                left = n0 - closed
                self.pos[a] = (s0, left, avg) if left else None
                if size > closed:
                    self.pos[a] = (sgn, size - closed, px)

    def _maybe_fail(self, what):
        self.calls += 1
        if self.fail_rate and self.rng.random() < self.fail_rate:
            raise ApiError(f"simulated failure on {what}")

    # ---- API surface used by the bot -------------------------------------------------
    def login(self):
        pass

    def accounts(self):
        self.calls += 1
        return [dict(v) for v in self.accts.values()]

    def find_contract(self, symbol):
        return {"id": self.cid, "tickSize": TICK, "activeContract": True}

    def bars(self, contract_id, start, end, unit_number=5, limit=2000, partial=True):
        self._maybe_fail("bars")
        now = self.clock.now()
        hist = self.five[(self.five.index >= start) & (self.five.index < now.replace(hour=0, minute=0))]
        rows = [dict(t=ts.isoformat(), o=b.open, h=b.high, l=b.low, c=b.close, v=0) for ts, b in hist.iterrows()]
        today = self._minutes_until(now)
        today = today[today.index.date == now.date()]
        if len(today):
            g = today.groupby(today.index.floor("5min"))
            for ts, x in g:
                rows.append(dict(t=ts.isoformat(), o=x.o.iloc[0], h=x.h.max(), l=x.l.min(), c=x.c.iloc[-1], v=0))
        return rows

    def place(self, account_id, contract_id, order_type, side, size, stop_price=None, tag=None):
        self._maybe_fail("place")
        oid = self.next_id
        self.next_id += 1
        if order_type == MARKET:
            px = self.price() + (TICK if side == BUY else -TICK)
            self._fill(account_id, side, size, px)
        else:
            self.orders[account_id][oid] = dict(id=oid, contractId=self.cid, type=order_type, side=side, size=size, stopPrice=stop_price)
        return oid

    def modify_stop(self, account_id, order_id, stop_price):
        self._maybe_fail("modify_stop")
        self.orders[account_id][order_id]["stopPrice"] = stop_price

    def cancel(self, account_id, order_id):
        self._maybe_fail("cancel")
        self.orders[account_id].pop(order_id, None)

    def open_orders(self, account_id):
        self._maybe_fail("open_orders")
        return list(self.orders[account_id].values())

    def positions(self, account_id):
        self._maybe_fail("positions")
        p = self.pos[account_id]
        if p is None:
            return []
        return [dict(contractId=self.cid, type=LONG if p[0] > 0 else SHORT, size=p[1], averagePrice=p[2])]

    def close_position(self, account_id, contract_id):
        self._maybe_fail("close_position")
        p = self.pos[account_id]
        if p:
            px = self.price() + (-TICK if p[0] > 0 else TICK)
            self._fill(account_id, 0 if p[0] < 0 else 1, p[1], px)     # buy to close a short, sell to close a long

    def trades(self, account_id, start, end):
        return [dict(profitAndLoss=f["pnl"], fees=0, voided=False) for f in self.fills[account_id] if start <= f["time"] <= end]


def load():
    five = pd.read_csv("data/5min/NDX.csv.gz")
    five["ts"] = pd.to_datetime(five.ts, utc=True).dt.tz_convert(NY)
    five = five.set_index("ts")
    m = pd.read_csv("data/ndx_1min_sample.csv.gz")
    m["ts"] = pd.to_datetime(m.ts, utc=True).dt.tz_convert(NY)
    return five, m.set_index("ts")


def run_day(day, five, minutes, poll=20, dry=False, fail_rate=0.0, entry_mode="market"):
    m = minutes[minutes.index.date == day]
    start = pd.Timestamp(f"{day} 09:24", tz=NY).to_pydatetime()
    clock = SimClock(start)
    api = SimProjectX(five, m, clock, fail_rate=fail_rate, seed=hash(day) % 1000)
    clock.broker = api
    log = logging.getLogger(f"sim-{day}")
    log.setLevel(logging.WARNING)
    cfg = dict(accounts=["SIM-1", "SIM-2"], dry_run=dry, poll_seconds=poll, log_dir="/tmp/claude-0/-home-user-Hill/803afca5-f875-54ad-bfda-380b3ec237b9/scratchpad/simlogs",
               stop_file="/nonexistent/STOP", contracts_per_account=1, daily_loss_limit=600, entry_mode=entry_mode)
    bot = Bot(cfg, api, clock, log)
    bot.start()
    bot.run(until=pd.Timestamp(f"{day} 16:06", tz=NY).to_pydatetime())
    model_pts = sum(t.get("pnl_pts", 0) for t in bot.closed_trades)
    acct = {a: api.accts[a]["balance"] - 50000 for a in api.accts}
    open_left = any(api.pos[a] for a in api.accts) or any(api.orders[a] for a in api.accts)
    costs = len(bot.closed_trades) * 2.24
    return dict(date=day, model_usd=model_pts * USD_PER_PT - costs, model_trades=len(bot.closed_trades),
                acct1=acct[1], acct2=acct[2], api_calls=api.calls, leftover=open_left)


if __name__ == "__main__":
    five, minutes = load()
    days = sorted(set(minutes.index.date))
    fail = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    mode = sys.argv[2] if len(sys.argv) > 2 else "market"
    if fail:
        days = days[::4]
        print(f"injecting API failures on {fail:.0%} of calls, {len(days)} days")
    rows = [run_day(d, five, minutes, fail_rate=fail, entry_mode=mode) for d in days]
    r = pd.DataFrame(rows)
    pd.set_option("display.width", 160)
    print(r.to_string(index=False, float_format=lambda v: f"{v:,.1f}"))
    ref = pd.read_csv("live/intrabar_sample.csv", parse_dates=["date"])
    ref["date"] = ref.date.dt.date
    j = r.merge(ref, on="date")
    print(f"\n{len(r)} simulated days. Model net ${r.model_usd.sum():,.0f}; account 1 net ${r.acct1.sum():,.0f}; "
          f"account 2 net ${r.acct2.sum():,.0f}; minute-resolved backtest (net of costs) ${j.pes.sum():,.0f} / ${j.opt.sum():,.0f}")
    print(f"days with leftover positions or orders after the close: {int(r.leftover.sum())}; "
          f"API calls per day: median {int(r.api_calls.median())}, max {int(r.api_calls.max())}")
