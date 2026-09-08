"""In-memory stand-in for uber_clone's Bucket4j-based RateLimiterService.kt.

Fixed-window counter, not a true token bucket — close enough to prove the "N requests per
window, per key" behavior for M1. Likely replaced by a Redis-backed limiter in M3 (Celery/
Channels for the async pieces land first — see MILESTONE_NOTES.md), which would also make rate
limits work across multiple instances (this one doesn't).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Window:
    count: int
    window_start: float


class RateLimiter:
    def __init__(self, capacity: int, window_seconds: float) -> None:
        self._capacity = capacity
        self._window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if window is None or now - window.window_start >= self._window_seconds:
                self._windows[key] = _Window(count=1, window_start=now)
                return True
            if window.count >= self._capacity:
                return False
            window.count += 1
            return True

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()
