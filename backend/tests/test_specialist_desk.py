"""Tests for the Specialist AI Trading Desk pipeline."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.ai.specialist_desk import (
    run_specialist_pipeline,
    ai_manage_trade,
)
from app.ai.schema import Direction, Strategy


def _mock_urlopen_for_pipeline(responses):
    """Create a mock urlopen that returns sequential responses for the 4 pipeline stages."""
    call_idx = [0]

    def side_effect(req, timeout=None):
        idx = min(call_idx[0], len(responses) - 1)
        call_idx[0] += 1
        resp = MagicMock()
        resp.read.return_value = json.dumps(responses[idx]).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    return side_effect


PAYLOAD = {
    "instrument": "US100",
    "current_price": 18500.0,
    "market_classification": "bullish_trend",
    "indicators": {
        "price": 18500.0, "atr": 50.0, "trend": "up", "rsi": 58.0,
        "ema_20": 18450, "ema_50": 18300, "support": 18400, "resistance": 18700,
    },
    "support_resistance": {"support": 18400, "resistance": 18700},
}


def _analyst_response():
    return {"choices": [{"message": {"content": json.dumps({
        "trend_regime": "strong_uptrend",
        "key_support": 18400.0,
        "key_resistance": 18700.0,
        "nearest_level": "support",
        "distance_to_level_pct": 0.54,
        "momentum": "accelerating",
        "volatility_state": "normal",
        "directional_bias": "bullish",
        "bias_strength": 8,
        "reasoning": "Strong uptrend with accelerating momentum",
    })}}]}


def _pattern_response():
    return {"choices": [{"message": {"content": json.dumps({
        "patterns_found": [{"name": "bull_flag", "type": "continuation", "reliability": 7}],
        "divergences": [],
        "price_action": "higher_highs",
        "pattern_bias": "bullish",
        "pattern_strength": 7,
        "best_pattern": "bull_flag",
        "reasoning": "Bull flag continuation pattern forming after pullback",
    })}}]}


def _signal_response(decision="long", confidence=78):
    return {"choices": [{"message": {"content": json.dumps({
        "decision": decision,
        "confidence": confidence,
        "strategy": "trend_pullback",
        "entry_zone_low": 18480.0,
        "entry_zone_high": 18520.0,
        "invalidation": "Break below 18400 support",
        "reasoning": "Analyst bullish + bull flag pattern = long entry",
    })}}]}


def _risk_response():
    return {"choices": [{"message": {"content": json.dumps({
        "instrument": "US100",
        "direction": "long",
        "strategy": "trend_pullback",
        "entry_type": "market",
        "entry_price": 18500.0,
        "stop_loss": 18400.0,
        "take_profit_1": 18650.0,
        "take_profit_2": 18800.0,
        "confidence": 78,
        "risk_reward": 1.5,
        "position_size": 0,
        "rationale": "Entry at pullback with SL below structure",
        "invalidation_condition": "Break below 18400",
        "risk_flags": [],
        "management_plan": {
            "move_sl_to_breakeven_at_R": 1.2,
            "trail_atr_mult": 2.5,
        },
    })}}]}


def test_full_pipeline_produces_trade():
    """All 4 stages succeed → produces a long trade proposal."""
    responses = [_analyst_response(), _pattern_response(), _signal_response(), _risk_response()]
    with patch("app.ai.specialist_desk.urllib.request.urlopen",
               side_effect=_mock_urlopen_for_pipeline(responses)):
        proposal, meta = run_specialist_pipeline(PAYLOAD, "US100", api_key="test-key")

    assert proposal.direction == Direction.LONG
    assert proposal.strategy == Strategy.TREND_PULLBACK
    assert proposal.stop_loss == pytest.approx(18400.0, abs=1)
    assert proposal.take_profit_1 == pytest.approx(18650.0, abs=1)
    assert "specialist_desk" in proposal.risk_flags
    assert meta["pipeline"] == "specialist_desk"
    assert "analyst" in meta["stages"]
    assert "pattern" in meta["stages"]
    assert "signal" in meta["stages"]
    assert "risk" in meta["stages"]


def test_signal_no_trade_returns_early():
    """Signal Generator says no_trade → pipeline stops, returns no_trade."""
    responses = [_analyst_response(), _pattern_response(), _signal_response("no_trade", 40)]
    with patch("app.ai.specialist_desk.urllib.request.urlopen",
               side_effect=_mock_urlopen_for_pipeline(responses)):
        proposal, meta = run_specialist_pipeline(PAYLOAD, "US100", api_key="test-key")

    assert proposal.direction == Direction.NO_TRADE
    assert "specialist_desk_no_trade" in proposal.risk_flags
    # Risk stage should NOT have been called
    assert "risk" not in meta["stages"]


def test_analyst_failure_raises():
    """If the analyst fails, the whole pipeline raises."""
    def fail_urlopen(req, timeout=None):
        raise ConnectionError("API down")

    with patch("app.ai.specialist_desk.urllib.request.urlopen", side_effect=fail_urlopen):
        with pytest.raises(RuntimeError, match="Market Analyst failed"):
            run_specialist_pipeline(PAYLOAD, "US100", api_key="test-key")


def test_no_api_key_raises():
    with patch("app.ai.specialist_desk.settings") as mock_settings:
        mock_settings.openrouter_api_key = ""
        with pytest.raises(RuntimeError, match="No OpenRouter API key"):
            run_specialist_pipeline(PAYLOAD, "US100", api_key="")


def test_ai_manage_trade_returns_action():
    """Trade Manager AI returns an action for an open trade."""
    manager_resp = {"choices": [{"message": {"content": json.dumps({
        "action": "tighten_stop",
        "new_stop_loss": 18550.0,
        "reason": "Momentum weakening, tighten trail",
        "trail_atr_mult": 2.0,
        "momentum_assessment": "weakening",
        "close_percent": 0,
    })}}]}

    with patch("app.ai.specialist_desk.urllib.request.urlopen",
               side_effect=_mock_urlopen_for_pipeline([manager_resp])):
        result = ai_manage_trade(
            {"instrument": "US100", "direction": "long", "entry_price": 18500,
             "current_price": 18650, "stop_loss": 18400, "current_r": 1.5,
             "atr": 50, "risk_per_unit": 100},
            {"price": 18650, "atr": 50, "rsi": 62},
            api_key="test-key",
        )

    assert result["action"] == "tighten_stop"
    assert result["trail_atr_mult"] == 2.0
