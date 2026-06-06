"""Emergency panel actions. All are deterministic and audited."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.services import accounts, engine
from app.services.audit import log_event
from app.services.settings_store import AI, RISK, get_bot_state, update_group
from app.telegram.notifier import notify


def sync_broker_hedging_mode(enabled: bool) -> bool | None:
    """Best-effort sync of the broker account's hedging mode to our setting.

    Returns the resulting broker mode, or None when not applicable (paper mode)
    or on a broker error — callers treat None as "couldn't confirm" and never
    crash the request over it. Real hedging is impossible unless the broker
    account is in hedging mode (otherwise opposing orders net out).
    """
    if settings.execution_mode == "paper":
        return None
    try:
        from app.broker.capital import get_capital_client

        client = get_capital_client()
        client.ensure_session()
        return client.set_hedging_mode(enabled)
    except Exception:  # noqa: BLE001
        return None


def set_hedging(db: Session, enabled: bool) -> bool | None:
    """Flip the hedging setting (DB) and sync the broker account mode."""
    update_group(db, RISK, {"hedging_enabled": enabled})
    broker_mode = sync_broker_hedging_mode(enabled)
    db.commit()
    log_event(
        db,
        "settings_updated",
        {"group": "risk", "keys": ["hedging_enabled"],
         "hedging_enabled": enabled, "broker_hedging_mode": broker_mode},
    )
    notify(f"{'🛡️ Hedging enabled' if enabled else '🚫 Hedging disabled'}"
           f" (broker mode: {broker_mode})")
    return broker_mode


def stop_bot(db: Session) -> None:
    state = get_bot_state(db)
    state.bot_running = False
    state.auto_trading_enabled = False
    db.commit()
    log_event(db, "emergency_stop", {"action": "stop_bot"})
    notify("🛑 EMERGENCY STOP: bot halted, auto-trading disabled")


def disable_auto(db: Session) -> None:
    state = get_bot_state(db)
    state.auto_trading_enabled = False
    update_group(db, AI, {"auto_mode": False, "allow_open_trades": False})
    db.commit()
    log_event(db, "emergency_stop", {"action": "disable_auto"})
    notify("⏸️ Auto-trading disabled")


def lock_trading_today(db: Session, reason: str = "manual lock") -> None:
    state = get_bot_state(db)
    state.trading_locked = True
    state.lock_reason = reason
    db.commit()
    log_event(db, "emergency_stop", {"action": "lock_today", "reason": reason})
    notify(f"🔒 Trading locked for today: {reason}")


def unlock_trading(db: Session) -> None:
    state = get_bot_state(db)
    state.trading_locked = False
    state.lock_reason = None
    db.commit()
    log_event(db, "trading_unlocked", {})


def close_all_positions(db: Session) -> int:
    from app.market_data.factory import get_provider

    provider = get_provider()
    n = 0
    for trade in accounts.open_trades(db):
        try:
            price = provider.get_quote(trade.instrument).mid
        except Exception:  # noqa: BLE001
            price = trade.current_price or trade.entry_price
        engine.close_trade(db, trade, price, "emergency_close")
        n += 1
    db.commit()
    log_event(db, "emergency_stop", {"action": "close_all", "closed": n})
    notify(f"🚪 Closed all open positions ({n})")
    return n


def disable_hedging(db: Session) -> None:
    set_hedging(db, False)
    log_event(db, "emergency_stop", {"action": "disable_hedging"})


def disconnect_broker(db: Session) -> None:
    state = get_bot_state(db)
    state.broker_connected = False
    db.commit()
    log_event(db, "emergency_stop", {"action": "disconnect_broker"})
    notify("🔌 Broker API disconnected")
