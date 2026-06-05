"""Trade manager rule tests."""

from app.trade_manager.engine import compute_management_actions

PLAN = {
    "move_sl_to_breakeven_at_R": 1.0,
    "lock_profit_at_R": 1.5,
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


def test_breakeven_at_1r():
    a = base(current_price=101.0)
    assert a.new_stop_loss == 100.0
    assert "breakeven" in a.reasons[0]


def test_lock_half_r_at_1_5r():
    a = base(current_price=101.5)
    assert a.new_stop_loss == 100.5


def test_partial_close_at_2r():
    a = base(current_price=102.0)
    assert a.partial_close_percent == 50


def test_reversal_closes():
    a = base(current_price=100.5, reversal_signal=True)
    assert a.close is True


def test_invalidation_closes():
    a = base(invalidated=True)
    assert a.close is True


def test_short_breakeven():
    a = base(direction="short", entry=100.0, current_price=99.0, current_sl=101.0)
    assert a.new_stop_loss == 100.0


def test_no_change_below_1r():
    a = base(current_price=100.3)
    assert not a.has_changes


def test_trailing_after_partial():
    a = base(current_price=103.0, partial_done=True, profit_locked=True, trail_level=101.5)
    assert a.new_stop_loss == 101.5
    assert "trailing" in a.reasons[-1]
