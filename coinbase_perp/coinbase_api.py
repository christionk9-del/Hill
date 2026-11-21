"""Minimal Coinbase Advanced Trade REST client for US perpetual-style futures (Coinbase Financial Markets).

Only the calls the bot needs. Authentication is a CDP API key (Coinbase Developer Platform):
  - COINBASE_API_KEY_NAME   e.g. organizations/{org_id}/apiKeys/{key_id}  (or just the key id for Ed25519 keys)
  - COINBASE_API_SECRET     the private key: an EC PEM block (ES256) or a base64 Ed25519 secret
Each request carries a fresh 2-minute JWT. Keys are read from the environment, never from the config.

Give the key "View" and "Trade" permissions only. Do NOT enable "Transfer".

Every method raises ApiError on failure so the bot can decide what to do.
"""
import base64
import json
import secrets
import time
import uuid
from datetime import datetime, timezone

import requests

HOST = "api.coinbase.com"
BASE = f"https://{HOST}"
GRAN_SECONDS = {"ONE_MINUTE": 60, "FIVE_MINUTE": 300}


class ApiError(Exception):
    pass


def _num(x, default=0.0):
    if isinstance(x, dict):
        x = x.get("value")
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class Signer:
    """Builds the per-request JWT for a CDP key (ES256 PEM or Ed25519 base64)."""

    def __init__(self, key_name, secret):
        import jwt  # PyJWT
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self._jwt = jwt
        self.name = key_name
        secret = secret.replace("\\n", "\n").strip()
        if secret.startswith("-----BEGIN"):
            self.key = serialization.load_pem_private_key(secret.encode(), password=None)
            self.alg = "ES256"
        else:
            raw = base64.b64decode(secret)
            self.key = Ed25519PrivateKey.from_private_bytes(raw[:32])
            self.alg = "EdDSA"

    def token(self, method, path):
        now = int(time.time())
        payload = {"sub": self.name, "iss": "cdp", "nbf": now, "exp": now + 120, "uri": f"{method} {HOST}{path}"}
        return self._jwt.encode(payload, self.key, algorithm=self.alg, headers={"kid": self.name, "nonce": secrets.token_hex(16)})


class Coinbase:
    def __init__(self, key_name=None, secret=None, timeout=15, log=None):
        self.signer = Signer(key_name, secret) if key_name and secret else None
        self.s = requests.Session()
        self.s.headers.update({"Content-Type": "application/json", "User-Agent": "donchian-perp-bot"})
        self.timeout = timeout
        self.log = log or (lambda m: None)
        self._products = {}

    # ---------------------------------------------------------------- transport
    def _req(self, method, path, params=None, body=None, auth=True, retries=3):
        url = BASE + path
        last = None
        for attempt in range(retries):
            headers = {}
            if auth:
                if self.signer is None:
                    raise ApiError("no API key configured")
                headers["Authorization"] = "Bearer " + self.signer.token(method, path)
            try:
                r = self.s.request(method, url, params=params, data=json.dumps(body) if body is not None else None,
                                   headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                last = f"{method} {path}: {e}"
                time.sleep(1 + attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"{method} {path}: HTTP {r.status_code} {r.text[:200]}"
                time.sleep(1 + 2 * attempt)
                continue
            if r.status_code >= 400:
                raise ApiError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
            try:
                return r.json() if r.text else {}
            except ValueError:
                raise ApiError(f"{method} {path}: bad JSON {r.text[:200]}")
        raise ApiError(last or f"{method} {path}: failed")

    # ---------------------------------------------------------------- market data
    def product(self, product_id):
        if product_id not in self._products:
            p = self._req("GET", f"/api/v3/brokerage/products/{product_id}", auth=self.signer is not None) \
                if self.signer else self._req("GET", f"/api/v3/brokerage/market/products/{product_id}", auth=False)
            fpd = p.get("future_product_details") or {}
            self._products[product_id] = dict(
                id=product_id, tick=_num(p.get("price_increment"), 0.01), size_step=_num(p.get("base_increment"), 1.0),
                contract_size=_num(fpd.get("contract_size"), 1.0), product_type=p.get("product_type"),
                expiry_type=fpd.get("contract_expiry_type"), trading_disabled=bool(p.get("trading_disabled")),
                status=p.get("status"), raw=p)
        return self._products[product_id]

    def candles(self, product_id, start, end, granularity="FIVE_MINUTE"):
        """Bars between start and end (aware datetimes), oldest first, as (utc datetime, o, h, l, c).
        The newest bar may still be forming."""
        step = GRAN_SECONDS[granularity] * 300
        t0, t1 = int(start.timestamp()), int(end.timestamp())
        out = {}
        path = f"/api/v3/brokerage/products/{product_id}/candles" if self.signer else f"/api/v3/brokerage/market/products/{product_id}/candles"
        while t0 < t1:
            hi = min(t0 + step, t1)
            j = self._req("GET", path, params=dict(start=str(t0), end=str(hi), granularity=granularity), auth=self.signer is not None)
            for c in j.get("candles", []):
                ts = int(c["start"])
                out[ts] = (datetime.fromtimestamp(ts, timezone.utc), float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]))
            t0 = hi
        return [out[k] for k in sorted(out)]

    def best_bid_ask(self, product_id):
        j = self._req("GET", "/api/v3/brokerage/best_bid_ask", params={"product_ids": product_id})
        for pb in j.get("pricebooks", []):
            if pb.get("product_id") == product_id and pb.get("bids") and pb.get("asks"):
                return float(pb["bids"][0]["price"]), float(pb["asks"][0]["price"])
        raise ApiError(f"no book for {product_id}")

    # ---------------------------------------------------------------- account
    def balance(self):
        """Total USD account value used for sizing and the daily loss limit."""
        b = self._req("GET", "/api/v3/brokerage/cfm/balance_summary").get("balance_summary", {})
        total = _num(b.get("total_usd_balance"), None)
        if total is None:
            raise ApiError(f"balance_summary missing total_usd_balance: {list(b)}")
        return dict(total=total + _num(b.get("unrealized_pnl")), cash=total,
                    buying_power=_num(b.get("futures_buying_power")), raw=b)

    def position(self, product_id):
        """(side +1/-1, contracts, avg entry) or None when flat."""
        try:
            p = self._req("GET", f"/api/v3/brokerage/cfm/positions/{product_id}").get("position") or {}
        except ApiError as e:
            if "404" in str(e):
                return None
            raise
        n = _num(p.get("number_of_contracts"))
        if not n:
            return None
        side = p.get("side", "")
        sgn = 1 if side == "LONG" else -1 if side == "SHORT" else (1 if n > 0 else -1)
        return (sgn, abs(n), _num(p.get("avg_entry_price")))

    def open_orders(self, product_id):
        orders, cursor = [], None
        while True:
            params = {"product_ids": product_id, "order_status": "OPEN", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            j = self._req("GET", "/api/v3/brokerage/orders/historical/batch", params=params)
            for o in j.get("orders", []):
                cfg = o.get("order_configuration") or {}
                sl = cfg.get("stop_limit_stop_limit_gtc") or {}
                orders.append(dict(id=o["order_id"], client_id=o.get("client_order_id"), side=1 if o.get("side") == "BUY" else -1,
                                   kind="stop" if sl else "other", stop=_num(sl.get("stop_price"), None),
                                   limit=_num(sl.get("limit_price"), None), size=_num(sl.get("base_size"), None), raw=o))
            if not j.get("has_next") or not j.get("cursor"):
                return orders
            cursor = j["cursor"]

    def fills(self, product_id, start):
        j = self._req("GET", "/api/v3/brokerage/orders/historical/fills",
                      params={"product_ids": product_id, "start_sequence_timestamp": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 250})
        return j.get("fills", [])

    # ---------------------------------------------------------------- orders
    @staticmethod
    def _cid(tag):
        return f"{tag}-{uuid.uuid4().hex[:12]}"

    def _create(self, body):
        j = self._req("POST", "/api/v3/brokerage/orders", body=body, retries=1)   # never blind-retry an order
        if not j.get("success"):
            raise ApiError(f"order rejected: {json.dumps(j.get('error_response') or j)[:300]}")
        return j["success_response"]["order_id"]

    def market(self, product_id, side, contracts, tag="dv"):
        return self._create({"client_order_id": self._cid(tag), "product_id": product_id, "side": "BUY" if side > 0 else "SELL",
                             "order_configuration": {"market_market_ioc": {"base_size": _fmt(contracts)}}})

    def stop_order(self, product_id, side, contracts, stop_price, limit_price, tag="dv"):
        """Stop-limit GTC. side +1 = buy stop (triggers when price rises to stop_price)."""
        return self._create({"client_order_id": self._cid(tag), "product_id": product_id, "side": "BUY" if side > 0 else "SELL",
                             "order_configuration": {"stop_limit_stop_limit_gtc": {
                                 "base_size": _fmt(contracts), "stop_price": _fmt(stop_price), "limit_price": _fmt(limit_price),
                                 "stop_direction": "STOP_DIRECTION_STOP_UP" if side > 0 else "STOP_DIRECTION_STOP_DOWN"}}})

    def edit_stop(self, order_id, contracts, stop_price, limit_price):
        j = self._req("POST", "/api/v3/brokerage/orders/edit", retries=1,
                      body={"order_id": order_id, "size": _fmt(contracts), "stop_price": _fmt(stop_price), "price": _fmt(limit_price)})
        if not j.get("success"):
            raise ApiError(f"edit rejected: {json.dumps(j.get('errors') or j)[:300]}")

    def cancel(self, order_ids):
        if not order_ids:
            return
        j = self._req("POST", "/api/v3/brokerage/orders/batch_cancel", body={"order_ids": list(order_ids)})
        bad = [r for r in j.get("results", []) if not r.get("success")]
        if bad:
            raise ApiError(f"cancel failed: {bad[:3]}")

    def close_position(self, product_id, contracts):
        j = self._req("POST", "/api/v3/brokerage/orders/close_position", retries=1,
                      body={"client_order_id": self._cid("close"), "product_id": product_id, "size": _fmt(contracts)})
        if not j.get("success"):
            raise ApiError(f"close_position rejected: {json.dumps(j.get('error_response') or j)[:300]}")

    def preview_market(self, product_id, side, contracts):
        return self._req("POST", "/api/v3/brokerage/orders/preview", body={
            "product_id": product_id, "side": "BUY" if side > 0 else "SELL",
            "order_configuration": {"market_market_ioc": {"base_size": _fmt(contracts)}}})


def _fmt(x):
    s = f"{float(x):.10f}".rstrip("0").rstrip(".")
    return s or "0"
