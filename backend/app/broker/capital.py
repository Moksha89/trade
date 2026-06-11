"""Capital.com REST client.

Implements the endpoints the bot needs: session auth (with automatic re-auth on
expiry), account info, market/instrument discovery, historical prices, position
open/close/modify, and deal confirmation. Live prices stream over WebSocket
(see `stream.py`).

Auth model (Capital.com): POST /api/v1/session with header `X-CAP-API-KEY` and
body `{identifier, password}` returns `CST` and `X-SECURITY-TOKEN` response
headers that authenticate subsequent calls.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

import httpx

from app.config import settings
from app.indicators.engine import Candle
from app.market_data.base import EPIC_MAP, MarketSnapshot, Quote

# Capital.com candle resolution names.
RESOLUTION_MAP = {
    "1M": "MINUTE",
    "5M": "MINUTE_5",
    "15M": "MINUTE_15",
    "1H": "HOUR",
    "4H": "HOUR_4",
    "Daily": "DAY",
}


class CapitalError(RuntimeError):
    pass


class CapitalClient:
    def __init__(
        self,
        api_key: str | None = None,
        identifier: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        session_ttl_seconds: int = 9 * 60,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.capital_api_key
        self.identifier = identifier if identifier is not None else settings.capital_identifier
        self.password = password if password is not None else settings.capital_password
        self.base_url = (base_url or settings.capital_base_url).rstrip("/")
        self._cst: str | None = None
        self._security_token: str | None = None
        self._authed_at: float = 0.0
        self._ttl = session_ttl_seconds
        self._client = httpx.Client(base_url=self.base_url, timeout=20.0)
        self._account_ccy: str | None = None
        self._meta_cache: dict[str, tuple[str, float]] = {}

    # ---- session ---------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.identifier and self.password)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "X-CAP-API-KEY": self.api_key,
            "CST": self._cst or "",
            "X-SECURITY-TOKEN": self._security_token or "",
            "Content-Type": "application/json",
        }

    def ensure_session(self) -> None:
        if not self.configured:
            raise CapitalError("Capital.com credentials not configured")
        fresh = self._cst and (time.time() - self._authed_at) < self._ttl
        if fresh:
            return
        self.login()

    def login(self) -> None:
        resp = self._client.post(
            "/api/v1/session",
            headers={"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={"identifier": self.identifier, "password": self.password},
        )
        if resp.status_code != 200:
            raise CapitalError(f"session auth failed: {resp.status_code} {resp.text}")
        self._cst = resp.headers.get("CST")
        self._security_token = resp.headers.get("X-SECURITY-TOKEN")
        self._authed_at = time.time()
        if not self._cst or not self._security_token:
            raise CapitalError("session auth returned no tokens")

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        from app.broker.rate_limiter import get_rate_limiter

        self.ensure_session()
        get_rate_limiter().acquire()
        resp = self._client.request(method, path, headers=self._auth_headers(), **kwargs)
        if resp.status_code == 401:  # session expired → re-auth once
            self.login()
            get_rate_limiter().acquire()
            resp = self._client.request(
                method, path, headers=self._auth_headers(), **kwargs
            )
        return resp

    def session_tokens(self) -> dict[str, str | None]:
        return {"cst": self._cst, "security_token": self._security_token}

    # ---- account / markets ----------------------------------------------
    def get_accounts(self) -> dict[str, Any]:
        resp = self._request("GET", "/api/v1/accounts")
        if resp.status_code != 200:
            raise CapitalError(f"accounts failed: {resp.status_code} {resp.text}")
        return resp.json()

    def get_account_balance(self) -> dict[str, float]:
        data = self.get_accounts()
        accounts = data.get("accounts", [])
        if not accounts:
            return {"balance": 0.0, "available": 0.0}
        acct = accounts[0]
        bal = acct.get("balance", {})
        return {
            "balance": float(bal.get("balance", 0.0)),
            "available": float(bal.get("available", 0.0)),
            "pnl": float(bal.get("profitLoss", 0.0) or 0.0),
            "deposit": float(bal.get("deposit", 0.0) or 0.0),
        }

    def discover_markets(self, search: str) -> list[dict[str, Any]]:
        resp = self._request("GET", "/api/v1/markets", params={"searchTerm": search})
        if resp.status_code != 200:
            raise CapitalError(f"markets failed: {resp.status_code} {resp.text}")
        return resp.json().get("markets", [])

    def _epic(self, instrument: str) -> str:
        return EPIC_MAP.get(instrument, instrument)

    def account_currency(self) -> str:
        if self._account_ccy is None:
            data = self.get_accounts()
            accounts = data.get("accounts", [])
            self._account_ccy = (accounts[0].get("currency") if accounts else "") or ""
        return self._account_ccy

    def instrument_meta(self, instrument: str) -> tuple[str, float]:
        """Return (quote_currency, lot_size) for an instrument, cached."""
        epic = self._epic(instrument)
        if epic not in self._meta_cache:
            resp = self._request("GET", f"/api/v1/markets/{epic}")
            if resp.status_code != 200:
                raise CapitalError(f"market meta failed: {resp.status_code}")
            inst = resp.json().get("instrument", {})
            ccy = (inst.get("currency") or "").upper()
            lot = float(inst.get("lotSize") or 1.0)
            self._meta_cache[epic] = (ccy, lot)
        return self._meta_cache[epic]

    def risk_unit_multiplier(self, instrument: str) -> float:
        """Account-currency value of a 1-point move for a size-1 position.

        Returns 0.0 when it cannot be determined (unknown quote currency with no
        FX rate, or a transient broker error). Callers treat a non-positive
        multiplier as "cannot size" and skip the trade, so a wrongly-sized order
        in an unsupported currency can never be placed.
        """
        from app.services.fx import quote_to_account

        try:
            quote_ccy, lot = self.instrument_meta(instrument)
            fx = quote_to_account(quote_ccy, self.account_currency())
        except CapitalError:
            return 0.0
        if fx is None:
            return 0.0
        return fx * lot

    def get_candles(self, instrument: str, resolution: str, count: int) -> list[Candle]:
        epic = self._epic(instrument)
        res = RESOLUTION_MAP.get(resolution, "MINUTE")
        resp = self._request(
            "GET", f"/api/v1/prices/{epic}", params={"resolution": res, "max": count}
        )
        if resp.status_code != 200:
            raise CapitalError(f"prices failed: {resp.status_code} {resp.text}")
        out: list[Candle] = []
        for i, p in enumerate(resp.json().get("prices", [])):
            def mid(node: dict[str, Any], key: str) -> float:
                v = node.get(key, {})
                return float((v.get("bid", 0) + v.get("ask", 0)) / 2.0)

            out.append(
                Candle(
                    ts=i,
                    open=mid(p, "openPrice"),
                    high=mid(p, "highPrice"),
                    low=mid(p, "lowPrice"),
                    close=mid(p, "closePrice"),
                    volume=float(p.get("lastTradedVolume", 0) or 0),
                )
            )
        return out

    def get_quote(self, instrument: str) -> Quote:
        epic = self._epic(instrument)
        resp = self._request("GET", f"/api/v1/markets/{epic}")
        if resp.status_code != 200:
            raise CapitalError(f"market snapshot failed: {resp.status_code}")
        snap = resp.json().get("snapshot", {})
        bid = float(snap.get("bid", 0.0))
        ask = float(snap.get("offer", bid))
        return Quote(instrument=instrument, bid=bid, ask=ask, spread_points=abs(ask - bid))

    def get_snapshot(self, instrument: str) -> MarketSnapshot:
        epic = self._epic(instrument)
        resp = self._request("GET", f"/api/v1/markets/{epic}")
        if resp.status_code != 200:
            raise CapitalError(f"market snapshot failed: {resp.status_code}")
        body = resp.json()
        snap = body.get("snapshot", {})
        bid = float(snap.get("bid", 0.0))
        ask = float(snap.get("offer", bid))
        status = snap.get("marketStatus", "TRADEABLE")
        sentiment = None
        try:
            s = self._request(
                "GET", "/api/v1/clientsentiment", params={"marketIds": epic}
            )
            if s.status_code == 200:
                arr = s.json().get("clientSentiments", [])
                if arr:
                    sentiment = float(arr[0].get("longPositionPercentage", 0.0))
        except Exception:  # noqa: BLE001 — sentiment is best-effort
            sentiment = None
        return MarketSnapshot(
            instrument=instrument,
            quote=Quote(instrument, bid, ask, abs(ask - bid)),
            market_open=status == "TRADEABLE",
            client_sentiment_long_pct=sentiment,
        )

    # ---- trading ---------------------------------------------------------
    def open_position(
        self,
        instrument: str,
        direction: str,
        size: float,
        stop_level: float,
        profit_level: float,
    ) -> dict[str, Any]:
        epic = self._epic(instrument)
        payload = {
            "epic": epic,
            "direction": direction.upper(),  # BUY | SELL
            "size": size,
            "stopLevel": stop_level,
            "profitLevel": profit_level,
            "guaranteedStop": False,
        }
        resp = self._request("POST", "/api/v1/positions", json=payload)
        if resp.status_code not in (200, 201):
            raise CapitalError(f"open position failed: {resp.status_code} {resp.text}")
        return resp.json()  # {"dealReference": ...}

    def confirm(self, deal_reference: str) -> dict[str, Any]:
        resp = self._request("GET", f"/api/v1/confirms/{deal_reference}")
        if resp.status_code != 200:
            raise CapitalError(f"confirm failed: {resp.status_code} {resp.text}")
        return resp.json()

    def modify_position(
        self, deal_id: str, stop_level: float | None, profit_level: float | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if stop_level is not None:
            payload["stopLevel"] = stop_level
        if profit_level is not None:
            payload["profitLevel"] = profit_level
        resp = self._request("PUT", f"/api/v1/positions/{deal_id}", json=payload)
        if resp.status_code != 200:
            raise CapitalError(f"modify failed: {resp.status_code} {resp.text}")
        return resp.json()

    def close_position(self, deal_id: str) -> dict[str, Any]:
        resp = self._request("DELETE", f"/api/v1/positions/{deal_id}")
        if resp.status_code != 200:
            raise CapitalError(f"close failed: {resp.status_code} {resp.text}")
        return resp.json()

    def get_positions(self) -> list[dict[str, Any]]:
        resp = self._request("GET", "/api/v1/positions")
        if resp.status_code != 200:
            raise CapitalError(f"positions failed: {resp.status_code} {resp.text}")
        return resp.json().get("positions", [])

    def position_has_stop(self, deal_id: str) -> bool:
        """Authoritatively check a position carries a server-side stop.

        The /confirms payload does not always echo `stopLevel`, so we read the
        live position and look for a stop on the position node. Returns False if
        the position cannot be found or carries no stop.
        """
        try:
            for row in self.get_positions():
                pos = row.get("position", row)
                if str(pos.get("dealId")) == str(deal_id):
                    return pos.get("stopLevel") is not None
        except CapitalError:
            return False
        return False

    def get_hedging_mode(self) -> bool:
        """Whether the account holds opposing positions as separate hedges.

        When false (netting), opening a short against an open long reduces/closes
        the long instead of creating a hedge — so real hedging requires this on.
        """
        resp = self._request("GET", "/api/v1/accounts/preferences")
        if resp.status_code != 200:
            raise CapitalError(f"preferences read failed: {resp.status_code} {resp.text}")
        return bool(resp.json().get("hedgingMode", False))

    def set_hedging_mode(self, enabled: bool) -> bool:
        """Enable/disable account hedging mode. Requires no conflicting open
        positions broker-side. Returns the resulting mode."""
        resp = self._request(
            "PUT", "/api/v1/accounts/preferences", json={"hedgingMode": bool(enabled)}
        )
        if resp.status_code != 200:
            raise CapitalError(f"set hedging mode failed: {resp.status_code} {resp.text}")
        return self.get_hedging_mode()

    # ---- working orders (limit / stop) ------------------------------------
    def create_working_order(
        self,
        instrument: str,
        direction: str,
        size: float,
        level: float,
        order_type: str,
        stop_level: float,
        profit_level: float,
        expiry: str = "GTC",
    ) -> dict[str, Any]:
        """Place a limit or stop working order.

        order_type: "LIMIT" or "STOP"
        expiry: "GTC" (good till cancelled) or ISO datetime
        """
        epic = self._epic(instrument)
        payload = {
            "epic": epic,
            "direction": direction.upper(),
            "size": size,
            "level": level,
            "type": order_type.upper(),
            "stopLevel": stop_level,
            "profitLevel": profit_level,
            "guaranteedStop": False,
            "timeInForce": expiry,
        }
        resp = self._request("POST", "/api/v1/workingorders", json=payload)
        if resp.status_code not in (200, 201):
            raise CapitalError(f"create working order failed: {resp.status_code} {resp.text}")
        return resp.json()

    def get_working_orders(self) -> list[dict[str, Any]]:
        resp = self._request("GET", "/api/v1/workingorders")
        if resp.status_code != 200:
            raise CapitalError(f"working orders failed: {resp.status_code} {resp.text}")
        return resp.json().get("workingOrders", [])

    def cancel_working_order(self, deal_id: str) -> dict[str, Any]:
        resp = self._request("DELETE", f"/api/v1/workingorders/{deal_id}")
        if resp.status_code != 200:
            raise CapitalError(f"cancel order failed: {resp.status_code} {resp.text}")
        return resp.json()

    def close(self) -> None:
        self._client.close()


@lru_cache(maxsize=1)
def get_capital_client() -> "CapitalClient":
    """Process-wide shared client.

    A new ``CapitalClient`` starts with no session, so creating one per call
    (as the factories used to) forced a fresh ``login()`` on every scan/manage
    tick and tripped Capital.com's session-creation rate limit (429). Sharing a
    single instance lets the 9-minute session cache do its job: one login per
    TTL instead of several per tick.
    """
    return CapitalClient()
