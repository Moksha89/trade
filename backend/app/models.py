"""SQLAlchemy ORM models for the trading system."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SettingRow(Base):
    """Key/value store for runtime-editable settings groups (risk, strategy, ai)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class BotState(Base):
    """Singleton (id=1) holding live bot status flags."""

    __tablename__ = "bot_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    bot_running: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trading_locked_today: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    broker_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_ai_decision: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_risk_rejection: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class TradeIdea(Base):
    """An AI proposal plus the deterministic risk-engine decision."""

    __tablename__ = "trade_ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    instrument: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(16))  # long | short | no_trade
    strategy: Mapped[str] = mapped_column(String(48))
    entry_type: Mapped[str] = mapped_column(String(16), default="market")
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss: Mapped[float] = mapped_column(Float, default=0.0)
    take_profit_1: Mapped[float] = mapped_column(Float, default=0.0)
    take_profit_2: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    risk_reward: Mapped[float] = mapped_column(Float, default=0.0)
    position_size: Mapped[float] = mapped_column(Float, default=0.0)
    risk_aed: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    invalidation_condition: Mapped[str] = mapped_column(Text, default="")
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)
    management_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    market_classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Risk engine outcome
    risk_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_reason: Mapped[str] = mapped_column(Text, default="")
    # Workflow status: proposed | approved | rejected | executed | cancelled | expired
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    ai_prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Trade(Base):
    """An open or closed position (paper, demo, or live)."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idea_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="paper")  # paper|demo|live
    instrument: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))  # long | short
    strategy: Mapped[str] = mapped_column(String(48), default="")
    entry_price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit_1: Mapped[float] = mapped_column(Float, default=0.0)
    take_profit_2: Mapped[float] = mapped_column(Float, default=0.0)
    initial_risk_aed: Mapped[float] = mapped_column(Float, default=0.0)
    initial_risk_per_unit: Mapped[float] = mapped_column(Float, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pl: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed
    breakeven_moved: Mapped[bool] = mapped_column(Boolean, default=False)
    profit_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    partial_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    management_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    deal_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sltp_update: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    close_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class JournalSnapshot(Base):
    __tablename__ = "journal_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Base):
    """Append-only audit trail of every meaningful system event."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    event: Mapped[str] = mapped_column(String(48))
    instrument: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
