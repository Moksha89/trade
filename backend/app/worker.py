"""24/7 bot worker.

Runs the three scheduled cadences from the spec using APScheduler:
  every 30s  → health check + manage open trades
  every 5m   → scan instruments, classify, AI propose, risk evaluate
  every 15m  → journal snapshot + Telegram summary

The scan/auto-execute steps only run while the bot is marked running in
BotState (toggled from the dashboard). Health + management always run so trades
are never left unmanaged.
"""

from __future__ import annotations

import json
import signal
from datetime import datetime, timezone

import time

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal, engine as db_engine, init_db
from app.services import engine
from app.services.settings_store import get_bot_state


def _log(event: str, **detail) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **detail}),
          flush=True)


def _wait_for_db(retries: int = 30, delay: float = 2.0) -> None:
    """Block until the database accepts connections (compose start ordering)."""
    for attempt in range(1, retries + 1):
        try:
            with db_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001
            _log("db_wait", attempt=attempt, error=str(exc)[:120])
            time.sleep(delay)
    raise RuntimeError("database not reachable after retries")


def cycle_30s() -> None:
    with SessionLocal() as db:
        engine.run_health(db)
        engine.manage_open_trades(db)


def cycle_5m() -> None:
    with SessionLocal() as db:
        state = get_bot_state(db)
        if not state.bot_running:
            _log("scan_paused", reason="bot not running")
            return
        ideas = engine.run_scan(db)
        _log("scan_complete", ideas=len(ideas))


def cycle_15m() -> None:
    with SessionLocal() as db:
        engine.save_journal(db)


def main() -> None:
    _wait_for_db()
    init_db()
    _log("worker_start", execution_mode=settings.execution_mode,
         auto_mode_enabled=settings.auto_mode_enabled)
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(cycle_30s, "interval", seconds=30, id="health_manage",
                      max_instances=1, coalesce=True)
    scheduler.add_job(cycle_5m, "interval", minutes=5, id="scan_propose",
                      max_instances=1, coalesce=True)
    scheduler.add_job(cycle_15m, "interval", minutes=15, id="journal",
                      max_instances=1, coalesce=True)

    def _shutdown(*_):
        _log("worker_stop")
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    scheduler.start()


if __name__ == "__main__":
    main()
