"""Trading API: bot control, ideas, trades, settings, journal, logs, backtest, emergency."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.auth.security import get_current_user
from app.api.serializers import (
    audit_to_dict,
    idea_to_dict,
    journal_to_dict,
    trade_to_dict,
)
from app.config import settings
from app.db import get_db
from app.models import AuditLog, JournalSnapshot, Trade, TradeIdea
from app.services import accounts, emergency, engine
from app.services.audit import log_event
from app.services.settings_store import (
    AI,
    BROKER_RUNTIME,
    RISK,
    STRATEGY,
    get_bot_state,
    get_group,
    update_group,
)

router = APIRouter(prefix="/api", tags=["trading"], dependencies=[Depends(get_current_user)])


# ---- bot control ---------------------------------------------------------
@router.get("/bot/state")
def bot_state(db: Session = Depends(get_db)) -> dict:
    s = get_bot_state(db)
    return {
        "bot_running": s.bot_running,
        "auto_trading_enabled": s.auto_trading_enabled,
        "trading_locked": s.trading_locked,
        "lock_reason": s.lock_reason,
        "broker_connected": s.broker_connected,
        "execution_mode": settings.execution_mode,
        "last_heartbeat": s.last_heartbeat.isoformat() if s.last_heartbeat else None,
    }


@router.post("/bot/start")
def bot_start(db: Session = Depends(get_db)) -> dict:
    s = get_bot_state(db)
    s.bot_running = True
    db.commit()
    log_event(db, "bot_started", {})
    return {"bot_running": True}


@router.post("/bot/stop")
def bot_stop(db: Session = Depends(get_db)) -> dict:
    emergency.stop_bot(db)
    return {"bot_running": False}


def _run_scan_background() -> None:
    """Run a full universe scan on its own DB session (off the request path)."""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        engine.run_scan(db)
    finally:
        db.close()


@router.post("/bot/scan")
def bot_scan() -> dict:
    """Trigger a scan/propose cycle immediately (manual).

    A full scan iterates the whole instrument universe and calls the AI for
    each, which takes far longer than the gateway timeout. So we kick it off in
    a background thread and return right away; results land via the normal
    ideas/trades feeds.
    """
    threading.Thread(target=_run_scan_background, daemon=True).start()
    return {"started": True}


@router.post("/bot/manage")
def bot_manage(db: Session = Depends(get_db)) -> dict:
    engine.manage_open_trades(db)
    return {"ok": True}


# ---- ideas ---------------------------------------------------------------
@router.get("/ideas")
def list_ideas(limit: int = 50, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(TradeIdea).order_by(desc(TradeIdea.id)).limit(limit))
    return [idea_to_dict(i) for i in rows]


@router.post("/ideas/{idea_id}/approve")
def approve_idea(idea_id: int, db: Session = Depends(get_db)) -> dict:
    idea = db.get(TradeIdea, idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")
    if not idea.risk_approved:
        raise HTTPException(400, f"risk engine rejected this idea: {idea.risk_reason}")
    trade = engine.execute_idea(db, idea)
    if not trade:
        raise HTTPException(400, f"execution failed: {idea.risk_reason}")
    return {"trade": trade_to_dict(trade)}


@router.post("/ideas/{idea_id}/reject")
def reject_idea(idea_id: int, db: Session = Depends(get_db)) -> dict:
    idea = db.get(TradeIdea, idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")
    idea.status = "rejected"
    db.commit()
    log_event(db, "idea_rejected_by_user", {"idea_id": idea_id})
    return {"ok": True}


# ---- trades --------------------------------------------------------------
@router.get("/trades")
def list_trades(status: str | None = None, limit: int = 100, db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(Trade).order_by(desc(Trade.id)).limit(limit)
    if status:
        stmt = select(Trade).where(Trade.status == status).order_by(desc(Trade.id)).limit(limit)
    return [trade_to_dict(t) for t in db.scalars(stmt)]


@router.post("/trades/{trade_id}/close")
def close_trade(trade_id: int, db: Session = Depends(get_db)) -> dict:
    trade = db.get(Trade, trade_id)
    if not trade or trade.status != "open":
        raise HTTPException(404, "open trade not found")
    from app.market_data.factory import get_provider

    try:
        price = get_provider().get_quote(trade.instrument).mid
    except Exception:  # noqa: BLE001
        price = trade.current_price or trade.entry_price
    engine.close_trade(db, trade, price, "manual_close")
    db.commit()
    return {"trade": trade_to_dict(trade)}


@router.post("/trades/{trade_id}/move-sl")
def move_sl(trade_id: int, stop_loss: float = Body(..., embed=True), db: Session = Depends(get_db)) -> dict:
    trade = db.get(Trade, trade_id)
    if not trade or trade.status != "open":
        raise HTTPException(404, "open trade not found")
    from app.execution.factory import get_executor

    res = get_executor().modify(trade.deal_id, stop_loss, None)
    if not res.ok:
        log_event(db, "sltp_move_failed", {"trade_id": trade_id, "intended_sl": stop_loss, "error": res.error, "manual": True})
        db.commit()
        raise HTTPException(502, f"broker rejected stop move: {res.error}")
    trade.stop_loss = stop_loss
    trade.last_sltp_update = engine._utcnow()
    log_event(db, "sltp_moved", {"trade_id": trade_id, "new_sl": stop_loss, "manual": True})
    db.commit()
    return {"trade": trade_to_dict(trade)}


@router.post("/trades/{trade_id}/move-tp")
def move_tp(trade_id: int, take_profit: float = Body(..., embed=True), db: Session = Depends(get_db)) -> dict:
    trade = db.get(Trade, trade_id)
    if not trade or trade.status != "open":
        raise HTTPException(404, "open trade not found")
    from app.execution.factory import get_executor

    res = get_executor().modify(trade.deal_id, None, take_profit)
    if not res.ok:
        log_event(db, "sltp_move_failed", {"trade_id": trade_id, "intended_tp": take_profit, "error": res.error, "manual": True})
        db.commit()
        raise HTTPException(502, f"broker rejected target move: {res.error}")
    trade.take_profit_1 = take_profit
    trade.last_sltp_update = engine._utcnow()
    log_event(db, "sltp_moved", {"trade_id": trade_id, "new_tp": take_profit, "manual": True})
    db.commit()
    return {"trade": trade_to_dict(trade)}


# ---- settings ------------------------------------------------------------
@router.get("/settings/{group}")
def get_settings(group: str, db: Session = Depends(get_db)) -> dict:
    if group not in (RISK, STRATEGY, AI):
        raise HTTPException(404, "unknown settings group")
    return get_group(db, group)


@router.put("/settings/{group}")
def put_settings(group: str, patch: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict:
    if group not in (RISK, STRATEGY, AI):
        raise HTTPException(404, "unknown settings group")
    merged = update_group(db, group, patch)
    broker_hedging = None
    if group == RISK and "hedging_enabled" in patch:
        # Real hedging needs the broker account in hedging mode; sync it so the
        # panel toggle actually changes broker behavior (not just the label).
        broker_hedging = emergency.sync_broker_hedging_mode(bool(patch["hedging_enabled"]))
    log_event(
        db, "settings_updated",
        {"group": group, "keys": list(patch.keys()), "broker_hedging_mode": broker_hedging},
    )
    return merged


# ---- journal / logs ------------------------------------------------------
@router.get("/journal")
def list_journal(limit: int = 50, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(JournalSnapshot).order_by(desc(JournalSnapshot.id)).limit(limit))
    return [journal_to_dict(j) for j in rows]


@router.get("/logs")
def list_logs(limit: int = 200, event: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(AuditLog).order_by(desc(AuditLog.id)).limit(limit)
    if event:
        stmt = select(AuditLog).where(AuditLog.event == event).order_by(desc(AuditLog.id)).limit(limit)
    return [audit_to_dict(a) for a in db.scalars(stmt)]


# ---- backtest ------------------------------------------------------------
@router.post("/backtest")
def run_backtest_endpoint(
    instrument: str = Body("US100", embed=True),
    bars: int = Body(500, embed=True),
    db: Session = Depends(get_db),
) -> dict:
    from app.backtest.engine import run_backtest
    from app.market_data.factory import get_provider

    candles = get_provider("paper").get_candles(instrument, "5M", bars)
    report = run_backtest(instrument, candles, get_group(db, RISK), get_group(db, STRATEGY))
    log_event(db, "backtest_run", {"instrument": instrument, "trades": report.trades})
    return report.as_dict()


# ---- emergency -----------------------------------------------------------
@router.post("/emergency/{action}")
def emergency_action(action: str, db: Session = Depends(get_db)) -> dict:
    actions = {
        "stop-bot": lambda: emergency.stop_bot(db),
        "disable-auto": lambda: emergency.disable_auto(db),
        "lock-today": lambda: emergency.lock_trading_today(db),
        "unlock": lambda: emergency.unlock_trading(db),
        "close-all": lambda: emergency.close_all_positions(db),
        "disable-hedging": lambda: emergency.disable_hedging(db),
        "disconnect-broker": lambda: emergency.disconnect_broker(db),
    }
    fn = actions.get(action)
    if not fn:
        raise HTTPException(404, "unknown emergency action")
    result = fn()
    return {"ok": True, "action": action, "result": result}


# ---- broker connection ---------------------------------------------------
@router.get("/broker/status")
def broker_status(db: Session = Depends(get_db)) -> dict:
    from app.broker.capital import get_capital_client

    client = get_capital_client()
    state = get_bot_state(db)
    broker_rt = get_group(db, BROKER_RUNTIME)
    info: dict[str, Any] = {
        "environment": settings.capital_environment,
        "configured": client.configured,
        "connected": state.broker_connected,
        "identifier_set": bool(settings.capital_identifier),
        "api_key_set": bool(settings.capital_api_key),
        "execution_mode": settings.execution_mode,
        "balance": broker_rt.get("balance"),
        "available": broker_rt.get("available"),
        "synced_at": broker_rt.get("synced_at"),
    }
    return info


@router.post("/broker/reconnect")
def broker_reconnect(db: Session = Depends(get_db)) -> dict:
    from app.broker.capital import CapitalError, get_capital_client

    client = get_capital_client()
    if not client.configured:
        raise HTTPException(400, "Capital.com credentials not configured (need identifier)")
    try:
        client.login()
        balance = client.get_account_balance()
        state = get_bot_state(db)
        state.broker_connected = True
        db.commit()
        log_event(db, "broker_connected", {"balance": balance})
        return {"connected": True, "balance": balance}
    except CapitalError as exc:
        raise HTTPException(502, f"connect failed: {exc}") from exc
