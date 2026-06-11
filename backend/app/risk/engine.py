"""Deterministic risk engine.

This module is the final authority on whether a trade may execute. The AI cannot
bypass it. Given an AI proposal plus the current account/market context and the
configured risk + strategy settings, it returns an approve/reject decision and
the authoritative position size and AED risk.

No randomness, no network calls, no LLM — fully deterministic and unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai.schema import Direction, Strategy, TradeProposal


@dataclass
class RiskContext:
    account_capital: float
    open_trades: list[dict[str, Any]] = field(default_factory=list)
    current_open_risk_aed: float = 0.0
    trades_today: int = 0
    losses_today: int = 0
    realized_pl_today: float = 0.0  # negative = loss
    realized_pl_week: float = 0.0
    spread_points: float = 0.0
    news_risk: bool = False
    market_open: bool = True
    trading_locked: bool = False
    # Account-currency value of a 1-point move for a size-1 position. For an
    # account whose currency matches the instrument quote currency this is 1.0;
    # for an AED account trading USD-quoted instruments it is the USD→AED rate
    # times the lot size. Used to size positions in the account currency.
    account_ccy_per_point: float = 1.0
    # Higher-timeframe trend per timeframe label, e.g. {"1H": "down", "4H": "up"}.
    # Each value is one of "up" | "down" | "sideways". Used to block trades that
    # fight the higher-timeframe trend. Empty disables the check.
    htf_trends: dict[str, str] = field(default_factory=dict)
    # Setup-quality signals computed from the entry-timeframe indicators/candles.
    at_support: bool = False  # price sits near a support level
    at_resistance: bool = False  # price sits near a resistance level
    bullish_confirmation: bool = False  # up candle + positive momentum
    bearish_confirmation: bool = False  # down candle + negative momentum
    volatility_pct: float = 0.0  # ATR as % of price
    atr: float = 0.0  # absolute ATR (quote currency), for anti-scalp distance


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    computed_size: float = 0.0
    computed_risk_aed: float = 0.0
    risk_per_unit: float = 0.0
    quality_score: float = 0.0


_STRATEGY_TOGGLE = {
    Strategy.TREND_PULLBACK: "trend_pullback",
    Strategy.BREAKOUT_RETEST: "breakout_retest",
    Strategy.BREAKDOWN_RETEST: "breakdown_retest",
    Strategy.RANGE_REVERSAL: "range_reversal",
    Strategy.MOMENTUM_CONTINUATION: "momentum_continuation",
}


def evaluate_proposal(
    proposal: TradeProposal,
    ctx: RiskContext,
    risk: dict[str, Any],
    strategy: dict[str, Any],
    skip_setup_quality: bool = False,
) -> RiskDecision:
    """Approve/reject a proposal.

    `skip_setup_quality` bypasses the setup-pattern gates (trend alignment,
    higher-tf bias, support/resistance location, confirmation candle, volatility
    band, anti-scalp). Those are validated at scan time; the execution-time
    re-check only needs to re-verify dynamic risk (locks, losses, spread, size).
    """

    def reject(reason: str) -> RiskDecision:
        return RiskDecision(approved=False, reason=reason)

    # 0. Not a trade.
    if not proposal.is_trade or proposal.strategy == Strategy.NO_TRADE:
        return reject("AI returned no_trade")

    # 1. Global locks / market state.
    if ctx.trading_locked:
        return reject("Trading locked for today")
    if not ctx.market_open:
        return reject("Market closed / not tradable")
    if ctx.news_risk and risk.get("news_filter_enabled", True):
        return reject("High-impact news nearby")

    # 2. Instrument allow-list.
    allowed = risk.get("allowed_instruments", [])
    if allowed and proposal.instrument not in allowed:
        return reject(f"Instrument {proposal.instrument} not allowed")

    # 3. Strategy toggles.
    if proposal.direction == Direction.LONG and not strategy.get("allow_bullish", True):
        return reject("Bullish trades disabled")
    if proposal.direction == Direction.SHORT and not strategy.get("allow_bearish", True):
        return reject("Bearish trades disabled")
    toggle = _STRATEGY_TOGGLE.get(proposal.strategy)
    if toggle and not strategy.get(toggle, True):
        return reject(f"Strategy {proposal.strategy.value} disabled")

    # 4. Confidence threshold.
    min_conf = float(risk.get("min_confidence", 70))
    if proposal.confidence < min_conf:
        return reject(
            f"Confidence {proposal.confidence:.0f} below threshold {min_conf:.0f}"
        )

    # 4b–4g. Quality scoring system. Each setup factor earns points toward a
    # composite quality score (out of 100). Only setups that reach the minimum
    # threshold are approved. This replaces the old binary pass/fail gates and
    # lets trades with 4/5 factors aligned pass while blocking weak setups.
    #
    # Scoring weights:
    #   HTF bias aligned       +25
    #   At key S/R level       +25
    #   Momentum confirmation  +20
    #   Good volatility band   +15
    #   AI confidence bonus    +15  (scaled: conf at threshold=0, conf 90=+15)
    quality_score = 0.0
    quality_notes: list[str] = []
    if not skip_setup_quality:
        # 4b. Counter-trend is still a HARD reject (biggest loss cause).
        if risk.get("trend_alignment_enabled", True) and ctx.htf_trends:
            opposing = "down" if proposal.direction == Direction.LONG else "up"
            against = [tf for tf, tr in ctx.htf_trends.items() if tr == opposing]
            if against:
                return reject(
                    f"Counter-trend: {proposal.direction.value} vs "
                    f"{'/'.join(sorted(against))} {opposing}trend"
                )

        # 4c. HTF directional bias (scored). If no HTF data available or
        # HTF check disabled, award full points (don't penalize for missing data).
        if not risk.get("require_htf_bias", True) or not ctx.htf_trends:
            quality_score += 25
            quality_notes.append("htf_bias_skipped")
        else:
            favouring = "up" if proposal.direction == Direction.LONG else "down"
            if any(tr == favouring for tr in ctx.htf_trends.values()):
                quality_score += 25
                quality_notes.append("htf_bias_aligned")

        # 4d. Entry location (scored). If location filter disabled, award full.
        if not risk.get("require_location_filter", True):
            quality_score += 25
            quality_notes.append("location_skipped")
        elif proposal.direction == Direction.LONG:
            if ctx.at_resistance:
                quality_notes.append("long_into_resistance")
            elif ctx.at_support:
                quality_score += 25
                quality_notes.append("at_support")
        else:
            if ctx.at_support:
                quality_notes.append("short_into_support")
            elif ctx.at_resistance:
                quality_score += 25
                quality_notes.append("at_resistance")

        # 4e. Confirmation candle + momentum (scored). If disabled, award full.
        if not risk.get("require_confirmation", True):
            quality_score += 20
            quality_notes.append("confirmation_skipped")
        elif proposal.direction == Direction.LONG and ctx.bullish_confirmation:
            quality_score += 20
            quality_notes.append("bullish_confirmed")
        elif proposal.direction == Direction.SHORT and ctx.bearish_confirmation:
            quality_score += 20
            quality_notes.append("bearish_confirmed")

        # 4f. Volatility band (scored, with hard reject for extremes).
        if ctx.volatility_pct > 0:
            vmin = float(risk.get("min_volatility_pct", 0.15))
            vmax = float(risk.get("max_volatility_pct", 2.0))
            if vmin <= ctx.volatility_pct <= vmax:
                quality_score += 15
                quality_notes.append("volatility_ok")
            elif ctx.volatility_pct > vmax * 2 or ctx.volatility_pct < vmin * 0.3:
                return reject(
                    f"Extreme volatility {ctx.volatility_pct:.2f}% "
                    f"(band {vmin:g}-{vmax:g}%)"
                )

        # 4g. AI confidence bonus (scaled 0–15 points).
        min_conf = float(risk.get("min_confidence", 70))
        conf_bonus = min(15.0, max(0.0, (proposal.confidence - min_conf) / 20.0 * 15.0))
        quality_score += conf_bonus

        # Minimum quality threshold.
        min_quality = float(risk.get("min_quality_score", 40))
        if quality_score < min_quality:
            return reject(
                f"Quality score {quality_score:.0f} below minimum {min_quality:.0f} "
                f"(factors: {', '.join(quality_notes) or 'none'})"
            )

    # 5. Valid SL/TP geometry (no trade without SL + TP).
    entry = proposal.entry_price
    sl = proposal.stop_loss
    tp = proposal.take_profit_1
    if entry <= 0 or sl <= 0 or tp <= 0:
        return reject("Missing entry/stop-loss/take-profit")
    if proposal.direction == Direction.LONG and not (sl < entry < tp):
        return reject("Invalid long geometry (need SL < entry < TP)")
    if proposal.direction == Direction.SHORT and not (tp < entry < sl):
        return reject("Invalid short geometry (need TP < entry < SL)")

    risk_per_unit = abs(entry - sl)
    if risk_per_unit <= 0:
        return reject("Zero stop distance")

    # 5b. Minimum stop distance relative to ATR.
    # Prevents the AI from proposing impossibly tight stops (e.g. 5 pips on
    # EURUSD 15M) that any normal fill slippage will blow past the risk cap.
    if not skip_setup_quality:
        min_stop_atr = float(risk.get("min_stop_atr_multiple", 0.5))
        if ctx.atr > 0 and risk_per_unit < min_stop_atr * ctx.atr:
            return reject(
                f"Stop distance {risk_per_unit:.4g} too tight "
                f"(<{min_stop_atr:g}\u00d7 ATR {ctx.atr:.4g})"
            )

    # 6. Minimum risk/reward.
    reward = abs(tp - entry)
    rr = reward / risk_per_unit
    min_rr = float(risk.get("min_risk_reward", 2.0))
    if rr < min_rr - 1e-9:
        return reject(f"Risk/reward {rr:.2f} below minimum {min_rr:.2f}")

    # 6b. Anti-scalp. The target must be a real move, not a tight scalp that the
    # spread and noise eat. Require the reward distance to be a multiple of ATR
    # so the bot trades structure, not micro instant trades.
    if not skip_setup_quality:
        min_reward_atr = float(risk.get("min_reward_atr", 1.0))
        if ctx.atr > 0 and reward < min_reward_atr * ctx.atr:
            return reject(
                f"Target {reward:.4g} too close (<{min_reward_atr:g}x ATR "
                f"{ctx.atr:.4g}) — scalp"
            )

    # 7. Stop-loss distance not too wide.
    max_sl_pct = float(risk.get("max_sl_distance_pct", 2.0))
    sl_pct = risk_per_unit / entry * 100.0
    if sl_pct > max_sl_pct:
        return reject(f"Stop distance {sl_pct:.2f}% exceeds max {max_sl_pct:.2f}%")

    # 8. Spread filter.
    max_spread = float(risk.get("max_spread_points", 5.0))
    if ctx.spread_points > max_spread:
        return reject(f"Spread {ctx.spread_points} above max {max_spread}")

    # 8b. Stop must be wide enough relative to the spread. A stop only ~1x the
    # spread means the spread (and the slippage that comes with it) eats most of
    # the risk budget — this is exactly what wrecked the first crypto trades: a
    # tight stop on a wide-spread pair fills away from the quote and the real
    # risk blows past the per-trade cap. Require the stop distance to be a
    # multiple of the current spread so only setups with room to breathe pass.
    min_stop_spread = float(risk.get("min_stop_to_spread_ratio", 3.0))
    if ctx.spread_points > 0 and risk_per_unit < min_stop_spread * ctx.spread_points:
        return reject(
            f"Stop distance {risk_per_unit:.4g} too tight vs spread "
            f"{ctx.spread_points:.4g} (need ≥{min_stop_spread:g}x spread)"
        )

    # 9. Daily / weekly loss limits.
    daily_limit = float(risk.get("daily_loss_limit", 150))
    if -ctx.realized_pl_today >= daily_limit:
        return reject("Daily loss limit reached")
    weekly_limit = float(risk.get("weekly_loss_limit", 400))
    if -ctx.realized_pl_week >= weekly_limit:
        return reject("Weekly loss limit reached")

    # 10. Stop after N losses.
    max_losses = int(risk.get("stop_after_n_losses", 2))
    if ctx.losses_today >= max_losses:
        return reject(f"Reached {ctx.losses_today} losses today")

    # 11. Max trades per day.
    max_per_day = int(risk.get("max_trades_per_day", 3))
    if ctx.trades_today >= max_per_day:
        return reject("Max trades per day reached")

    # 12. Max active trades.
    max_active = int(risk.get("max_active_trades", 2))
    if len(ctx.open_trades) >= max_active:
        return reject(f"Already {len(ctx.open_trades)} active trades (max {max_active})")

    # 13. Same-instrument guard. With hedging off, block any second position on
    # the instrument. With hedging on, allow an opposing-direction position (a
    # hedge) but still block a same-direction one — averaging/pyramiding into a
    # position is never allowed.
    hedging = bool(risk.get("hedging_enabled", False))
    for t in ctx.open_trades:
        if t.get("instrument") != proposal.instrument:
            continue
        same_dir = t.get("direction") == proposal.direction.value
        if same_dir or not hedging:
            kind = "Duplicate" if same_dir else "Existing"
            return reject(
                f"{kind} position on {proposal.instrument} "
                f"(hedging {'on' if hedging else 'off'})"
            )

    # 14. Position sizing from risk budget and combined-risk cap.
    max_risk_trade = float(risk.get("max_risk_per_trade", 50))
    max_combined = float(risk.get("max_combined_open_risk", 100))
    remaining_combined = max_combined - ctx.current_open_risk_aed
    if remaining_combined <= 0:
        return reject("Combined open risk cap reached")
    risk_budget = min(max_risk_trade, remaining_combined)
    # Convert the per-unit stop distance (quote currency) into account currency
    # so the AED risk budget sizes the position correctly.
    if ctx.account_ccy_per_point <= 0:
        return reject("Cannot size: unverified currency conversion for instrument")
    # Add slippage buffer: size as if the stop is wider by 2× spread so that
    # normal fill slippage doesn't push actual risk past the per-trade cap and
    # trigger an abort (which wastes spread cost for no benefit).
    slippage_buffer = 2.0 * max(ctx.spread_points, 0.0)
    risk_per_unit_buffered = risk_per_unit + slippage_buffer
    risk_per_unit_acct = risk_per_unit_buffered * ctx.account_ccy_per_point
    if risk_per_unit_acct <= 0:
        return reject("Zero stop distance")
    size = risk_budget / risk_per_unit_acct
    if size <= 0:
        return reject("Computed size is zero")
    computed_risk = size * risk_per_unit_acct

    return RiskDecision(
        approved=True,
        reason=f"Approved (quality {quality_score:.0f})",
        computed_size=round(size, 6),
        computed_risk_aed=round(computed_risk, 2),
        risk_per_unit=round(risk_per_unit, 6),
        quality_score=round(quality_score, 1),
    )
