"""Select an executor for the active mode."""

from __future__ import annotations

from app.config import settings
from app.execution.base import Executor
from app.execution.paper import PaperExecutor


def get_executor(mode: str | None = None) -> Executor:
    mode = (mode or settings.execution_mode).lower()
    if mode == "paper":
        return PaperExecutor()
    from app.broker.capital import get_capital_client
    from app.execution.broker import BrokerExecutor

    client = get_capital_client()
    if not client.configured:
        # No broker credentials yet → safe paper simulation.
        return PaperExecutor()
    return BrokerExecutor(client, mode=mode)
