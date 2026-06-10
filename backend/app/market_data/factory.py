"""Select a market data provider based on execution mode and broker config.

- paper mode (or missing Capital.com credentials) → synthetic provider
- demo/approval/live mode with credentials → Capital.com REST provider
"""

from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.market_data.base import MarketDataProvider
from app.market_data.synthetic import SyntheticProvider


@lru_cache(maxsize=1)
def _synthetic() -> SyntheticProvider:
    return SyntheticProvider()


def get_provider(mode: str | None = None) -> MarketDataProvider:
    mode = (mode or settings.execution_mode).lower()
    if mode == "paper":
        return _synthetic()
    # demo/approval/live need a real broker; fall back to synthetic if unconfigured.
    from app.broker.capital import get_capital_client

    client = get_capital_client()
    if client.configured:
        return client
    return _synthetic()
