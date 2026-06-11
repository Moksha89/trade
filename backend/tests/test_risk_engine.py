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


# Legacy tests target the geometry/RR/spread/sizing gates. The setup-location
# and confirmation-candle gates are exercised by their own dedicated tests
# below, so disable them here to keep these focused.
RISK = {
    **default_risk(),
    "require_location_filter": False,
    "require_confirmation": False,
}
STRICT = default_risk()  # all setup-quality gates enabled
STRAT = default_strategy()


def _good_long_ctx(**kw):
    """Context whose setup-quality signals all pass for a long."""
    base = dict(
        account_capital=5000.0,
        at_support=True,
        at_resistance=False,
        bullish_confirmation=True,
        htf_trends={"1H": "up", "4H": "up"},
    )
    base.update(kw)
    return RiskContext(**base)


def _good_short_ctx(**kw):
    """Context whose setup-quality signals all pass for a short."""
    base = dict(
        account_capital=5000.0,
        at_support=False,
        at_resistance=True,
        bearish_confirmation=True,
        htf_trends={"1H": "down", "4H": "down"},
    )
    base.update(kw)
    return RiskContext(**base)


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


def _short(entry=100.0, sl=101.0, tp=98.0, conf=80.0, instrument="US100"):
    return TradeProposal(
        instrument=instrument,
        direction=Direction.SHORT,
        strategy=Strategy.TREND_PULLBACK,
        entry_type=EntryType.MARKET,
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp,
        confidence=conf,
    )


def test_duplicate_instrument_rejected():
    ctx = _ctx(open_trades=[{"instrument": "US100", "direction": "long"}])
    d = evaluate_proposal(_long(), ctx, RISK, STRAT)
    assert not d.approved and "Duplicate" in d.reason


def test_opposing_instrument_rejected_when_hedging_off():
    risk = {**RISK, "hedging_enabled": False}
    ctx = _ctx(open_trades=[{"instrument": "US100", "direction": "long"}])
    d = evaluate_proposal(_short(), ctx, risk, STRAT)
    assert not d.approved and "Existing position" in d.reason


def test_opposing_instrument_allowed_when_hedging_on():
    risk = {**RISK, "hedging_enabled": True}
    ctx = _ctx(open_trades=[{"instrument": "US100", "direction": "long"}])
    d = evaluate_proposal(_short(), ctx, risk, STRAT)
    assert d.approved


def test_same_direction_blocked_even_when_hedging_on():
    # Hedging allows the opposite side only — never averaging into the same side.
    risk = {**RISK, "hedging_enabled": True}
    ctx = _ctx(open_trades=[{"instrument": "US100", "direction": "long"}])
    d = evaluate_proposal(_long(), ctx, risk, STRAT)
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


# --- Higher-timeframe trend alignment (4b) ---

def test_long_blocked_when_higher_tf_down():
    # Long while 4H trend is down -> counter-trend, rejected.
    d = evaluate_proposal(_long(), _ctx(htf_trends={"1H": "up", "4H": "down"}), RISK, STRAT)
    assert not d.approved and "Counter-trend" in d.reason and "4H" in d.reason


def test_short_blocked_when_higher_tf_up():
    d = evaluate_proposal(_short(), _ctx(htf_trends={"1H": "up", "4H": "up"}), RISK, STRAT)
    assert not d.approved and "Counter-trend" in d.reason


def test_long_allowed_when_higher_tf_aligned_or_sideways():
    # 1H up (agrees), 4H sideways (neutral) -> allowed.
    d = evaluate_proposal(_long(), _ctx(htf_trends={"1H": "up", "4H": "sideways"}), RISK, STRAT)
    assert d.approved


def test_short_allowed_when_higher_tf_down_or_sideways():
    d = evaluate_proposal(_short(), _ctx(htf_trends={"1H": "down", "4H": "sideways"}), RISK, STRAT)
    assert d.approved


def test_trend_alignment_disabled_allows_counter_trend():
    risk = {**RISK, "trend_alignment_enabled": False, "require_htf_bias": False}
    d = evaluate_proposal(_long(), _ctx(htf_trends={"1H": "down", "4H": "down"}), risk, STRAT)
    assert d.approved


def test_no_htf_data_does_not_block():
    # Empty htf_trends (data hiccup) must not fabricate a block.
    d = evaluate_proposal(_long(), _ctx(htf_trends={}), RISK, STRAT)
    assert d.approved


# --- Setup-quality scoring (4c–4g) ---

def test_long_requires_htf_bullish_bias():
    # 1H/4H both sideways -> no bullish bias -> lower score than aligned.
    d = evaluate_proposal(_long(), _good_long_ctx(htf_trends={"1H": "sideways", "4H": "sideways"}), STRICT, STRAT)
    d_aligned = evaluate_proposal(_long(), _good_long_ctx(), STRICT, STRAT)
    assert d_aligned.quality_score > d.quality_score


def test_long_blocked_when_not_at_support():
    # Missing support + no HTF bias + no confirmation -> quality too low.
    d = evaluate_proposal(
        _long(),
        _good_long_ctx(at_support=False, htf_trends={}, bullish_confirmation=False),
        STRICT, STRAT,
    )
    assert not d.approved and "Quality score" in d.reason


def test_long_blocked_when_into_resistance():
    # At resistance + no other factors -> low score, rejected.
    d = evaluate_proposal(
        _long(),
        _good_long_ctx(at_resistance=True, at_support=False, htf_trends={}, bullish_confirmation=False),
        STRICT, STRAT,
    )
    assert not d.approved and "Quality score" in d.reason


def test_long_blocked_without_bullish_confirmation():
    # Missing confirmation + no HTF + no location -> quality too low.
    d = evaluate_proposal(
        _long(),
        _good_long_ctx(bullish_confirmation=False, htf_trends={}, at_support=False),
        STRICT, STRAT,
    )
    assert not d.approved and "Quality score" in d.reason


def test_long_approved_with_full_quality_setup():
    d = evaluate_proposal(_long(), _good_long_ctx(), STRICT, STRAT)
    assert d.approved, d.reason
    assert d.quality_score >= 40


def test_short_requires_resistance_and_bearish_confirmation():
    d = evaluate_proposal(_short(), _good_short_ctx(), STRICT, STRAT)
    assert d.approved, d.reason
    # Missing resistance + confirmation + HTF -> quality too low.
    d2 = evaluate_proposal(
        _short(),
        _good_short_ctx(at_resistance=False, bearish_confirmation=False, htf_trends={}),
        STRICT, STRAT,
    )
    assert not d2.approved and "Quality score" in d2.reason


def test_anti_scalp_blocks_tight_target():
    # reward = |102-100| = 2.0; ATR 3.0 -> need >= 3.0 -> blocked as scalp.
    # Use a wider stop (SL=97) so the min-stop-vs-ATR gate passes (3 >= 0.5*3).
    risk_cfg = {**STRICT, "min_stop_atr_multiple": 0.0}
    d = evaluate_proposal(_long(), _good_long_ctx(atr=3.0), risk_cfg, STRAT)
    assert not d.approved and "scalp" in d.reason


def test_anti_scalp_allows_real_move():
    # reward 2.0 vs ATR 1.0 -> passes (2x ATR).
    d = evaluate_proposal(_long(), _good_long_ctx(atr=1.0), STRICT, STRAT)
    assert d.approved, d.reason


def test_volatility_band_blocks_chaotic_market():
    d = evaluate_proposal(_long(), _good_long_ctx(volatility_pct=12.0), STRICT, STRAT)
    assert not d.approved and "volatility" in d.reason.lower()


def test_volatility_band_blocks_dead_market():
    d = evaluate_proposal(_long(), _good_long_ctx(volatility_pct=0.001), STRICT, STRAT)
    assert not d.approved and "volatility" in d.reason.lower()


def test_setup_filters_toggle_off():
    risk = {**STRICT, "require_htf_bias": False, "require_location_filter": False,
            "require_confirmation": False, "min_reward_atr": 0.0}
    # Bare ctx (no support/confirmation signals) is approved when filters are off.
    d = evaluate_proposal(_long(), _ctx(), risk, STRAT)
    assert d.approved, d.reason
