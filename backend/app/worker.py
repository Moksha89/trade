"""Bot loop worker (Phase 0 stub).

Phase 0 only emits a structured heartbeat so the worker container has a real
entrypoint and Compose topology is exercised. The real scheduler (30s/5m/15m
cadences from the build spec) is implemented in later phases.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from app.config import settings


def heartbeat() -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "worker_heartbeat",
        "execution_mode": settings.execution_mode,
        "auto_mode_enabled": settings.auto_mode_enabled,
        "hedging_enabled": settings.hedging_enabled,
    }
    print(json.dumps(payload), flush=True)


def main() -> None:
    print(json.dumps({"event": "worker_start"}), flush=True)
    while True:
        heartbeat()
        time.sleep(30)


if __name__ == "__main__":
    main()
