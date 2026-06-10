"""Tests for the Ollama shadow provider (parsing + safety).

These mock the HTTP call so they run without a live Ollama server.
"""

from __future__ import annotations

import json

import pytest

from app.ai import ollama_engine
from app.ai.schema import Direction


def _payload(instrument: str = "GOLD") -> dict:
    return {"instrument": instrument, "current_price": 2000.0}


def test_parses_valid_json_into_proposal(monkeypatch):
    raw = json.dumps(
        {
            "instrument": "GOLD",
            "direction": "short",
            "strategy": "trend_pullback",
            "entry_type": "market",
            "entry_price": 2000.0,
            "stop_loss": 2010.0,
            "take_profit_1": 1980.0,
            "take_profit_2": 1970.0,
            "confidence": 74,
            "risk_reward": 2.0,
            "rationale": "x",
        }
    )
    monkeypatch.setattr(ollama_engine, "_post_chat", lambda *a, **k: raw)
    prop, latency = ollama_engine.propose_trade_ollama(_payload())
    assert prop.direction == Direction.SHORT
    assert prop.instrument == "GOLD"
    assert latency >= 0


def test_strips_markdown_fences(monkeypatch):
    raw = "```json\n" + json.dumps({"instrument": "GOLD", "direction": "no_trade", "strategy": "no_trade"}) + "\n```"
    monkeypatch.setattr(ollama_engine, "_post_chat", lambda *a, **k: raw)
    prop, _ = ollama_engine.propose_trade_ollama(_payload())
    assert prop.direction == Direction.NO_TRADE


def test_forces_correct_instrument(monkeypatch):
    # Model echoes the schema's placeholder instrument; we overwrite it.
    raw = json.dumps({"instrument": "WRONG", "direction": "no_trade", "strategy": "no_trade"})
    monkeypatch.setattr(ollama_engine, "_post_chat", lambda *a, **k: raw)
    prop, _ = ollama_engine.propose_trade_ollama(_payload("US100"))
    assert prop.instrument == "US100"


def test_propagates_errors_for_caller_to_swallow(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ollama_engine, "_post_chat", boom)
    with pytest.raises(RuntimeError):
        ollama_engine.propose_trade_ollama(_payload())
