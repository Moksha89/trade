"""Live reconciliation: a position that vanished from the broker (its
server-side stop/target fired, or it was closed in the app) must be mirrored
into our DB so the slot frees up and P/L is booked."""

from app.db import SessionLocal, init_db
from app.models import Trade
from app.services import engine

init_db()


class _Client:
    def __init__(self, deals):
        self._deals = deals

    def get_positions(self):
        return [{"position": {"dealId": d}} for d in self._deals]


class _Exec:
    mode = "live"

    def __init__(self, deals):
        self.client = _Client(deals)

    def modify(self, *a, **k):  # pragma: no cover - not hit in this test
        from app.execution.base import ExecutionResult

        return ExecutionResult(ok=True)

    def close(self, *a, **k):  # pragma: no cover
        from app.execution.base import ExecutionResult

        return ExecutionResult(ok=True)


def _make_trade(db, deal_id):
    t = Trade(
        mode="live",
        instrument="BCHUSD",
        direction="long",
        entry_price=224.75,
        size=10.0,
        stop_loss=220.5,
        take_profit_1=227.64,
        initial_risk_aed=50.0,
        initial_risk_per_unit=4.25,
        unrealized_pl=-23.0,
        deal_id=deal_id,
        status="open",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_vanished_broker_position_is_reconciled_closed(monkeypatch):
    db = SessionLocal()
    try:
        t = _make_trade(db, "deal-gone-1")
        # Broker holds NO positions → our open trade must be closed.
        monkeypatch.setattr(engine, "get_executor", lambda: _Exec(deals=[]))
        engine.manage_open_trades(db)
        db.refresh(t)
        assert t.status == "closed"
        assert t.close_reason == "closed_on_broker"
        # Last marked uPL (-23) is booked as realized P/L in AED.
        assert t.realized_pl == -23.0
        assert t.unrealized_pl == 0.0
    finally:
        db.query(Trade).delete()
        db.commit()
        db.close()


def test_still_open_broker_position_is_not_reconciled(monkeypatch):
    db = SessionLocal()
    try:
        t = _make_trade(db, "deal-alive-1")
        monkeypatch.setattr(engine, "get_executor", lambda: _Exec(deals=["deal-alive-1"]))

        class _Q:
            bid = 223.0
            ask = 223.2
            mid = 223.1

        monkeypatch.setattr(
            engine, "get_provider", lambda: type("P", (), {"get_quote": lambda self, i: _Q()})()
        )
        engine.manage_open_trades(db)
        db.refresh(t)
        assert t.status == "open"
    finally:
        db.query(Trade).delete()
        db.commit()
        db.close()
