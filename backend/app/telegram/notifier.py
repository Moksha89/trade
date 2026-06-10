"""Send Telegram messages via the Bot API.

If no bot token / chat id is configured, this is a no-op so the system runs fine
in paper mode and tests without Telegram. Failures never raise into the caller.
"""

from __future__ import annotations

import httpx

from app.config import settings


def notify(text: str) -> bool:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10.0,
        )
        return resp.status_code == 200
    except Exception:  # noqa: BLE001 — alerts must never crash the bot
        return False
