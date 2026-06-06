"""Broker executor for Capital.com demo/live.

Enforces the spec's hard rule: every position must carry a server-side
stop-loss. If the deal confirmation comes back without an attached stop, the
position is closed immediately and the result is marked failed so the caller can
alert.
"""

from __future__ import annotations

from app.broker.capital import CapitalClient, CapitalError
from app.execution.base import ExecutionResult


class BrokerExecutor:
    def __init__(self, client: CapitalClient, mode: str = "demo") -> None:
        self.client = client
        self.mode = mode

    def open(
        self,
        instrument: str,
        direction: str,
        size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> ExecutionResult:
        broker_dir = "BUY" if direction == "long" else "SELL"
        try:
            res = self.client.open_position(
                instrument, broker_dir, size, stop_loss, take_profit
            )
            deal_ref = res.get("dealReference")
            confirm = self.client.confirm(deal_ref)
            status = confirm.get("dealStatus") or confirm.get("status")
            if status not in ("ACCEPTED", "OPEN"):
                reason = confirm.get("reason") or confirm.get("rejectReason") or "unknown"
                return ExecutionResult(
                    ok=False,
                    error=f"deal not accepted: {status} ({reason})",
                    deal_reference=deal_ref,
                )
            deal_id = confirm.get("affectedDeals", [{}])[0].get("dealId") or confirm.get(
                "dealId"
            )
            # Verify server-side stop is attached; otherwise abort and close.
            # /confirms does not always echo stopLevel, so fall back to reading
            # the live position before deciding there is no stop.
            level = confirm.get("level") or entry_price
            has_stop = confirm.get("stopLevel") is not None
            if not has_stop and deal_id:
                has_stop = self.client.position_has_stop(deal_id)
            if not has_stop:
                if deal_id:
                    try:
                        self.client.close_position(deal_id)
                    except CapitalError:
                        pass
                return ExecutionResult(
                    ok=False,
                    error="no server-side stop-loss attached; position closed",
                    deal_reference=deal_ref,
                    deal_id=deal_id,
                )
            return ExecutionResult(
                ok=True, fill_price=float(level), deal_reference=deal_ref, deal_id=deal_id
            )
        except CapitalError as exc:
            return ExecutionResult(ok=False, error=str(exc))

    def modify(
        self, deal_id: str | None, stop_loss: float | None, take_profit: float | None
    ) -> ExecutionResult:
        if not deal_id:
            return ExecutionResult(ok=False, error="missing deal_id")
        try:
            self.client.modify_position(deal_id, stop_loss, take_profit)
            return ExecutionResult(ok=True, deal_id=deal_id)
        except CapitalError as exc:
            return ExecutionResult(ok=False, error=str(exc), deal_id=deal_id)

    def close(self, deal_id: str | None, price: float) -> ExecutionResult:
        if not deal_id:
            return ExecutionResult(ok=False, error="missing deal_id")
        try:
            self.client.close_position(deal_id)
            return ExecutionResult(ok=True, fill_price=price, deal_id=deal_id)
        except CapitalError as exc:
            return ExecutionResult(ok=False, error=str(exc), deal_id=deal_id)
