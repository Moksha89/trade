"""Dynamic position sizing — scale risk based on recent performance.

Instead of a fixed AED risk per trade, this adjusts the risk budget using:
1. Anti-martingale: reduce size after losses, increase after wins
2. Drawdown scaling: reduce proportionally as drawdown deepens
3. Streak awareness: cool down after consecutive losses

The result is a multiplier (0.25–1.5) applied to the base risk-per-trade.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Trade


def compute_size_multiplier(
    db: Session,
    risk: dict[str, Any],
) -> float:
    """Compute a risk multiplier based on recent performance.

    Returns a float between 0.25 and 1.5:
    - 1.0 = normal risk
    - < 1.0 = reduced (drawdown, losing streak)
    - > 1.0 = increased (winning streak, small drawdown from peak)
    """
    if not risk.get("dynamic_sizing_enabled", True):
        return 1.0

    # Get recent closed trades (last 20)
    recent = list(
        db.scalars(
            select(Trade)
            .where(Trade.status == "closed")
            .order_by(Trade.closed_at.desc())
            .limit(20)
        )
    )

    if len(recent) < 3:
        return 1.0  # not enough history

    # 1. Win/loss streak factor
    streak = 0
    for t in recent:
        if t.realized_pl > 0:
            if streak >= 0:
                streak += 1
            else:
                break
        elif t.realized_pl < 0:
            if streak <= 0:
                streak -= 1
            else:
                break
        else:
            break

    # Losing streak: reduce by 25% per consecutive loss (min 0.25x)
    # Winning streak: increase by 10% per win (max 1.5x)
    streak_factor = 1.0
    if streak < 0:
        streak_factor = max(0.25, 1.0 + (streak * 0.25))  # -1 loss=0.75, -2=0.50, -3=0.25
    elif streak > 0:
        streak_factor = min(1.5, 1.0 + (streak * 0.10))  # +1 win=1.10, +2=1.20, etc.

    # 2. Drawdown factor (from equity high-water mark)
    hwm = float(risk.get("equity_high_water_mark", 0))
    capital = float(risk.get("account_capital", 5000))
    dd_factor = 1.0
    if hwm > 0 and capital < hwm:
        dd_pct = (hwm - capital) / hwm * 100.0
        # Scale down linearly: at 5% DD → 0.75x, at 10% DD → 0.5x
        dd_factor = max(0.25, 1.0 - (dd_pct / 10.0) * 0.5)

    # 3. Recent win rate factor (last 10 trades)
    last_10 = recent[:10]
    wins = sum(1 for t in last_10 if t.realized_pl > 0)
    wr = wins / len(last_10) if last_10 else 0.5
    # If win rate < 30%, reduce. If > 60%, allow increase.
    wr_factor = 1.0
    if wr < 0.3:
        wr_factor = 0.5
    elif wr > 0.6:
        wr_factor = 1.2

    # Combined multiplier (geometric mean-ish)
    multiplier = streak_factor * dd_factor * wr_factor

    # Clamp to [0.25, 1.5]
    return round(max(0.25, min(1.5, multiplier)), 2)
