"""Executor interface + result type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ExecutionResult:
    ok: bool
    fill_price: float = 0.0
    deal_reference: str | None = None
    deal_id: str | None = None
    error: str | None = None


class Executor(Protocol):
    mode: str

    def open(
        self,
        instrument: str,
        direction: str,
        size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> ExecutionResult:
        ...

    def modify(
        self, deal_id: str | None, stop_loss: float | None, take_profit: float | None
    ) -> ExecutionResult:
        ...

    def clear_take_profit(self, deal_id: str | None) -> ExecutionResult:
        ...

    def close(self, deal_id: str | None, price: float) -> ExecutionResult:
        ...
