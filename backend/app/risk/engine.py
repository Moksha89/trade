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


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    computed_size: float = 0.0
    computed_risk_aed: float = 0.0
    risk_per_unit: float = 0.0


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
) -> RiskDecision:
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

    # 6. Minimum risk/reward.
    reward = abs(tp - entry)
    rr = reward / risk_per_unit
    min_rr = float(risk.get("min_risk_reward", 2.0))
    if rr < min_rr - 1e-9:
        return reject(f"Risk/reward {rr:.2f} below minimum {min_rr:.2f}")

    # 7. Stop-loss distance not too wide.
    max_sl_pct = float(risk.get("max_sl_distance_pct", 2.0))
    sl_pct = risk_per_unit / entry * 100.0
    if sl_pct > max_sl_pct:
        return reject(f"Stop distance {sl_pct:.2f}% exceeds max {max_sl_pct:.2f}%")

    # 8. Spread filter.
    max_spread = float(risk.get("max_spread_points", 5.0))
    if ctx.spread_points > max_spread:
        return reject(f"Spread {ctx.spread_points} above max {max_spread}")

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

    # 13. No duplicate instrument.
    if any(t.get("instrument") == proposal.instrument for t in ctx.open_trades):
        return reject(f"Duplicate trade on {proposal.instrument}")

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
    risk_per_unit_acct = risk_per_unit * ctx.account_ccy_per_point
    if risk_per_unit_acct <= 0:
        return reject("Zero stop distance")
    size = risk_budget / risk_per_unit_acct
    if size <= 0:
        return reject("Computed size is zero")
    computed_risk = size * risk_per_unit_acct

    return RiskDecision(
        approved=True,
        reason="Approved",
        computed_size=round(size, 6),
        computed_risk_aed=round(computed_risk, 2),
        risk_per_unit=round(risk_per_unit, 6),
    )
