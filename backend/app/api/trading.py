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
from app.models import AuditLog, JournalSnapshot, ShadowDecision, Trade, TradeIdea
from app.services import accounts, emergency, engine
from app.services.audit import log_event
from app.services.live_pricing import live_mark
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
    trades = list(db.scalars(stmt))
    # Re-price open trades live on read so uP/L and current price move every poll
    # instead of only every 30s when the worker re-marks. In-memory only (the
    # request session is never committed).
    for t in trades:
        if t.status == "open":
            live_mark(t)
    return [trade_to_dict(t) for t in trades]


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


# ---- AI shadow comparison (Claude vs local Ollama) -----------------------
def _shadow_to_dict(r: ShadowDecision) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "instrument": r.instrument,
        "market_classification": r.market_classification,
        "agree": r.agree,
        "claude": {
            "direction": r.claude_direction,
            "strategy": r.claude_strategy,
            "confidence": r.claude_confidence,
            "risk_reward": r.claude_risk_reward,
            "entry": r.claude_entry,
            "stop_loss": r.claude_stop_loss,
            "take_profit_1": r.claude_take_profit_1,
            "latency_ms": r.claude_latency_ms,
        },
        "ollama": {
            "model": r.ollama_model,
            "direction": r.ollama_direction,
            "strategy": r.ollama_strategy,
            "confidence": r.ollama_confidence,
            "risk_reward": r.ollama_risk_reward,
            "entry": r.ollama_entry,
            "stop_loss": r.ollama_stop_loss,
            "take_profit_1": r.ollama_take_profit_1,
            "latency_ms": r.ollama_latency_ms,
            "error": r.ollama_error,
        },
    }


@router.get("/ai-comparison")
def ai_comparison(limit: int = 100, db: Session = Depends(get_db)) -> dict:
    """Side-by-side Claude vs local-model (Ollama) decisions + rollup metrics."""
    from app.ai.ollama_engine import ollama_health

    rows = list(
        db.scalars(select(ShadowDecision).order_by(desc(ShadowDecision.id)).limit(limit))
    )
    total = len(rows)
    ok = [r for r in rows if not r.ollama_error]
    errors = total - len(ok)
    agree = sum(1 for r in ok if r.agree)

    def _is_trade(d: str) -> bool:
        return d in ("long", "short")

    both_trade = sum(1 for r in ok if _is_trade(r.claude_direction) and _is_trade(r.ollama_direction))
    both_no_trade = sum(
        1 for r in ok if not _is_trade(r.claude_direction) and not _is_trade(r.ollama_direction)
    )
    claude_only = sum(1 for r in ok if _is_trade(r.claude_direction) and not _is_trade(r.ollama_direction))
    ollama_only = sum(1 for r in ok if not _is_trade(r.claude_direction) and _is_trade(r.ollama_direction))

    def _avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    summary = {
        "total": total,
        "comparable": len(ok),
        "errors": errors,
        "agree": agree,
        "agreement_rate": round(100 * agree / len(ok), 1) if ok else 0.0,
        "both_trade": both_trade,
        "both_no_trade": both_no_trade,
        "claude_trade_only": claude_only,
        "ollama_trade_only": ollama_only,
        "avg_claude_latency_ms": _avg([r.claude_latency_ms for r in ok]),
        "avg_ollama_latency_ms": _avg([r.ollama_latency_ms for r in ok]),
        "avg_claude_confidence": _avg([r.claude_confidence for r in ok if _is_trade(r.claude_direction)]),
        "avg_ollama_confidence": _avg([r.ollama_confidence for r in ok if _is_trade(r.ollama_direction)]),
    }
    ai_cfg = get_group(db, AI)
    return {
        "summary": summary,
        "recent": [_shadow_to_dict(r) for r in rows],
        "enabled": bool(ai_cfg.get("shadow_compare_enabled", False)),
        "shadow_model": ai_cfg.get("shadow_model", settings.ollama_model),
        "ollama": ollama_health(),
    }


# ---- backtest ------------------------------------------------------------
@router.post("/backtest")
def run_backtest_endpoint(
    instrument: str = Body("US100", embed=True),
    bars: int = Body(500, embed=True),
    db: Session = Depends(get_db),
) -> dict:
    from app.backtest.engine import run_backtest
    from app.market_data.factory import get_provider

    candles = get_provider("paper").get_candles(instrument, "15M", bars)
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
