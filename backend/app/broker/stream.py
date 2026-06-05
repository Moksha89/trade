"""Capital.com WebSocket live-price client.

Thin async wrapper over the streaming endpoint. The 24/7 loop primarily uses
REST snapshots; this is provided for live tick consumption (e.g. tightening
trailing stops between REST polls) and is wired in once a demo session exists.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import websockets

from app.broker.capital import CapitalClient
from app.config import settings
from app.market_data.base import EPIC_MAP


class CapitalStream:
    def __init__(self, client: CapitalClient) -> None:
        self.client = client
        self.url = settings.capital_ws_url

    async def subscribe_quotes(self, instruments: list[str]) -> AsyncIterator[dict]:
        self.client.ensure_session()
        tokens = self.client.session_tokens()
        epics = [EPIC_MAP.get(i, i) for i in instruments]
        async with websockets.connect(self.url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "destination": "marketData.subscribe",
                        "correlationId": "1",
                        "cst": tokens["cst"],
                        "securityToken": tokens["security_token"],
                        "payload": {"epics": epics},
                    }
                )
            )
            while True:
                raw = await ws.recv()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("destination") == "quote":
                    yield msg.get("payload", {})
                await asyncio.sleep(0)
