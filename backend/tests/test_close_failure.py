"""When the broker rejects a close, the trade must stay open (not be marked
closed in the DB). Marking it closed would leave a live position unmanaged and
double-count its P/L when the reconcile loop later re-adopts it."""

from app.db import SessionLocal, init_db
from app.execution.base import ExecutionResult
from app.models import Trade
from app.services import engine

init_db()


class _Exec:
    mode = "live"

    def __init__(self, ok: bool):
        self._ok = ok

    def close(self, deal_id, price):
        if self._ok:
            return ExecutionResult(ok=True, fill_price=price, deal_id=deal_id)
        return ExecutionResult(ok=False, error="error.position.closeerror", deal_id=deal_id)


def _make_trade(db):
    t = Trade(
        mode="live", instrument="GOLD", direction="short", entry_price=4105.2,
        size=2.0, stop_loss=4120.0, take_profit_1=4080.0, initial_risk_aed=50.0,
        initial_risk_per_unit=14.8, unrealized_pl=10.0, realized_pl=0.0,
        deal_id="deal-1", status="open",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_close_failure_keeps_trade_open(monkeypatch):
    db = SessionLocal()
    try:
        t = _make_trade(db)
        monkeypatch.setattr(engine, "get_executor", lambda: _Exec(ok=False))
        ok = engine.close_trade(db, t, 4090.0, "stop_loss")
        db.refresh(t)
        # Broker rejected → trade NOT closed, no P/L booked, caller told it failed.
        assert ok is False
        assert t.status == "open"
        assert t.close_reason is None
        assert t.realized_pl == 0.0
    finally:
        db.query(Trade).delete()
        db.commit()
        db.close()


def test_close_success_books_and_closes(monkeypatch):
    db = SessionLocal()
    try:
        t = _make_trade(db)
        monkeypatch.setattr(engine, "get_executor", lambda: _Exec(ok=True))
        ok = engine.close_trade(db, t, 4090.0, "stop_loss")
        db.refresh(t)
        assert ok is True
        assert t.status == "closed"
        assert t.close_reason == "stop_loss"
        # short: (entry 4105.2 - fill 4090.0) * size 2 * mult > 0
        assert t.realized_pl > 0
    finally:
        db.query(Trade).delete()
        db.commit()
        db.close()
