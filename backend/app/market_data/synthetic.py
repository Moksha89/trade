"""Synthetic market data for paper mode / tests.

Generates a reproducible random-walk price history per instrument so the full
pipeline (indicators → classifier → AI → risk → paper execution) can run end to
end without any broker connection. Each instrument has a configurable drift so
the demo shows a mix of bullish, bearish, and ranging behaviour.
"""

from __future__ import annotations

import math
import random
import time

from app.indicators.engine import Candle
from app.market_data.base import MarketSnapshot, Quote

# instrument -> (base_price, drift_per_bar, volatility, spread_points)
_PROFILES = {
    "US100": (18500.0, 4.0, 35.0, 1.5),
    "US500": (5200.0, 1.0, 8.0, 0.8),
    "Gold": (2350.0, -0.6, 5.0, 0.4),
    "EUR/USD": (1.0850, 0.0, 0.0008, 0.6),
    "GBP/USD": (1.2700, 0.0001, 0.0010, 0.8),
    "BTC/USD": (64000.0, 20.0, 450.0, 6.0),
}


class SyntheticProvider:
    def __init__(self, seed: int = 7) -> None:
        self._seed = seed

    # Canonical history length so the most-recent price is identical regardless
    # of how many bars a caller requests (the tail is always the same walk).
    _CANON = 600

    def _history(self, instrument: str, count: int) -> list[float]:
        base, drift, vol, _ = _PROFILES.get(instrument, (100.0, 0.2, 1.0, 1.0))
        length = max(count, self._CANON)
        rng = random.Random(hash((instrument, self._seed)) & 0xFFFFFFFF)
        price = base
        prices = []
        for _ in range(length):
            price = max(price + drift + rng.gauss(0, vol), base * 0.5)
            prices.append(price)
        return prices[-count:]

    def get_candles(self, instrument: str, resolution: str, count: int) -> list[Candle]:
        prices = self._history(instrument, count)
        _, _, vol, _ = _PROFILES.get(instrument, (100.0, 0.2, 1.0, 1.0))
        now = int(time.time())
        candles: list[Candle] = []
        for i, p in enumerate(prices):
            wick = abs(vol) * 0.5 or p * 0.001
            candles.append(
                Candle(
                    ts=now - (count - i) * 60,
                    open=p - wick * 0.3,
                    high=p + wick,
                    low=p - wick,
                    close=p,
                    volume=1000.0,
                )
            )
        return candles

    def get_quote(self, instrument: str) -> Quote:
        candles = self.get_candles(instrument, "MINUTE", 5)
        last = candles[-1].close
        _, _, vol, spread = _PROFILES.get(instrument, (100.0, 0.2, 1.0, 1.0))
        # Live-tick oscillation so paper trades evolve between polls. Bounded so
        # it stays within a realistic band around the last canonical close.
        wobble = math.sin(time.time() / 17.0 + hash(instrument) % 7) * vol * 0.5
        mid = last + wobble
        half = spread / 2.0
        return Quote(
            instrument=instrument,
            bid=mid - half,
            ask=mid + half,
            spread_points=spread,
        )

    def risk_unit_multiplier(self, instrument: str) -> float:
        # Synthetic prices are denominated directly in the account currency.
        return 1.0

    def get_snapshot(self, instrument: str) -> MarketSnapshot:
        quote = self.get_quote(instrument)
        rng = random.Random(hash((instrument, "sentiment")) & 0xFFFFFFFF)
        return MarketSnapshot(
            instrument=instrument,
            quote=quote,
            market_open=True,
            client_sentiment_long_pct=round(rng.uniform(30, 70), 1),
        )
