"""On-read live pricing for the dashboard.

The worker only re-prices open trades and re-reads the broker balance every 30s
(the management/health cadence). The UI polls every 5s, so between worker ticks
the numbers sit frozen and look like they aren't moving. These helpers re-price
open trades and re-read the balance on each API read, behind a short shared TTL
cache so we never hammer the broker (one quote per instrument every few seconds,
regardless of how many clients are polling).

Marks are applied to the ORM objects in memory only; the request session is
never committed, so nothing here writes to the DB.
"""

from __future__ import annotations

import time
from typing import Any

from app.models import Trade

_QUOTE_TTL = 3.0
_BALANCE_TTL = 4.0

_quote_cache: dict[str, tuple[float, tuple[float, float]]] = {}
_balance_cache: dict[str, Any] = {"ts": 0.0, "data": None}


def _quote_bid_ask(instrument: str) -> tuple[float, float]:
    now = time.time()
    hit = _quote_cache.get(instrument)
    if hit and now - hit[0] < _QUOTE_TTL:
        return hit[1]
    from app.market_data.factory import get_provider

    q = get_provider().get_quote(instrument)
    val = (float(q.bid), float(q.ask))
    _quote_cache[instrument] = (now, val)
    return val


def mark_values(
    direction: str, size: float, entry: float, mult: float, bid: float, ask: float
) -> tuple[float, float]:
    """Return (current_price, unrealized_pl) for a position marked to the price
    it would actually close at (cross the spread: bid for longs, ask for shorts)."""
    price = bid if direction == "long" else ask
    if direction == "long":
        upl = size * (price - entry) * mult
    else:
        upl = size * (entry - price) * mult
    return price, round(upl, 2)


def live_mark(trade: Trade) -> None:
    """Refresh `current_price`/`unrealized_pl` on an open trade in memory.

    Broker/quote errors are swallowed so a single bad instrument never breaks
    the whole trades feed — the stale stored value is simply kept.
    """
    try:
        bid, ask = _quote_bid_ask(trade.instrument)
    except Exception:  # noqa: BLE001
        return
    from app.services.engine import _ccy_mult

    price, upl = mark_values(
        trade.direction, trade.size, trade.entry_price, _ccy_mult(trade), bid, ask
    )
    trade.current_price = price
    trade.unrealized_pl = upl


def live_balance() -> dict[str, float] | None:
    """Live broker balance/available/open-P&L, cached for a few seconds.

    Returns None when the broker isn't configured; returns the last good value
    on a transient broker error so the dashboard doesn't flicker to blank.
    """
    now = time.time()
    if _balance_cache["data"] is not None and now - _balance_cache["ts"] < _BALANCE_TTL:
        return _balance_cache["data"]
    from app.broker.capital import get_capital_client

    client = get_capital_client()
    if not client.configured:
        return None
    try:
        client.ensure_session()
        data = client.get_account_balance()
    except Exception:  # noqa: BLE001
        return _balance_cache["data"]
    _balance_cache["ts"] = now
    _balance_cache["data"] = data
    return data
