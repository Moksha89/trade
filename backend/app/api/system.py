"""System routes: health + live Main Dashboard status."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.security import get_current_user
from app.config import settings
from app.db import get_db
from app.services import accounts
from app.services.settings_store import BROKER_RUNTIME, RISK, get_bot_state, get_group

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str = "ok"
    app_env: str
    execution_mode: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(app_env=settings.app_env, execution_mode=settings.execution_mode)


@router.get("/api/dashboard/status")
def dashboard_status(
    current_user: str = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    state = get_bot_state(db)
    risk = get_group(db, RISK)
    broker_rt = get_group(db, BROKER_RUNTIME)
    s = accounts.stats(db)
    daily_limit = float(risk.get("daily_loss_limit", 150))
    loss_today = max(0.0, -float(s["realized_pl_today"]))
    # Prefer the live broker balance when connected; otherwise the configured
    # (paper) account capital.
    paper_capital = float(risk.get("account_capital", settings.account_start_capital))
    live_balance = broker_rt.get("balance")
    live_available = broker_rt.get("available")
    broker_live = bool(broker_rt.get("connected")) and live_balance is not None
    account_balance = float(live_balance) if broker_live else paper_capital
    available_funds = (
        float(live_available)
        if broker_live and live_available is not None
        else paper_capital + float(s["realized_pl_today"])
    )
    return {
        "bot_running": state.bot_running,
        "execution_mode": settings.execution_mode,
        "auto_mode_enabled": state.auto_trading_enabled,
        "hedging_enabled": settings.hedging_enabled,
        "broker_connected": state.broker_connected,
        "broker_environment": broker_rt.get("environment", settings.capital_environment),
        "trading_locked": state.trading_locked,
        "lock_reason": state.lock_reason,
        "account_balance": account_balance,
        "available_funds": available_funds,
        "today_pl": float(s["realized_pl_today"]),
        "weekly_pl": float(s["realized_pl_week"]),
        "open_trades_count": int(s["open_trades_count"]),
        "max_active_trades": int(risk.get("max_active_trades", 2)),
        "current_open_risk": accounts.current_open_risk(db),
        "max_combined_open_risk": float(risk.get("max_combined_open_risk", 100)),
        "daily_loss_limit": daily_limit,
        "daily_loss_limit_used": round(loss_today, 2),
        "last_ai_decision": state.last_ai_decision,
        "last_risk_rejection_reason": state.last_risk_rejection,
        "last_heartbeat": state.last_heartbeat.isoformat() if state.last_heartbeat else None,
    }
