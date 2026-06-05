"""Market data provider interface and shared dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.indicators.engine import Candle


@dataclass
class Quote:
    instrument: str
    bid: float
    ask: float
    spread_points: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass
class MarketSnapshot:
    instrument: str
    quote: Quote
    market_open: bool
    client_sentiment_long_pct: float | None = None


# Capital.com epics for the v1 instruments. Used by the live provider; the
# synthetic provider keys off the friendly name directly.
EPIC_MAP = {
    "US100": "US100",
    "US500": "US500",
    "Gold": "GOLD",
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "BTC/USD": "BTCUSD",
}


class MarketDataProvider(Protocol):
    def get_candles(self, instrument: str, resolution: str, count: int) -> list[Candle]:
        ...

    def get_quote(self, instrument: str) -> Quote:
        ...

    def get_snapshot(self, instrument: str) -> MarketSnapshot:
        ...
