"""Paper-to-live promotion gate.

Prevents the system from trading live until paper trading results meet
configurable thresholds.  Checks are run at startup and on each scan cycle.

This ensures the strategy is validated on paper before risking real money.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Trade
from app.services.audit import log_event
from app.telegram.notifier import notify


def check_promotion_readiness(
    db: Session,
    risk: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Check if paper trading results meet the promotion criteria.

    Returns (is_ready, report) where report contains the current stats
    vs required thresholds.
    """
    if not risk.get("promotion_gate_enabled", False):
        return True, {"status": "gate_disabled"}

    min_trades = int(risk.get("promotion_min_trades", 50))
    min_win_rate = float(risk.get("promotion_min_win_rate", 40.0))
    min_profit_factor = float(risk.get("promotion_min_profit_factor", 1.3))
    max_drawdown = float(risk.get("promotion_max_drawdown_pct", 15.0))

    paper_trades = list(
        db.scalars(
            select(Trade)
            .where(Trade.status == "closed", Trade.mode == "paper")
            .order_by(Trade.closed_at.desc())
            .limit(min_trades * 2)
        )
    )

    total = len(paper_trades)
    if total < min_trades:
        return False, {
            "status": "insufficient_trades",
            "trades": total,
            "required": min_trades,
            "message": f"Need {min_trades - total} more paper trades",
        }

    wins = sum(1 for t in paper_trades if t.realized_pl > 0)
    win_rate = (wins / total * 100) if total > 0 else 0.0

    gross_win = sum(t.realized_pl for t in paper_trades if t.realized_pl > 0)
    gross_loss = abs(sum(t.realized_pl for t in paper_trades if t.realized_pl < 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    # Max drawdown of paper equity
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in reversed(paper_trades):
        equity += t.realized_pl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    dd_pct = (max_dd / peak * 100) if peak > 0 else 0.0

    report = {
        "status": "evaluated",
        "trades": total,
        "required_trades": min_trades,
        "win_rate": round(win_rate, 1),
        "required_win_rate": min_win_rate,
        "profit_factor": round(pf, 2),
        "required_profit_factor": min_profit_factor,
        "max_drawdown_pct": round(dd_pct, 1),
        "allowed_drawdown_pct": max_drawdown,
    }

    ready = (
        total >= min_trades
        and win_rate >= min_win_rate
        and pf >= min_profit_factor
        and dd_pct <= max_drawdown
    )

    if ready:
        report["message"] = "READY for live trading — all criteria met"
    else:
        failures = []
        if win_rate < min_win_rate:
            failures.append(f"win rate {win_rate:.1f}% < {min_win_rate:.0f}%")
        if pf < min_profit_factor:
            failures.append(f"profit factor {pf:.2f} < {min_profit_factor:.1f}")
        if dd_pct > max_drawdown:
            failures.append(f"drawdown {dd_pct:.1f}% > {max_drawdown:.0f}%")
        report["message"] = f"Not ready: {'; '.join(failures)}"

    return ready, report
