"""Local-model (Ollama) trade proposer used for SHADOW comparison only.

This calls a self-hosted Ollama model with the *exact same* system prompt,
schema and market payload that Claude receives, so the two can be compared on
identical inputs. The result of this module never places a live trade — it is
recorded alongside Claude's live decision purely to measure whether the free
local model is good enough to eventually replace the paid API.

Kept deliberately dependency-free (stdlib urllib) so it can't affect the live
trading path; any error is swallowed by the caller.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from app.ai.engine import SCHEMA_HINT, SYSTEM_PROMPT
from app.ai.schema import TradeProposal
from app.config import settings


def _post_chat(base_url: str, model: str, payload: dict[str, Any], timeout: float) -> str:
    """Call Ollama's /api/chat with JSON-forced output and return the content."""
    user_content = (
        "/nothink\n"  # disable qwen3 thinking mode for speed
        "Schema (return exactly this shape as JSON):\n"
        + json.dumps(SCHEMA_HINT, indent=2)
        + "\n\nPrepared market data:\n"
        + json.dumps(payload, indent=2, default=str)
        + "\n\nReturn only the JSON object."
    )
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            "format": "json",  # force a single valid JSON object
            "options": {"temperature": 0.2, "num_ctx": 8192},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted host)
        data = json.loads(resp.read().decode())
    return data["message"]["content"]


def propose_trade_ollama(
    payload: dict[str, Any],
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
) -> tuple[TradeProposal, int]:
    """Return (proposal, latency_ms) from the local model for the given payload.

    Raises on any failure; the caller is responsible for catching so the live
    scan is never affected by the shadow model.
    """
    model = model or settings.ollama_model
    base_url = base_url or settings.ollama_base_url
    timeout = timeout or settings.ollama_timeout_seconds

    t0 = time.time()
    text = _post_chat(base_url, model, payload, timeout)
    latency_ms = int((time.time() - t0) * 1000)

    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)
    # Echo the correct instrument in case the model copied the schema example.
    data["instrument"] = payload.get("instrument", data.get("instrument", ""))
    _coerce(data)
    proposal = TradeProposal.model_validate(data)
    return proposal, latency_ms


# Map common phrasings a local model emits onto our strict enums so an
# otherwise-fine decision isn't discarded over a synonym. The agreement metric
# is direction-based, so getting direction right matters most.
_DIR_SYNONYMS = {
    "buy": "long", "long": "long", "bullish": "long",
    "sell": "short", "short": "short", "bearish": "short",
    "hold": "no_trade", "none": "no_trade", "flat": "no_trade",
    "neutral": "no_trade", "wait": "no_trade", "no trade": "no_trade",
    "no_trade": "no_trade", "skip": "no_trade", "": "no_trade",
}
_VALID_STRATEGIES = {
    "trend_pullback", "breakout_retest", "breakdown_retest",
    "range_reversal", "momentum_continuation", "no_trade",
}
_NUMERIC_FIELDS = (
    "entry_price", "stop_loss", "take_profit_1", "take_profit_2",
    "confidence", "risk_reward", "position_size",
)


def _coerce(data: dict[str, Any]) -> None:
    """Best-effort normalisation of a local model's JSON to our schema."""
    d = str(data.get("direction", "")).strip().lower()
    direction = _DIR_SYNONYMS.get(d, d)
    data["direction"] = direction

    strat = str(data.get("strategy", "")).strip().lower().replace(" ", "_")
    if strat not in _VALID_STRATEGIES:
        strat = "no_trade" if direction == "no_trade" else "trend_pullback"
    data["strategy"] = strat

    et = str(data.get("entry_type", "market")).strip().lower()
    data["entry_type"] = et if et in ("market", "limit", "stop") else "market"

    for f in _NUMERIC_FIELDS:
        v = data.get(f)
        if isinstance(v, str):
            try:
                v = float(v.replace(",", "").replace("%", "").strip())
            except ValueError:
                v = 0.0
            data[f] = v
        elif v is None:
            data[f] = 0.0
    # Normalise confidence to 0–100: some models return 0–1 (e.g. 0.7 == 70).
    conf = data.get("confidence")
    if isinstance(conf, (int, float)) and 0 < conf <= 1:
        data["confidence"] = conf * 100


def ollama_health(base_url: str | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """Lightweight reachability/version check for the dashboard."""
    base_url = base_url or settings.ollama_base_url
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/api/version", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
        return {"reachable": True, "version": data.get("version")}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "error": str(exc)[:200]}
