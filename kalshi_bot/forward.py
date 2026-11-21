"""Live forward test (paper) of strategies on ALL Kalshi 15-minute up/down markets.

Every cycle (~2 s): read live quotes for every current up/down market, live spot
for crypto from Coinbase, and decide for each strategy. Each strategy bets at most
once per market, $1 at the live ask (only if the ask size covers it), Kalshi taker
fee included (rounded up per order). Bets settle from Kalshi's real result.

    python -m kalshi_bot forward --hours 3        # run (writes data/forward.sqlite)
    python -m kalshi_bot forward-report           # P&L by strategy
"""
import math
import sqlite3
import time
from datetime import datetime, timezone

from concurrent.futures import ThreadPoolExecutor

import requests

from .updown import fair_up

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com/products/{}-USD"
CRYPTO = {"KXBTC15M": "BTC", "KXETH15M": "ETH", "KXSOL15M": "SOL", "KXXRP15M": "XRP", "KXDOGE15M": "DOGE",
          "KXADA15M": "ADA", "KXBCH15M": "BCH", "KXNEAR15M": "NEAR", "KXZEC15M": "ZEC", "KXHYPE15M": "HYPE",
          "KXBNB15M": "BNB", "KXTON15M": "TON"}
OTHER = ["KXNDQ15M", "KXINX15M", "KXDJIA15M", "KXEURUSD15M", "KXGBPUSD15M", "KXUSDJPY15M", "KXUSDCAD15M",
         "KXAUDUSD15M", "KXUSDMXN15M", "KX2YRRATE15M", "KX5YRRATE15M", "KX10YRRATE15M", "KX30YRRATE15M"]
STAKE = 1.0

SCHEMA = """CREATE TABLE IF NOT EXISTS bets (
  id INTEGER PRIMARY KEY, ts REAL, strategy TEXT, series TEXT, ticker TEXT, side TEXT,
  price REAL, contracts INTEGER, fee REAL, ask_size REAL, fair REAL, tau REAL, spot REAL, strike REAL,
  spot_age REAL, still_there INTEGER, result TEXT, payout REAL,
  tp_target REAL, exit_price REAL, exit_ts REAL, exit_fee REAL, UNIQUE(strategy, ticker));
CREATE TABLE IF NOT EXISTS heartbeat (ts REAL, markets INTEGER, bets INTEGER, note TEXT);"""


def taker_fee(contracts, price):
    return math.ceil(0.07 * contracts * price * (1 - price) * 100 - 1e-9) / 100


def f(m, k):
    v = m.get(k)
    return float(v) if v not in (None, "") else None


class Feed:
    def __init__(self):
        self.s = requests.Session()
        self.s.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=32))
        self.spot, self.sigma, self._sig_ts = {}, {}, {}

    def get(self, url, **params):
        for i in range(4):
            try:
                r = self.s.get(url, params=params, timeout=10)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 429:
                    time.sleep(1 + i)
                    continue
                return None
            except requests.RequestException:
                time.sleep(1)
        return None

    def current_markets(self, series_list):
        with ThreadPoolExecutor(8) as ex:
            res = ex.map(lambda s: self.get(f"{KALSHI}/markets", series_ticker=s, status="open", limit=10) or {},
                         series_list)
        return [m for d in res for m in d.get("markets", []) if m.get("floor_strike") is not None]

    def refresh_crypto(self, coins, now):
        with ThreadPoolExecutor(12) as ex:
            list(ex.map(self.update_spot, coins))
            list(ex.map(lambda c: self.update_sigma(c, now), coins))

    def quotes(self, tickers):
        out = {}
        for i in range(0, len(tickers), 50):
            d = self.get(f"{KALSHI}/markets", tickers=",".join(tickers[i:i + 50]), limit=50) or {}
            out.update({m["ticker"]: m for m in d.get("markets", [])})
        return out

    def update_spot(self, coin):
        d = self.get(COINBASE.format(coin) + "/ticker")
        if d and d.get("price"):
            t = datetime.fromisoformat(d["time"].replace("Z", "+00:00")).timestamp()
            self.spot[coin] = (float(d["price"]), t)

    def update_sigma(self, coin, now):
        if now - self._sig_ts.get(coin, 0) < 60:
            return
        c = self.get(COINBASE.format(coin) + "/candles", granularity=60) or []
        closes = [x[4] for x in sorted(c)[-61:]]
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a and b]
        if len(rets) >= 30:
            m = sum(rets) / len(rets)
            self.sigma[coin] = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
            self._sig_ts[coin] = now


TAKE_PROFIT = {"user_down_2_3m_tp20": 0.20}   # strategy -> take-profit fraction (others hold)


def strategies(m, series, now, feed):
    """Yield (strategy, side, fair) decisions for one market snapshot."""
    close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp()
    tau = (close - now) / 60
    since_open = now - (close - 900)
    if tau <= 0.25:
        return
    ya, na = f(m, "yes_ask_dollars"), f(m, "no_ask_dollars")
    coin = CRYPTO.get(series)
    fair_yes = None
    if coin and coin in feed.spot and coin in feed.sigma:
        spot, t = feed.spot[coin]
        if now - t < 5:
            fair_yes = fair_up(spot, float(m["floor_strike"]), tau, feed.sigma[coin])
    for side, ask in (("yes", ya), ("no", na)):
        if not ask or not 0.005 <= ask <= 0.995:
            continue
        fair = None if fair_yes is None else (fair_yes if side == "yes" else 1 - fair_yes)
        cost = ask * (1 + 0.07 * (1 - ask))
        if fair is not None and 1 <= tau <= 14:
            if fair - cost >= 0.05:
                yield "model_edge_5", side, fair
            if fair - cost >= 0.10:
                yield "model_edge_10", side, fair
        if tau <= 3 and 0.85 <= ask <= 0.97:
            yield "late_favorite", side, fair
        if tau >= 2 and ask <= 0.10:
            yield "longshot_10c", side, fair
        if side == "yes" and 13 <= tau <= 14:
            yield "baseline_yes", side, fair
        # BTC-only rules from the take-profit backtest (kalshi_bot/updown_tp.py)
        if series == "KXBTC15M" and 0.20 <= ask <= 0.35:
            if side == "no" and 120 <= since_open <= 180:
                yield "user_down_2_3m_tp20", side, fair
            if 0 <= since_open <= 45:
                yield ("fade_1st_min_down" if side == "no" else "fade_1st_min_up"), side, fair


def run(hours=3.0, db="data/forward.sqlite", cycle=2.0, log=print):
    import os
    os.makedirs("data", exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(bets)")}
    for col, typ in (("tp_target", "REAL"), ("exit_price", "REAL"), ("exit_ts", "REAL"), ("exit_fee", "REAL")):
        if col not in have:
            con.execute(f"ALTER TABLE bets ADD COLUMN {col} {typ}")
    feed, series_all = Feed(), list(CRYPTO) + OTHER
    end, markets, last_discover, pending = time.time() + hours * 3600, {}, 0, {}
    while time.time() < end:
        now = time.time()
        near_open = (now % 900) < 50          # windows open on the quarter hour
        if now - last_discover > 30 or near_open:
            markets = {m["ticker"]: m for m in feed.current_markets(series_all)}
            last_discover = now
        with ThreadPoolExecutor(2) as ex:
            fut_q = ex.submit(feed.quotes, list(markets))
            feed.refresh_crypto(list(set(CRYPTO.values())), now)
            q = fut_q.result()
        now = time.time()
        # fill-persistence check on bets made last cycle
        for bid, (tk, side, price) in list(pending.items()):
            mm = q.get(tk)
            ask = f(mm, f"{side}_ask_dollars") if mm else None
            con.execute("UPDATE bets SET still_there=? WHERE id=?", (int(ask is not None and ask <= price + 1e-9), bid))
            del pending[bid]
        nb = 0
        for tk, m in q.items():
            if m.get("status") != "active":
                continue
            series = tk.split("-")[0]
            for strat, side, fair in strategies(m, series, now, feed):
                ask, size = f(m, f"{side}_ask_dollars"), f(m, f"{side}_ask_size_fp") or 0
                n = max(1, int(STAKE // ask))
                if size < n:
                    continue
                coin = CRYPTO.get(series)
                spot, st = feed.spot.get(coin, (None, None))
                cur = con.execute(
                    "INSERT OR IGNORE INTO bets (ts,strategy,series,ticker,side,price,contracts,fee,ask_size,fair,tau,spot,strike,spot_age)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)" if strat not in TAKE_PROFIT else
                    "INSERT OR IGNORE INTO bets (ts,strategy,series,ticker,side,price,contracts,fee,ask_size,fair,tau,spot,strike,spot_age,tp_target)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now, strat, series, tk, side, ask, n, taker_fee(n, ask), size, fair,
                     (datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp() - now) / 60,
                     spot, float(m["floor_strike"]), (now - st) if st else None)
                    + (() if strat not in TAKE_PROFIT else (round(ask * (1 + TAKE_PROFIT[strat]), 4),)))
                if cur.rowcount:
                    pending[cur.lastrowid] = (tk, side, ask)
                    nb += 1
        # take-profit exits: sell at our side's bid once it reaches the target
        for bid_id, tk, side, n, target in con.execute(
                "SELECT id, ticker, side, contracts, tp_target FROM bets WHERE tp_target IS NOT NULL "
                "AND exit_price IS NULL AND result IS NULL").fetchall():
            mm = q.get(tk)
            bid = f(mm, f"{side}_bid_dollars") if mm else None
            size = f(mm, f"{side}_bid_size_fp") or 0 if mm else 0
            if bid is not None and bid >= target - 1e-9 and size >= n:
                con.execute("UPDATE bets SET exit_price=?, exit_ts=?, exit_fee=? WHERE id=?",
                            (bid, now, taker_fee(n, bid), bid_id))
        con.execute("INSERT INTO heartbeat VALUES (?,?,?,?)", (now, len(q), nb, ""))
        con.commit()
        settle(con, feed)
        time.sleep(max(0, cycle - (time.time() - now)))
    settle(con, feed, final=True)


def settle(con, feed, final=False):
    rows = con.execute("SELECT DISTINCT ticker FROM bets WHERE result IS NULL").fetchall()
    if not rows:
        return
    if not final and int(time.time()) % 60 > 3:   # settle about once a minute
        return
    q = feed.quotes([r[0] for r in rows])
    for tk, m in q.items():
        res = m.get("result")
        if res in ("yes", "no"):
            con.execute("UPDATE bets SET result=?, payout=CASE WHEN exit_price IS NOT NULL "
                        "THEN contracts*exit_price - exit_fee WHEN side=? THEN contracts ELSE 0 END "
                        "WHERE ticker=? AND result IS NULL", (res, res, tk))
    con.commit()


def report(db="data/forward.sqlite"):
    con = sqlite3.connect(db)
    hb = con.execute("SELECT min(ts), max(ts), count(*) FROM heartbeat").fetchone()
    if hb[0]:
        print(f"Forward test window: {datetime.fromtimestamp(hb[0], timezone.utc):%Y-%m-%d %H:%M} -> "
              f"{datetime.fromtimestamp(hb[1], timezone.utc):%H:%M} UTC ({(hb[1]-hb[0])/3600:.1f} h, {hb[2]} cycles)")
    q = """SELECT strategy, CASE WHEN series IN ({}) THEN 'crypto' ELSE 'fin/fx/rates' END grp,
        count(*), sum(result IS NOT NULL), sum(payout>0), sum(price*contracts+fee) FILTER (WHERE result IS NOT NULL),
        sum(payout) FILTER (WHERE result IS NOT NULL), avg(still_there), avg(price)
        FROM bets GROUP BY 1,2 ORDER BY 1,2""".format(",".join(f"'{s}'" for s in CRYPTO))
    print(f"{'strategy':16} {'group':13} {'bets':>5} {'settled':>7} {'wins':>5} {'cost $':>8} {'payout $':>9} "
          f"{'P&L $':>7} {'$/$1':>6} {'avg px':>6} {'quote held':>10}")
    out = []
    for s, g, n, ns, w, c, p, st, px in con.execute(q).fetchall():
        c, p = c or 0, p or 0
        out.append((s, g, n, ns, w, c, p))
        print(f"{s:16} {g:13} {n:>5} {ns:>7} {w or 0:>5} {c:>8.2f} {p:>9.2f} {p-c:>7.2f} "
              f"{(p/c if c else float('nan')):>6.2f} {px:>6.2f} {(st if st is not None else float('nan')):>10.0%}")
    return out
