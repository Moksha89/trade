"""Compute deterministic management actions for an open trade.

Implements the spec's rules (thresholds are configurable per `plan`; defaults
shown):
  +0.7R → move stop-loss to breakeven
  +1.0R → move stop-loss to lock +0.3R (banks profit even if it later reverses)
  TP1 / +2R → close `partial_close_percent` (once) and start ATR trailing
  trailing → ratchet the stop to the supplied ATR level (never loosens)
  reversal/invalidation → close

Each tick we recompute every eligible stop (breakeven, profit-lock, trail) and
move to the *tightest favourable* one, so the protective stop only ever
ratchets in the trade's favour.
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

    be_at = float(plan.get("move_sl_to_breakeven_at_R", 0.7))
    lock_at = float(plan.get("lock_profit_at_R", 1.0))
    lock_off_r = float(plan.get("lock_profit_offset_R", 0.3))
    partial_at = float(plan.get("partial_close_at_R", 2.0))
    partial_pct = float(plan.get("partial_close_percent", 50))

    def beyond_current(candidate: float) -> bool:
        # Only ever tighten in the favourable direction past the current stop.
        if direction == "long":
            return candidate > current_sl
        return candidate < current_sl

    def tighter(a: float, b: float) -> bool:
        # Is `a` a more favourable (tighter) stop than `b`?
        return a > b if direction == "long" else a < b

    # Collect every stop that is currently eligible, then move to the tightest
    # favourable one. Recomputed each tick, so it naturally ratchets and never
    # loosens (beyond_current guards against moving the stop backwards).
    candidates: list[tuple[float, str]] = []
    if r >= be_at:
        candidates.append((entry, f"breakeven at {r:.2f}R"))
    if r >= lock_at:
        offset = lock_off_r * risk_per_unit
        locked = entry + offset if direction == "long" else entry - offset
        candidates.append((locked, f"lock +{lock_off_r:g}R at {r:.2f}R"))
    if trail_level is not None:
        candidates.append((float(trail_level), "trailing stop"))

    best: tuple[float, str] | None = None
    for value, reason in candidates:
        if not beyond_current(value):
            continue
        if best is None or tighter(value, best[0]):
            best = (round(value, 5), reason)
    if best is not None:
        actions.new_stop_loss = best[0]
        actions.reasons.append(best[1])

    # Partial close at the first target (once).
    if r >= partial_at and not partial_done:
        actions.partial_close_percent = partial_pct
        actions.reasons.append(f"partial {partial_pct:.0f}% at {r:.2f}R")

    return actions
