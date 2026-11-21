"""Coinbase perpetual-style futures bot for the intraday Donchian breakout (same rules as live/engine.py).

For each configured market (for example nano BTC and nano ETH perps):
  - Signals come from 5-minute bars of the signal product (default: the spot pair, e.g. BTC-USD,
    which is what the backtest used) during 9:30-16:00 New York on weekdays.
  - Entry: resting stop-limit orders at the channel (or a market order when the model sees the
    breakout). Order prices are moved from spot to the perp by the current perp - spot basis.
  - Protective stop-limit order at the exchange right after the fill, only ever tightened.
    If price runs through a stop's limit the model exit closes the position at market.
  - Flat after the 15:55 bar; hard flatten at 16:02 New York as a backstop.

Sizing: contracts = account value x risk_pct / (stop distance x contract size), capped by
max_contracts and by max_leverage (position notional / account value).

Safety:
  - dry_run is on by default: live prices, no orders sent (every order is logged).
  - A file named STOP next to the config flattens everything and ends the bot.
  - Daily loss limit as % of the day's starting account value: flatten and stop for the day.
  - Every bar close reconciles the exchange with the model (missing stop, unexpected position,
    wrong side, oversize) and fixes mismatches. A position that cannot be flattened switches the
    market off with a CRITICAL log line.
  - On restart, an open position that matches the model's replayed trade is adopted; anything
    else is flattened.

    python -m coinbase_perp.bot check coinbase_perp/config.json   # read-only checks, no orders
    python -m coinbase_perp.bot run   coinbase_perp/config.json
"""
import csv
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live.engine import DonchianEngine, Params          # noqa: E402
from coinbase_perp.coinbase_api import Coinbase, ApiError  # noqa: E402

NY = ZoneInfo("America/New_York")
BAR = timedelta(minutes=5)

PRESETS = {
    # what the MNQ forward test runs (chosen on Nasdaq data)
    "forward_test": dict(n=24, exit_n=24, stop_atr=1.0, trail_atr=2.0, max_trades=3, first_bar=1, last_bar=48, atr_len=14),
    # the lab baseline (chosen earlier on SPY); held up better on crypto after costs
    "lab_baseline": dict(n=78, exit_n=24, stop_atr=3.0, trail_atr=None, max_trades=3, first_bar=1, last_bar=66, atr_len=14),
}


class RealClock:
    def now(self):
        return datetime.now(NY)

    def sleep(self, s):
        time.sleep(s)


def rth_index(ts):
    m = ts.hour * 60 + ts.minute - 570
    return m // 5 if 0 <= m < 390 else None


def strategy_params(cfg):
    p = dict(PRESETS[cfg.get("preset", "forward_test")])
    p.update(cfg.get("strategy", {}))
    return p


class Market:
    def __init__(self, mc, params):
        self.pid = mc["product_id"]
        self.sig = mc.get("signal_product", self.pid)
        self.risk_pct = float(mc.get("risk_pct", 0.5))
        self.max_contracts = int(mc.get("max_contracts", 1))
        self.max_leverage = float(mc.get("max_leverage", 3.0))
        self.engine = DonchianEngine(Params(**params))
        self.info = None
        self.basis = 0.0
        self.in_pos = False
        self.side = 0
        self.contracts = 0
        self.stop_id = None
        self.stop_px = None
        self.entry_ids = {}        # side -> order id of resting entry
        self.entry_lv = {}         # side -> order price
        self.entry_size = 0
        self.follow = True         # False while the model holds a trade the account did not take
        self.disabled = False      # switched off for the day
        self.last_done = None
        self.cur = None
        self.entry_seen = None
        self.trades = []           # model trades today
        self.closed = []           # every closed model trade (reporting)


class Bot:
    def __init__(self, cfg, api, clock=None, log=None):
        self.cfg = cfg
        self.api = api
        self.clock = clock or RealClock()
        self.log = log or logging.getLogger("cbbot")
        self.dry = bool(cfg.get("dry_run", True))
        self.entry_mode = cfg.get("entry_mode", "resting")
        self.poll = float(cfg.get("poll_seconds", 5))
        self.stop_buf = float(cfg.get("stop_limit_buffer_pct", 0.5)) / 100
        self.entry_buf = float(cfg.get("entry_limit_buffer_pct", 0.15)) / 100
        self.max_chase_R = float(cfg.get("max_chase_R", 0.5))
        self.loss_pct = float(cfg.get("daily_loss_limit_pct", 3.0)) / 100
        self.paper_equity = float(cfg.get("paper_equity", 10000))
        self.stop_file = cfg.get("stop_file", "STOP")
        self.log_dir = cfg.get("log_dir", "logs")
        os.makedirs(self.log_dir, exist_ok=True)
        params = strategy_params(cfg)
        self.markets = [Market(mc, params) for mc in cfg["markets"] if mc.get("enabled", True)]
        self.equity = None
        self.day = None
        self.day_equity = None
        self.halted = False
        self.eod_done = False

    # ---------------------------------------------------------------- setup
    def start(self, attempts=5):
        for k in range(attempts):
            try:
                return self._start()
            except ApiError as e:
                if k == attempts - 1:
                    raise
                self.log.error(f"startup API error: {e}; retrying")
                self.clock.sleep(5 * (k + 1))

    def _start(self):
        for m in self.markets:
            m.engine = DonchianEngine(m.engine.p)           # clean model on every attempt
            m.info = self.api.product(m.pid)
            sig = self.api.product(m.sig) if m.sig != m.pid else m.info
            m.engine.p.tick = sig["tick"]
            if m.info.get("trading_disabled"):
                raise SystemExit(f"{m.pid}: trading disabled on the exchange")
            self.log.info(f"{m.pid}: tick {m.info['tick']}, contract size {m.info['contract_size']}, signals from {m.sig}, "
                          f"risk {m.risk_pct}%/trade, max {m.max_contracts} contracts, max leverage {m.max_leverage}x")
        self._refresh_equity()
        self.log.info(f"{'DRY RUN - no orders will be sent' if self.dry else 'LIVE ORDERS'}; entry mode {self.entry_mode}; "
                      f"strategy {strategy_params(self.cfg)}; account value {self.equity:,.2f}")
        for m in self.markets:
            self._warmup(m)

    def _bars(self, m, start, end):
        """5-minute session bars of the signal product, missing bars filled flat at the prior close."""
        raw = self.api.candles(m.sig, start, end)
        out, prev = [], None
        for t, o, h, l, c in raw:
            ts = t.astimezone(NY)
            if ts.weekday() >= 5 or rth_index(ts) is None:
                prev = c
                continue
            if out and out[-1][0].date() == ts.date() and prev is not None:
                k = out[-1][0] + BAR
                while k < ts:
                    out.append((k, prev, prev, prev, prev))
                    k += BAR
            out.append((ts, o, h, l, c))
            prev = c
        return out

    def _warmup(self, m):
        now = self.clock.now()
        bars = self._bars(m, now - timedelta(days=7), now)
        done = [b for b in bars if b[0] + BAR <= now]
        hist = [b for b in done if b[0].date() < now.date()]
        for b in hist:
            m.engine.warmup(b[2], b[3], b[4])
        m.last_done = hist[-1][0] if hist else None
        todays = [b for b in done if b[0].date() == now.date()]
        for b in todays:                                   # started mid-session: rebuild the model's day
            self._begin(m, b)
            m.engine.on_price(b[2], b[3])
            m.engine.end_bar(b[1], b[2], b[3], b[4])
            m.last_done = b[0]
        self.log.info(f"{m.pid}: warmed up on {len(hist)} bars (+{len(todays)} from today); ready {m.engine.ready()}")
        if self.dry:
            m.follow = m.engine.pos is None
            return
        pos = self.api.position(m.pid)
        mp = m.engine.pos
        if pos and mp and pos[0] == mp.side and pos[1] <= m.max_contracts:
            m.in_pos, m.side, m.contracts, m.follow = True, pos[0], int(pos[1]), True
            self.log.warning(f"{m.pid}: adopting open position {pos} that matches the model trade; stop {mp.stop}")
            self._reconcile(m)
        elif pos:
            self.log.warning(f"{m.pid}: open position {pos} does not match the model; flattening")
            self._flatten(m, "startup")
            m.follow = mp is None
        else:
            self._cancel_bot_orders(m)
            m.follow = mp is None

    # ---------------------------------------------------------------- loop
    def run(self, until=None):
        while True:
            now = self.clock.now()
            if until and now >= until:
                return
            if os.path.exists(self.stop_file):
                self.log.warning("STOP file found: flattening everything and exiting")
                self.flatten_all("stop file")
                return
            hm = now.hour * 60 + now.minute
            if now.weekday() >= 5 or not (9 * 60 + 20 <= hm <= 16 * 60 + 10):
                self.clock.sleep(30 if until else 60)
                continue
            try:
                self.step(now)
            except ApiError as e:
                self.log.error(f"API error: {e}; retrying next poll")
            except Exception:
                self.log.exception("unexpected error; flattening for safety")
                self.flatten_all("error")
            self.clock.sleep(self.poll)

    def step(self, now):
        if self.day != now.date():
            self._new_day(now)
        for m in self.markets:
            self._step_market(m, now)
        if now.hour * 60 + now.minute >= 16 * 60 + 2 and not self.eod_done:
            self.flatten_all("end of day backstop")
            self._daily_report(now)
            self.eod_done = True

    def _new_day(self, now):
        self.day, self.eod_done, self.halted = now.date(), False, False
        self._refresh_equity()
        self.day_equity = self.equity
        for m in self.markets:
            m.disabled, m.trades = False, []
        self.log.info(f"new day {self.day}; account value {self.equity:,.2f}")

    def _step_market(self, m, now):
        bars = self._bars(m, now - timedelta(hours=8), now + timedelta(minutes=1))
        for b in bars:
            ts = b[0]
            if m.last_done is not None and ts <= m.last_done:
                continue
            if ts + BAR <= now:                       # completed bar
                if m.cur != ts:
                    self._begin(m, b)
                self._price(m, b)
                for e in m.engine.end_bar(b[1], b[2], b[3], b[4]):
                    self._handle(m, e, b)
                m.last_done = ts
                self._refresh_equity()
                self._reconcile(m)
                self._loss_guard()
            else:                                     # bar in progress
                if m.cur != ts:
                    self._begin(m, b)
                    self._arm(m)
                self._check_resting_fill(m, b)
                self._price(m, b)

    def _begin(self, m, b):
        m.entry_seen = None
        m.engine.begin_bar(b[0].date(), rth_index(b[0]), b[1], True)
        m.cur = b[0]

    def _price(self, m, b):
        h, l = b[2], b[3]
        ps = m.engine.pos
        if ps is not None and ps.bar_idx == m.engine.idx and m.entry_seen is not None:
            eh, el = m.entry_seen                       # on the entry bar only moves after the fill count:
            h = max(b[4], h if h > eh else float("-inf"))   # new extremes, or the last price
            l = min(b[4], l if l < el else float("inf"))
        ev = m.engine.on_price(h, l)
        if ev is not None and ev.kind == "entry":
            m.entry_seen = (b[2], b[3])
        self._handle(m, ev, b)

    # ---------------------------------------------------------------- helpers
    def _refresh_equity(self):
        if self.dry:
            self.equity = self.paper_equity
            return
        self.equity = self.api.balance()["total"]

    def _update_basis(self, m):
        if m.sig == m.pid or self.dry and not hasattr(self.api, "best_bid_ask"):
            m.basis = 0.0
            return
        try:
            bid, ask = self.api.best_bid_ask(m.pid)
            sb, sa = self.api.best_bid_ask(m.sig)
            m.basis = (bid + ask) / 2 - (sb + sa) / 2
        except ApiError as e:
            self.log.warning(f"{m.pid}: basis update failed ({e}); keeping {m.basis:.2f}")

    def _px(self, m, model_px, up):
        """Model (signal) price -> exchange price, rounded to the tick away from the market for stops."""
        t = m.info["tick"]
        x = (model_px + m.basis) / t
        return round((math.ceil(x - 1e-9) if up else math.floor(x + 1e-9)) * t, 10)

    def _size(self, m, R, price):
        cs = m.info["contract_size"]
        if R <= 0 or not self.equity:
            return 0
        n = math.floor(self.equity * m.risk_pct / 100 / (R * cs))
        n = min(n, m.max_contracts, math.floor(self.equity * m.max_leverage / (price * cs)))
        return max(int(n), 0)

    # ---------------------------------------------------------------- events -> orders
    def _handle(self, m, ev, bar):
        if ev is None:
            return
        if ev.kind == "entry":
            m.trades.append(dict(time=bar[0].isoformat(), product=m.pid, side=ev.side, entry=ev.price, stop=ev.stop, R=ev.info["R"]))
            self.log.info(f"{m.pid}: MODEL ENTRY {'LONG' if ev.side > 0 else 'SHORT'} at {ev.price:.2f}, stop {ev.stop:.2f}, R {ev.info['R']:.2f}")
            if m.follow and not m.disabled and not self.halted and not m.in_pos:
                try:
                    self._enter(m, ev)
                except ApiError as e:
                    self.log.error(f"{m.pid}: entry failed: {e}; reconciling")
                    self._reconcile(m)
            else:
                m.follow = False
                self._disarm(m)
        elif ev.kind == "stop_moved":
            if m.in_pos and m.follow:
                self._move_stop(m, ev.stop)
        elif ev.kind == "exit":
            r = ev.info["pnl_pts"] / m.trades[-1]["R"] if m.trades else 0.0
            if m.trades:
                m.trades[-1].update(exit=ev.price, reason=ev.reason, pnl_pts=ev.info["pnl_pts"], r=r, exit_time=bar[0].isoformat())
                m.closed.append(dict(m.trades[-1]))
            self.log.info(f"{m.pid}: MODEL EXIT ({ev.reason}) at {ev.price:.2f}, {ev.info['pnl_pts']:+.2f} pts ({r:+.2f}R)")
            if m.in_pos:
                self._exit(m, ev.reason)
            m.follow = True

    def _arm(self, m):
        if self.entry_mode != "resting":
            return
        lv = m.engine.entry_levels() if (m.follow and not m.disabled and not self.halted) else None
        if m.in_pos or lv is None:
            self._disarm(m)
            return
        self._update_basis(m)
        R = m.engine.p.stop_atr * m.engine.atr_now
        size = self._size(m, R, lv[0] + m.basis)
        if size < 1:
            self._disarm(m)
            return
        if m.entry_size and size != m.entry_size:
            self._disarm(m)                            # size changed: re-place both
        want = {1: self._px(m, lv[0], True), -1: self._px(m, lv[1], False)}
        for side, px in want.items():
            lim = self._px(m, (px - m.basis) * (1 + side * self.entry_buf), side > 0)
            if self.dry:
                m.entry_lv[side], m.entry_size = px, size
                continue
            try:
                if side in m.entry_ids:
                    if m.entry_lv.get(side) != px:
                        self.api.edit_stop(m.entry_ids[side], size, px, lim)
                        m.entry_lv[side] = px
                else:
                    m.entry_ids[side] = self.api.stop_order(m.pid, side, size, px, lim, tag="dv-in")
                    m.entry_lv[side] = px
                m.entry_size = size
            except ApiError as e:
                self.log.warning(f"{m.pid}: could not arm {'buy' if side > 0 else 'sell'} stop at {px}: {e}")
                if side in m.entry_ids:
                    self._cancel(m, [m.entry_ids.pop(side)])
                m.entry_lv.pop(side, None)

    def _disarm(self, m):
        if m.entry_ids and not self.dry:
            self._cancel(m, list(m.entry_ids.values()))
        m.entry_ids, m.entry_lv, m.entry_size = {}, {}, 0

    def _cancel(self, m, ids):
        try:
            self.api.cancel(ids)
        except ApiError as e:
            self.log.warning(f"{m.pid}: cancel {ids} failed: {e}")

    def _check_resting_fill(self, m, b):
        """Live resting mode: notice an exchange fill before the next bar close and protect it at once."""
        if self.dry or not m.entry_ids or m.in_pos or m.engine.pos is not None:
            return
        pos = self.api.position(m.pid)
        if not pos:
            return
        eng = m.engine
        if eng.entry_levels() is None:
            return
        side = pos[0]
        ev = eng.enter(side, max(eng.hi, eng.bar_open) if side > 0 else min(eng.lo, eng.bar_open))
        m.entry_seen = (b[2], b[3])
        self._handle(m, ev, b)

    def _enter(self, m, ev):
        R = ev.info["R"]
        if self.entry_mode == "resting" and m.entry_size:
            return self._enter_resting(m, ev)
        self._update_basis(m)
        size = self._size(m, R, ev.price + m.basis)
        if size < 1:
            self.log.warning(f"{m.pid}: position size rounds to 0 contracts at {m.risk_pct}% risk; skipping trade")
            m.follow = False
            return
        stop_px = self._px(m, ev.stop, ev.side < 0)
        if self.dry:
            self.log.info(f"[dry] {m.pid}: would {'BUY' if ev.side > 0 else 'SELL'} {size} at market, then stop at {stop_px}")
            m.in_pos, m.side, m.contracts, m.stop_px = True, ev.side, size, stop_px
            return
        if not self._chase_ok(m, ev):
            return
        self.api.market(m.pid, ev.side, size, tag="dv-mkt")
        pos = None
        for _ in range(10):
            pos = self.api.position(m.pid)
            if pos:
                break
            self.clock.sleep(1)
        if not pos:
            self.log.error(f"{m.pid}: market entry not filled after 10 s")
            self._cancel_bot_orders(m)
            return
        self._protect(m, pos, ev)

    def _chase_ok(self, m, ev):
        try:
            bid, ask = self.api.best_bid_ask(m.pid)
        except ApiError:
            return True
        px = ask if ev.side > 0 else bid
        model_px = ev.price + m.basis
        late = (px - model_px) * ev.side / ev.info["R"]
        if late > self.max_chase_R:
            self.log.warning(f"{m.pid}: price {px} is {late:.2f}R past the model entry; not chasing")
            m.follow = False
            return False
        return True

    def _enter_resting(self, m, ev):
        size = m.entry_size
        if self.dry:
            self.log.info(f"[dry] {m.pid}: resting {'buy' if ev.side > 0 else 'sell'} stop {size} at {m.entry_lv.get(ev.side)} fills; "
                          f"stop at {self._px(m, ev.stop, ev.side < 0)}")
            m.in_pos, m.side, m.contracts, m.stop_px = True, ev.side, size, self._px(m, ev.stop, ev.side < 0)
            m.entry_ids, m.entry_lv, m.entry_size = {}, {}, 0
            return
        pos = None
        for _ in range(4):
            pos = self.api.position(m.pid)
            if pos:
                break
            self.clock.sleep(1)
        if pos and pos[0] == ev.side:
            other = m.entry_ids.get(-ev.side)
            if other:
                self._cancel(m, [other])
            m.entry_ids, m.entry_lv, m.entry_size = {}, {}, 0
            self._protect(m, pos, ev)
            return
        if pos:
            self.log.warning(f"{m.pid}: position on the wrong side; flattening")
            self._flatten(m, "wrong side")
            return
        self.log.warning(f"{m.pid}: resting entry not filled; cancelling it and entering at market if not too late")
        self._disarm(m)
        self._enter(m, ev)

    def _protect(self, m, pos, ev):
        side, n, avg = pos
        stop_px = self._px(m, ev.stop, side < 0)
        lim = self._stop_limit(m, stop_px, side)
        m.in_pos, m.side, m.contracts = True, side, int(n)
        m.stop_id = self.api.stop_order(m.pid, -side, int(n), stop_px, lim, tag="dv-sl")
        m.stop_px = stop_px
        self.log.info(f"{m.pid}: filled {int(n)} {'long' if side > 0 else 'short'} at {avg:.2f}; stop {stop_px} (limit {lim})")

    def _stop_limit(self, m, stop_px, pos_side):
        t = m.info["tick"]
        x = stop_px * (1 - pos_side * self.stop_buf) / t
        return round((math.floor(x) if pos_side > 0 else math.ceil(x)) * t, 10)

    def _move_stop(self, m, model_stop):
        self._update_basis(m)
        new = self._px(m, model_stop, m.side < 0)
        if m.stop_px is not None and ((m.side > 0 and new <= m.stop_px) or (m.side < 0 and new >= m.stop_px)):
            return                                     # never loosen
        if self.dry:
            self.log.info(f"[dry] {m.pid}: stop {m.stop_px} -> {new}")
            m.stop_px = new
            return
        if m.stop_id is None:
            return self._reconcile(m)
        try:
            self.api.edit_stop(m.stop_id, m.contracts, new, self._stop_limit(m, new, m.side))
            m.stop_px = new
        except ApiError as e:
            self.log.warning(f"{m.pid}: stop edit failed ({e}); replacing")
            try:
                self.api.cancel([m.stop_id])
                m.stop_id = self.api.stop_order(m.pid, -m.side, m.contracts, new, self._stop_limit(m, new, m.side), tag="dv-sl")
                m.stop_px = new
            except ApiError as e2:
                self.log.error(f"{m.pid}: stop replace failed ({e2}); reconciling")
                self._reconcile(m)

    def _exit(self, m, reason):
        if self.dry:
            self.log.info(f"[dry] {m.pid}: flat ({reason})")
            m.in_pos, m.stop_id, m.stop_px = False, None, None
            return
        if reason == "stop":
            self.clock.sleep(2)                       # the exchange stop normally fills at the same level
        self._flatten(m, reason)

    # ---------------------------------------------------------------- safety
    def _bot_orders(self, m):
        return [o for o in self.api.open_orders(m.pid) if str(o.get("client_id") or "").startswith(("dv-", "close-"))]

    def _cancel_bot_orders(self, m, keep=()):
        ids = [o["id"] for o in self._bot_orders(m) if o["id"] not in keep]
        if ids:
            self._cancel(m, ids)

    def _flatten(self, m, reason):
        for attempt in range(2):
            pos = self.api.position(m.pid)
            if pos:
                try:
                    self.api.close_position(m.pid, pos[1])
                except ApiError as e:
                    self.log.warning(f"{m.pid}: close_position failed ({e}); trying a market order")
                    try:
                        self.api.market(m.pid, -pos[0], pos[1], tag="dv-flat")
                    except ApiError as e2:
                        self.log.error(f"{m.pid}: market close failed: {e2}")
                self.clock.sleep(1)
            self._cancel_bot_orders(m)
            m.entry_ids, m.entry_lv, m.entry_size = {}, {}, 0
            if not self.api.position(m.pid):
                self.log.info(f"{m.pid}: flat ({reason})")
                m.in_pos, m.stop_id, m.stop_px, m.contracts = False, None, None, 0
                return True
        self.log.critical(f"{m.pid}: STILL HAS A POSITION after two flatten attempts ({reason}). "
                          f"Market switched off for today. Check Coinbase now.")
        m.in_pos, m.disabled = False, True
        return False

    def _reconcile(self, m):
        if self.dry or (m.disabled and not m.in_pos):
            return
        try:
            pos = self.api.position(m.pid)
            model = m.engine.pos if m.follow else None
            if pos and (model is None or not m.in_pos):
                self.log.warning(f"{m.pid}: position {pos} with no model trade; flattening")
                return self._flatten(m, "unexpected position")
            if pos and (pos[0] != model.side or pos[1] > m.max_contracts):
                self.log.warning(f"{m.pid}: position {pos} wrong side or above max size; flattening")
                return self._flatten(m, "mismatch")
            if pos:
                m.side, m.contracts = pos[0], int(pos[1])
                want = self._px(m, model.stop, pos[0] < 0)
                stops = [o for o in self._bot_orders(m) if o["kind"] == "stop" and o["side"] == -pos[0] and o["id"] not in m.entry_ids.values()]
                if not stops:
                    lim = self._stop_limit(m, want, pos[0])
                    m.stop_id = self.api.stop_order(m.pid, -pos[0], m.contracts, want, lim, tag="dv-sl")
                    m.stop_px = want
                    self.log.warning(f"{m.pid}: protective stop was missing; placed at {want}")
                else:
                    s = stops[0]
                    m.stop_id = s["id"]
                    have = s["stop"] if s["stop"] is not None else want
                    better = (pos[0] > 0 and want > have) or (pos[0] < 0 and want < have)
                    if better or s["size"] != m.contracts:
                        px = want if better else have
                        self.api.edit_stop(s["id"], m.contracts, px, self._stop_limit(m, px, pos[0]))
                        self.log.info(f"{m.pid}: stop resynced {have} -> {px}, size {m.contracts}")
                        have = px
                    m.stop_px = have
                    extra = [o["id"] for o in stops[1:]]
                    if extra:
                        self._cancel(m, extra)
                return
            if m.in_pos:
                self.log.info(f"{m.pid}: stopped out at the exchange")
            m.in_pos, m.stop_id, m.stop_px, m.contracts = False, None, None, 0
            self._cancel_bot_orders(m, keep=set(m.entry_ids.values()))
        except ApiError as e:
            self.log.error(f"{m.pid}: reconcile failed: {e}")

    def _loss_guard(self):
        if self.dry or self.halted or not self.day_equity:
            return
        if self.equity <= self.day_equity * (1 - self.loss_pct):
            self.log.warning(f"account down {self.day_equity - self.equity:,.2f} today "
                             f"(limit {self.loss_pct:.1%}); flattening and stopping for the day")
            self.flatten_all("daily loss limit")
            self.halted = True

    def flatten_all(self, reason):
        for m in self.markets:
            try:
                if self.dry:
                    m.in_pos = False
                    m.entry_ids, m.entry_lv, m.entry_size = {}, {}, 0
                    continue
                self._flatten(m, reason)
            except ApiError as e:
                self.log.error(f"{m.pid}: flatten failed: {e}")

    # ---------------------------------------------------------------- reporting
    def _daily_report(self, now):
        self._refresh_equity()
        path = os.path.join(self.log_dir, "daily.csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date", "product", "model_trades", "model_R", "account_value", "day_pnl", "mode"])
            for m in self.markets:
                done = [t for t in m.trades if "exit" in t]
                w.writerow([now.date().isoformat(), m.pid, len(done), round(sum(t["r"] for t in done), 3), round(self.equity, 2),
                            round(self.equity - (self.day_equity or self.equity), 2), "dry" if self.dry else "live"])
        with open(os.path.join(self.log_dir, "model_trades.jsonl"), "a") as f:
            for m in self.markets:
                for t in m.trades:
                    f.write(json.dumps(t) + "\n")
        self.log.info("daily report written")


# -------------------------------------------------------------------- CLI
def setup_logging(log_dir):
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("cbbot")
    log.setLevel(logging.INFO)
    fh = logging.FileHandler(os.path.join(log_dir, f"bot-{datetime.now(NY):%Y-%m-%d}.log"))
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log


def load_config(path):
    cfg = json.load(open(path))
    base = os.path.dirname(os.path.abspath(path))
    cfg["log_dir"] = os.path.join(base, cfg.get("log_dir", "logs"))
    cfg["stop_file"] = os.path.join(base, cfg.get("stop_file", "STOP"))
    if cfg.get("preset", "forward_test") not in PRESETS:
        raise SystemExit(f"preset must be one of {list(PRESETS)}")
    return cfg


def make_api(cfg, log):
    name = os.environ.get(cfg.get("api_key_name_env", "COINBASE_API_KEY_NAME"))
    secret = os.environ.get(cfg.get("api_secret_env", "COINBASE_API_SECRET"))
    if not name or not secret:
        raise SystemExit("set COINBASE_API_KEY_NAME and COINBASE_API_SECRET in your environment (never in the config file)")
    return Coinbase(name, secret, log=log.info)


def check(cfg, api, log):
    """Read-only: confirm the key works, the products are perps, and show sizing. Sends no orders."""
    b = api.balance()
    log.info(f"futures account: total {b['total']:,.2f} USD, buying power {b['buying_power']:,.2f}")
    params = strategy_params(cfg)
    for mc in cfg["markets"]:
        p = api.product(mc["product_id"])
        log.info(f"{p['id']}: type {p['product_type']}, expiry {p['expiry_type']}, status {p['status']}, "
                 f"tick {p['tick']}, contract size {p['contract_size']}")
        if p["product_type"] != "FUTURE":
            log.error(f"{p['id']} is not a futures product")
        sig = mc.get("signal_product", p["id"])
        bars = api.candles(sig, datetime.now(timezone.utc) - timedelta(hours=3), datetime.now(timezone.utc))
        log.info(f"  signal {sig}: {len(bars)} 5-minute bars in the last 3 h, last close {bars[-1][4] if bars else 'n/a'}")
        bid, ask = api.best_bid_ask(p["id"])
        log.info(f"  perp bid/ask {bid} / {ask}; position {api.position(p['id'])}; open orders {len(api.open_orders(p['id']))}")
        if bars:
            hl = [x[2] - x[3] for x in bars]
            R = params["stop_atr"] * sum(hl) / len(hl)
            m = Market(mc, params)
            m.info = p
            bot = Bot.__new__(Bot)
            bot.equity = b["total"]
            n = Bot._size(bot, m, R, (bid + ask) / 2)
            log.info(f"  typical stop ~{R:,.2f}: {n} contracts at {m.risk_pct}% risk "
                     f"(notional {n * p['contract_size'] * (bid + ask) / 2:,.0f} USD)")
        try:
            pv = api.preview_market(p["id"], 1, 1)
            log.info(f"  preview 1-contract buy (not sent): {json.dumps({k: pv.get(k) for k in ('order_total', 'commission_total', 'errs', 'warning', 'leverage')})}")
        except ApiError as e:
            log.warning(f"  preview failed: {e}")


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("run", "check"):
        raise SystemExit("usage: python -m coinbase_perp.bot run|check path/to/config.json")
    cfg = load_config(sys.argv[2])
    log = setup_logging(cfg["log_dir"])
    api = make_api(cfg, log)
    if sys.argv[1] == "check":
        return check(cfg, api, log)
    bot = Bot(cfg, api, log=log)
    bot.start()
    bot.run()


if __name__ == "__main__":
    main()
