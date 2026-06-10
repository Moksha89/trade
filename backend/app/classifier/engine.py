"""Classify each instrument into one of the eight market conditions.

This runs before any AI call so the system only asks for a proposal when a setup
may plausibly be valid. The classifier is deterministic.
"""

from __future__ import annotations

from enum import Enum

from app.indicators.engine import IndicatorSet


class MarketCondition(str, Enum):
    BULLISH_TREND = "bullish_trend"
    BEARISH_TREND = "bearish_trend"
    RANGE_BOUND = "range_bound"
    BREAKOUT = "breakout"
    BREAKDOWN = "breakdown"
    MOMENTUM = "momentum"
    CHOPPY = "choppy"
    NEWS_RISK = "news_risk"


def classify_market(
    ind: IndicatorSet,
    *,
    news_risk: bool = False,
    spread_too_high: bool = False,
) -> MarketCondition:
    if news_risk:
        return MarketCondition.NEWS_RISK

    price = ind.price
    rng = ind.resistance - ind.support
    band = (rng / price * 100.0) if price else 0.0

    # Very low volatility + tight range → choppy / no-trade.
    if ind.volatility_pct < 0.05 or band < 0.1:
        return MarketCondition.CHOPPY

    near_resistance = ind.resistance and (ind.resistance - price) <= 0.1 * rng
    near_support = ind.support and (price - ind.support) <= 0.1 * rng

    # Breakout / breakdown: price pushing through the recent range edge with momentum.
    if price >= ind.resistance and ind.macd_hist > 0 and ind.rsi >= 55:
        return MarketCondition.BREAKOUT
    if price <= ind.support and ind.macd_hist < 0 and ind.rsi <= 45:
        return MarketCondition.BREAKDOWN

    # Strong directional momentum.
    if ind.trend == "up" and ind.rsi >= 65 and ind.macd_hist > 0:
        return MarketCondition.MOMENTUM
    if ind.trend == "down" and ind.rsi <= 35 and ind.macd_hist < 0:
        return MarketCondition.MOMENTUM

    if ind.trend == "up":
        return MarketCondition.BULLISH_TREND
    if ind.trend == "down":
        return MarketCondition.BEARISH_TREND

    # Sideways: only interesting near the edges, otherwise choppy mid-range.
    if near_support or near_resistance:
        return MarketCondition.RANGE_BOUND
    return MarketCondition.CHOPPY


# Conditions where it is worth asking the AI for a proposal.
TRADEABLE_CONDITIONS = {
    MarketCondition.BULLISH_TREND,
    MarketCondition.BEARISH_TREND,
    MarketCondition.RANGE_BOUND,
    MarketCondition.BREAKOUT,
    MarketCondition.BREAKDOWN,
    MarketCondition.MOMENTUM,
}


def is_tradeable(condition: MarketCondition) -> bool:
    return condition in TRADEABLE_CONDITIONS
