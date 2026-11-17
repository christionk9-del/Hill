"""Small REST client for the TopstepX (ProjectX Gateway) API.

Only the calls the bot needs. Endpoints and fields follow the ProjectX Gateway API as used by
the open-source project-x-py and projectx-api clients:
  POST /api/Auth/loginKey          {userName, apiKey} -> {token}
  POST /api/Auth/validate          -> {newToken}
  POST /api/Account/search         {onlyActiveAccounts} -> {accounts:[{id,name,balance,canTrade}]}
  POST /api/Contract/search        {searchText, live} -> {contracts:[{id,name,tickSize,tickValue,activeContract}]}
  POST /api/History/retrieveBars   {contractId, live, startTime, endTime, unit, unitNumber, limit, includePartialBar}
  POST /api/Order/place            {accountId, contractId, type, side, size, stopPrice?, customTag?} -> {orderId}
  POST /api/Order/modify           {accountId, orderId, stopPrice}
  POST /api/Order/cancel           {accountId, orderId}
  POST /api/Order/searchOpen       {accountId} -> {orders:[{id,contractId,type,side,size,stopPrice,status}]}
  POST /api/Position/searchOpen    {accountId} -> {positions:[{contractId,type,size,averagePrice}]}
  POST /api/Position/closeContract {accountId, contractId}
  POST /api/Trade/search           {accountId, startTimestamp, endTimestamp} -> {trades:[...]}
Order type: 2 = market, 4 = stop. Side: 0 = buy, 1 = sell. Position type: 1 = long, 2 = short.
"""
import time
from datetime import datetime, timezone

import requests

MARKET, STOP = 2, 4
BUY, SELL = 0, 1
LONG, SHORT = 1, 2


class ApiError(RuntimeError):
    pass


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ProjectX:
    def __init__(self, base_url, username, api_key, calls_per_minute=150, timeout=15, log=print):
        self.base = base_url.rstrip("/")
        self.username, self.api_key = username, api_key
        self.timeout, self.log = timeout, log
        self.session = requests.Session()
        self.token, self.token_time = None, 0.0
        self.min_gap = 60.0 / calls_per_minute
        self.last_call = 0.0

    # ---- plumbing --------------------------------------------------------------------
    def login(self):
        r = self.session.post(f"{self.base}/api/Auth/loginKey", json={"userName": self.username, "apiKey": self.api_key},
                              timeout=self.timeout)
        data = self._check(r)
        self.token, self.token_time = data["token"], time.time()
        self.session.headers["Authorization"] = f"Bearer {self.token}"

    def _refresh(self):
        try:
            data = self._post("/api/Auth/validate", {}, refresh=False)
            self.token, self.token_time = data.get("newToken", self.token), time.time()
            self.session.headers["Authorization"] = f"Bearer {self.token}"
        except Exception:
            self.login()

    @staticmethod
    def _check(r):
        if r.status_code != 200:
            raise ApiError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        if not data.get("success", True) or data.get("errorCode", 0) not in (0, None):
            raise ApiError(f"API error {data.get('errorCode')}: {data.get('errorMessage')}")
        return data

    def _post(self, path, body, refresh=True, retries=3):
        if refresh and time.time() - self.token_time > 20 * 3600:     # tokens last about 24 hours
            self._refresh()
        for attempt in range(retries + 1):
            wait = self.min_gap - (time.time() - self.last_call)
            if wait > 0:
                time.sleep(wait)
            self.last_call = time.time()
            try:
                r = self.session.post(f"{self.base}{path}", json=body, timeout=self.timeout)
            except requests.RequestException as e:
                if attempt == retries:
                    raise ApiError(f"network error on {path}: {e}")
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 401 and attempt < retries:
                self.login()
                continue
            if r.status_code == 429 and attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            return self._check(r)

    # ---- calls -----------------------------------------------------------------------
    def accounts(self):
        return self._post("/api/Account/search", {"onlyActiveAccounts": True})["accounts"]

    def find_contract(self, symbol):
        cs = self._post("/api/Contract/search", {"searchText": symbol, "live": False})["contracts"]
        cs = [c for c in cs if str(c.get("id", "")).startswith(f"CON.F.US.{symbol}.")] or cs
        active = [c for c in cs if c.get("activeContract")]
        if not (active or cs):
            raise ApiError(f"no contract found for {symbol}")
        return (active or cs)[0]

    def bars(self, contract_id, start, end, unit_number=5, limit=2000, partial=True):
        body = {"contractId": contract_id, "live": False, "startTime": iso(start), "endTime": iso(end),
                "unit": 2, "unitNumber": unit_number, "limit": limit, "includePartialBar": partial}
        bars = self._post("/api/History/retrieveBars", body)["bars"]
        return sorted(bars, key=lambda b: b["t"])

    def place(self, account_id, contract_id, order_type, side, size, stop_price=None, tag=None):
        body = {"accountId": account_id, "contractId": contract_id, "type": order_type, "side": side, "size": size}
        if stop_price is not None:
            body["stopPrice"] = stop_price
        if tag:
            body["customTag"] = tag
        return self._post("/api/Order/place", body).get("orderId")

    def modify_stop(self, account_id, order_id, stop_price):
        return self._post("/api/Order/modify", {"accountId": account_id, "orderId": order_id, "stopPrice": stop_price})

    def cancel(self, account_id, order_id):
        return self._post("/api/Order/cancel", {"accountId": account_id, "orderId": order_id})

    def open_orders(self, account_id):
        return self._post("/api/Order/searchOpen", {"accountId": account_id})["orders"]

    def positions(self, account_id):
        return self._post("/api/Position/searchOpen", {"accountId": account_id})["positions"]

    def close_position(self, account_id, contract_id):
        return self._post("/api/Position/closeContract", {"accountId": account_id, "contractId": contract_id})

    def trades(self, account_id, start, end):
        return self._post("/api/Trade/search", {"accountId": account_id, "startTimestamp": iso(start),
                                                "endTimestamp": iso(end)})["trades"]


def now_utc():
    return datetime.now(timezone.utc)
