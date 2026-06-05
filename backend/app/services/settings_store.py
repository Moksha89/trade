"""DB-backed, runtime-editable settings groups (risk, strategy, ai).

Each group is stored as one JSON row in the `settings` table keyed by group
name. Defaults are seeded from environment-driven `Settings` on first run.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import BotState, SettingRow

RISK = "risk"
STRATEGY = "strategy"
AI = "ai"


def default_risk() -> dict[str, Any]:
    return {
        "max_active_trades": env.max_active_trades,
        "max_risk_per_trade": env.max_risk_per_trade,
        "max_combined_open_risk": env.max_combined_open_risk,
        "max_trades_per_day": env.max_trades_per_day,
        "daily_loss_limit": env.daily_max_loss,
        "weekly_loss_limit": env.weekly_max_loss,
        "stop_after_n_losses": env.stop_after_n_losses,
        "min_risk_reward": env.min_risk_reward,
        "min_confidence": env.ai_min_confidence,
        "allowed_instruments": ["US100", "US500", "Gold"],
        "max_spread_points": 5.0,
        "max_sl_distance_pct": 2.0,
        "news_filter_enabled": True,
        "market_hours_filter_enabled": True,
        "account_capital": env.account_start_capital,
    }


def default_strategy() -> dict[str, Any]:
    return {
        "allow_bullish": True,
        "allow_bearish": True,
        "trend_pullback": True,
        "breakout_retest": True,
        "breakdown_retest": True,
        "range_reversal": True,
        "momentum_continuation": False,
        "multi_timeframe_confirmation": True,
        "timeframes": ["15M", "1H", "4H", "Daily"],
        "trailing_method": "atr",  # swing | ema20 | atr
        "move_be_at_r": 1.0,
        "lock_profit_at_r": 1.5,
        "partial_close_at_r": 2.0,
        "partial_close_percent": 50,
        "auto_close_on_reversal": True,
    }


def default_ai() -> dict[str, Any]:
    return {
        "provider": env.ai_provider,
        "model": env.anthropic_model,
        "confidence_threshold": env.ai_min_confidence,
        "call_frequency_seconds": 300,
        "allow_create_ideas": True,
        "allow_manage_sltp": True,
        "allow_open_trades": False,
        "require_approval": True,
        "auto_mode": False,
    }


_DEFAULTS = {RISK: default_risk, STRATEGY: default_strategy, AI: default_ai}


def seed_defaults(db: Session) -> None:
    for key, factory in _DEFAULTS.items():
        if db.get(SettingRow, key) is None:
            db.add(SettingRow(key=key, value=factory()))
    if db.get(BotState, 1) is None:
        db.add(BotState(id=1, auto_trading_enabled=env.auto_mode_enabled))


def get_group(db: Session, key: str) -> dict[str, Any]:
    row = db.get(SettingRow, key)
    if row is None:
        value = _DEFAULTS[key]()
        db.add(SettingRow(key=key, value=value))
        db.commit()
        return value
    return dict(row.value)


def update_group(db: Session, key: str, patch: dict[str, Any]) -> dict[str, Any]:
    row = db.get(SettingRow, key)
    if row is None:
        merged = {**_DEFAULTS[key](), **patch}
        row = SettingRow(key=key, value=merged)
        db.add(row)
    else:
        merged = {**row.value, **patch}
        row.value = merged
    db.commit()
    return merged


def get_bot_state(db: Session) -> BotState:
    state = db.get(BotState, 1)
    if state is None:
        state = BotState(id=1, auto_trading_enabled=env.auto_mode_enabled)
        db.add(state)
        db.commit()
    return state
