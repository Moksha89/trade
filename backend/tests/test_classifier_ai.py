"""Classifier + AI heuristic fallback tests (no network)."""

from app.ai.engine import propose_trade
from app.ai.schema import Direction
from app.classifier.engine import MarketCondition, classify_market, is_tradeable
from app.indicators.engine import Candle, compute_indicators


def _series(prices):
    return [Candle(ts=i, open=p, high=p + 0.5, low=p - 0.5, close=p, volume=100.0)
            for i, p in enumerate(prices)]


def test_classify_bullish_trend():
    ind = compute_indicators(_series([100 + i * 0.5 for i in range(220)]))
    cond = classify_market(ind)
    assert cond in (MarketCondition.BULLISH_TREND, MarketCondition.MOMENTUM, MarketCondition.BREAKOUT)
    assert is_tradeable(cond)


def test_news_risk_overrides():
    ind = compute_indicators(_series([100 + i for i in range(220)]))
    assert classify_market(ind, news_risk=True) == MarketCondition.NEWS_RISK


def test_heuristic_proposal_long_in_uptrend():
    ind = compute_indicators(_series([100 + i * 0.5 for i in range(220)]))
    cond = classify_market(ind)
    proposal, payload, phash = propose_trade("US100", ind, cond)
    assert "instrument" in payload and len(phash) == 16
    if proposal.is_trade:
        assert proposal.direction == Direction.LONG
        assert proposal.stop_loss < proposal.entry_price < proposal.take_profit_1
        assert proposal.risk_reward >= 1.9
