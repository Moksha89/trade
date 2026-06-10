"""Performance memory — our own realized track record, fed back to the AI.

The AI otherwise sees only live indicators for a single instrument and has no
memory of how our trades actually worked out. This module summarizes our closed
trades (per instrument, per direction, per strategy) so the prompt can carry a
"YOUR ACTUAL TRACK RECORD" block. The AI is told to weight it heavily: lean into
instrument/direction/strategy combinations that have a positive record and
return no_trade where the matching record is clearly negative.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Trade


def _summarize(trades: list[Trade]) -> dict[str, Any] | None:
    n = len(trades)
    if n == 0:
        return None
    wins = sum(1 for t in trades if t.realized_pl > 0)
    net = sum(t.realized_pl for t in trades)
    rs = [t.realized_pl / t.initial_risk_aed for t in trades if t.initial_risk_aed]
    avg_r = sum(rs) / len(rs) if rs else 0.0
    return {
        "trades": n,
        "win_rate_pct": round(100.0 * wins / n, 1),
        "net_aed": round(net, 2),
        "avg_r": round(avg_r, 2),
    }


def performance_memory(
    db: Session, instrument: str, lookback: int = 2000
) -> dict[str, Any]:
    """Realized track record for `instrument`, plus the account-wide summary.

    Returns an empty dict when there is no closed-trade history yet (so the
    prompt simply omits the block rather than carrying an empty one).
    """
    closed = (
        db.execute(
            select(Trade)
            .where(Trade.status == "closed")
            .order_by(Trade.closed_at.desc())
            .limit(lookback)
        )
        .scalars()
        .all()
    )
    if not closed:
        return {}

    inst = [t for t in closed if t.instrument == instrument]
    out: dict[str, Any] = {}

    overall = _summarize(closed)
    if overall:
        out["account_overall"] = overall

    inst_summary = _summarize(inst)
    if inst_summary:
        out["instrument"] = inst_summary

    by_direction: dict[str, Any] = {}
    for d in ("long", "short"):
        s = _summarize([t for t in inst if t.direction == d])
        if s:
            by_direction[d] = s
    if by_direction:
        out["by_direction"] = by_direction

    by_strategy: dict[str, Any] = {}
    strategies = {t.strategy for t in inst if t.strategy}
    for strat in strategies:
        s = _summarize([t for t in inst if t.strategy == strat])
        if s:
            by_strategy[strat] = s
    if by_strategy:
        out["by_strategy"] = by_strategy

    return out
