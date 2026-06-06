"""Execution-time slippage guard.

A market order can fill away from the price we sized against. Risk must be
re-derived from the ACTUAL fill; if the slip pushes per-trade risk well past
the cap, the position is aborted (closed) instead of riding oversized.
"""

from app.db import SessionLocal, init_db
from app.execution.base import ExecutionResult
from app.models import Trade, TradeIdea
from app.services import engine
from app.services.settings_store import (
    RISK,
    STRATEGY,
    default_risk,
    default_strategy,
    update_group,
)

init_db()

FX = 3.6725  # AED per 1-point move (size 1)


class _Provider:
    def get_quote(self, instrument):
        # We size the long against ask = 100 → risk/unit 1.0.
        return type("Q", (), {"ask": 100.0, "bid": 100.0, "mid": 100.0, "spread_points": 0.0})()

    def risk_unit_multiplier(self, instrument):
        return FX


class _Exec:
    mode = "live"

    def __init__(self, fill_price):
        self._fill = fill_price
        self.closed = []

    def open(self, **kw):
        return ExecutionResult(ok=True, fill_price=self._fill, deal_reference="r1", deal_id="d1")

    def modify(self, *a, **k):
        return ExecutionResult(ok=True)

    def close(self, deal_id, price):
        self.closed.append((deal_id, price))
        return ExecutionResult(ok=True, fill_price=price, deal_id=deal_id)


def _approved_idea(db):
    idea = TradeIdea(
        instrument="ADAUSD",
        direction="long",
        strategy="trend_pullback",
        entry_type="market",
        entry_price=100.0,
        stop_loss=99.0,
        take_profit_1=102.0,
        confidence=80.0,
        risk_reward=2.0,
        risk_approved=True,
        status="approved",
    )
    db.add(idea)
    db.commit()
    db.refresh(idea)
    return idea


def _setup(db):
    risk = default_risk()
    risk["allowed_instruments"] = ["ADAUSD"]
    update_group(db, RISK, risk)
    update_group(db, STRATEGY, default_strategy())


def test_excessive_slip_aborts_position(monkeypatch):
    db = SessionLocal()
    try:
        _setup(db)
        idea = _approved_idea(db)
        ex = _Exec(fill_price=103.0)  # long filled 3 above → risk/unit 4 → ~200 AED >> cap
        monkeypatch.setattr(engine, "get_provider", lambda: _Provider())
        monkeypatch.setattr(engine, "get_executor", lambda: ex)
        out = engine.execute_idea(db, idea)
        assert out is None
        db.refresh(idea)
        assert idea.status == "rejected" and "aborted" in idea.risk_reason
        # Position was closed to honor the cap, and no open trade remains.
        assert ex.closed and ex.closed[0][0] == "d1"
        assert db.query(Trade).filter(Trade.status == "open").count() == 0
    finally:
        db.query(Trade).delete()
        db.query(TradeIdea).delete()
        db.commit()
        db.close()


def test_small_slip_accepted_records_actual_risk(monkeypatch):
    db = SessionLocal()
    try:
        _setup(db)
        idea = _approved_idea(db)
        ex = _Exec(fill_price=100.2)  # risk/unit 1.2 → ~60 AED ≤ cap*1.3
        monkeypatch.setattr(engine, "get_provider", lambda: _Provider())
        monkeypatch.setattr(engine, "get_executor", lambda: ex)
        out = engine.execute_idea(db, idea)
        assert out is not None
        assert out.entry_price == 100.2
        # initial_risk_aed reflects the ACTUAL fill, not the pre-fill estimate.
        assert abs(out.initial_risk_aed - out.size * 1.2 * FX) < 0.01
        assert not ex.closed
    finally:
        db.query(Trade).delete()
        db.query(TradeIdea).delete()
        db.commit()
        db.close()
