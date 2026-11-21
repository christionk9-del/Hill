"""Offline stand-in for the Coinbase client, driven by 1-minute bars, so the real bot runs end to end.

  - Market orders fill at the current price plus slippage.
  - Stop-limit orders trigger when a minute bar trades through the stop and fill at the worse of the
    stop and the bar's open plus slippage, but never beyond the limit; if the limit is skipped the
    order rests as a limit order until price comes back.
  - Fees are charged per side as a fraction of notional.
  - candles() returns 5-minute bars as they looked at the simulated time (last one partial).

    python coinbase_perp/sim_exchange.py [BTC|ETH] [forward_test|lab_baseline] [fee_bps] [slip_bps] [fail_rate]
"""
import logging
import os
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from coinbase_perp.coinbase_api import ApiError   # noqa: E402
from coinbase_perp.bot import Bot, NY               # noqa: E402

CONTRACT = {"BTC": 0.01, "ETH": 0.1}
SCRATCH = os.environ.get("CB_SIM_LOGS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "sim"))


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


class SimCoinbase:
    def __init__(self, pid, five_hist, minutes, clock, contract_size, tick=0.01, equity=10000.0,
                 fee_bps=2.0, slip_bps=1.0, fail_rate=0.0, seed=0):
        self.pid, self.clock = pid, clock
        self.hist = [(ts.to_pydatetime(), float(r.open), float(r.high), float(r.low), float(r.close)) for ts, r in five_hist.iterrows()]
        self.mt = list(minutes.index.to_pydatetime())
        self.m = minutes[["open", "high", "low", "close"]].to_numpy(float)
        self.cs, self.tick = contract_size, tick
        self.fee, self.slip = fee_bps / 1e4, slip_bps / 1e4
        self.cash = equity
        self.pos = None                     # (side, n, avg)
        self.orders = {}
        self.next_id = 1
        self.k = 0                          # minute bars processed
        self.fail_rate, self.rng = fail_rate, np.random.default_rng(seed)
        self.calls = 0
        self.fees_paid = 0.0
        self.fills = []

    # ---- market state
    def _done(self, t):
        """Number of minute bars fully completed at time t."""
        k = self.k
        while k < len(self.mt) and self.mt[k] + timedelta(minutes=1) <= t:
            k += 1
        return k

    def price(self):
        k = self._done(self.clock.now())
        return self.m[k - 1, 3] if k else self.hist[-1][4]

    def advance(self, t):
        k1 = self._done(t)
        for k in range(self.k, k1):
            o, h, l, c = self.m[k]
            for oid, od in list(self.orders.items()):
                buy = od["side"] > 0
                if not od["triggered"]:
                    if (buy and h >= od["stop"]) or (not buy and l <= od["stop"]):
                        od["triggered"] = True
                        px = (max(od["stop"], o) * (1 + self.slip)) if buy else (min(od["stop"], o) * (1 - self.slip))
                        if (buy and px <= od["limit"]) or (not buy and px >= od["limit"]):
                            self._fill(od["side"], od["size"], px)
                            del self.orders[oid]
                            continue
                if od["triggered"] and oid in self.orders:
                    if (buy and l <= od["limit"]) or (not buy and h >= od["limit"]):
                        self._fill(od["side"], od["size"], od["limit"])
                        del self.orders[oid]
        self.k = k1

    def _fill(self, side, n, px):
        fee = self.fee * px * n * self.cs
        self.cash -= fee
        self.fees_paid += fee
        p = self.pos
        if p is None:
            self.pos = (side, n, px)
        elif p[0] == side:
            self.pos = (side, p[1] + n, (p[2] * p[1] + px * n) / (p[1] + n))
        else:
            closed = min(p[1], n)
            self.cash += (px - p[2]) * p[0] * closed * self.cs
            left = p[1] - closed
            self.pos = (p[0], left, p[2]) if left else None
            if n > closed:
                self.pos = (side, n - closed, px)
        self.fills.append(dict(time=self.clock.now(), side=side, n=n, px=px))

    def _maybe_fail(self, what):
        self.calls += 1
        if self.fail_rate and self.rng.random() < self.fail_rate:
            raise ApiError(f"simulated failure on {what}")

    # ---- client surface used by the bot
    def product(self, pid):
        return dict(id=pid, tick=self.tick, size_step=1, contract_size=self.cs, product_type="FUTURE",
                    expiry_type="PERPETUAL", trading_disabled=False, status="online")

    def candles(self, pid, start, end, granularity="FIVE_MINUTE"):
        self._maybe_fail("candles")
        now = self.clock.now()
        day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = [b for b in self.hist if start <= b[0] < min(end, day0)]
        k = self._done(now)
        i = k
        while i > 0 and self.mt[i - 1] >= day0:
            i -= 1
        cur = None
        for j in range(i, k):
            ts = self.mt[j]
            b0 = ts.replace(minute=ts.minute - ts.minute % 5, second=0)
            o, h, l, c = self.m[j]
            if cur is None or cur[0] != b0:
                if cur:
                    rows.append(tuple(cur))
                cur = [b0, o, h, l, c]
            else:
                cur[2], cur[3], cur[4] = max(cur[2], h), min(cur[3], l), c
        if cur:
            rows.append(tuple(cur))
        b0 = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        m0 = now.hour * 60 + now.minute
        if rows and rows[-1][0] < b0 and 570 <= m0 < 960 and now.weekday() < 5:
            c = rows[-1][4]                       # a new bar has started: the exchange shows it at once
            rows.append((b0, c, c, c, c))
        return rows

    def best_bid_ask(self, pid):
        p = self.price()
        return p - self.tick, p + self.tick

    def balance(self):
        self._maybe_fail("balance")
        u = 0.0
        if self.pos:
            u = (self.price() - self.pos[2]) * self.pos[0] * self.pos[1] * self.cs
        return dict(total=self.cash + u, cash=self.cash, buying_power=self.cash)

    def position(self, pid):
        self._maybe_fail("position")
        return self.pos

    def open_orders(self, pid):
        self._maybe_fail("open_orders")
        return [dict(id=k, client_id=v["cid"], side=v["side"], kind="stop", stop=v["stop"], limit=v["limit"], size=v["size"])
                for k, v in self.orders.items()]

    def market(self, pid, side, n, tag="dv"):
        self._maybe_fail("market")
        p = self.price()
        self._fill(side, n, p * (1 + side * self.slip))
        oid = f"m{self.next_id}"
        self.next_id += 1
        return oid

    def stop_order(self, pid, side, n, stop, limit, tag="dv"):
        self._maybe_fail("stop_order")
        p = self.price()
        if (side > 0 and stop <= p) or (side < 0 and stop >= p):
            raise ApiError("stop price on the wrong side of the market")
        oid = f"o{self.next_id}"
        self.next_id += 1
        self.orders[oid] = dict(side=side, size=n, stop=stop, limit=limit, triggered=False, cid=f"{tag}-sim")
        return oid

    def edit_stop(self, oid, n, stop, limit):
        self._maybe_fail("edit_stop")
        od = self.orders.get(oid)
        if od is None or od["triggered"]:
            raise ApiError("order not editable")
        p = self.price()
        if (od["side"] > 0 and stop <= p) or (od["side"] < 0 and stop >= p):
            raise ApiError("stop price on the wrong side of the market")
        od.update(size=n, stop=stop, limit=limit)

    def cancel(self, ids):
        self._maybe_fail("cancel")
        for i in ids:
            self.orders.pop(i, None)

    def close_position(self, pid, n):
        self._maybe_fail("close_position")
        if self.pos:
            s, q, _ = self.pos
            self._fill(-s, min(n, q), self.price() * (1 - s * self.slip))


# ------------------------------------------------------------------------ data + runner
def load(sym):
    """5-minute history (backtest file + minutes rolled up) and the 1-minute session bars."""
    m = pd.read_csv(f"data/1min/{sym}.csv.gz")
    m["ts"] = pd.to_datetime(m.ts, utc=True).dt.tz_convert(NY)
    m = m.set_index("ts")[["open", "high", "low", "close"]]
    f5 = m.resample("5min").agg(dict(open="first", high="max", low="min", close="last")).dropna()
    mm = f5.index.hour * 60 + f5.index.minute
    f5 = f5[(mm >= 570) & (mm < 960) & (f5.index.dayofweek < 5)]
    old = pd.read_csv(f"data/5min/{sym}.csv.gz")
    old["ts"] = pd.to_datetime(old.ts, utc=True).dt.tz_convert(NY)
    old = old.set_index("ts")[["open", "high", "low", "close"]]
    five = pd.concat([old[old.index < f5.index[0]], f5])
    return five, m


def run_day(sym, day, five, minutes, preset="forward_test", fee_bps=2.0, slip_bps=1.0, fail_rate=0.0,
            entry_mode="resting", poll=10, equity=10000.0, risk_pct=0.5, max_contracts=1000, max_leverage=5.0):
    mday = minutes[minutes.index.date == day]
    hist = five[(five.index.date < day) & (five.index.date >= day - timedelta(days=10))]
    clock = SimClock(pd.Timestamp(f"{day} 09:24", tz=NY).to_pydatetime())
    api = SimCoinbase(f"{sym}-PERP", hist, mday, clock, CONTRACT[sym], equity=equity, fee_bps=fee_bps, slip_bps=slip_bps,
                      fail_rate=fail_rate, seed=hash((sym, str(day))) % 10000)
    clock.broker = api
    log = logging.getLogger(f"sim-{sym}")
    log.setLevel(logging.ERROR)
    cfg = dict(dry_run=False, preset=preset, entry_mode=entry_mode, poll_seconds=poll, daily_loss_limit_pct=100,
               log_dir=SCRATCH, stop_file="/nonexistent/STOP",
               markets=[dict(product_id=f"{sym}-PERP", risk_pct=risk_pct, max_contracts=max_contracts, max_leverage=max_leverage)])
    bot = Bot(cfg, api, clock, log)
    bot.start()
    bot.run(until=pd.Timestamp(f"{day} 16:06", tz=NY).to_pydatetime())
    m = bot.markets[0]
    return dict(date=day, pnl=round(api.cash - equity, 2), fees=round(api.fees_paid, 2), model_trades=len(m.closed),
                model_R=round(sum(t["r"] for t in m.closed), 3), fills=len(api.fills),
                leftover=bool(api.pos) or bool(api.orders), calls=api.calls, trades=m.closed)


if __name__ == "__main__":
    a = sys.argv[1:]
    sym = a[0] if a else "BTC"
    preset = a[1] if len(a) > 1 else "forward_test"
    fee, slip = (float(a[2]) if len(a) > 2 else 2.0), (float(a[3]) if len(a) > 3 else 1.0)
    fail = float(a[4]) if len(a) > 4 else 0.0
    five, minutes = load(sym)
    days = sorted(set(minutes.index.date))
    days = [d for d in days if (minutes.index.date == d).sum() >= 385][5:]
    if fail:
        days = days[::5]
    rows = [run_day(sym, d, five, minutes, preset, fee, slip, fail) for d in days]
    r = pd.DataFrame(rows).drop(columns="trades")
    eq = 10000 + r.pnl.cumsum()
    dd = (eq.cummax() - eq).max()
    print(f"{sym} {preset} fee {fee} bps/side slip {slip} bps/side, fail {fail:.0%}: {len(r)} days, "
          f"net ${r.pnl.sum():,.0f} on $10,000 at 0.5% risk (fees ${r.fees.sum():,.0f}), "
          f"model {r.model_R.sum():+.1f}R gross over {r.model_trades.sum()} trades, max DD ${dd:,.0f}, "
          f"winning days {np.mean(r.pnl > 0):.0%}, leftover days {int(r.leftover.sum())}, calls/day {int(r.calls.median())}")
