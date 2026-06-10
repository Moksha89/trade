"""Indicator engine tests on synthetic series."""

import math

from app.indicators.engine import Candle, compute_indicators


def _series(prices: list[float]) -> list[Candle]:
    candles = []
    for i, p in enumerate(prices):
        candles.append(
            Candle(ts=i, open=p, high=p + 0.5, low=p - 0.5, close=p, volume=100.0)
        )
    return candles


def test_uptrend_detected():
    ind = compute_indicators(_series([100 + i for i in range(220)]))
    assert ind.trend == "up"
    assert ind.ema20 > ind.ema50 > ind.ema200
    assert ind.price == 319.0


def test_downtrend_detected():
    ind = compute_indicators(_series([320 - i for i in range(220)]))
    assert ind.trend == "down"
    assert ind.ema20 < ind.ema50 < ind.ema200


def test_rsi_bounds_and_atr_positive():
    ind = compute_indicators(_series([100 + math.sin(i / 5) for i in range(120)]))
    assert 0 <= ind.rsi <= 100
    assert ind.atr > 0
    assert ind.vwap is not None  # volume present


def test_support_resistance_from_swings():
    ind = compute_indicators(_series([100 + i for i in range(60)]))
    assert ind.resistance >= ind.support
    assert ind.swing_high >= ind.swing_low
