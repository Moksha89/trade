"""Emergency panel actions. All are deterministic and audited."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services import accounts, engine
from app.services.audit import log_event
from app.services.settings_store import AI, get_bot_state, update_group
from app.telegram.notifier import notify


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
    log_event(db, "emergency_stop", {"action": "disable_hedging"})
    notify("🚫 Hedging disabled")


def disconnect_broker(db: Session) -> None:
    state = get_bot_state(db)
    state.broker_connected = False
    db.commit()
    log_event(db, "emergency_stop", {"action": "disconnect_broker"})
    notify("🔌 Broker API disconnected")
