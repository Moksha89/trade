"""Generate trade proposals.

Primary path: call Claude with prepared structured data and parse strict JSON.
Fallback path (no API key or API error): a deterministic heuristic proposer so
paper mode and tests run without external dependencies. The fallback is clearly
flagged in `risk_flags` and the audit log.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.ai.schema import Direction, EntryType, Strategy, TradeProposal
from app.classifier.engine import MarketCondition
from app.config import settings
from app.indicators.engine import IndicatorSet

SYSTEM_PROMPT = (
    "You are a disciplined CFD trading analyst. You ONLY propose trades; a separate "
    "deterministic risk engine decides whether to execute. You must respond with a "
    "single JSON object and nothing else, matching the provided schema exactly. "
    "Analyse ONLY the instrument given in the input data and echo that exact "
    "instrument string in your response; the schema example is illustrative only. "
    "If there is no high-quality setup, return direction and strategy as 'no_trade'. "
    "Never invent prices; base entry/SL/TP on the supplied indicator data. "
    "Respect a minimum risk/reward of 1:2."
)

SCHEMA_HINT = {
    "instrument": "<echo the instrument from the input data>",
    "direction": "long | short | no_trade",
    "strategy": "trend_pullback | breakout_retest | breakdown_retest | range_reversal | momentum_continuation | no_trade",
    "entry_type": "market | limit | stop",
    "entry_price": 0,
    "stop_loss": 0,
    "take_profit_1": 0,
    "take_profit_2": 0,
    "confidence": 0,
    "risk_reward": 0,
    "position_size": 0,
    "rationale": "short explanation",
    "invalidation_condition": "when this setup becomes invalid",
    "risk_flags": [],
    "management_plan": {
        "move_sl_to_breakeven_at_R": 1.0,
        "lock_profit_at_R": 1.5,
        "partial_close_at_R": 2.0,
        "partial_close_percent": 50,
        "trailing_method": "swing | ema20 | atr",
    },
}


def build_payload(
    instrument: str,
    ind: IndicatorSet,
    condition: MarketCondition,
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "instrument": instrument,
        "current_price": ind.price,
        "market_classification": condition.value,
        "indicators": ind.as_dict(),
        "support_resistance": {"support": ind.support, "resistance": ind.resistance},
        "client_sentiment": context.get("client_sentiment"),
        "news_risk": context.get("news_risk", False),
        "open_positions": context.get("open_positions", []),
        "existing_exposure_aed": context.get("existing_exposure_aed", 0),
        "available_risk_budget_aed": context.get("available_risk_budget_aed", 0),
    }


def prompt_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _heuristic_proposal(
    instrument: str, ind: IndicatorSet, condition: MarketCondition
) -> TradeProposal:
    """Deterministic fallback aligned with the classifier and strategy rules."""
    atr = ind.atr or max(ind.price * 0.002, 0.0001)
    price = ind.price

    def rr(entry: float, sl: float, tp: float) -> float:
        risk = abs(entry - sl)
        return abs(tp - entry) / risk if risk else 0.0

    long_map = {
        MarketCondition.BULLISH_TREND: Strategy.TREND_PULLBACK,
        MarketCondition.BREAKOUT: Strategy.BREAKOUT_RETEST,
        MarketCondition.RANGE_BOUND: Strategy.RANGE_REVERSAL,
        MarketCondition.MOMENTUM: Strategy.MOMENTUM_CONTINUATION,
    }
    short_map = {
        MarketCondition.BEARISH_TREND: Strategy.TREND_PULLBACK,
        MarketCondition.BREAKDOWN: Strategy.BREAKDOWN_RETEST,
        MarketCondition.RANGE_BOUND: Strategy.RANGE_REVERSAL,
        MarketCondition.MOMENTUM: Strategy.MOMENTUM_CONTINUATION,
    }

    if condition in (
        MarketCondition.BULLISH_TREND,
        MarketCondition.BREAKOUT,
    ) or (condition == MarketCondition.MOMENTUM and ind.trend == "up"):
        direction = Direction.LONG
        strategy = long_map.get(condition, Strategy.TREND_PULLBACK)
        sl = price - 1.5 * atr
        tp1 = price + 3.0 * atr
        tp2 = price + 4.5 * atr
    elif condition in (
        MarketCondition.BEARISH_TREND,
        MarketCondition.BREAKDOWN,
    ) or (condition == MarketCondition.MOMENTUM and ind.trend == "down"):
        direction = Direction.SHORT
        strategy = short_map.get(condition, Strategy.TREND_PULLBACK)
        sl = price + 1.5 * atr
        tp1 = price - 3.0 * atr
        tp2 = price - 4.5 * atr
    elif condition == MarketCondition.RANGE_BOUND:
        # Reversal toward the opposite side of the range.
        if (price - ind.support) <= (ind.resistance - price):
            direction = Direction.LONG
            sl = ind.support - atr
            tp1 = price + 3.0 * atr
            tp2 = ind.resistance
        else:
            direction = Direction.SHORT
            sl = ind.resistance + atr
            tp1 = price - 3.0 * atr
            tp2 = ind.support
        strategy = Strategy.RANGE_REVERSAL
    else:
        return TradeProposal(
            instrument=instrument,
            direction=Direction.NO_TRADE,
            strategy=Strategy.NO_TRADE,
            rationale=f"No valid setup for condition {condition.value}",
            risk_flags=["heuristic_fallback"],
        )

    confidence = 72.0 if condition != MarketCondition.MOMENTUM else 70.0
    return TradeProposal(
        instrument=instrument,
        direction=direction,
        strategy=strategy,
        entry_type=EntryType.MARKET,
        entry_price=price,
        stop_loss=round(sl, 5),
        take_profit_1=round(tp1, 5),
        take_profit_2=round(tp2, 5),
        confidence=confidence,
        risk_reward=round(rr(price, sl, tp1), 2),
        rationale=(
            f"{strategy.value} on {condition.value}: EMA trend={ind.trend}, "
            f"RSI={ind.rsi:.0f}, MACD hist={ind.macd_hist:.4f}."
        ),
        invalidation_condition="Close beyond stop-loss or trend/structure flips.",
        risk_flags=["heuristic_fallback"],
    )


def _call_claude(payload: dict[str, Any], model: str) -> TradeProposal:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    user_content = (
        "Schema (return exactly this shape as JSON):\n"
        + json.dumps(SCHEMA_HINT, indent=2)
        + "\n\nPrepared market data:\n"
        + json.dumps(payload, indent=2, default=str)
        + "\n\nReturn only the JSON object."
    )
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    text = text.strip()
    # Strip markdown fences if present.
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)
    return TradeProposal.model_validate(data)


def propose_trade(
    instrument: str,
    ind: IndicatorSet,
    condition: MarketCondition,
    context: dict[str, Any] | None = None,
    model: str | None = None,
) -> tuple[TradeProposal, dict[str, Any], str]:
    """Return (proposal, payload, prompt_hash). Falls back to heuristic on error."""
    context = context or {}
    payload = build_payload(instrument, ind, condition, context)
    phash = prompt_hash(payload)
    model = model or settings.anthropic_model

    if settings.anthropic_api_key:
        try:
            proposal = _call_claude(payload, model)
            return proposal, payload, phash
        except Exception:  # noqa: BLE001 — any failure → safe deterministic fallback
            proposal = _heuristic_proposal(instrument, ind, condition)
            proposal.risk_flags = list({*proposal.risk_flags, "ai_error_fallback"})
            return proposal, payload, phash

    proposal = _heuristic_proposal(instrument, ind, condition)
    return proposal, payload, phash
