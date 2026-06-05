"""ORM -> dict serializers for API responses."""

from __future__ import annotations

from typing import Any

from app.models import AuditLog, JournalSnapshot, Trade, TradeIdea


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt else None


def trade_to_dict(t: Trade) -> dict[str, Any]:
    r_multiple = 0.0
    if t.initial_risk_per_unit:
        move = (
            (t.current_price - t.entry_price)
            if t.direction == "long"
            else (t.entry_price - t.current_price)
        )
        r_multiple = round(move / t.initial_risk_per_unit, 2)
    return {
        "id": t.id,
        "idea_id": t.idea_id,
        "mode": t.mode,
        "instrument": t.instrument,
        "direction": t.direction,
        "strategy": t.strategy,
        "entry_price": t.entry_price,
        "current_price": t.current_price,
        "size": t.size,
        "stop_loss": t.stop_loss,
        "take_profit_1": t.take_profit_1,
        "take_profit_2": t.take_profit_2,
        "r_multiple": r_multiple,
        "initial_risk_aed": t.initial_risk_aed,
        "unrealized_pl": t.unrealized_pl,
        "realized_pl": t.realized_pl,
        "status": t.status,
        "breakeven_moved": t.breakeven_moved,
        "profit_locked": t.profit_locked,
        "partial_closed": t.partial_closed,
        "deal_id": t.deal_id,
        "opened_at": _iso(t.opened_at),
        "closed_at": _iso(t.closed_at),
        "last_sltp_update": _iso(t.last_sltp_update),
        "close_reason": t.close_reason,
    }


def idea_to_dict(i: TradeIdea) -> dict[str, Any]:
    return {
        "id": i.id,
        "created_at": _iso(i.created_at),
        "instrument": i.instrument,
        "direction": i.direction,
        "strategy": i.strategy,
        "entry_type": i.entry_type,
        "entry_price": i.entry_price,
        "stop_loss": i.stop_loss,
        "take_profit_1": i.take_profit_1,
        "take_profit_2": i.take_profit_2,
        "confidence": i.confidence,
        "risk_reward": i.risk_reward,
        "position_size": i.position_size,
        "risk_aed": i.risk_aed,
        "rationale": i.rationale,
        "invalidation_condition": i.invalidation_condition,
        "risk_flags": i.risk_flags,
        "management_plan": i.management_plan,
        "market_classification": i.market_classification,
        "risk_approved": i.risk_approved,
        "risk_reason": i.risk_reason,
        "status": i.status,
    }


def audit_to_dict(a: AuditLog) -> dict[str, Any]:
    return {
        "id": a.id,
        "created_at": _iso(a.created_at),
        "event": a.event,
        "instrument": a.instrument,
        "detail": a.detail,
    }


def journal_to_dict(j: JournalSnapshot) -> dict[str, Any]:
    return {"id": j.id, "created_at": _iso(j.created_at), "snapshot": j.snapshot}
