"""Risk engine tests — the deterministic final authority."""

from app.ai.schema import Direction, EntryType, Strategy, TradeProposal
from app.risk.engine import RiskContext, evaluate_proposal
from app.services.settings_store import default_risk, default_strategy


def _long(entry=100.0, sl=99.0, tp=102.0, conf=80.0, instrument="US100"):
    return TradeProposal(
        instrument=instrument,
        direction=Direction.LONG,
        strategy=Strategy.TREND_PULLBACK,
        entry_type=EntryType.MARKET,
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp,
        confidence=conf,
    )


def _ctx(**kw):
    base = dict(account_capital=5000.0)
    base.update(kw)
    return RiskContext(**base)


RISK = default_risk()
STRAT = default_strategy()


def test_valid_long_approved_and_sized():
    d = evaluate_proposal(_long(), _ctx(), RISK, STRAT)
    assert d.approved
    # risk_per_unit = 1.0; budget = min(50, 100) = 50 -> size 50
    assert d.risk_per_unit == 1.0
    assert d.computed_size == 50.0
    assert d.computed_risk_aed == 50.0


def test_low_confidence_rejected():
    d = evaluate_proposal(_long(conf=60.0), _ctx(), RISK, STRAT)
    assert not d.approved and "Confidence" in d.reason


def test_bad_risk_reward_rejected():
    # tp only 0.5 above entry vs 1.0 risk -> RR 0.5
    d = evaluate_proposal(_long(tp=100.5), _ctx(), RISK, STRAT)
    assert not d.approved and "Risk/reward" in d.reason


def test_invalid_geometry_rejected():
    d = evaluate_proposal(_long(sl=101.0), _ctx(), RISK, STRAT)
    assert not d.approved and "geometry" in d.reason


def test_max_active_trades_enforced():
    ctx = _ctx(open_trades=[{"instrument": "US500"}, {"instrument": "Gold"}])
    d = evaluate_proposal(_long(), ctx, RISK, STRAT)
    assert not d.approved and "active trades" in d.reason


def test_duplicate_instrument_rejected():
    ctx = _ctx(open_trades=[{"instrument": "US100"}])
    d = evaluate_proposal(_long(), ctx, RISK, STRAT)
    assert not d.approved and "Duplicate" in d.reason


def test_daily_loss_limit_stops_trading():
    ctx = _ctx(realized_pl_today=-RISK["daily_loss_limit"])
    d = evaluate_proposal(_long(), ctx, RISK, STRAT)
    assert not d.approved and "Daily loss" in d.reason


def test_stop_after_n_losses():
    ctx = _ctx(losses_today=RISK["stop_after_n_losses"])
    d = evaluate_proposal(_long(), ctx, RISK, STRAT)
    assert not d.approved and "losses today" in d.reason


def test_news_filter_blocks():
    d = evaluate_proposal(_long(), _ctx(news_risk=True), RISK, STRAT)
    assert not d.approved and "news" in d.reason.lower()


def test_market_closed_blocks():
    d = evaluate_proposal(_long(), _ctx(market_open=False), RISK, STRAT)
    assert not d.approved and "closed" in d.reason.lower()


def test_spread_too_high_blocks():
    d = evaluate_proposal(_long(), _ctx(spread_points=99.0), RISK, STRAT)
    assert not d.approved and "Spread" in d.reason


def test_sl_too_wide_blocks():
    # 5% stop distance exceeds default 2% cap
    d = evaluate_proposal(_long(sl=95.0, tp=115.0), _ctx(), RISK, STRAT)
    assert not d.approved and "Stop distance" in d.reason


def test_combined_risk_cap_limits_size():
    ctx = _ctx(current_open_risk_aed=70.0)  # remaining 30
    d = evaluate_proposal(_long(), ctx, RISK, STRAT)
    assert d.approved
    assert d.computed_risk_aed == 30.0


def test_quote_currency_conversion_scales_size():
    # USD-quoted instrument on an AED account: a 1-point stop is worth 3.6725
    # AED per unit, so the 50 AED budget buys fewer units but risks exactly the
    # budget in account currency (not 3.67x more).
    ctx = _ctx(account_ccy_per_point=3.6725)
    d = evaluate_proposal(_long(), ctx, RISK, STRAT)
    assert d.approved
    assert d.computed_size == round(50.0 / 3.6725, 6)
    assert d.computed_risk_aed == 50.0


def test_unverified_currency_blocks_trade():
    # A 0.0 multiplier means the provider could not verify the quote-currency
    # conversion; the engine must refuse to size rather than guess.
    d = evaluate_proposal(_long(), _ctx(account_ccy_per_point=0.0), RISK, STRAT)
    assert not d.approved and "currency conversion" in d.reason


def test_bearish_disabled_blocks_short():
    strat = {**STRAT, "allow_bearish": False}
    short = TradeProposal(
        instrument="US100",
        direction=Direction.SHORT,
        strategy=Strategy.TREND_PULLBACK,
        entry_price=100.0,
        stop_loss=101.0,
        take_profit_1=98.0,
        confidence=80.0,
    )
    d = evaluate_proposal(short, _ctx(), RISK, strat)
    assert not d.approved and "Bearish" in d.reason


def test_stop_too_tight_vs_spread_rejected():
    # risk_per_unit = 1.0; spread 0.5 -> need stop >= 3 x 0.5 = 1.5 -> reject.
    d = evaluate_proposal(_long(), _ctx(spread_points=0.5), RISK, STRAT)
    assert not d.approved and "too tight vs spread" in d.reason


def test_stop_wide_enough_vs_spread_approved():
    # risk_per_unit = 1.0; spread 0.2 -> need stop >= 0.6 -> 1.0 passes.
    d = evaluate_proposal(_long(), _ctx(spread_points=0.2), RISK, STRAT)
    assert d.approved


def test_zero_spread_skips_ratio_gate():
    # No live spread (0.0) must not block — gate only applies when known.
    d = evaluate_proposal(_long(), _ctx(spread_points=0.0), RISK, STRAT)
    assert d.approved
