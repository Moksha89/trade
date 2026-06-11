"""Account analytics — equity curve, Sharpe ratio, rolling performance.

Computes institutional-grade performance metrics from the trade history.
These power the dashboard analytics panel and feed into dynamic sizing.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Trade


def compute_analytics(db: Session, start_capital: float = 5000.0) -> dict[str, Any]:
    """Compute full account analytics from closed trade history.

    Returns metrics including:
    - Equity curve (daily snapshots)
    - Sharpe/Sortino ratios
    - Max drawdown (absolute and %)
    - Profit factor
    - Win rate, expectancy
    - Best/worst day
    - Average hold time
    """
    trades = list(
        db.scalars(
            select(Trade)
            .where(Trade.status == "closed")
            .order_by(Trade.opened_at.asc())
        )
    )

    if not trades:
        return {
            "total_trades": 0,
            "equity": start_capital,
            "message": "No closed trades yet",
        }

    # Build equity curve
    equity = start_capital
    equity_curve: list[dict] = [{"equity": equity, "date": None}]
    daily_returns: list[float] = []
    current_day: datetime | None = None
    day_pl = 0.0
    peak = equity
    max_dd = 0.0
    max_dd_pct = 0.0
    wins = 0
    losses = 0
    total_win_amount = 0.0
    total_loss_amount = 0.0
    hold_times: list[float] = []
    r_multiples: list[float] = []
    by_hour: dict[int, dict] = {}
    by_strategy: dict[str, dict] = {}

    for t in trades:
        pl = t.realized_pl or 0.0
        equity += pl

        # Track max drawdown
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
        dd_pct = (dd / peak * 100) if peak > 0 else 0.0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

        # Win/loss tracking
        if pl > 0:
            wins += 1
            total_win_amount += pl
        elif pl < 0:
            losses += 1
            total_loss_amount += abs(pl)

        # R-multiple
        if t.initial_risk_aed and t.initial_risk_aed > 0:
            r_multiples.append(pl / t.initial_risk_aed)

        # Hold time
        if t.opened_at and t.closed_at:
            hold_sec = (t.closed_at - t.opened_at).total_seconds()
            hold_times.append(hold_sec / 3600)  # hours

        # By hour of day (entry hour)
        if t.opened_at:
            hour = t.opened_at.hour
            if hour not in by_hour:
                by_hour[hour] = {"trades": 0, "wins": 0, "pl": 0.0}
            by_hour[hour]["trades"] += 1
            by_hour[hour]["wins"] += 1 if pl > 0 else 0
            by_hour[hour]["pl"] += pl

        # By strategy
        strat = t.strategy or "unknown"
        if strat not in by_strategy:
            by_strategy[strat] = {"trades": 0, "wins": 0, "pl": 0.0, "total_r": 0.0}
        by_strategy[strat]["trades"] += 1
        by_strategy[strat]["wins"] += 1 if pl > 0 else 0
        by_strategy[strat]["pl"] += pl
        if t.initial_risk_aed and t.initial_risk_aed > 0:
            by_strategy[strat]["total_r"] += pl / t.initial_risk_aed

        # Daily returns aggregation
        trade_day = (t.closed_at or t.opened_at).date() if t.closed_at else None
        if trade_day:
            if current_day and trade_day != current_day:
                daily_returns.append(day_pl)
                day_pl = pl
            else:
                day_pl += pl
            current_day = trade_day

    # Don't forget the last day
    if day_pl != 0:
        daily_returns.append(day_pl)

    # Compute ratios
    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    profit_factor = (total_win_amount / total_loss_amount) if total_loss_amount > 0 else float("inf")
    avg_win = (total_win_amount / wins) if wins > 0 else 0.0
    avg_loss = (total_loss_amount / losses) if losses > 0 else 0.0
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    # Sharpe ratio (annualized, assuming ~252 trading days)
    sharpe = 0.0
    sortino = 0.0
    if daily_returns and len(daily_returns) >= 5:
        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
        std_ret = math.sqrt(variance) if variance > 0 else 0.0
        if std_ret > 0:
            sharpe = round((mean_ret / std_ret) * math.sqrt(252), 2)
        # Sortino (downside deviation only)
        downside = [r for r in daily_returns if r < 0]
        if downside:
            down_var = sum(r ** 2 for r in downside) / len(downside)
            down_std = math.sqrt(down_var)
            if down_std > 0:
                sortino = round((mean_ret / down_std) * math.sqrt(252), 2)

    # Average R
    avg_r = round(sum(r_multiples) / len(r_multiples), 2) if r_multiples else 0.0

    # Best/worst hour
    best_hour = max(by_hour.items(), key=lambda x: x[1]["pl"])[0] if by_hour else None
    worst_hour = min(by_hour.items(), key=lambda x: x[1]["pl"])[0] if by_hour else None

    # Strategy breakdown
    for s in by_strategy.values():
        s["win_rate"] = round(s["wins"] / s["trades"] * 100, 1) if s["trades"] > 0 else 0.0
        s["pl"] = round(s["pl"], 2)
        s["total_r"] = round(s["total_r"], 2)

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "expectancy_aed": round(expectancy, 2),
        "avg_win_aed": round(avg_win, 2),
        "avg_loss_aed": round(avg_loss, 2),
        "avg_r": avg_r,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_aed": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 1),
        "equity": round(equity, 2),
        "peak_equity": round(peak, 2),
        "net_pl": round(equity - start_capital, 2),
        "avg_hold_hours": round(sum(hold_times) / len(hold_times), 1) if hold_times else 0.0,
        "best_hour_utc": best_hour,
        "worst_hour_utc": worst_hour,
        "by_strategy": by_strategy,
        "by_hour": {str(k): v for k, v in sorted(by_hour.items())},
    }
