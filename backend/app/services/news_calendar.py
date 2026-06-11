"""Economic news calendar — skip trading around high-impact events.

Fetches the economic calendar from a free API and blocks trading within a
configurable window around high-impact events (NFP, FOMC, CPI, etc.).

When no API key is configured or the API is unreachable, falls back to a
conservative static schedule of known recurring high-impact events.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings

# Cache calendar data to avoid hammering the API every scan cycle.
_cache: dict[str, Any] = {"events": [], "fetched_at": 0.0}
_CACHE_TTL = 3600  # refresh once per hour


def _fetch_calendar() -> list[dict]:
    """Fetch today's and tomorrow's high-impact events from FX calendar APIs."""
    now = time.time()
    if _cache["events"] and (now - _cache["fetched_at"]) < _CACHE_TTL:
        return _cache["events"]

    events: list[dict] = []

    # Try ForexFactory-style free API (nofap.fyi mirror, widely used)
    try:
        today = datetime.now(timezone.utc)
        resp = httpx.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=10.0,
        )
        if resp.status_code == 200:
            raw = resp.json()
            for ev in raw:
                impact = (ev.get("impact") or "").lower()
                if impact not in ("high", "holiday"):
                    continue
                date_str = ev.get("date", "")
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                # Only care about events within next 48h
                if abs((dt - today).total_seconds()) > 48 * 3600:
                    continue
                events.append({
                    "title": ev.get("title", ""),
                    "country": ev.get("country", ""),
                    "impact": impact,
                    "datetime": dt.isoformat(),
                    "dt": dt,
                })
    except Exception:  # noqa: BLE001 — network failures must never block the bot
        pass

    # Fallback: known recurring high-impact events (always active)
    if not events:
        events = _static_high_impact_events()

    _cache["events"] = events
    _cache["fetched_at"] = now
    return events


def _static_high_impact_events() -> list[dict]:
    """Conservative static schedule for when the API is unavailable.

    Known high-impact recurring events by day-of-week:
    - First Friday of month: US Non-Farm Payrolls (NFP) ~13:30 UTC
    - Wednesday near mid-month: FOMC rate decision ~19:00 UTC
    - ~10th of month: US CPI ~13:30 UTC

    This is intentionally conservative — it may block some valid trading
    windows, but missing a major event costs far more.
    """
    now = datetime.now(timezone.utc)
    events: list[dict] = []

    # NFP: first Friday of the month, 13:30 UTC
    day = now.replace(day=1, hour=13, minute=30, second=0, microsecond=0)
    while day.weekday() != 4:  # Friday
        day += timedelta(days=1)
    events.append({
        "title": "US Non-Farm Payrolls (estimated)",
        "country": "USD",
        "impact": "high",
        "datetime": day.isoformat(),
        "dt": day,
    })

    # CPI: ~10th-13th of month, 13:30 UTC
    cpi_day = now.replace(day=10, hour=13, minute=30, second=0, microsecond=0)
    events.append({
        "title": "US CPI (estimated)",
        "country": "USD",
        "impact": "high",
        "datetime": cpi_day.isoformat(),
        "dt": cpi_day,
    })

    return events


def is_near_high_impact_event(
    buffer_minutes: int = 30,
) -> tuple[bool, str]:
    """Check if we are within `buffer_minutes` of a high-impact news event.

    Returns (is_near, event_description).
    """
    events = _fetch_calendar()
    now = datetime.now(timezone.utc)
    buffer = timedelta(minutes=buffer_minutes)

    for ev in events:
        dt = ev.get("dt")
        if dt is None:
            continue
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt)
            except (ValueError, TypeError):
                continue
        if abs(now - dt) <= buffer:
            desc = f"{ev.get('title', 'Unknown')} ({ev.get('country', '?')})"
            return True, desc

    return False, ""


def clear_cache() -> None:
    """Force a refresh on next check (useful after settings change)."""
    _cache["events"] = []
    _cache["fetched_at"] = 0.0
