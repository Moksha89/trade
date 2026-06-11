"""Account-level drawdown protection.

Tracks the equity high-water mark and locks trading when the account drops
below a configurable percentage from the peak. This prevents catastrophic
loss spirals that daily/weekly caps alone cannot catch.

All state is persisted in the settings store so it survives restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services.audit import log_event
from app.services.settings_store import RISK, get_group, update_group
from app.telegram.notifier import notify


def check_drawdown(db: Session, current_equity: float) -> bool:
    """Update high-water mark and return True if trading should continue.

    Returns False (and locks trading) when the drawdown exceeds the
    configured threshold.  The lock is sticky — it stays until manually
    cleared from the dashboard or via the API.
    """
    risk = get_group(db, RISK)

    if not risk.get("drawdown_guard_enabled", True):
        return True

    max_dd_pct = float(risk.get("max_drawdown_pct", 10.0))
    hwm = float(risk.get("equity_high_water_mark", current_equity))

    # Update high-water mark if we have a new peak.
    if current_equity > hwm:
        hwm = current_equity
        update_group(db, RISK, {"equity_high_water_mark": hwm})
        db.commit()

    if hwm <= 0:
        return True

    dd_pct = (hwm - current_equity) / hwm * 100.0

    if dd_pct >= max_dd_pct:
        from app.services.settings_store import get_bot_state

        state = get_bot_state(db)
        if not state.trading_locked:
            state.trading_locked = True
            state.lock_reason = (
                f"Drawdown {dd_pct:.1f}% hit {max_dd_pct:.0f}% limit "
                f"(equity {current_equity:.0f}, peak {hwm:.0f})"
            )
            log_event(
                db,
                "drawdown_lock",
                {
                    "equity": round(current_equity, 2),
                    "hwm": round(hwm, 2),
                    "drawdown_pct": round(dd_pct, 2),
                    "limit_pct": max_dd_pct,
                },
            )
            notify(
                f"🛑 DRAWDOWN LOCK: account down {dd_pct:.1f}% from peak "
                f"({current_equity:.0f} vs {hwm:.0f} HWM). "
                f"Trading locked until manual reset."
            )
            db.commit()
        return False

    return True
