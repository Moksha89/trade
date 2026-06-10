"""Strict schema for AI trade proposals.

Claude must return JSON matching this schema exactly. Anything else is rejected
and never executed.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    NO_TRADE = "no_trade"


class Strategy(str, Enum):
    TREND_PULLBACK = "trend_pullback"
    BREAKOUT_RETEST = "breakout_retest"
    BREAKDOWN_RETEST = "breakdown_retest"
    RANGE_REVERSAL = "range_reversal"
    MOMENTUM_CONTINUATION = "momentum_continuation"
    NO_TRADE = "no_trade"


class EntryType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class ManagementPlan(BaseModel):
    move_sl_to_breakeven_at_R: float = 0.7
    lock_profit_at_R: float = 1.0
    lock_profit_offset_R: float = 0.3
    partial_close_at_R: float = 2.0
    partial_close_percent: float = 50
    trail_start_R: float = 2.0  # begin ATR trailing once the first target (~TP1) is reached
    trailing_method: str = "atr"  # swing | ema20 | atr


class TradeProposal(BaseModel):
    instrument: str
    direction: Direction
    strategy: Strategy
    entry_type: EntryType = EntryType.MARKET
    entry_price: float = 0
    stop_loss: float = 0
    take_profit_1: float = 0
    take_profit_2: float = 0
    confidence: float = 0
    risk_reward: float = 0
    position_size: float = 0
    rationale: str = ""
    invalidation_condition: str = ""
    risk_flags: list[str] = Field(default_factory=list)
    management_plan: ManagementPlan = Field(default_factory=ManagementPlan)

    @property
    def is_trade(self) -> bool:
        return self.direction in (Direction.LONG, Direction.SHORT)
