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
            "options": {"temperature": 0.2},
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
    # Normalise confidence to a 0–100 scale: some local models return 0–1
    # (e.g. 0.7) where Claude returns 70, which would skew the comparison.
    conf = data.get("confidence")
    if isinstance(conf, (int, float)) and 0 < conf <= 1:
        data["confidence"] = conf * 100
    proposal = TradeProposal.model_validate(data)
    return proposal, latency_ms


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
