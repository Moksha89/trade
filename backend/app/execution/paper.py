"""Paper executor — simulates fills without touching any broker."""

from __future__ import annotations

import uuid

from app.execution.base import ExecutionResult


class PaperExecutor:
    mode = "paper"

    def open(
        self,
        instrument: str,
        direction: str,
        size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> ExecutionResult:
        ref = f"paper-{uuid.uuid4().hex[:12]}"
        return ExecutionResult(
            ok=True,
            fill_price=entry_price,
            deal_reference=ref,
            deal_id=ref,
        )

    def modify(
        self, deal_id: str | None, stop_loss: float | None, take_profit: float | None
    ) -> ExecutionResult:
        return ExecutionResult(ok=True, deal_id=deal_id)

    def clear_take_profit(self, deal_id: str | None) -> ExecutionResult:
        return ExecutionResult(ok=True, deal_id=deal_id)

    def close(self, deal_id: str | None, price: float) -> ExecutionResult:
        return ExecutionResult(ok=True, fill_price=price, deal_id=deal_id)
