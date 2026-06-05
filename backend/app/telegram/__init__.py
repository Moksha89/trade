"""Telegram alerts (graceful no-op when not configured)."""

from app.telegram.notifier import notify

__all__ = ["notify"]
