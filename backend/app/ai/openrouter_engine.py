"""Multi-model consensus engine via OpenRouter.

Calls 2-3 models in parallel, only proposes a trade when the majority agree on
direction. This eliminates the single-model bias problem (e.g. Ollama's 7B
shorting everything) by requiring independent confirmation.

Consensus rules:
  - 2/3+ models agree on direction → trade with averaged confidence
  - Stop-loss = widest (most conservative) from agreeing models
  - Take-profit = closest (most conservative) from agreeing models
  - All disagree or < 2 valid responses → no_trade
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.ai.engine import SCHEMA_HINT, SYSTEM_PROMPT
from app.ai.ollama_engine import _coerce
from app.ai.schema import Direction, Strategy, TradeProposal
from app.config import settings

logger = logging.getLogger(__name__)

# Models chosen for speed, structured-output quality, and cost-effectiveness.
DEFAULT_MODELS = [
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.1-70b-instruct",
    "mistralai/mistral-small-3.2-24b-instruct",
]


def _call_openrouter(
    api_key: str,
    model: str,
    payload: dict[str, Any],
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Call a single OpenRouter model and return parsed JSON data."""
    user_content = (
        "Schema (return exactly this shape as JSON):\n"
        + json.dumps(SCHEMA_HINT, indent=2)
        + "\n\nPrepared market data:\n"
        + json.dumps(payload, indent=2, default=str)
        + "\n\nReturn only the JSON object."
    )
    body = json.dumps({
        "model": model,
        "temperature": 0.2,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
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
            "X-Title": "Trade AI Consensus",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        result = json.loads(resp.read().decode())

    text = result["choices"][0]["message"]["content"].strip()
    # Strip markdown fences if present.
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def _call_model_safe(
    api_key: str,
    model: str,
    payload: dict[str, Any],
    instrument: str,
    timeout: float = 60.0,
) -> tuple[str, TradeProposal | None, int]:
    """Call one model, return (model_name, proposal_or_None, latency_ms)."""
    t0 = time.time()
    try:
        data = _call_openrouter(api_key, model, payload, timeout)
        data["instrument"] = instrument
        _coerce(data)
        # Normalise confidence
        conf = data.get("confidence")
        if isinstance(conf, (int, float)) and 0 < conf <= 1:
            data["confidence"] = conf * 100
        proposal = TradeProposal.model_validate(data)
        latency = int((time.time() - t0) * 1000)
        return model, proposal, latency
    except Exception as exc:  # noqa: BLE001
        latency = int((time.time() - t0) * 1000)
        logger.warning("OpenRouter %s failed (%dms): %s", model, latency, exc)
        return model, None, latency


def propose_trade_consensus(
    payload: dict[str, Any],
    instrument: str,
    *,
    api_key: str | None = None,
    models: list[str] | None = None,
    timeout: float = 60.0,
) -> tuple[TradeProposal, dict[str, Any]]:
    """Call multiple models in parallel and return consensus proposal.

    Returns (proposal, metadata) where metadata contains per-model results.
    Raises RuntimeError if no consensus is reached.
    """
    api_key = api_key or settings.openrouter_api_key
    if not api_key:
        raise RuntimeError("No OpenRouter API key configured")
    models = models or DEFAULT_MODELS

    # Call all models in parallel.
    results: list[tuple[str, TradeProposal | None, int]] = []
    with ThreadPoolExecutor(max_workers=len(models)) as pool:
        futures = {
            pool.submit(_call_model_safe, api_key, m, payload, instrument, timeout): m
            for m in models
        }
        for future in as_completed(futures):
            results.append(future.result())

    # Gather valid proposals.
    valid: list[tuple[str, TradeProposal]] = []
    meta: dict[str, Any] = {"models_called": len(models), "responses": {}}
    for model_name, proposal, latency in results:
        short_name = model_name.split("/")[-1]
        if proposal is None:
            meta["responses"][short_name] = {"status": "error", "latency_ms": latency}
        elif not proposal.is_trade or proposal.direction == Direction.NO_TRADE:
            meta["responses"][short_name] = {
                "status": "no_trade",
                "latency_ms": latency,
            }
        else:
            meta["responses"][short_name] = {
                "status": "ok",
                "direction": proposal.direction.value,
                "confidence": proposal.confidence,
                "strategy": proposal.strategy.value,
                "stop_loss": proposal.stop_loss,
                "take_profit_1": proposal.take_profit_1,
                "risk_reward": proposal.risk_reward,
                "latency_ms": latency,
            }
            valid.append((model_name, proposal))

    # Count directions.
    direction_votes: dict[str, list[tuple[str, TradeProposal]]] = {}
    for model_name, prop in valid:
        d = prop.direction.value
        direction_votes.setdefault(d, []).append((model_name, prop))

    # Find majority direction (need 2+ votes).
    consensus_dir = None
    consensus_group: list[tuple[str, TradeProposal]] = []
    for d, group in sorted(direction_votes.items(), key=lambda x: -len(x[1])):
        if len(group) >= 2:
            consensus_dir = d
            consensus_group = group
            break

    meta["consensus_direction"] = consensus_dir
    meta["votes"] = {d: len(g) for d, g in direction_votes.items()}

    if not consensus_dir or len(consensus_group) < 2:
        meta["consensus"] = False
        raise RuntimeError(
            f"No consensus: votes={meta['votes']}, "
            f"valid={len(valid)}/{len(models)}"
        )

    meta["consensus"] = True
    meta["agreeing_models"] = [m.split("/")[-1] for m, _ in consensus_group]

    # Build consensus proposal from agreeing models.
    proposals = [p for _, p in consensus_group]

    # Average confidence.
    avg_confidence = sum(p.confidence for p in proposals) / len(proposals)

    # Most conservative stop-loss (widest from entry).
    entry = proposals[0].entry_price
    if consensus_dir == "long":
        # For longs, widest stop = lowest SL
        best_sl = min(p.stop_loss for p in proposals if p.stop_loss > 0) if any(p.stop_loss > 0 for p in proposals) else proposals[0].stop_loss
        # Closest TP = lowest TP (most conservative)
        best_tp1 = min(p.take_profit_1 for p in proposals if p.take_profit_1 > 0) if any(p.take_profit_1 > 0 for p in proposals) else proposals[0].take_profit_1
        tp2_candidates = [p.take_profit_2 for p in proposals if p.take_profit_2 and p.take_profit_2 > 0]
        best_tp2 = min(tp2_candidates) if tp2_candidates else best_tp1 * 1.5 if best_tp1 else 0
    else:
        # For shorts, widest stop = highest SL
        best_sl = max(p.stop_loss for p in proposals if p.stop_loss > 0) if any(p.stop_loss > 0 for p in proposals) else proposals[0].stop_loss
        # Closest TP = highest TP (most conservative)
        best_tp1 = max(p.take_profit_1 for p in proposals if p.take_profit_1 > 0) if any(p.take_profit_1 > 0 for p in proposals) else proposals[0].take_profit_1
        tp2_candidates = [p.take_profit_2 for p in proposals if p.take_profit_2 and p.take_profit_2 > 0]
        best_tp2 = max(tp2_candidates) if tp2_candidates else best_tp1

    # Use the first agreeing proposal as the base and override key fields.
    base = proposals[0]
    risk_dist = abs(entry - best_sl)
    reward_dist = abs(best_tp1 - entry)
    rr = round(reward_dist / risk_dist, 2) if risk_dist > 0 else 0.0

    # Merge rationales.
    rationale_parts = [f"[{m.split('/')[-1]}] {p.rationale}" for m, p in consensus_group if p.rationale]
    merged_rationale = " | ".join(rationale_parts[:3])

    consensus_proposal = TradeProposal(
        instrument=instrument,
        direction=base.direction,
        strategy=base.strategy,
        entry_type=base.entry_type,
        entry_price=round(entry, 6),
        stop_loss=round(best_sl, 6),
        take_profit_1=round(best_tp1, 6),
        take_profit_2=round(best_tp2, 6) if best_tp2 else None,
        confidence=round(avg_confidence, 1),
        risk_reward=rr,
        rationale=merged_rationale[:500],
        invalidation_condition=base.invalidation_condition,
        management_plan=base.management_plan,
        risk_flags=["openrouter_consensus"],
    )

    return consensus_proposal, meta
