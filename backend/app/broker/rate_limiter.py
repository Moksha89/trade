"""API rate limit manager for Capital.com.

Capital.com enforces rate limits (typically ~10 req/sec for REST).
This module provides a thread-safe token-bucket rate limiter that wraps
API calls and prevents 429 errors.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe token-bucket rate limiter.

    Allows `max_calls` requests per `period` seconds. Callers that would
    exceed the limit block until a token is available.
    """

    def __init__(self, max_calls: int = 8, period: float = 1.0) -> None:
        self.max_calls = max_calls
        self.period = period
        self._lock = threading.Lock()
        self._tokens = float(max_calls)
        self._last_refill = time.monotonic()
        self._total_waits = 0
        self._total_calls = 0

    def acquire(self) -> float:
        """Block until a token is available. Returns the wait time in seconds."""
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    self._total_calls += 1
                    return waited

            # No tokens available — wait for the next refill
            sleep_time = self.period / self.max_calls
            time.sleep(sleep_time)
            waited += sleep_time
            self._total_waits += 1

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * (self.max_calls / self.period)
        self._tokens = min(float(self.max_calls), self._tokens + new_tokens)
        self._last_refill = now

    def stats(self) -> dict:
        """Rate limiter statistics for monitoring."""
        return {
            "max_calls_per_second": self.max_calls / self.period,
            "tokens_available": round(self._tokens, 1),
            "total_calls": self._total_calls,
            "total_waits": self._total_waits,
        }


# Global rate limiter instance for Capital.com API
_capital_limiter: RateLimiter | None = None
_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """Get (or create) the global Capital.com rate limiter."""
    global _capital_limiter
    if _capital_limiter is None:
        with _limiter_lock:
            if _capital_limiter is None:
                _capital_limiter = RateLimiter(max_calls=8, period=1.0)
    return _capital_limiter
