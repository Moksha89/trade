"""System routes: health and a minimal dashboard status stub.

The status payload mirrors the Main Dashboard fields from the build spec so the
frontend can render against a stable shape from Phase 0 onward. Values are
placeholders until the bot loop and broker integration land in later phases.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.security import get_current_user
from app.config import settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str = "ok"
    app_env: str
    execution_mode: str


class DashboardStatus(BaseModel):
    bot_running: bool
    execution_mode: str  # demo | approval | live | paper
    auto_mode_enabled: bool
    hedging_enabled: bool
    broker_connected: bool
    account_balance: float | None
    available_funds: float | None
    today_pl: float | None
    weekly_pl: float | None
    open_trades_count: int
    max_active_trades: int
    current_open_risk: float | None
    daily_loss_limit_used: float | None
    last_ai_decision: str | None
    last_risk_rejection_reason: str | None


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        app_env=settings.app_env,
        execution_mode=settings.execution_mode,
    )


@router.get("/api/dashboard/status", response_model=DashboardStatus)
def dashboard_status(current_user: str = Depends(get_current_user)) -> DashboardStatus:
    # Placeholder values for Phase 0. Real values come from the bot/worker state.
    return DashboardStatus(
        bot_running=False,
        execution_mode=settings.execution_mode,
        auto_mode_enabled=settings.auto_mode_enabled,
        hedging_enabled=settings.hedging_enabled,
        broker_connected=False,
        account_balance=None,
        available_funds=None,
        today_pl=None,
        weekly_pl=None,
        open_trades_count=0,
        max_active_trades=2,
        current_open_risk=None,
        daily_loss_limit_used=None,
        last_ai_decision=None,
        last_risk_rejection_reason=None,
    )
