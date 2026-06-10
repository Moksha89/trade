"""Performance memory: our realized track record fed back into the AI prompt."""

from datetime import datetime, timezone

from app.ai.engine import build_payload
from app.classifier.engine import MarketCondition
from app.db import SessionLocal, init_db
from app.indicators.engine import IndicatorSet
from app.models import Trade
from app.services.performance import performance_memory

init_db()

INSTR = "TESTGOLD"


def _trade(direction, pl, strategy="trend_pullback", risk=50.0):
    return Trade(
        instrument=INSTR,
        direction=direction,
        strategy=strategy,
        entry_price=100.0,
        size=1.0,
        stop_loss=99.0,
        initial_risk_aed=risk,
        realized_pl=pl,
        status="closed",
        closed_at=datetime.now(timezone.utc),
    )


def _seed(db):
    db.query(Trade).filter(Trade.instrument == INSTR).delete()
    db.add_all([
        _trade("long", 80.0),
        _trade("long", -50.0),
        _trade("short", -40.0),
        _trade("short", -45.0, strategy="range_reversal"),
    ])
    db.commit()


def test_memory_summarizes_instrument_and_splits():
    db = SessionLocal()
    try:
        _seed(db)
        mem = performance_memory(db, INSTR)
        # Instrument totals: 4 trades, 1 win → 25%, net = 80-50-40-45 = -55.
        assert mem["instrument"]["trades"] == 4
        assert mem["instrument"]["win_rate_pct"] == 25.0
        assert mem["instrument"]["net_aed"] == -55.0
        # Direction split: long 1/2 win, short 0/2.
        assert mem["by_direction"]["long"]["trades"] == 2
        assert mem["by_direction"]["long"]["win_rate_pct"] == 50.0
        assert mem["by_direction"]["short"]["win_rate_pct"] == 0.0
        # Strategy split present for the named strategies.
        assert "range_reversal" in mem["by_strategy"]
        assert "account_overall" in mem
    finally:
        db.close()


def test_memory_empty_for_unknown_instrument():
    db = SessionLocal()
    try:
        _seed(db)
        mem = performance_memory(db, INSTR)
        # Unknown instrument has no instrument-level record (but account exists).
        mem2 = performance_memory(db, "NONEXISTENT_XYZ")
        assert "instrument" not in mem2
    finally:
        db.close()


def _ind():
    return IndicatorSet(
        price=100.0, ema20=100.0, ema50=100.0, ema200=100.0, rsi=50.0,
        macd=0.0, macd_signal=0.0, macd_hist=0.1, atr=1.0, vwap=100.0,
        support=98.0, resistance=102.0, swing_high=103.0, swing_low=97.0,
        volatility_pct=1.0, trend="up",
    )


def test_build_payload_includes_memory_when_present():
    mem = {"instrument": {"trades": 4, "win_rate_pct": 25.0, "net_aed": -55.0, "avg_r": -0.3}}
    payload = build_payload(INSTR, _ind(), MarketCondition.BULLISH_TREND, {"performance_memory": mem})
    assert payload["performance_memory"] == mem


def test_build_payload_omits_memory_when_empty():
    payload = build_payload(INSTR, _ind(), MarketCondition.BULLISH_TREND, {"performance_memory": {}})
    assert "performance_memory" not in payload
