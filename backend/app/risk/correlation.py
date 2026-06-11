"""Correlation guard — prevent doubling up on correlated instruments.

US100/US500/USTEC are essentially the same trade. Opening both doubles
your effective risk without doubling the diversification.  This module
defines known correlation groups and blocks new positions that would
overlap with existing open trades in the same group.
"""

from __future__ import annotations

from typing import Any

# Correlation groups: instruments within the same group are treated as
# effectively the same market.  A position in any member blocks new
# positions in the same group (unless hedging is enabled).
CORRELATION_GROUPS: list[set[str]] = [
    {"US100", "US500", "USTEC", "US30"},  # US equity indices
    {"Gold", "Silver", "XAUUSD", "XAGUSD"},  # precious metals
    {"EUR/USD", "GBP/USD", "EURUSD", "GBPUSD"},  # major FX (vs USD)
    {"BTC/USD", "ETH/USD", "BTCUSD", "ETHUSD"},  # crypto vs USD
]


def _find_group(instrument: str) -> set[str] | None:
    for group in CORRELATION_GROUPS:
        if instrument in group:
            return group
    return None


def check_correlation(
    instrument: str,
    direction: str,
    open_trades: list[dict[str, Any]],
    enabled: bool = True,
    max_correlated: int = 1,
) -> tuple[bool, str]:
    """Check if opening `instrument` would create an over-concentrated position.

    Returns (allowed, reason).

    - `max_correlated`: how many positions in the same correlation group are
      allowed.  Default 1 means only one instrument per group at a time.
    """
    if not enabled:
        return True, ""

    group = _find_group(instrument)
    if group is None:
        return True, ""

    correlated = [
        t for t in open_trades
        if t.get("instrument") in group and t.get("instrument") != instrument
    ]

    if len(correlated) >= max_correlated:
        conflicting = [t.get("instrument", "?") for t in correlated]
        return False, (
            f"Correlated position: {instrument} blocked — already holding "
            f"{', '.join(conflicting)} in the same group "
            f"(max {max_correlated} per group)"
        )

    return True, ""
