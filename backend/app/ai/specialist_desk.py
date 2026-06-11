"""Specialist AI Trading Desk — each model plays a unique expert role.

Architecture:
  1. Market Analyst    → macro view, trend regime, key levels, sentiment
  2. Pattern Scanner   → chart patterns, candlesticks, divergences, formations
  3. Signal Generator  → combines analyst + pattern → trade/no-trade decision
  4. Risk Manager      → SL/TP placement, position sizing, R/R optimisation
  5. Trade Manager     → monitors open trades, adjusts SL/TP dynamically

The first 4 run sequentially during scan (output of one feeds into the next).
Trade Manager runs every 30s on open positions to trail stops and lock profits.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.ai.engine import SCHEMA_HINT
from app.ai.ollama_engine import _coerce
from app.ai.schema import TradeProposal
from app.config import settings

logger = logging.getLogger(__name__)

# ─── Role-specific system prompts ────────────────────────────────────────────

MARKET_ANALYST_PROMPT = """\
You are a Senior Market Analyst AI. Your ONLY job is to analyze market structure and conditions.
You receive raw indicator data for one instrument and must return a JSON analysis.

Your analysis covers:
1. **Trend Regime**: Is this trending (strong/weak), ranging, or transitioning?
2. **Key Levels**: Where are the critical support/resistance zones? Are we near any?
3. **Momentum**: Is momentum accelerating, decelerating, or diverging from price?
4. **Volatility**: Is vol expanding (breakout imminent) or contracting (range)?
5. **Bias**: Given ALL factors, what is the directional bias? (bullish/bearish/neutral)

Return ONLY this JSON structure:
{
  "trend_regime": "strong_uptrend | weak_uptrend | strong_downtrend | weak_downtrend | range | transition",
  "key_support": <float>,
  "key_resistance": <float>,
  "nearest_level": "support | resistance | none",
  "distance_to_level_pct": <float>,
  "momentum": "accelerating | decelerating | diverging | flat",
  "volatility_state": "expanding | contracting | normal",
  "directional_bias": "bullish | bearish | neutral",
  "bias_strength": <1-10>,
  "reasoning": "<one sentence>"
}
"""

PATTERN_SCANNER_PROMPT = """\
You are a Pattern Recognition AI specialist. Your ONLY job is to identify chart patterns and formations.
You receive indicator data AND the Market Analyst's assessment for context.

Scan for:
1. **Chart Patterns**: flags, pennants, wedges, triangles, double tops/bottoms, head & shoulders
2. **Candlestick Patterns**: engulfing, pin bar, doji, morning/evening star, hammer
3. **Divergences**: RSI divergence, MACD divergence (bullish or bearish)
4. **Price Action**: higher highs/lows, lower highs/lows, equal highs/lows

Return ONLY this JSON structure:
{
  "patterns_found": [
    {"name": "<pattern name>", "type": "continuation | reversal | neutral", "reliability": <1-10>}
  ],
  "divergences": [
    {"indicator": "rsi | macd", "type": "bullish | bearish", "strength": <1-10>}
  ],
  "price_action": "higher_highs | lower_lows | consolidating | breakout | breakdown",
  "pattern_bias": "bullish | bearish | neutral",
  "pattern_strength": <1-10>,
  "best_pattern": "<most reliable pattern found or 'none'>",
  "reasoning": "<one sentence>"
}
"""

SIGNAL_GENERATOR_PROMPT = """\
You are a Signal Generator AI. Your job is to make the FINAL trade/no-trade decision.
You receive the Market Analyst's analysis AND the Pattern Scanner's findings.

Decision rules:
- ONLY trade when analyst bias AND pattern bias AGREE on direction
- Minimum bias_strength + pattern_strength >= 12 (out of 20)
- NEVER trade against a strong trend (strong_uptrend → longs only, strong_downtrend → shorts only)
- In range: only trade at boundaries with reversal patterns
- In transition: stay flat (no_trade)
- If confidence < 60, return no_trade

Return ONLY this JSON structure:
{
  "decision": "long | short | no_trade",
  "confidence": <0-100>,
  "strategy": "trend_pullback | breakout_retest | breakdown_retest | range_reversal | momentum_continuation | no_trade",
  "entry_zone_low": <float>,
  "entry_zone_high": <float>,
  "invalidation": "<what would invalidate this setup>",
  "reasoning": "<one sentence combining analyst + pattern logic>"
}
"""

RISK_MANAGER_PROMPT = """\
You are a Risk Manager AI. Your job is to set precise SL/TP levels and position sizing.
You receive the Signal Generator's decision, market data, AND indicator data.

Rules:
- Stop-loss MUST be at least 1× ATR from entry (use the 'atr' value provided)
- Stop-loss should be placed beyond the nearest structure level (support for longs, resistance for shorts)
- Take-profit 1 should give at least 1.5× risk/reward
- Take-profit 2 should target the next major level or 2.5-3× risk
- Risk per trade: max 100 AED (use position_size to control this)

Return ONLY this JSON structure matching the trade schema:
{
  "instrument": "<echo from input>",
  "direction": "<from signal>",
  "strategy": "<from signal>",
  "entry_type": "market | limit",
  "entry_price": <float>,
  "stop_loss": <float>,
  "take_profit_1": <float>,
  "take_profit_2": <float>,
  "confidence": <from signal>,
  "risk_reward": <calculated R/R>,
  "position_size": 0,
  "rationale": "<combined reasoning from all specialists>",
  "invalidation_condition": "<from signal>",
  "risk_flags": [],
  "management_plan": {
    "move_sl_to_breakeven_at_R": 1.2,
    "lock_profit_at_R": 1.5,
    "lock_profit_offset_R": 0.5,
    "trail_atr_mult": 2.5,
    "partial_close_at_R": 2.0,
    "partial_close_percent": 50,
    "trail_start_R": 2.0,
    "trailing_method": "atr"
  }
}
"""

TRADE_MANAGER_PROMPT = """\
You are a Trade Manager AI. Your job is to dynamically manage an OPEN trade.
You receive the current trade details, live price, and fresh indicator data.

Your decisions:
1. Should the stop-loss be moved? (tighten only — never widen)
2. Should we take partial profits?
3. Should we close the trade entirely? (reversal signal, invalidation)
4. What is the optimal trailing stop level based on current ATR and structure?

Rules:
- NEVER move stop-loss further from entry (only tighten)
- Move to breakeven after +1.2R
- Lock +0.5R profit after +1.5R
- Start trailing at 2.5× ATR after +2.0R
- Close if strong reversal pattern detected or key level broken against us
- Factor in momentum: if decelerating, tighten trail to 1.5× ATR

Return ONLY this JSON structure:
{
  "action": "hold | tighten_stop | partial_close | close",
  "new_stop_loss": <float or null>,
  "reason": "<why this action>",
  "trail_atr_mult": <float, current recommended trail multiplier>,
  "momentum_assessment": "strong | weakening | reversing",
  "close_percent": <0-100, only if partial_close>
}
"""


# ─── OpenRouter API call ─────────────────────────────────────────────────────

def _call_specialist(
    api_key: str,
    model: str,
    system_prompt: str,
    user_content: str,
    timeout: float = 45.0,
) -> dict[str, Any]:
    """Call a single specialist model and return parsed JSON."""
    body = json.dumps({
        "model": model,
        "temperature": 0.15,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://trade-bot.local",
            "X-Title": "Trade Specialist Desk",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        result = json.loads(resp.read().decode())

    text = result["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


# ─── Model assignments ────────────────────────────────────────────────────────
# Each role gets a different model to leverage different strengths.

ROLE_MODELS = {
    "analyst": "google/gemini-2.5-flash",               # Fast, great at structured analysis
    "pattern": "meta-llama/llama-3.1-70b-instruct",    # Strong pattern recognition
    "signal": "mistralai/mistral-small-3.2-24b-instruct",  # Good decision-making, fast
    "risk": "google/gemini-2.5-flash",                 # Precise with numbers
    "manager": "meta-llama/llama-3.1-70b-instruct",    # Good at reasoning about evolving situations
}


# ─── Pipeline functions ───────────────────────────────────────────────────────

def run_specialist_pipeline(
    payload: dict[str, Any],
    instrument: str,
    *,
    api_key: str | None = None,
) -> tuple[TradeProposal, dict[str, Any]]:
    """Run the full 4-stage specialist pipeline for one instrument.

    Returns (proposal, metadata_with_all_specialist_outputs).
    Raises RuntimeError if the pipeline cannot produce a decision.
    """
    api_key = api_key or settings.openrouter_api_key
    if not api_key:
        raise RuntimeError("No OpenRouter API key configured")

    meta: dict[str, Any] = {"pipeline": "specialist_desk", "stages": {}}
    t_start = time.time()

    # ── Stage 1: Market Analyst ──────────────────────────────────────────
    user_input_analyst = (
        f"Instrument: {instrument}\n"
        f"Market data:\n{json.dumps(payload, indent=2, default=str)}"
    )
    try:
        t0 = time.time()
        analyst_result = _call_specialist(
            api_key, ROLE_MODELS["analyst"], MARKET_ANALYST_PROMPT, user_input_analyst
        )
        meta["stages"]["analyst"] = {
            "status": "ok", "latency_ms": int((time.time() - t0) * 1000),
            "result": analyst_result,
        }
        logger.info("Specialist Analyst for %s: bias=%s strength=%s",
                    instrument, analyst_result.get("directional_bias"),
                    analyst_result.get("bias_strength"))
    except Exception as exc:  # noqa: BLE001
        meta["stages"]["analyst"] = {"status": "error", "error": str(exc)[:200]}
        raise RuntimeError(f"Market Analyst failed: {exc}") from exc

    # ── Stage 2: Pattern Scanner ─────────────────────────────────────────
    user_input_pattern = (
        f"Instrument: {instrument}\n"
        f"Market data:\n{json.dumps(payload, indent=2, default=str)}\n\n"
        f"Market Analyst Assessment:\n{json.dumps(analyst_result, indent=2)}"
    )
    try:
        t0 = time.time()
        pattern_result = _call_specialist(
            api_key, ROLE_MODELS["pattern"], PATTERN_SCANNER_PROMPT, user_input_pattern
        )
        meta["stages"]["pattern"] = {
            "status": "ok", "latency_ms": int((time.time() - t0) * 1000),
            "result": pattern_result,
        }
        logger.info("Specialist Pattern for %s: bias=%s patterns=%s",
                    instrument, pattern_result.get("pattern_bias"),
                    [p.get("name") for p in pattern_result.get("patterns_found", [])])
    except Exception as exc:  # noqa: BLE001
        meta["stages"]["pattern"] = {"status": "error", "error": str(exc)[:200]}
        raise RuntimeError(f"Pattern Scanner failed: {exc}") from exc

    # ── Stage 3: Signal Generator ────────────────────────────────────────
    user_input_signal = (
        f"Instrument: {instrument}\n"
        f"Current price: {payload.get('current_price')}\n"
        f"Market classification: {payload.get('market_classification')}\n\n"
        f"Market Analyst:\n{json.dumps(analyst_result, indent=2)}\n\n"
        f"Pattern Scanner:\n{json.dumps(pattern_result, indent=2)}"
    )
    try:
        t0 = time.time()
        signal_result = _call_specialist(
            api_key, ROLE_MODELS["signal"], SIGNAL_GENERATOR_PROMPT, user_input_signal
        )
        meta["stages"]["signal"] = {
            "status": "ok", "latency_ms": int((time.time() - t0) * 1000),
            "result": signal_result,
        }
        logger.info("Specialist Signal for %s: decision=%s confidence=%s",
                    instrument, signal_result.get("decision"), signal_result.get("confidence"))
    except Exception as exc:  # noqa: BLE001
        meta["stages"]["signal"] = {"status": "error", "error": str(exc)[:200]}
        raise RuntimeError(f"Signal Generator failed: {exc}") from exc

    # If signal says no_trade, bail early.
    decision = str(signal_result.get("decision", "no_trade")).lower().strip()
    if decision == "no_trade" or signal_result.get("confidence", 0) < 55:
        from app.ai.schema import Direction, Strategy
        proposal = TradeProposal(
            instrument=instrument,
            direction=Direction.NO_TRADE,
            strategy=Strategy.NO_TRADE,
            confidence=signal_result.get("confidence", 0),
            rationale=signal_result.get("reasoning", "No setup"),
            risk_flags=["specialist_desk_no_trade"],
        )
        meta["total_latency_ms"] = int((time.time() - t_start) * 1000)
        return proposal, meta

    # ── Stage 4: Risk Manager ────────────────────────────────────────────
    user_input_risk = (
        f"Instrument: {instrument}\n"
        f"Signal decision: {json.dumps(signal_result, indent=2)}\n\n"
        f"Market data:\n{json.dumps(payload, indent=2, default=str)}\n\n"
        f"Market Analyst levels:\n"
        f"  Support: {analyst_result.get('key_support')}\n"
        f"  Resistance: {analyst_result.get('key_resistance')}\n"
        f"  ATR: {payload.get('indicators', {}).get('atr')}\n\n"
        f"Return the full trade proposal JSON matching this schema:\n"
        f"{json.dumps(SCHEMA_HINT, indent=2)}"
    )
    try:
        t0 = time.time()
        risk_result = _call_specialist(
            api_key, ROLE_MODELS["risk"], RISK_MANAGER_PROMPT, user_input_risk
        )
        meta["stages"]["risk"] = {
            "status": "ok", "latency_ms": int((time.time() - t0) * 1000),
            "result": risk_result,
        }
        logger.info("Specialist Risk for %s: SL=%s TP1=%s R/R=%s",
                    instrument, risk_result.get("stop_loss"),
                    risk_result.get("take_profit_1"), risk_result.get("risk_reward"))
    except Exception as exc:  # noqa: BLE001
        meta["stages"]["risk"] = {"status": "error", "error": str(exc)[:200]}
        raise RuntimeError(f"Risk Manager failed: {exc}") from exc

    # Build the final proposal from the Risk Manager's output.
    risk_result["instrument"] = instrument
    # Ensure direction/strategy come from signal if Risk Manager echoed differently
    risk_result["direction"] = decision
    risk_result["strategy"] = signal_result.get("strategy", "trend_pullback")
    risk_result["confidence"] = signal_result.get("confidence", 70)
    _coerce(risk_result)

    # Build combined rationale
    rationale_parts = []
    if analyst_result.get("reasoning"):
        rationale_parts.append(f"[Analyst] {analyst_result['reasoning']}")
    if pattern_result.get("reasoning"):
        rationale_parts.append(f"[Pattern] {pattern_result['reasoning']}")
    if signal_result.get("reasoning"):
        rationale_parts.append(f"[Signal] {signal_result['reasoning']}")
    risk_result["rationale"] = " | ".join(rationale_parts)[:500]
    risk_result["risk_flags"] = ["specialist_desk"]

    proposal = TradeProposal.model_validate(risk_result)
    meta["total_latency_ms"] = int((time.time() - t_start) * 1000)
    return proposal, meta


# ─── Trade Manager (for open positions) ──────────────────────────────────────

def ai_manage_trade(
    trade_info: dict[str, Any],
    indicator_data: dict[str, Any],
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Ask the Trade Manager AI for dynamic SL/TP adjustments.

    Args:
        trade_info: dict with keys: instrument, direction, entry_price, current_price,
                    stop_loss, take_profit_1, current_r, atr, risk_per_unit
        indicator_data: raw indicator dict for current market state

    Returns:
        dict with keys: action, new_stop_loss, reason, trail_atr_mult, etc.
    """
    api_key = api_key or settings.openrouter_api_key
    if not api_key:
        raise RuntimeError("No OpenRouter API key configured")

    user_content = (
        f"Open trade details:\n{json.dumps(trade_info, indent=2, default=str)}\n\n"
        f"Current indicators:\n{json.dumps(indicator_data, indent=2, default=str)}\n\n"
        "What action should be taken?"
    )
    try:
        result = _call_specialist(
            api_key, ROLE_MODELS["manager"], TRADE_MANAGER_PROMPT, user_content, timeout=30.0
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("Trade Manager AI failed: %s", exc)
        return {"action": "hold", "reason": f"AI error: {exc}", "new_stop_loss": None}
