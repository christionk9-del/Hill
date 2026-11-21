"""BTC 15-minute up/down speed bot (paper by default).

Edge thesis (tick backtest, Jan 20 2026): Kalshi quotes lag Bitcoin by a few seconds.
Buying when the model's fair value beats the ask by >= 10 points returned ~$1.19 per $1
with a live price, ~$1.08 at 2 s stale, break-even at 5 s. So everything here is about
speed: streaming prices in, one bet per window, fill-or-kill orders out.

Feeds
  BTC price : Coinbase WebSocket ticker (no key needed).
  Kalshi    : WebSocket order book with your API key (fast), or REST polling twice a
              second without one (slower, for testing).
Settlement (from the market rules): YES if the average of CF Benchmarks' BRTI over the
last 60 s before close >= the 60 s average before open (the market's floor_strike).
Coinbase stands in for BRTI; `basis` widens the model's uncertainty for the difference.

    python -m kalshi_bot speedbot                 # paper, auto-picks Kalshi WS if keys are set
    python -m kalshi_bot speedbot --live          # real fill-or-kill orders (keys required)
    python -m kalshi_bot speedbot-report
"""
import asyncio
import json
import math
import os
import sqlite3
import ssl
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from .config import CONFIG

SERIES = "KXBTC15M"
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"
COINBASE_REST = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
TAKER = 0.07


# ------------------------------------------------------------------ model
def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def fee_per_contract(p):
    return TAKER * p * (1 - p)


def order_fee(n, p):
    return math.ceil(TAKER * n * p * (1 - p) * 100 - 1e-9) / 100


def fair_up(spot, strike, tau_min, sigma_min, window_avg=None, basis=0.0):
    """P(YES): P(average of the last 60 s before close >= strike).

    tau_min >= 1: the whole averaging minute is ahead. The average of a Brownian path
      over 1 minute has the variance of ~1/3 minute, so total variance = sigma^2 (tau - 2/3).
    tau_min < 1: (1 - tau) of the minute is already observed (window_avg); only the
      rest is random, with variance (tau * S * sigma)^2 * tau / 3 in price terms.
    basis: relative sd of (our price feed - BRTI), added in quadrature.
    """
    if tau_min <= 0:
        a = window_avg if window_avg is not None else spot
        return 1.0 if a >= strike else 0.0
    if not sigma_min or sigma_min <= 0:
        return 0.5
    if tau_min >= 1:
        sd = math.sqrt(sigma_min ** 2 * (tau_min - 2 / 3) + basis ** 2)
        return norm_cdf(math.log(spot / strike) / sd)
    e = 1 - tau_min
    known = window_avg if window_avg is not None else spot
    mean = e * known + tau_min * spot
    sd = math.sqrt((tau_min * spot * sigma_min) ** 2 * tau_min / 3 + (spot * basis) ** 2)
    return norm_cdf((mean - strike) / sd) if sd > 0 else (1.0 if mean >= strike else 0.0)


def ssl_context():
    return ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)


# ------------------------------------------------------------------ BTC price
class SpotFeed:
    """Last Coinbase BTC-USD trade plus a 1-second price history for volatility and averages."""

    def __init__(self):
        self.price, self.ts, self.sigma = None, 0.0, None
        self.secs = deque(maxlen=3700)           # (unix second, last price in that second)
        self.connected = False

    def on_price(self, p, now):
        self.price, self.ts = p, now
        s = int(now)
        if self.secs and self.secs[-1][0] == s:
            self.secs[-1] = (s, p)
        else:
            self.secs.append((s, p))

    def series(self, start, end):
        """Per-second prices for [start, end), carrying the last price forward over gaps."""
        out, last, it = [], None, iter(self.secs)
        cur = next(it, None)
        for s in range(int(start), int(end)):
            while cur and cur[0] <= s:
                last, cur = cur[1], next(it, None)
            if last is not None:
                out.append(last)
        return out

    def window_avg(self, start, end):
        xs = self.series(start, end)
        return sum(xs) / len(xs) if xs else None

    def update_sigma(self, now, step=5, lookback=3600):
        xs = self.series(now - lookback, now)[::step]
        rets = [math.log(b / a) for a, b in zip(xs, xs[1:]) if a and b]
        if len(rets) >= 60:
            m = sum(rets) / len(rets)
            sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
            self.sigma = sd * math.sqrt(60 / step)   # per minute

    def warmup(self):
        """Seed sigma from the last hour of 1-minute candles so the bot can start at once."""
        c = requests.get(COINBASE_REST, params={"granularity": 60}, timeout=15).json()
        closes = [x[4] for x in sorted(c)[-61:]]
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        m = sum(rets) / len(rets)
        self.sigma = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))

    async def run(self):
        import websockets
        backoff = 1
        while True:
            try:
                async with websockets.connect(COINBASE_WS, ssl=ssl_context(), open_timeout=20,
                                              ping_interval=20, max_size=2 ** 22) as ws:
                    await ws.send(json.dumps({"type": "subscribe", "product_ids": ["BTC-USD"],
                                              "channels": ["ticker"]}))
                    self.connected, backoff = True, 1
                    async for raw in ws:
                        m = json.loads(raw)
                        if m.get("type") == "ticker" and m.get("price"):
                            self.on_price(float(m["price"]), time.time())
            except Exception as e:  # noqa: BLE001 - reconnect on anything
                self.connected = False
                print(f"[spot] reconnecting after {type(e).__name__}: {str(e)[:120]}", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)


# ------------------------------------------------------------------ Kalshi book
def _levels(msg, side):
    """Order book levels as {price_dollars: size}, from either `yes_dollars` or cent `yes` lists."""
    out = {}
    if msg.get(f"{side}_dollars") is not None:
        for p, q in msg[f"{side}_dollars"]:
            out[round(float(p), 4)] = float(q)
    else:
        for p, q in msg.get(side) or []:
            out[round(float(p) / 100, 4)] = float(q)
    return out


class Book:
    """Top of book for the current market. Kalshi lists BIDS for YES and for NO;
    buying YES costs 1 - best NO bid (and the reverse)."""

    def __init__(self):
        self.ticker, self.ts, self.source = None, 0.0, None
        self.bids = {"yes": {}, "no": {}}
        self.top = {}          # REST mode: {"yes": (ask, size), "no": (ask, size)}

    def reset(self, ticker):
        self.ticker, self.ts, self.bids, self.top = ticker, 0.0, {"yes": {}, "no": {}}, {}

    def snapshot(self, msg, now):
        self.bids = {"yes": _levels(msg, "yes"), "no": _levels(msg, "no")}
        self.top, self.ts, self.source = {}, now, "ws"

    def delta(self, msg, now):
        side = msg["side"]
        p = round(float(msg["price_dollars"]), 4) if msg.get("price_dollars") is not None else round(float(msg["price"]) / 100, 4)
        d = float(msg["delta_fp"]) if msg.get("delta_fp") is not None else float(msg["delta"])
        q = self.bids[side].get(p, 0.0) + d
        if q <= 1e-9:
            self.bids[side].pop(p, None)
        else:
            self.bids[side][p] = q
        self.ts = now

    def set_top(self, quotes, now):
        self.top, self.ts, self.source = quotes, now, "rest"

    def ask(self, side):
        """(ask price, size available at it) to BUY `side`, or (None, 0)."""
        if self.top:
            return self.top.get(side, (None, 0))
        other = self.bids["no" if side == "yes" else "yes"]
        if not other:
            return None, 0
        best = max(other)
        return round(1 - best, 4), other[best]


async def kalshi_ws_book(client, book, windows, ws_url):
    """Stream the order book of the current window's market; reconnect at each new window."""
    import websockets
    from urllib.parse import urlparse
    path, backoff = urlparse(ws_url).path, 1
    while True:
        tk = windows.ticker()
        if not tk:
            await asyncio.sleep(0.5)
            continue
        try:
            async with websockets.connect(ws_url, ssl=ssl_context(), open_timeout=20, ping_interval=15,
                                          additional_headers=client.signed_headers("GET", path)) as ws:
                book.reset(tk)
                await ws.send(json.dumps({"id": 1, "cmd": "subscribe",
                                          "params": {"channels": ["orderbook_delta"], "market_tickers": [tk]}}))
                backoff, seq = 1, None
                while windows.ticker() == tk:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    m = json.loads(raw)
                    typ, msg = m.get("type"), m.get("msg") or {}
                    if msg.get("market_ticker") not in (None, tk):
                        continue
                    if m.get("seq") is not None:
                        if seq is not None and m["seq"] != seq + 1 and typ == "orderbook_delta":
                            raise RuntimeError("sequence gap, resubscribing")
                        seq = m["seq"]
                    if typ == "orderbook_snapshot":
                        book.snapshot(msg, time.time())
                    elif typ == "orderbook_delta":
                        book.delta(msg, time.time())
                    elif typ == "error":
                        raise RuntimeError(f"Kalshi WS error: {msg}")
        except Exception as e:  # noqa: BLE001
            print(f"[kalshi-ws] reconnecting after {type(e).__name__}: {str(e)[:160]}", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def kalshi_rest_book(base_url, book, windows, interval=0.5):
    s = requests.Session()
    while True:
        tk = windows.ticker()
        if tk:
            try:
                d = await asyncio.to_thread(lambda: s.get(f"{base_url}/markets", params={"tickers": tk}, timeout=5).json())
                m = (d.get("markets") or [None])[0]
                if m:
                    if book.ticker != tk:
                        book.reset(tk)
                    q = {side: (float(m[f"{side}_ask_dollars"]) if m.get(f"{side}_ask_dollars") else None,
                                float(m.get(f"{side}_ask_size_fp") or 0)) for side in ("yes", "no")}
                    book.set_top(q, time.time())
            except Exception as e:  # noqa: BLE001
                print(f"[kalshi-rest] {type(e).__name__}: {str(e)[:120]}", flush=True)
        await asyncio.sleep(interval)


# ------------------------------------------------------------------ windows
class Windows:
    """Tracks the current 15-minute market (ticker, strike, open/close)."""

    def __init__(self, base_url):
        self.base, self.cur, self.s = base_url, None, requests.Session()

    def ticker(self):
        c, now = self.cur, time.time()
        return c["ticker"] if c and c["open_ts"] <= now < c["close_ts"] else None

    def _fetch(self):
        d = self.s.get(f"{self.base}/markets", params={"series_ticker": SERIES, "status": "open", "limit": 5}, timeout=10).json()
        now, best = time.time(), None
        for m in d.get("markets", []):
            close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp()
            opn = datetime.fromisoformat(m["open_time"].replace("Z", "+00:00")).timestamp()
            if opn <= now < close and m.get("floor_strike") is not None:
                best = {"ticker": m["ticker"], "strike": float(m["floor_strike"]), "open_ts": opn, "close_ts": close}
        return best

    async def run(self):
        while True:
            now = time.time()
            if self.ticker() is None or (now % 900) < 15:
                try:
                    self.cur = await asyncio.to_thread(self._fetch) or self.cur
                except Exception as e:  # noqa: BLE001
                    print(f"[windows] {type(e).__name__}: {str(e)[:120]}", flush=True)
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(2)


# ------------------------------------------------------------------ bot
@dataclass
class SpeedConfig:
    min_edge: float = 0.10        # fair - (ask + fee), in probability points
    stake: float = 1.0            # $ per bet
    min_tau: float = 1.0          # minutes left: only trade between these
    max_tau: float = 14.0
    price_min: float = 0.10       # whole-cent range (sub-cent ticks below 10c / above 90c)
    price_max: float = 0.90
    max_spot_age: float = 1.0     # seconds: skip if the BTC price is older than this
    max_book_age: float = 1.5     # seconds: skip if the Kalshi book is older than this
    bets_per_window: int = 1
    basis: float = 0.0002         # Coinbase vs BRTI uncertainty (relative, ~ $12 at $58k)
    daily_budget: float = CONFIG.daily_budget   # live only
    max_daily_loss: float = CONFIG.daily_budget  # live only: stop for the day after this
    live: bool = False


SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
  id INTEGER PRIMARY KEY, ts REAL, mode TEXT, ticker TEXT, close_ts REAL, side TEXT,
  ask REAL, ask_size REAL, contracts INTEGER, fee REAL, fair REAL, edge REAL, tau REAL,
  spot REAL, strike REAL, sigma REAL, spot_age REAL, book_age REAL, book_source TEXT,
  order_status TEXT, order_id TEXT, filled INTEGER, quote_held INTEGER, result TEXT, payout REAL);
CREATE TABLE IF NOT EXISTS snaps (
  ts REAL, ticker TEXT, tau REAL, spot REAL, strike REAL, sigma REAL, fair_yes REAL,
  yes_ask REAL, no_ask REAL, spot_age REAL, book_age REAL);
CREATE TABLE IF NOT EXISTS windows (ticker TEXT PRIMARY KEY, close_ts REAL, result TEXT);
"""


class SpeedBot:
    def __init__(self, cfg: SpeedConfig, db="data/speedbot.sqlite", client=None, base_url=None):
        from .client import KalshiClient
        os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
        self.cfg, self.db = cfg, sqlite3.connect(db)
        self.db.executescript(SCHEMA)
        self.client = client or KalshiClient()
        self.base = base_url or CONFIG.base_url
        self.spot, self.book, self.windows = SpotFeed(), Book(), Windows(self.base)
        self.mode = "live" if cfg.live else "paper"
        self._last_snap = 0.0

    # -- state helpers
    def bets_in_window(self, tk):
        return self.db.execute("SELECT count(*) FROM bets WHERE ticker=? AND mode=? AND filled>0", (tk, self.mode)).fetchone()[0]

    def today_spend_and_loss(self):
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        spent, lost = self.db.execute(
            "SELECT COALESCE(sum(contracts*ask+fee),0), COALESCE(sum(CASE WHEN result IS NOT NULL THEN contracts*ask+fee-payout END),0) "
            "FROM bets WHERE mode=? AND ts>=? AND filled>0", (self.mode, start)).fetchone()
        return spent, lost

    # -- decision
    def evaluate(self, now):
        c, cfg = self.windows.cur, self.cfg
        tk = self.windows.ticker()
        if not tk or self.book.ticker != tk or self.spot.price is None or not self.spot.sigma:
            return None
        tau = (c["close_ts"] - now) / 60
        spot_age, book_age = now - self.spot.ts, now - self.book.ts
        avg = self.spot.window_avg(c["close_ts"] - 60, now) if tau < 1 else None
        fair_yes = fair_up(self.spot.price, c["strike"], tau, self.spot.sigma, avg, cfg.basis)
        ya, ys = self.book.ask("yes")
        na, ns = self.book.ask("no")
        if now - self._last_snap >= 5:
            self._last_snap = now
            self.db.execute("INSERT INTO snaps VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (now, tk, tau, self.spot.price, c["strike"], self.spot.sigma, fair_yes, ya, na, spot_age, book_age))
            self.db.commit()
        if not (cfg.min_tau <= tau <= cfg.max_tau) or spot_age > cfg.max_spot_age or book_age > cfg.max_book_age:
            return None
        if self.bets_in_window(tk) >= cfg.bets_per_window:
            return None
        best = None
        for side, ask, size, fair in (("yes", ya, ys, fair_yes), ("no", na, ns, 1 - fair_yes)):
            if ask is None or not (cfg.price_min <= ask <= cfg.price_max):
                continue
            edge = fair - (ask + fee_per_contract(ask))
            n = max(1, int(cfg.stake // ask))
            if edge >= cfg.min_edge and size >= n and (best is None or edge > best["edge"]):
                best = dict(ticker=tk, close_ts=c["close_ts"], side=side, ask=ask, size=size, n=n, fair=fair,
                            edge=edge, tau=tau, spot=self.spot.price, strike=c["strike"], sigma=self.spot.sigma,
                            spot_age=spot_age, book_age=book_age)
        return best

    async def place(self, b):
        cfg, status, oid, filled = self.cfg, "paper", None, b["n"]
        if cfg.live:
            spent, lost = self.today_spend_and_loss()
            cost = b["n"] * b["ask"] + order_fee(b["n"], b["ask"])
            if spent + cost > cfg.daily_budget or lost >= cfg.max_daily_loss:
                return
            try:
                r = await asyncio.to_thread(self.client.buy_limit, b["ticker"], b["side"], b["n"],
                                            int(round(b["ask"] * 100)), "fill_or_kill")
                o = r.get("order", r)
                oid, status = o.get("order_id"), o.get("status", "sent")
                filled = int(float(o.get("fill_count_fp") or o.get("fill_count") or (b["n"] if status == "executed" else 0)))
            except Exception as e:  # noqa: BLE001
                status, filled = f"error: {str(e)[:80]}", 0
        cur = self.db.execute(
            "INSERT INTO bets (ts,mode,ticker,close_ts,side,ask,ask_size,contracts,fee,fair,edge,tau,spot,strike,sigma,"
            "spot_age,book_age,book_source,order_status,order_id,filled) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), self.mode, b["ticker"], b["close_ts"], b["side"], b["ask"], b["size"], b["n"],
             order_fee(b["n"], b["ask"]), b["fair"], b["edge"], b["tau"], b["spot"], b["strike"], b["sigma"],
             b["spot_age"], b["book_age"], self.book.source, status, oid, filled))
        self.db.commit()
        print(f"[{self.mode}] BUY {b['side'].upper()} {b['ticker']} x{b['n']} @{b['ask']:.2f} fair {b['fair']:.2f} "
              f"edge {b['edge']:+.2f} tau {b['tau']:.1f}m spot_age {b['spot_age']:.2f}s book_age {b['book_age']:.2f}s -> {status}",
              flush=True)
        await asyncio.sleep(1.0)       # was the price still there a second later?
        ask, _ = self.book.ask(b["side"])
        self.db.execute("UPDATE bets SET quote_held=? WHERE id=?", (int(ask is not None and ask <= b["ask"] + 1e-9), cur.lastrowid))
        self.db.commit()

    async def decide_loop(self):
        while True:
            b = self.evaluate(time.time())
            if b:
                await self.place(b)
            await asyncio.sleep(0.1)

    async def settle_loop(self):
        s = requests.Session()
        while True:
            await asyncio.sleep(30)
            now = time.time()
            tks = {r[0]: r[1] for r in self.db.execute(
                "SELECT DISTINCT ticker, close_ts FROM bets WHERE result IS NULL AND close_ts < ?", (now - 20,))}
            for tk in {r[0] for r in self.db.execute("SELECT DISTINCT ticker FROM snaps WHERE ts < ?", (now - 960,))} - \
                    {r[0] for r in self.db.execute("SELECT ticker FROM windows")}:
                tks.setdefault(tk, None)
            for tk in list(tks)[:40]:
                try:
                    m = await asyncio.to_thread(lambda: s.get(f"{self.base}/markets/{tk}", timeout=10).json().get("market", {}))
                except Exception:  # noqa: BLE001
                    continue
                res = m.get("result")
                if res in ("yes", "no"):
                    self.db.execute("INSERT OR REPLACE INTO windows VALUES (?,?,?)", (tk, tks.get(tk), res))
                    self.db.execute("UPDATE bets SET result=?, payout=CASE WHEN side=? THEN filled ELSE 0 END "
                                    "WHERE ticker=? AND result IS NULL", (res, res, tk))
            self.db.commit()

    async def heartbeat(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            self.spot.update_sigma(now)
            c = self.windows.cur
            ya, _ = self.book.ask("yes"); na, _ = self.book.ask("no")
            tau = (c["close_ts"] - now) / 60 if c else float("nan")
            fair = fair_up(self.spot.price, c["strike"], tau, self.spot.sigma, None, self.cfg.basis) if c and self.spot.price and tau >= 1 else float("nan")
            print(f"[hb] {datetime.now(timezone.utc):%H:%M:%S} {self.book.ticker} tau {tau:.1f}m BTC {self.spot.price} "
                  f"sigma/min {self.spot.sigma or 0:.5f} fairYES {fair:.2f} yesAsk {ya} noAsk {na} "
                  f"spot_age {now - self.spot.ts:.2f}s book_age {now - self.book.ts:.2f}s ({self.book.source})", flush=True)

    async def run(self, book_mode="auto", minutes=None):
        self.spot.warmup()
        use_ws = book_mode == "ws" or (book_mode == "auto" and self.client.signed_headers("GET", "/x"))
        ws_url = self.base.replace("https://", "wss://").replace("/trade-api/v2", "/trade-api/ws/v2")
        book_task = kalshi_ws_book(self.client, self.book, self.windows, ws_url) if use_ws else \
            kalshi_rest_book(self.base, self.book, self.windows)
        if self.cfg.live and not use_ws:
            print("[warn] live mode without the Kalshi WebSocket: book is polled, so the edge is smaller", flush=True)
        print(f"[start] mode={self.mode} book={'websocket' if use_ws else 'rest-poll'} min_edge={self.cfg.min_edge} "
              f"stake=${self.cfg.stake}", flush=True)
        tasks = [self.spot.run(), self.windows.run(), book_task, self.decide_loop(), self.settle_loop(), self.heartbeat()]
        if minutes:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=minutes * 60)
        else:
            await asyncio.gather(*tasks)


def report(db="data/speedbot.sqlite"):
    import random
    con = sqlite3.connect(db)
    for mode in ("paper", "live"):
        rows = con.execute("SELECT contracts*ask+fee, payout, edge, spot_age, book_age, quote_held, book_source "
                           "FROM bets WHERE mode=? AND filled>0", (mode,)).fetchall()
        if not rows:
            continue
        done = [r for r in rows if r[1] is not None]
        cost, pay = sum(r[0] for r in done), sum(r[1] for r in done)
        print(f"== {mode}: {len(rows)} bets, {len(done)} settled, {sum(1 for r in done if r[1] > 0)} won ==")
        if done:
            boot = []
            for _ in range(2000):
                s = [random.choice(done) for _ in done]
                boot.append(sum(x[1] for x in s) / sum(x[0] for x in s))
            boot.sort()
            print(f"  staked ${cost:.2f}  paid ${pay:.2f}  P&L ${pay - cost:+.2f}  $ back per $1 {pay / cost:.3f} "
                  f"(90% range {boot[100]:.2f}-{boot[1900]:.2f})")
        print(f"  avg BTC price age {sum(r[3] for r in rows) / len(rows):.2f}s, book age {sum(r[4] for r in rows) / len(rows):.2f}s, "
              f"quote still there 1s later {sum(r[5] or 0 for r in rows) / len(rows):.0%}, book via {rows[-1][6]}")
        for lo, hi in ((0.10, 0.15), (0.15, 0.25), (0.25, 1.0)):
            b = [r for r in done if lo <= r[2] < hi]
            if b:
                print(f"  edge {lo:.2f}-{hi:.2f}: {len(b)} settled, $ back per $1 {sum(x[1] for x in b) / sum(x[0] for x in b):.3f}")
    cal = con.execute("""SELECT s.fair_yes, w.result, s.ticker FROM snaps s JOIN windows w USING(ticker) WHERE s.tau >= 1""").fetchall()
    if cal:
        print(f"== model calibration: {len(cal)} snapshots from {len({r[2] for r in cal})} settled windows ==")
        for lo in (0, .2, .4, .6, .8):
            b = [r for r in cal if lo <= r[0] < lo + .2]
            if b:
                print(f"  model says {lo:.0%}-{lo + .2:.0%}: YES won {sum(r[1] == 'yes' for r in b) / len(b):.0%} (n={len(b)})")
