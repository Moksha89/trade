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
    def __init__(self, bid=100.0, ask=100.0):
        self._bid, self._ask = bid, ask

    def get_candles(self, *a, **k):
        return [object()] * 5  # unused: compute_indicators is patched

    def get_quote(self, instrument):
        return types.SimpleNamespace(bid=self._bid, ask=self._ask)

    def risk_unit_multiplier(self, instrument):
        return FX


def _exec(calls, cleared=None):
    class _E:
        mode = "live"

        def modify(self, deal_id, sl, tp):
            calls.append((sl, tp))
            return types.SimpleNamespace(ok=True, error=None)

        def clear_take_profit(self, deal_id):
            if cleared is not None:
                cleared.append(deal_id)
            return types.SimpleNamespace(ok=True, error=None)

    return _E()


def _risk(trailing_tp=True):
    r = default_risk()
    r["manual_trailing_tp"] = trailing_tp
    return r


def test_protect_attaches_stop_only_trailing_default(monkeypatch):
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
    calls: list = []

    # Trailing default: a protective stop is attached (97.0) but NO fixed TP, so
    # the winner can ride. The ATR trail is armed via trail_start_R.
    engine._protect_and_grade_manual(db, _Prov(100.0, 100.0), _exec(calls), t, _risk(True), live=True)
    db.refresh(t)

    assert (97.0, None) in calls
    assert all(tp is None for _, tp in calls)  # no take-profit ever sent
    assert t.stop_loss == 97.0 and (t.take_profit_1 or 0) == 0
    assert t.management_plan["manual_protected"] is True
    assert t.management_plan["trail_start_R"] == 1.0
    assert t.management_plan["grade"] == "good"
    _clear(db)


def test_protect_attaches_sltp_when_missing_legacy(monkeypatch):
    db = SessionLocal()
    _clear(db)
    # Legacy fixed-TP mode (manual_trailing_tp=False): SL + TP both attached.
    t = Trade(
        idea_id=None, mode="live", instrument="US100", direction="long",
        strategy="manual", entry_price=100.0, size=0.05, stop_loss=100.0,
        take_profit_1=0.0, current_price=100.0, status="open",
        management_plan={}, deal_id="DZ",
    )
    db.add(t)
    db.commit()
    _patch_analysis(monkeypatch, 2.0, _sig(True, False, True, False), {"1H": "up", "4H": "up"})
    calls: list = []

    engine._protect_and_grade_manual(db, _Prov(100.0, 100.0), _exec(calls), t, _risk(False), live=True)
    db.refresh(t)

    # SL and TP are sent as separate calls.
    assert (97.0, None) in calls and (None, 106.0) in calls
    assert t.stop_loss == 97.0 and t.take_profit_1 == 106.0
    assert t.management_plan["manual_protected"] is True
    _clear(db)


def test_protect_keeps_existing_user_stop_legacy(monkeypatch):
    db = SessionLocal()
    _clear(db)
    # Legacy fixed-TP mode: user already set a stop (95) but no TP — only the TP
    # should be added, using the user's stop distance (5.0) for the RR.
    t = Trade(
        idea_id=None, mode="live", instrument="US100", direction="long",
        strategy="manual", entry_price=100.0, size=0.05, stop_loss=95.0,
        take_profit_1=0.0, current_price=100.0, status="open",
        management_plan={}, deal_id="DY",
    )
    db.add(t)
    db.commit()
    _patch_analysis(monkeypatch, 2.0, _sig(True, False, True, False), {"1H": "up", "4H": "up"})
    calls: list = []

    engine._protect_and_grade_manual(db, _Prov(100.0, 100.0), _exec(calls), t, _risk(False), live=True)
    db.refresh(t)

    # Only a TP call (entry + 5.0 * RR 2.0 = 110); the user's stop is untouched.
    assert calls == [(None, 110.0)]
    assert t.stop_loss == 95.0 and t.take_profit_1 == 110.0
    _clear(db)


def test_trailing_clears_preexisting_broker_tp(monkeypatch):
    db = SessionLocal()
    _clear(db)
    # A manual trade that already carries a fixed broker TP (set before trailing
    # was enabled) must have that TP removed so the trailing stop is the exit.
    t = Trade(
        idea_id=None, mode="live", instrument="GOLD", direction="long",
        strategy="manual", entry_price=100.0, size=0.05, stop_loss=97.0,
        take_profit_1=106.0, current_price=101.0, status="open",
        management_plan={"manual_protected": True}, deal_id="DT",
    )
    db.add(t)
    db.commit()
    _patch_analysis(monkeypatch, 2.0, _sig(False, False, True, False), {"1H": "up", "4H": "up"})
    calls: list = []
    cleared: list = []

    engine._protect_and_grade_manual(
        db, _Prov(101.0, 101.0), _exec(calls, cleared), t, _risk(True), live=True
    )
    db.refresh(t)

    assert cleared == ["DT"]
    assert (t.take_profit_1 or 0) == 0
    assert t.management_plan["manual_tp_cleared"] is True
    _clear(db)


def test_enforce_risk_cap_tightens_wide_user_stop(monkeypatch):
    db = SessionLocal()
    _clear(db)
    # User opened a short and placed their OWN wide stop (entry 100, stop 130 =>
    # distance 30, size 1, FX 3.6725 => risk ~110 AED, over the 50 cap). The bot
    # must tighten that stop to the cap distance (~13.6 => risk ~50).
    t = Trade(
        idea_id=None, mode="live", instrument="US100", direction="short",
        strategy="manual", entry_price=100.0, size=1.0, stop_loss=130.0,
        take_profit_1=0.0, current_price=100.0, status="open",
        management_plan={"manual_protected": True, "manual_tp_cleared": True},
        deal_id="DC",
    )
    db.add(t)
    db.commit()
    _patch_analysis(monkeypatch, 2.0, _sig(False, True, False, True), {"1H": "down", "4H": "down"})
    calls: list = []

    engine._protect_and_grade_manual(db, _Prov(100.0, 100.0), _exec(calls), t, _risk(True), live=True)
    db.refresh(t)

    assert 100.0 < t.stop_loss < 130.0  # tightened toward entry, still above (short)
    assert t.initial_risk_aed <= 50.5   # risk now within the cap
    assert any(sl is not None for sl, _ in calls)
    _clear(db)


def test_enforce_risk_cap_leaves_profit_locked_stop(monkeypatch):
    db = SessionLocal()
    _clear(db)
    # Short whose stop has already trailed BELOW entry (into profit). The cap
    # enforcer must NOT touch it (that stop locks profit, risk is negative).
    t = Trade(
        idea_id=None, mode="live", instrument="US100", direction="short",
        strategy="manual", entry_price=100.0, size=1.0, stop_loss=95.0,
        take_profit_1=0.0, current_price=92.0, status="open",
        management_plan={"manual_protected": True, "manual_tp_cleared": True},
        deal_id="DP",
    )
    db.add(t)
    db.commit()
    _patch_analysis(monkeypatch, 2.0, _sig(False, True, False, True), {"1H": "down", "4H": "down"})
    calls: list = []

    engine._protect_and_grade_manual(db, _Prov(92.0, 92.0), _exec(calls), t, _risk(True), live=True)
    db.refresh(t)

    assert t.stop_loss == 95.0  # untouched — profit-side stop owned by the trail
    assert calls == []
    _clear(db)


def test_bot_trade_trailing_clears_tp_and_arms_trail(monkeypatch):
    db = SessionLocal()
    _clear(db)
    # A bot trade is born with a fixed broker TP. With bot_trailing_tp on, the
    # TP is cleared once and the ATR trail armed so the winner can ride.
    t = Trade(
        idea_id=None, mode="live", instrument="US100", direction="short",
        strategy="momentum_continuation", entry_price=100.0, size=0.05,
        stop_loss=101.0, take_profit_1=96.0, take_profit_2=94.0,
        current_price=100.0, status="open", management_plan={}, deal_id="DB",
    )
    db.add(t)
    db.commit()
    calls: list = []
    cleared: list = []

    engine._apply_bot_trailing_and_cap(db, _Prov(100.0, 100.0), _exec(calls, cleared), t, default_risk())
    db.commit()
    db.refresh(t)

    assert cleared == ["DB"]
    assert (t.take_profit_1 or 0) == 0 and (t.take_profit_2 or 0) == 0
    assert t.management_plan["bot_tp_cleared"] is True
    assert t.management_plan["trail_start_R"] == 1.0
    _clear(db)


def test_bot_trade_risk_cap_tightens_wide_stop(monkeypatch):
    db = SessionLocal()
    _clear(db)
    # The hard risk cap applies to bot trades too: a stop risking >50 AED is
    # tightened to the cap distance.
    t = Trade(
        idea_id=None, mode="live", instrument="US100", direction="short",
        strategy="momentum_continuation", entry_price=100.0, size=1.0,
        stop_loss=130.0, take_profit_1=0.0, current_price=100.0, status="open",
        management_plan={"bot_tp_cleared": True}, deal_id="DBC",
    )
    db.add(t)
    db.commit()
    _patch_analysis(monkeypatch, 2.0, _sig(False, True, False, True), {"1H": "down", "4H": "down"})
    calls: list = []

    engine._apply_bot_trailing_and_cap(db, _Prov(100.0, 100.0), _exec(calls), t, default_risk())
    db.commit()
    db.refresh(t)

    assert 100.0 < t.stop_loss < 130.0
    assert t.initial_risk_aed <= 50.5
    _clear(db)


def test_protect_clamps_tp_to_valid_side_of_price(monkeypatch):
    db = SessionLocal()
    _clear(db)
    # Short already in profit: entry 4105.2, no stop/TP, price has fallen to
    # 4087 (past the entry-based 2R target). The TP must be clamped BELOW the
    # live price (minus buffer), not left above it where the broker rejects it.
    t = Trade(
        idea_id=None, mode="live", instrument="GOLD", direction="short",
        strategy="manual", entry_price=4105.2, size=0.05, stop_loss=4105.2,
        take_profit_1=0.0, current_price=4087.0, status="open",
        management_plan={}, deal_id="DG",
    )
    db.add(t)
    db.commit()
    _patch_analysis(monkeypatch, 4.5, _sig(True, False, False, True), {"1H": "down", "4H": "down"})
    calls: list = []

    engine._protect_and_grade_manual(db, _Prov(4087.0, 4087.0), _exec(calls), t, _risk(False), live=True)
    db.refresh(t)

    # Stop sits above the live ask; target sits below the live bid (buffer applied).
    assert t.stop_loss > 4087.0
    assert t.take_profit_1 < 4087.0
    assert t.management_plan["manual_protected"] is True
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

    engine._protect_and_grade_manual(db, _Prov(100.0, 100.0), _exec(calls), t, default_risk(), live=True)
    first = len(calls)
    engine._protect_and_grade_manual(db, _Prov(100.0, 100.0), _exec(calls), t, default_risk(), live=True)
    assert first >= 1 and len(calls) == first  # second pass skips modify entirely
    _clear(db)
