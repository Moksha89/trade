"""Tests for the OpenRouter multi-model consensus engine."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.ai.openrouter_engine import (
    _call_model_safe,
    propose_trade_consensus,
)
from app.ai.schema import Direction, Strategy


def _mock_openrouter_response(direction="long", confidence=75, strategy="trend_pullback",
                               entry=100.0, sl=99.0, tp1=102.0):
    """Build a fake OpenRouter API response."""
    return {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "instrument": "US100",
                    "direction": direction,
                    "strategy": strategy,
                    "entry_type": "market",
                    "entry_price": entry,
                    "stop_loss": sl,
                    "take_profit_1": tp1,
                    "take_profit_2": tp1 * 1.02,
                    "confidence": confidence,
                    "risk_reward": round(abs(tp1 - entry) / abs(entry - sl), 2),
                    "rationale": f"Test {direction} from model",
                    "invalidation_condition": "test",
                    "risk_flags": [],
                })
            }
        }]
    }


def _make_urlopen_mock(responses):
    """Create a mock for urllib.request.urlopen that returns different responses
    based on call order."""
    call_count = [0]

    def side_effect(req, timeout=None):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        resp = MagicMock()
        resp.read.return_value = json.dumps(responses[idx]).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    return side_effect


PAYLOAD = {
    "instrument": "US100",
    "current_price": 100.0,
    "market_classification": "bullish_trend",
    "indicators": {"price": 100.0, "atr": 1.0, "trend": "up", "rsi": 55.0},
}


def test_consensus_reached_when_2_of_3_agree():
    """Two longs and one short → consensus is long."""
    responses = [
        _mock_openrouter_response("long", 75),
        _mock_openrouter_response("long", 80),
        _mock_openrouter_response("short", 70, sl=101.0, tp1=98.0),
    ]
    with patch("app.ai.openrouter_engine.urllib.request.urlopen",
               side_effect=_make_urlopen_mock(responses)):
        proposal, meta = propose_trade_consensus(
            PAYLOAD, "US100",
            api_key="test-key",
            models=["m1", "m2", "m3"],
        )
    assert proposal.direction == Direction.LONG
    assert meta["consensus"] is True
    assert meta["consensus_direction"] == "long"
    assert proposal.confidence == pytest.approx(77.5, abs=0.1)
    assert "openrouter_consensus" in proposal.risk_flags


def test_no_consensus_when_all_disagree():
    """Long, short, no_trade → no consensus → RuntimeError."""
    responses = [
        _mock_openrouter_response("long", 75),
        _mock_openrouter_response("short", 70, sl=101.0, tp1=98.0),
        _mock_openrouter_response("no_trade", 0),
    ]
    with patch("app.ai.openrouter_engine.urllib.request.urlopen",
               side_effect=_make_urlopen_mock(responses)):
        with pytest.raises(RuntimeError, match="No consensus"):
            propose_trade_consensus(
                PAYLOAD, "US100",
                api_key="test-key",
                models=["m1", "m2", "m3"],
            )


def test_consensus_uses_widest_stop_for_long():
    """For longs, consensus picks the lowest (widest) stop-loss."""
    responses = [
        _mock_openrouter_response("long", 75, sl=99.0, tp1=102.0),
        _mock_openrouter_response("long", 80, sl=98.5, tp1=103.0),
        _mock_openrouter_response("long", 70, sl=99.5, tp1=101.5),
    ]
    with patch("app.ai.openrouter_engine.urllib.request.urlopen",
               side_effect=_make_urlopen_mock(responses)):
        proposal, meta = propose_trade_consensus(
            PAYLOAD, "US100",
            api_key="test-key",
            models=["m1", "m2", "m3"],
        )
    # Widest stop for long = lowest SL
    assert proposal.stop_loss == pytest.approx(98.5, abs=0.01)
    # Most conservative TP for long = lowest TP
    assert proposal.take_profit_1 == pytest.approx(101.5, abs=0.01)


def test_consensus_uses_widest_stop_for_short():
    """For shorts, consensus picks the highest (widest) stop-loss."""
    responses = [
        _mock_openrouter_response("short", 75, entry=100.0, sl=101.0, tp1=98.0),
        _mock_openrouter_response("short", 80, entry=100.0, sl=101.5, tp1=97.5),
        _mock_openrouter_response("short", 70, entry=100.0, sl=100.5, tp1=98.5),
    ]
    with patch("app.ai.openrouter_engine.urllib.request.urlopen",
               side_effect=_make_urlopen_mock(responses)):
        proposal, meta = propose_trade_consensus(
            PAYLOAD, "US100",
            api_key="test-key",
            models=["m1", "m2", "m3"],
        )
    # Widest stop for short = highest SL
    assert proposal.stop_loss == pytest.approx(101.5, abs=0.01)
    # Most conservative TP for short = highest TP (closest to entry)
    assert proposal.take_profit_1 == pytest.approx(98.5, abs=0.01)


def test_no_api_key_raises():
    with patch("app.ai.openrouter_engine.settings") as mock_settings:
        mock_settings.openrouter_api_key = ""
        with pytest.raises(RuntimeError, match="No OpenRouter API key"):
            propose_trade_consensus(PAYLOAD, "US100", api_key="")


def test_single_model_failure_still_reaches_consensus():
    """One model fails but the other two agree → consensus."""
    good1 = _mock_openrouter_response("long", 75)
    good2 = _mock_openrouter_response("long", 80)

    call_count = [0]
    def side_effect(req, timeout=None):
        call_count[0] += 1
        if call_count[0] == 2:
            raise ConnectionError("model down")
        resp_data = good1 if call_count[0] == 1 else good2
        resp = MagicMock()
        resp.read.return_value = json.dumps(resp_data).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    with patch("app.ai.openrouter_engine.urllib.request.urlopen",
               side_effect=side_effect):
        proposal, meta = propose_trade_consensus(
            PAYLOAD, "US100",
            api_key="test-key",
            models=["m1", "m2", "m3"],
        )
    assert proposal.direction == Direction.LONG
    assert meta["consensus"] is True
