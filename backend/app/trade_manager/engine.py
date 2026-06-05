"""Compute deterministic management actions for an open trade.

Implements the spec's rules:
  +1R   → move stop-loss to breakeven
  +1.5R → move stop-loss to lock +0.5R
  +2R or TP1 hit → close `partial_close_percent` (once)
  after TP1 → trail remaining using the configured method
  reversal/invalidation → close
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ManagementActions:
    current_r: float
    new_stop_loss: float | None = None
    partial_close_percent: float | None = None
    close: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return (
            self.new_stop_loss is not None
            or self.partial_close_percent is not None
            or self.close
        )


def _r_multiple(direction: str, entry: float, price: float, risk_per_unit: float) -> float:
    if risk_per_unit <= 0:
        return 0.0
    move = (price - entry) if direction == "long" else (entry - price)
    return move / risk_per_unit


def compute_management_actions(
    *,
    direction: str,
    entry: float,
    current_price: float,
    risk_per_unit: float,
    current_sl: float,
    plan: dict,
    breakeven_done: bool,
    profit_locked: bool,
    partial_done: bool,
    reversal_signal: bool = False,
    invalidated: bool = False,
    trail_level: float | None = None,
) -> ManagementActions:
    r = _r_multiple(direction, entry, current_price, risk_per_unit)
    actions = ManagementActions(current_r=round(r, 3))

    if invalidated:
        actions.close = True
        actions.reasons.append("setup invalidated")
        return actions
    if reversal_signal and plan.get("auto_close_on_reversal", True):
        actions.close = True
        actions.reasons.append("reversal signal")
        return actions

    be_at = float(plan.get("move_sl_to_breakeven_at_R", 1.0))
    lock_at = float(plan.get("lock_profit_at_R", 1.5))
    partial_at = float(plan.get("partial_close_at_R", 2.0))
    partial_pct = float(plan.get("partial_close_percent", 50))

    def better_sl(candidate: float) -> bool:
        # Only ever tighten in the favourable direction.
        if direction == "long":
            return candidate > current_sl
        return candidate < current_sl

    # Profit-lock at +1.5R (supersedes breakeven).
    if r >= lock_at and not profit_locked:
        lock_offset = 0.5 * risk_per_unit
        candidate = entry + lock_offset if direction == "long" else entry - lock_offset
        if better_sl(candidate):
            actions.new_stop_loss = round(candidate, 5)
            actions.reasons.append(f"lock +0.5R at {r:.2f}R")
    elif r >= be_at and not breakeven_done:
        if better_sl(entry):
            actions.new_stop_loss = round(entry, 5)
            actions.reasons.append(f"breakeven at {r:.2f}R")

    # Partial close at +2R (once).
    if r >= partial_at and not partial_done:
        actions.partial_close_percent = partial_pct
        actions.reasons.append(f"partial {partial_pct:.0f}% at {r:.2f}R")

    # Trailing after the partial / first target, if a trail level is supplied.
    if partial_done and trail_level is not None and better_sl(trail_level):
        actions.new_stop_loss = round(trail_level, 5)
        actions.reasons.append("trailing stop")

    return actions
