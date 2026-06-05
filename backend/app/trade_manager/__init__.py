"""Deterministic live-trade management (SL/TP movement, partials, trailing)."""

from app.trade_manager.engine import ManagementActions, compute_management_actions

__all__ = ["ManagementActions", "compute_management_actions"]
