"""Minimal Kalshi Trade API v2 client (RSA-PSS signed requests)."""
import base64
import time
import uuid
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .config import CONFIG


def cents(obj: dict, key: str):
    """Read a price in integer cents, tolerating both `yes_ask` (cents) and
    `yes_ask_dollars` ("0.0500") style fields."""
    v = obj.get(key)
    if v is not None:
        return int(round(float(v)))
    v = obj.get(f"{key}_dollars")
    if v is not None:
        return int(round(float(v) * 100))
    return None


def dollars(obj: dict, key: str):
    """Price in dollars as float. Combos trade at sub-cent prices, so prefer
    `<key>_dollars` ("0.0063"); fall back to integer-cent `<key>`."""
    v = obj.get(f"{key}_dollars")
    if v is not None:
        return float(v)
    v = obj.get(key)
    if v is not None:
        return float(v) / 100.0
    return None


class KalshiClient:
    def __init__(self, cfg=CONFIG, session=None):
        self.cfg = cfg
        self.s = session or requests.Session()
        self._key = None
        if cfg.key_id and cfg.private_key_path:
            with open(cfg.private_key_path, "rb") as fh:
                self._key = serialization.load_pem_private_key(fh.read(), password=None)
        self._path_prefix = urlparse(cfg.base_url).path.rstrip("/")

    # ---------- low level ----------
    def _headers(self, method: str, path: str) -> dict:
        return self.signed_headers(method, self._path_prefix + path)

    def signed_headers(self, method: str, full_path: str) -> dict:
        """Auth headers for any Kalshi path, e.g. '/trade-api/ws/v2' for the WebSocket."""
        if not self._key:
            return {}
        ts = str(int(time.time() * 1000))
        msg = (ts + method.upper() + full_path).encode()
        sig = self._key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.cfg.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }

    def _req(self, method, path, params=None, json=None, retries=4):
        url = self.cfg.base_url.rstrip("/") + path
        for attempt in range(retries):
            r = self.s.request(method, url, params=params, json=json,
                               headers=self._headers(method, path), timeout=20)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()

    def _paginate(self, path, key, params, max_pages=1000):
        params = dict(params)
        for _ in range(max_pages):
            data = self._req("GET", path, params=params)
            yield from data.get(key, [])
            cur = data.get("cursor")
            if not cur:
                return
            params["cursor"] = cur

    # ---------- public market data ----------
    def events(self, status="open", nested=True, **extra):
        p = {"status": status, "limit": 200, "with_nested_markets": str(nested).lower(), **extra}
        return self._paginate("/events", "events", p)

    def candlesticks(self, series_ticker, ticker, start_ts, end_ts, period_minutes=60):
        path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
        p = {"start_ts": int(start_ts), "end_ts": int(end_ts), "period_interval": period_minutes}
        return self._req("GET", path, params=p).get("candlesticks", [])

    def market(self, ticker):
        return self._req("GET", f"/markets/{ticker}")["market"]

    # ---------- combos (multivariate event collections + RFQ) ----------
    # VERIFY against https://docs.kalshi.com before going live; confirm on the demo
    # environment (KALSHI_BASE_URL=https://demo-api.kalshi.co/trade-api/v2) first.
    def collections(self, status="open", **extra):
        p = {"status": status, "limit": 200, **extra}
        return self._paginate("/multivariate_event_collections", "multivariate_contracts", p)

    def create_combo_market(self, collection_ticker, legs):
        """legs: [{"event_ticker", "market_ticker", "side"}] -> combo market_ticker."""
        body = {"selected_markets": legs}
        r = self._req("POST", f"/multivariate_event_collections/{collection_ticker}", json=body)
        return r.get("market_ticker") or r.get("market", {}).get("ticker")

    def create_rfq(self, market_ticker, contracts):
        body = {"market_ticker": market_ticker, "contracts": int(contracts), "rest_remainder": False}
        r = self._req("POST", "/communications/rfqs", json=body)
        return r.get("id") or r.get("rfq_id")

    def rfq_quotes(self, rfq_id):
        return self._req("GET", "/communications/quotes", params={"rfq_id": rfq_id}).get("quotes", [])

    def accept_quote(self, rfq_id, quote_id, side="yes"):
        return self._req("PUT", f"/communications/rfqs/{rfq_id}/quotes/{quote_id}/accept",
                         json={"accepted_side": side})

    def cancel_rfq(self, rfq_id):
        return self._req("DELETE", f"/communications/rfqs/{rfq_id}")

    # ---------- portfolio (auth) ----------
    def balance_cents(self) -> int:
        return int(self._req("GET", "/portfolio/balance")["balance"])

    def positions(self):
        return self._req("GET", "/portfolio/positions", params={"limit": 1000})

    def buy_limit(self, ticker: str, side: str, count: int, price_c: int, time_in_force: str | None = None):
        assert side in ("yes", "no")
        body = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": int(count),
            "type": "limit",
            f"{side}_price": int(price_c),
            "client_order_id": str(uuid.uuid4()),
        }
        if time_in_force:   # "fill_or_kill" / "immediate_or_cancel": never leave a resting order
            body["time_in_force"] = time_in_force
        return self._req("POST", "/portfolio/orders", json=body)
