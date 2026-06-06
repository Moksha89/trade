"""Account/portfolio statistics derived from Trade rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Trade


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _week_start(now: datetime) -> datetime:
    d = _day_start(now)
    return d - timedelta(days=d.weekday())


def open_trades(db: Session) -> list[Trade]:
    return list(db.scalars(select(Trade).where(Trade.status == "open")))


def closed_trades(db: Session) -> list[Trade]:
    return list(db.scalars(select(Trade).where(Trade.status == "closed")))


def current_open_risk(db: Session) -> float:
    """Combined open risk in account currency (AED).

    Converts each position's quote-currency stop distance into AED via the
    per-trade multiplier recorded at open (initial_risk_aed / (size *
    initial_risk_per_unit)) so the figure is comparable to the AED combined-risk
    cap. USD-quoted instruments would otherwise be under-counted by the FX rate.
    """
    total = 0.0
    for t in open_trades(db):
        adverse = abs(t.entry_price - t.stop_loss)
        # If the stop has been moved beyond entry (profit locked), risk is ~0.
        if t.direction == "long" and t.stop_loss >= t.entry_price:
            adverse = 0.0
        if t.direction == "short" and t.stop_loss <= t.entry_price:
            adverse = 0.0
        denom = t.size * (t.initial_risk_per_unit or 0.0)
        mult = (t.initial_risk_aed / denom) if (denom > 0 and t.initial_risk_aed) else 1.0
        total += t.size * adverse * mult
    return round(total, 2)


def stats(db: Session) -> dict[str, float | int]:
    now = datetime.now(timezone.utc)
    day0 = _day_start(now)
    week0 = _week_start(now)

    def aware(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    closed = closed_trades(db)
    opened_today = [t for t in (open_trades(db) + closed) if (aware(t.opened_at) or now) >= day0]
    closed_today = [t for t in closed if (aware(t.closed_at) or now) >= day0]
    closed_week = [t for t in closed if (aware(t.closed_at) or now) >= week0]

    return {
        "trades_today": len(opened_today),
        "losses_today": sum(1 for t in closed_today if t.realized_pl < 0),
        "realized_pl_today": round(sum(t.realized_pl for t in closed_today), 2),
        "realized_pl_week": round(sum(t.realized_pl for t in closed_week), 2),
        "open_trades_count": len(open_trades(db)),
    }
