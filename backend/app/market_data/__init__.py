"""Market data layer: provider interface + synthetic and Capital.com sources."""

from app.market_data.base import MarketDataProvider, MarketSnapshot, Quote
from app.market_data.factory import get_provider

__all__ = ["MarketDataProvider", "MarketSnapshot", "Quote", "get_provider"]
