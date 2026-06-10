"""Trade manager rule tests (tuned trailing: BE +0.7R, lock +0.3R @ +1R)."""

from app.trade_manager.engine import compute_management_actions

PLAN = {
    "move_sl_to_breakeven_at_R": 0.7,
    "lock_profit_at_R": 1.0,
    "lock_profit_offset_R": 0.3,
    "partial_close_at_R": 2.0,
    "partial_close_percent": 50,
    "auto_close_on_reversal": True,
}


def base(**kw):
    args = dict(
        direction="long",
        entry=100.0,
        current_price=100.0,
        risk_per_unit=1.0,
        current_sl=99.0,
        plan=PLAN,
        breakeven_done=False,
        profit_locked=False,
        partial_done=False,
    )
    args.update(kw)
    return compute_management_actions(**args)


def test_breakeven_at_0_7r():
    a = base(current_price=100.7)
    assert a.new_stop_loss == 100.0
    assert "breakeven" in a.reasons[0]


def test_no_change_below_0_7r():
    a = base(current_price=100.5)  # +0.5R, below the 0.7R breakeven trigger
    assert not a.has_changes


def test_lock_0_3r_at_1r():
    # At +1R the lock (+0.3R) is tighter than breakeven, so it wins.
    a = base(current_price=101.0)
    assert a.new_stop_loss == 100.3
    assert "lock" in a.reasons[0]


def test_partial_close_at_2r():
    a = base(current_price=102.0)
    assert a.partial_close_percent == 50


def test_reversal_closes():
    a = base(current_price=100.5, reversal_signal=True)
    assert a.close is True


def test_invalidation_closes():
    a = base(invalidated=True)
    assert a.close is True


def test_short_lock_at_1r():
    a = base(direction="short", entry=100.0, current_price=99.0, current_sl=101.0)
    assert a.new_stop_loss == 99.7  # entry - 0.3R
    assert "lock" in a.reasons[0]


def test_trailing_engages_without_partial():
    # New behaviour: the ATR trail is applied as soon as a trail level is
    # supplied (caller starts it at TP1), no longer gated on a prior partial.
    a = base(current_price=103.0, partial_done=True, trail_level=101.5)
    assert a.new_stop_loss == 101.5
    assert any("trailing" in r for r in a.reasons)


def test_picks_tightest_favourable_stop():
    # lock (+0.3R = 100.3) and trail (102.0) both eligible far in profit;
    # the tighter (higher, for a long) trail must win.
    a = base(current_price=103.0, trail_level=102.0)
    assert a.new_stop_loss == 102.0


def test_never_loosens_existing_stop():
    # Stop already locked at 101.5; a looser trail (101.0) must be ignored
    # (the stop is never moved backwards, even though a partial may fire).
    a = base(current_price=103.0, partial_done=True, current_sl=101.5, trail_level=101.0)
    assert a.new_stop_loss is None
