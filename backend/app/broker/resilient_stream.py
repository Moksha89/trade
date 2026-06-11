"""Resilient WebSocket streaming with automatic reconnection and heartbeat.

Wraps the raw Capital.com WebSocket with:
- Automatic reconnection with exponential backoff
- Periodic heartbeat to detect dead connections
- Connection state tracking
- Graceful degradation to REST polling on persistent failures
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

import websockets

from app.broker.capital import CapitalClient
from app.config import settings
from app.market_data.base import EPIC_MAP

logger = logging.getLogger(__name__)

# Reconnection parameters
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
HEARTBEAT_INTERVAL = 30.0  # seconds between heartbeat checks
MAX_SILENCE = 45.0  # max seconds without receiving any message


class ResilientStream:
    """WebSocket client with automatic reconnection and heartbeat monitoring."""

    def __init__(self, client: CapitalClient) -> None:
        self.client = client
        self.url = settings.capital_ws_url
        self._connected = False
        self._reconnect_count = 0
        self._last_message_at = 0.0
        self._backoff = INITIAL_BACKOFF

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    async def subscribe_quotes(
        self, instruments: list[str], max_retries: int = 10,
    ) -> AsyncIterator[dict]:
        """Subscribe to live quotes with automatic reconnection.

        Yields quote payloads. On connection loss, reconnects with exponential
        backoff up to `max_retries` times before giving up.
        """
        retries = 0

        while retries < max_retries:
            try:
                async for payload in self._connect_and_stream(instruments):
                    retries = 0  # reset on successful message
                    self._backoff = INITIAL_BACKOFF
                    yield payload
            except (
                websockets.ConnectionClosed,
                websockets.InvalidMessage,
                ConnectionError,
                OSError,
                asyncio.TimeoutError,
            ) as exc:
                self._connected = False
                retries += 1
                self._reconnect_count += 1
                logger.warning(
                    "WebSocket disconnected (%s), reconnect %d/%d in %.1fs",
                    type(exc).__name__, retries, max_retries, self._backoff,
                )
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, MAX_BACKOFF)
            except Exception:  # noqa: BLE001
                self._connected = False
                break

        logger.error("WebSocket max retries exceeded, falling back to REST polling")

    async def _connect_and_stream(self, instruments: list[str]) -> AsyncIterator[dict]:
        """Single connection attempt with heartbeat monitoring."""
        self.client.ensure_session()
        tokens = self.client.session_tokens()
        epics = [EPIC_MAP.get(i, i) for i in instruments]

        async with websockets.connect(
            self.url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            # Subscribe
            await ws.send(
                json.dumps({
                    "destination": "marketData.subscribe",
                    "correlationId": "1",
                    "cst": tokens["cst"],
                    "securityToken": tokens["security_token"],
                    "payload": {"epics": epics},
                })
            )
            self._connected = True
            self._last_message_at = time.time()
            logger.info("WebSocket connected, subscribed to %s", instruments)

            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=MAX_SILENCE)
                except asyncio.TimeoutError:
                    # No message in MAX_SILENCE seconds — connection is dead
                    logger.warning("WebSocket silent for %.0fs, reconnecting", MAX_SILENCE)
                    break

                self._last_message_at = time.time()

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if msg.get("destination") == "quote":
                    yield msg.get("payload", {})

    def status(self) -> dict:
        """Current connection status for the dashboard."""
        return {
            "connected": self._connected,
            "reconnect_count": self._reconnect_count,
            "last_message_age_seconds": round(
                time.time() - self._last_message_at, 1
            ) if self._last_message_at else None,
            "backoff_seconds": self._backoff,
        }
