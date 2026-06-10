"""Audit trail + structured JSON logging helper.

Every meaningful event is (a) printed as a single JSON line to stdout (picked up
by Docker / log shippers) and (b) persisted to the `audit_logs` table.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def log_event(
    db: Session,
    event: str,
    detail: dict[str, Any] | None = None,
    instrument: str | None = None,
    commit: bool = True,
) -> AuditLog:
    detail = detail or {}
    print(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "instrument": instrument,
                "detail": detail,
            },
            default=str,
        ),
        flush=True,
    )
    row = AuditLog(event=event, instrument=instrument, detail=detail)
    db.add(row)
    if commit:
        db.commit()
    return row
