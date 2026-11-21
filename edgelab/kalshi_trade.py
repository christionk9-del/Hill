"""Minimal, careful Kalshi Trade API client (V2 order endpoints), per docs.kalshi.com (Nov 2026).

* Auth: headers KALSHI-ACCESS-KEY / -TIMESTAMP (ms) / -SIGNATURE = base64(sign(timestamp + METHOD + path)),
  path from the API root WITHOUT query string; RSA keys use RSA-PSS(SHA-256, salt = digest length),
  Ed25519 keys sign the message directly.
* Orders: POST /portfolio/events/orders with a single YES book: side "bid" = buy YES at `price`,
  side "ask" = sell YES at `price` == buy NO at 1 - price. Prices/counts are fixed-point strings.
* A 408/5xx/timeout means the outcome is UNKNOWN: never resubmit blindly; look the order up by
  client_order_id (GET /portfolio/orders), then decide.

The private key never leaves the user's machine. Requires `pip install cryptography` (live trading only).
"""
from __future__ import annotations

import base64
import json
import os
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASES = {
    "prod": "https://external-api.kalshi.com/trade-api/v2",
    "demo": "https://external-api.demo.kalshi.co/trade-api/v2",
}
PUBLIC = "https://api.elections.kalshi.com/trade-api/v2"  # market data (no auth)
CONFIG_PATH = os.path.expanduser("~/.edgelab/kalshi.toml")


class KalshiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, unknown_outcome: bool = False):
        super().__init__(f"Kalshi {status} {code}: {message}")
        self.status, self.code, self.unknown_outcome = status, code, unknown_outcome


def load_config(path: str = CONFIG_PATH) -> dict:
    cfg = {}
    if os.path.exists(path):
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    cfg.setdefault("env", "demo")
    cfg["key_id"] = os.environ.get("KALSHI_KEY_ID", cfg.get("key_id", ""))
    cfg["private_key_path"] = os.path.expanduser(os.environ.get("KALSHI_PRIVATE_KEY_PATH", cfg.get("private_key_path", "")))
    cfg.setdefault("max_order_usd", 200.0)
    cfg.setdefault("max_daily_usd", 1000.0)
    cfg.setdefault("fee_rate", 0.07)
    cfg.setdefault("ledger", os.path.expanduser("~/.edgelab/arbs.db"))
    return cfg


def load_private_key(path: str):
    from cryptography.hazmat.primitives import serialization
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def sign(private_key, timestamp_ms: str, method: str, path: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    msg = f"{timestamp_ms}{method.upper()}{path.split('?')[0]}".encode()
    if isinstance(private_key, Ed25519PrivateKey):
        sig = private_key.sign(msg)
    else:
        sig = private_key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                               hashes.SHA256())
    return base64.b64encode(sig).decode()


def fp(x: float, places: int = 2) -> str:
    return f"{x:.{places}f}"


class KalshiClient:
    def __init__(self, env: str = "demo", key_id: str = "", private_key=None, transport=None):
        if env not in BASES:
            raise ValueError("env must be 'demo' or 'prod'")
        self.env, self.base, self.key_id, self.key = env, BASES[env], key_id, private_key
        self.transport = transport or self._urllib  # injectable for tests

    @classmethod
    def from_config(cls, cfg: dict, env: str | None = None):
        env = env or cfg["env"]
        if not cfg.get("key_id") or not cfg.get("private_key_path"):
            raise RuntimeError(f"Set key_id and private_key_path in {CONFIG_PATH} (or KALSHI_KEY_ID / KALSHI_PRIVATE_KEY_PATH)")
        return cls(env, cfg["key_id"], load_private_key(cfg["private_key_path"]))

    # -------------------------------------------------------------- transport
    @staticmethod
    def _urllib(method, url, headers, body):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={**headers, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"error": {"code": "http_error", "message": raw.decode(errors='replace')[:200]}}
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            return 0, {"error": {"code": "connection_error", "message": str(e)}}

    def _req(self, method, path, params=None, body=None, auth=True):
        url = self.base + path + (f"?{urllib.parse.urlencode(params)}" if params else "")
        headers = {"User-Agent": "edgelab-hedger/1.0"}
        if auth:
            ts = str(int(time.time() * 1000))
            sign_path = urllib.parse.urlparse(self.base + path).path
            headers.update({"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                            "KALSHI-ACCESS-SIGNATURE": sign(self.key, ts, method, sign_path)})
        status, data = self.transport(method, url, headers, body)
        if 200 <= status < 300:
            return data
        err = (data or {}).get("error") or data or {}
        code = err.get("code", "unknown") if isinstance(err, dict) else str(err)
        msg = err.get("message", "") if isinstance(err, dict) else ""
        unknown = status == 0 or status == 408 or status >= 500
        raise KalshiError(status, code, msg, unknown_outcome=unknown)

    # ------------------------------------------------------------ market data
    def orderbook(self, ticker: str, depth: int = 20) -> dict:
        """{'yes': [(price, count) best first], 'no': [...]} of resting BIDS, in dollars."""
        status, data = self.transport("GET", f"{PUBLIC}/markets/{ticker}/orderbook?depth={depth}", {"User-Agent": "edgelab"}, None)
        if status != 200:
            raise KalshiError(status, "orderbook_error", str(data)[:200], unknown_outcome=False)
        ob = data.get("orderbook_fp") or data.get("orderbook") or {}
        def side(k):
            rows = ob.get(f"{k}_dollars") or []
            return sorted(((float(p), float(c)) for p, c in rows), key=lambda x: -x[0])
        return {"yes": side("yes"), "no": side("no")}

    def market(self, ticker: str) -> dict:
        status, data = self.transport("GET", f"{PUBLIC}/markets/{ticker}", {"User-Agent": "edgelab"}, None)
        if status != 200:
            raise KalshiError(status, "market_error", str(data)[:200])
        return data.get("market", data)

    # -------------------------------------------------------------- portfolio
    def balance(self) -> float:
        d = self._req("GET", "/portfolio/balance")
        return float(d.get("balance_dollars") or d.get("balance", 0) / 100)

    def positions(self, ticker: str | None = None) -> dict:
        return self._req("GET", "/portfolio/positions", params={"ticker": ticker} if ticker else None)

    def find_order(self, ticker: str, client_order_id: str, tries: int = 5) -> dict | None:
        """Order reads are asynchronous: poll a few times before concluding it doesn't exist."""
        for i in range(tries):
            d = self._req("GET", "/portfolio/orders", params={"ticker": ticker, "limit": 100})
            for o in d.get("orders", []):
                if o.get("client_order_id") == client_order_id:
                    return o
            time.sleep(0.5 * (i + 1))
        return None

    # ----------------------------------------------------------------- orders
    def buy_ioc(self, ticker: str, outcome: str, count: float, max_cost: float) -> dict:
        """Buy `count` contracts of YES or NO, paying at most `max_cost` per contract, immediate-or-cancel.
        Returns {order_id, client_order_id, filled, avg_cost, avg_fee, unknown_outcome}."""
        if outcome not in ("yes", "no") or not (0 < max_cost < 1) or count <= 0:
            raise ValueError("bad order parameters")
        # single YES book: buying NO at <= q  ==  selling YES at >= 1 - q
        side, price = ("bid", max_cost) if outcome == "yes" else ("ask", 1 - max_cost)
        coid = f"edgelab-{uuid.uuid4()}"
        body = {"ticker": ticker, "client_order_id": coid, "side": side, "count": fp(count), "price": fp(price, 4),
                "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
                "cancel_order_on_pause": True}
        try:
            r = self._req("POST", "/portfolio/events/orders", body=body)
            return self._fill_summary(r, outcome, coid, unknown=False)
        except KalshiError as e:
            if not e.unknown_outcome:
                raise
            o = self.find_order(ticker, coid)  # outcome unknown: look it up, never resubmit blindly
            if o is None:
                return {"order_id": None, "client_order_id": coid, "filled": 0.0, "avg_cost": None, "avg_fee": None,
                        "unknown_outcome": True, "error": str(e)}
            filled = float(o.get("fill_count_fp") or 0)
            cost = float(o.get("taker_fill_cost_dollars") or 0) + float(o.get("maker_fill_cost_dollars") or 0)
            fees = float(o.get("taker_fees_dollars") or 0) + float(o.get("maker_fees_dollars") or 0)
            return {"order_id": o.get("order_id"), "client_order_id": coid, "filled": filled,
                    "avg_cost": cost / filled if filled else None, "avg_fee": fees / filled if filled else None,
                    "unknown_outcome": False, "recovered": True}

    @staticmethod
    def _fill_summary(r: dict, outcome: str, coid: str, unknown: bool) -> dict:
        filled = float(r.get("fill_count") or 0)
        avg_yes = float(r["average_fill_price"]) if r.get("average_fill_price") else None
        avg_cost = None if avg_yes is None else (avg_yes if outcome == "yes" else 1 - avg_yes)
        return {"order_id": r.get("order_id"), "client_order_id": coid, "filled": filled, "avg_cost": avg_cost,
                "avg_fee": float(r["average_fee_paid"]) if r.get("average_fee_paid") else None,
                "remaining": float(r.get("remaining_count") or 0), "unknown_outcome": unknown}
