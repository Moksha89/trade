"""Manual (broker-app) trades: the bot must attach a protective SL/TP when one
is missing and grade the setup good/caution/risky — advisory only, never closing
the position."""

import types

from app.db import SessionLocal, init_db
from app.models import Trade
from app.services import engine
from app.services.settings_store import default_risk

init_db()

FX = 3.6725


def _clear(db):
    for t in db.query(Trade).all():
        db.delete(t)
    db.commit()


def _sig(at_support, at_resistance, bull, bear):
    return {
        "at_support": at_support, "at_resistance": at_resistance,
        "bullish_confirmation": bull, "bearish_confirmation": bear,
    }


# ---- grading (pure) -------------------------------------------------------
def test_grade_good_short_with_trend_at_resistance():
    g = engine._grade_setup(
        "short", _sig(False, True, False, True), {"1H": "down", "4H": "down"}
    )
    assert g["verdict"] == "good" and g["reasons"] == []


def test_grade_risky_short_into_support():
    g = engine._grade_setup(
        "short", _sig(True, False, False, True), {"1H": "down", "4H": "down"}
    )
    assert g["verdict"] == "risky"
    assert any("support" in r for r in g["reasons"])


def test_grade_risky_counter_trend_long():
    g = engine._grade_setup(
        "long", _sig(True, False, True, False), {"1H": "down", "4H": "down"}
    )
    assert g["verdict"] == "risky"
    assert any("counter-trend" in r for r in g["reasons"])


def test_grade_caution_when_only_soft_issues():
    # With-trend long, but not at support and sideways higher timeframes => a
    # non-serious 'caution', not 'risky'.
    g = engine._grade_setup(
        "long", _sig(False, False, True, False), {"1H": "sideways", "4H": "sideways"}
    )
    assert g["verdict"] == "caution"


# ---- protection + grade wiring -------------------------------------------
def _patch_analysis(monkeypatch, atr, signals, trends):
    monkeypatch.setattr(engine, "compute_indicators", lambda c: types.SimpleNamespace(atr=atr))
    monkeypatch.setattr(engine, "_entry_signals", lambda *a, **k: signals)
    monkeypatch.setattr(engine, "_higher_timeframe_trends", lambda *a, **k: trends)


class _Prov:
    def get_candles(self, *a, **k):
        return [object()] * 5  # unused: compute_indicators is patched

    def risk_unit_multiplier(self, instrument):
        return FX


def _exec(captured):
    class _E:
        mode = "live"

        def modify(self, deal_id, sl, tp):
            captured["sl"], captured["tp"] = sl, tp
            return types.SimpleNamespace(ok=True, error=None)

    return _E()


def test_protect_attaches_sltp_when_missing(monkeypatch):
    db = SessionLocal()
    _clear(db)
    # Adopted manual long with NO stop (adoption stores stop==entry) and no TP.
    t = Trade(
        idea_id=None, mode="live", instrument="US100", direction="long",
        strategy="manual", entry_price=100.0, size=0.05, stop_loss=100.0,
        take_profit_1=0.0, current_price=100.0, status="open",
        management_plan={}, deal_id="DZ",
    )
    db.add(t)
    db.commit()
    _patch_analysis(monkeypatch, 2.0, _sig(True, False, True, False), {"1H": "up", "4H": "up"})
    captured: dict = {}

    engine._protect_and_grade_manual(db, _Prov(), _exec(captured), t, default_risk(), live=True)
    db.refresh(t)

    # ATR-based stop = 2.0 * 1.5 = 3.0 below entry; TP at RR 2.0 = 6.0 above.
    assert captured["sl"] == 97.0 and captured["tp"] == 106.0
    assert t.stop_loss == 97.0 and t.take_profit_1 == 106.0
    assert t.management_plan["manual_protected"] is True
    assert t.management_plan["grade"] == "good"
    _clear(db)


def test_protect_keeps_existing_user_stop(monkeypatch):
    db = SessionLocal()
    _clear(db)
    # User already set a stop (95) but no TP — only the TP should be added,
    # using the user's stop distance (5.0) for the RR.
    t = Trade(
        idea_id=None, mode="live", instrument="US100", direction="long",
        strategy="manual", entry_price=100.0, size=0.05, stop_loss=95.0,
        take_profit_1=0.0, current_price=100.0, status="open",
        management_plan={}, deal_id="DY",
    )
    db.add(t)
    db.commit()
    _patch_analysis(monkeypatch, 2.0, _sig(True, False, True, False), {"1H": "up", "4H": "up"})
    captured: dict = {}

    engine._protect_and_grade_manual(db, _Prov(), _exec(captured), t, default_risk(), live=True)
    db.refresh(t)

    assert captured["sl"] is None  # user's stop untouched
    assert captured["tp"] == 110.0  # entry + 5.0 * RR(2.0)
    assert t.stop_loss == 95.0 and t.take_profit_1 == 110.0
    _clear(db)


def test_protect_only_runs_once(monkeypatch):
    db = SessionLocal()
    _clear(db)
    t = Trade(
        idea_id=None, mode="live", instrument="US100", direction="short",
        strategy="manual", entry_price=100.0, size=0.05, stop_loss=100.0,
        take_profit_1=0.0, current_price=100.0, status="open",
        management_plan={}, deal_id="DX",
    )
    db.add(t)
    db.commit()
    _patch_analysis(monkeypatch, 2.0, _sig(False, True, False, True), {"1H": "down", "4H": "down"})

    calls: list = []

    class _E:
        mode = "live"

        def modify(self, deal_id, sl, tp):
            calls.append((sl, tp))
            return types.SimpleNamespace(ok=True, error=None)

    engine._protect_and_grade_manual(db, _Prov(), _E(), t, default_risk(), live=True)
    engine._protect_and_grade_manual(db, _Prov(), _E(), t, default_risk(), live=True)
    assert len(calls) == 1  # second pass sees manual_protected and skips modify
    _clear(db)
