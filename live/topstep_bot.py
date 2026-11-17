"""Topstep (TopstepX / ProjectX API) bot for the MNQ Donchian breakout variant.

One strategy model (live/engine.py) drives every listed account with the same orders:
  - entry: market order the moment price trades through the 24-bar channel,
  - protective stop order at the broker right after the fill, moved only in the trade's favour
    after each 5-minute close,
  - flat after the 15:55 bar, with a hard flatten at 16:02 New York time as a backstop.

Safety:
  - dry_run is on by default: everything runs on live prices, but no order is sent.
  - Only accounts named in the config are traded, at the configured size (max 3 contracts).
  - Per-account daily loss limit from the account balance; the account stops for the day.
  - A file named STOP next to the config flattens everything and ends the bot.
  - Every bar close reconciles each account with the model and fixes mismatches
    (missing stop, unexpected position, leftover orders).

Run on your own computer (Topstep does not allow VPS or VPN use):
    python live/topstep_bot.py live/config.json
"""
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:                         # Python < 3.9
    from backports.zoneinfo import ZoneInfo  # type: ignore

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live.engine import DonchianEngine, Params                      # noqa: E402
from live.projectx import ProjectX, ApiError, MARKET, STOP, BUY, SELL, LONG, SHORT  # noqa: E402

NY = ZoneInfo("America/New_York")
BAR = timedelta(minutes=5)


class RealClock:
    def now(self):
        return datetime.now(NY)

    def sleep(self, s):
        time.sleep(s)


def parse_t(t):
    return datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(NY)


def rth_index(ts):
    m = ts.hour * 60 + ts.minute - 570
    return m // 5 if 0 <= m < 390 else None


class Account:
    def __init__(self, name, acct_id):
        self.name, self.id = name, acct_id
        self.in_pos = False
        self.stop_id = None
        self.stop_px = None
        self.day_start_balance = None
        self.disabled_today = False
        self.entry_px = None


class Bot:
    def __init__(self, cfg, api, clock=None, log=None):
        self.cfg = cfg
        self.api = api
        self.clock = clock or RealClock()
        self.log = log or logging.getLogger("bot")
        self.dry = bool(cfg.get("dry_run", True))
        self.size = int(cfg.get("contracts_per_account", 1))
        if not 1 <= self.size <= 3:
            raise ValueError("contracts_per_account must be 1-3")
        self.tick = 0.25
        self.engine = DonchianEngine(Params(**cfg.get("strategy", {})))
        self.skip = set(cfg.get("skip_dates", []))
        self.loss_limit = float(cfg.get("daily_loss_limit", 600))
        self.poll = float(cfg.get("poll_seconds", 3))
        self.stop_file = cfg.get("stop_file", "STOP")
        self.log_dir = cfg.get("log_dir", "logs")
        os.makedirs(self.log_dir, exist_ok=True)
        self.accounts = []
        self.contract = None
        self.last_done = None          # start time of the last completed bar fed to end_bar
        self.cur = None                # start time of the bar the engine has begun
        self.follow = True             # False while the model holds a trade the accounts never joined
        self.model_trades = []
        self.day = None
        self.eod_done = False
        self.entry_seen = None         # bar high/low already seen when the model entered (same-bar stop check)
        self.closed_trades = []        # every model trade closed since start (kept for reporting)

    # ---------------------------------------------------------------- setup
    def start(self):
        self.api.login()                       # market data needs a session even in dry run
        self.contract = self.api.find_contract(self.cfg.get("symbol", "MNQ"))
        self.tick = float(self.contract.get("tickSize") or 0.25)
        cid = self.contract["id"]
        wanted = self.cfg["accounts"]
        live_accts = {a["name"]: a for a in self.api.accounts()}
        for name in wanted:
            a = live_accts.get(name)
            if a is None:
                raise SystemExit(f"account {name!r} not found; available: {sorted(live_accts)}")
            if not a.get("canTrade", True):
                self.log.warning(f"account {name} cannot trade right now; it will be skipped")
                continue
            self.accounts.append(Account(name, a["id"]))
        self.log.info(f"contract {cid} tick {self.tick}; accounts {[a.name for a in self.accounts]}; "
                      f"{'DRY RUN - no orders will be sent' if self.dry else 'LIVE ORDERS'}; size {self.size}")
        self._warmup()

    def _rth_bars(self, start, end):
        bars = self.api.bars(self.contract["id"], start, end)
        out = []
        for b in bars:
            ts = parse_t(b["t"])
            if ts.weekday() < 5 and rth_index(ts) is not None:
                out.append((ts, float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"])))
        return out

    def _warmup(self):
        now = self.clock.now()
        bars = self._rth_bars(now - timedelta(days=21), now)
        today = now.date()
        done = [b for b in bars if b[0] + BAR <= now]
        hist = [b for b in done if b[0].date() < today]
        for ts, o, h, l, c in hist:
            self.engine.warmup(h, l, c)
        self.last_done = hist[-1][0] if hist else None
        self.log.info(f"warmed up on {len(hist)} RTH bars; engine ready: {self.engine.ready()}")
        todays = [b for b in done if b[0].date() == today]
        if todays:
            self.log.warning(f"started mid-session: replaying {len(todays)} bars from today; "
                             f"accounts will not join a trade already open in the model")
            for b in todays:
                self._begin(b)
                ev = self.engine.on_price(b[2], b[3])
                if ev and ev.kind == "entry":
                    self.follow = False
                for e in self.engine.end_bar(b[1], b[2], b[3], b[4]):
                    pass
                self.last_done = b[0]
            if self.engine.pos is None:
                self.follow = True

    # ---------------------------------------------------------------- main loop
    def run(self, until=None):
        while True:
            now = self.clock.now()
            if until and now >= until:
                return
            if os.path.exists(self.stop_file):
                self.log.warning("STOP file found: flattening all accounts and exiting")
                self.flatten_all("stop file")
                return
            if now.weekday() >= 5 or not (9 * 60 + 20 <= now.hour * 60 + now.minute <= 16 * 60 + 10):
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
        bars = self._rth_bars(now - timedelta(days=2), now + timedelta(minutes=1))
        for b in bars:
            ts = b[0]
            if self.last_done is not None and ts <= self.last_done:
                continue
            if ts + BAR <= now:                       # completed bar
                if self.cur != ts:
                    self._begin(b)
                self._price(b)
                for e in self.engine.end_bar(b[1], b[2], b[3], b[4]):
                    self._handle(e, b)
                self.last_done = ts
                self._reconcile()
                self._loss_guard()
            else:                                     # bar in progress
                if self.cur != ts:
                    self._begin(b)
                self._price(b)
        hm = now.hour * 60 + now.minute
        if hm >= 16 * 60 + 2 and not self.eod_done:
            self.flatten_all("end of day backstop")
            self._daily_report(now)
            self.eod_done = True

    def _new_day(self, now):
        self.day = now.date()
        self.eod_done = False
        bal = {a["id"]: a.get("balance") for a in self.api.accounts()}
        for a in self.accounts:
            a.day_start_balance = bal.get(a.id)
            a.disabled_today = False
        self.log.info(f"new day {self.day}; start balances " + ", ".join(f"{a.name}={a.day_start_balance}" for a in self.accounts))

    def _price(self, b):
        """Feed the bar's running high/low. On the entry bar only moves after the fill count."""
        h, l = b[2], b[3]
        ps = self.engine.pos
        if ps is not None and ps.bar_idx == self.engine.idx and self.entry_seen is not None:
            eh, el = self.entry_seen
            h, l = (h if h > eh else float("-inf")), (l if l < el else float("inf"))
        ev = self.engine.on_price(h, l)
        if ev is not None and ev.kind == "entry":
            self.entry_seen = (b[2], b[3])
        self._handle(ev, b)

    def _begin(self, b):
        ts = b[0]
        self.entry_seen = None
        tradable = ts.date().isoformat() not in self.skip
        self.engine.begin_bar(ts.date(), rth_index(ts), b[1], tradable)
        self.cur = ts

    # ---------------------------------------------------------------- events -> orders
    def _round_stop(self, px, side):
        t = self.tick
        return round((int(px / t) if side > 0 else -int(-px / t)) * t, 10)

    def _handle(self, ev, bar):
        if ev is None:
            return
        if ev.kind == "entry":
            self.model_trades.append(dict(time=bar[0].isoformat(), side=ev.side, entry=ev.price, stop=ev.stop))
            self.log.info(f"MODEL ENTRY {'LONG' if ev.side > 0 else 'SHORT'} at {ev.price:.2f}, stop {ev.stop:.2f}, "
                          f"R {ev.info['R']:.2f} (channel {ev.info['lo']:.2f}-{ev.info['hi']:.2f})")
            if self.follow:
                for a in self.accounts:
                    if not a.disabled_today and not a.in_pos:
                        try:
                            self._enter(a, ev)
                        except ApiError as e:
                            self.log.error(f"{a.name}: entry failed: {e}; reconciling")
                            self._reconcile_account(a)
        elif ev.kind == "stop_moved":
            self.log.info(f"model stop -> {ev.stop:.2f}")
            if self.follow:
                for a in self.accounts:
                    if a.in_pos:
                        self._move_stop(a, ev.stop, ev.side)
        elif ev.kind == "exit":
            pnl = ev.info["pnl_pts"]
            if self.model_trades:
                self.model_trades[-1].update(exit=ev.price, reason=ev.reason, pnl_pts=pnl, exit_time=bar[0].isoformat())
                self.closed_trades.append(dict(self.model_trades[-1]))
            self.log.info(f"MODEL EXIT ({ev.reason}) at {ev.price:.2f}, {pnl:+.2f} pts")
            if self.follow:
                for a in self.accounts:
                    if a.in_pos:
                        self._exit(a, ev.reason)
            self.follow = True

    def _enter(self, a, ev):
        side = BUY if ev.side > 0 else SELL
        tag = f"dv-{self.clock.now():%Y%m%d%H%M%S}-{a.id}"
        stop_px = self._round_stop(ev.stop, ev.side)
        if self.dry:
            self.log.info(f"[dry] {a.name}: would BUY/SELL {self.size} at market ({'buy' if side == BUY else 'sell'}) "
                          f"then place stop at {stop_px}")
            a.in_pos, a.stop_px, a.entry_px = True, stop_px, ev.price
            return
        self.api.place(a.id, self.contract["id"], MARKET, side, self.size, tag=tag)
        pos = None
        for _ in range(12):                                   # wait up to ~12 s for the fill
            pos = self._position(a)
            if pos:
                break
            self.clock.sleep(1)
        if not pos:
            self.log.error(f"{a.name}: entry order not filled after 12 s; cancelling any working orders")
            self._cancel_all(a)
            return
        a.in_pos, a.entry_px = True, float(pos["averagePrice"])
        a.stop_id = self.api.place(a.id, self.contract["id"], STOP, SELL if side == BUY else BUY, int(pos["size"]),
                                   stop_price=stop_px, tag=tag + "-sl")
        a.stop_px = stop_px
        self.log.info(f"{a.name}: filled {pos['size']} at {a.entry_px:.2f}; stop {stop_px} (order {a.stop_id})")

    def _move_stop(self, a, model_stop, side):
        new = self._round_stop(model_stop, side)
        if a.stop_px is not None and ((side > 0 and new <= a.stop_px) or (side < 0 and new >= a.stop_px)):
            return                                            # never loosen
        if self.dry:
            self.log.info(f"[dry] {a.name}: would move stop {a.stop_px} -> {new}")
            a.stop_px = new
            return
        if a.stop_id is None:
            self._ensure_stop(a, side, new)
            return
        try:
            self.api.modify_stop(a.id, a.stop_id, new)
            a.stop_px = new
        except ApiError as e:
            self.log.warning(f"{a.name}: stop modify failed ({e}); checking position")
            self._reconcile_account(a)

    def _exit(self, a, reason):
        if self.dry:
            self.log.info(f"[dry] {a.name}: would flatten ({reason})")
            a.in_pos, a.stop_id, a.stop_px = False, None, None
            return
        if reason == "stop":
            self.clock.sleep(2)                               # broker stop normally fills at the same level
        self._flatten(a, reason)

    # ---------------------------------------------------------------- safety
    def _flatten(self, a, reason):
        """Close the account's position and cancel its orders, then confirm it is flat.
        If a position survives two attempts the account is switched off for the day."""
        for attempt in range(2):
            if self._position(a):
                self.api.close_position(a.id, self.contract["id"])
                self.clock.sleep(1)
            self._cancel_all(a)
            if not self._position(a):
                if attempt or reason:
                    self.log.info(f"{a.name}: flat ({reason})")
                a.in_pos, a.stop_id, a.stop_px = False, None, None
                return True
        self.log.critical(f"{a.name}: STILL HAS A POSITION after two flatten attempts ({reason}). "
                          f"Account switched off for today. Check it in TopstepX now.")
        a.in_pos, a.disabled_today = False, True
        return False

    def _position(self, a):
        for p in self.api.positions(a.id):
            if p.get("contractId") == self.contract["id"] and int(p.get("size", 0)) != 0:
                return p
        return None

    def _cancel_all(self, a):
        for o in self.api.open_orders(a.id):
            if o.get("contractId") == self.contract["id"]:
                try:
                    self.api.cancel(a.id, o["id"])
                except ApiError as e:
                    self.log.warning(f"{a.name}: cancel {o['id']} failed: {e}")

    def _ensure_stop(self, a, side, px):
        pos = self._position(a)
        if not pos:
            return
        a.stop_id = self.api.place(a.id, self.contract["id"], STOP, SELL if side > 0 else BUY, int(pos["size"]), stop_price=px)
        a.stop_px = px
        self.log.warning(f"{a.name}: protective stop was missing; placed at {px}")

    def _reconcile_account(self, a):
        if self.dry or (a.disabled_today and not a.in_pos and not self._position(a)):
            return
        pos = self._position(a)
        model = self.engine.pos if self.follow else None
        if pos and (model is None or not a.in_pos):
            self.log.warning(f"{a.name}: position with no model trade; flattening")
            self._flatten(a, "unexpected position")
            return
        if pos and abs(int(pos["size"])) > self.size:
            self.log.warning(f"{a.name}: size {pos['size']} above limit; flattening")
            self._flatten(a, "size above limit")
            return
        if pos:
            stops = [o for o in self.api.open_orders(a.id) if o.get("contractId") == self.contract["id"] and o.get("type") == STOP]
            want = self._round_stop(model.stop, model.side)
            if not stops:
                self._ensure_stop(a, model.side, want)
            else:
                a.stop_id = stops[0]["id"]
                have = float(stops[0].get("stopPrice") or want)
                if (model.side > 0 and want > have) or (model.side < 0 and want < have):
                    self.api.modify_stop(a.id, a.stop_id, want)
                    self.log.info(f"{a.name}: stop resynced {have} -> {want}")
                a.stop_px = max(have, want) if model.side > 0 else min(have, want)
        else:
            if a.in_pos:
                self.log.info(f"{a.name}: stopped out at the broker")
            a.in_pos, a.stop_id, a.stop_px = False, None, None
            if self.engine.pos is None:
                self._cancel_all(a)

    def _reconcile(self):
        for a in self.accounts:
            try:
                self._reconcile_account(a)
            except ApiError as e:
                self.log.error(f"{a.name}: reconcile failed: {e}")

    def _loss_guard(self):
        if self.dry:
            return
        bal = {x["id"]: x.get("balance") for x in self.api.accounts()}
        for a in self.accounts:
            b0, b = a.day_start_balance, bal.get(a.id)
            if b0 is None or b is None or a.disabled_today:
                continue
            if b <= b0 - self.loss_limit:
                self.log.warning(f"{a.name}: down {b0 - b:,.0f} today (limit {self.loss_limit:,.0f}); flattening and stopping for the day")
                self._flatten(a, "daily loss limit")
                a.disabled_today = True

    def flatten_all(self, reason):
        for a in self.accounts:
            try:
                if self.dry:
                    a.in_pos = False
                    continue
                self._flatten(a, reason)
            except ApiError as e:
                self.log.error(f"{a.name}: flatten failed: {e}")

    # ---------------------------------------------------------------- reporting
    def _daily_report(self, now):
        path = os.path.join(self.log_dir, "daily.csv")
        new = not os.path.exists(path)
        start = datetime(now.year, now.month, now.day, tzinfo=NY)
        rows = []
        bal = {x["id"]: x.get("balance") for x in self.api.accounts()}
        model_pts = sum(t.get("pnl_pts", 0) for t in self.model_trades if t["time"][:10] == now.date().isoformat())
        for a in self.accounts:
            pnl = fees = n = 0.0
            if not self.dry:
                for t in self.api.trades(a.id, start, now):
                    if t.get("voided"):
                        continue
                    if t.get("profitAndLoss") is not None:
                        pnl += float(t["profitAndLoss"])
                        n += 1
                    fees += float(t.get("fees") or 0)
            else:
                pnl = model_pts * 2.0 * self.size
            rows.append([now.date().isoformat(), a.name, round(pnl, 2), round(fees, 2), int(n), bal.get(a.id), "dry" if self.dry else "live"])
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date", "account", "pnl", "fees", "closed_trades", "balance", "mode"])
            w.writerows(rows)
        with open(os.path.join(self.log_dir, "model_trades.jsonl"), "a") as f:
            for t in self.model_trades:
                f.write(json.dumps(t) + "\n")
        self.model_trades = [t for t in self.model_trades if "exit" not in t]
        self.log.info(f"daily report written: {rows}")


def setup_logging(log_dir):
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger("bot")
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(os.path.join(log_dir, f"bot-{datetime.now(NY):%Y-%m-%d}.log"))
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    return root


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python live/topstep_bot.py path/to/config.json")
    cfg_path = sys.argv[1]
    cfg = json.load(open(cfg_path))
    base = os.path.dirname(os.path.abspath(cfg_path))
    cfg["log_dir"] = os.path.join(base, cfg.get("log_dir", "logs"))
    cfg["stop_file"] = os.path.join(base, cfg.get("stop_file", "STOP"))
    log = setup_logging(cfg["log_dir"])
    user = os.environ.get(cfg.get("username_env", "TOPSTEPX_USERNAME"))
    key = os.environ.get(cfg.get("api_key_env", "TOPSTEPX_API_KEY"))
    if not user or not key:
        raise SystemExit("set TOPSTEPX_USERNAME and TOPSTEPX_API_KEY in your environment (never in the config file)")
    api = ProjectX(cfg.get("base_url", "https://api.topstepx.com"), user, key, log=log.info)
    bot = Bot(cfg, api, log=log)
    bot.start()
    bot.run()


if __name__ == "__main__":
    main()
