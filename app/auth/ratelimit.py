"""Simple in-memory login rate limit (one web process; resets on restart)."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable


class LoginRateLimiter:
    """Blocks a client after too many failed logins within a sliding window.

    Two limits apply: per (client IP, username) and per client IP across all usernames.
    """

    def __init__(
        self,
        max_failures_per_user: int = 5,
        max_failures_per_ip: int = 20,
        window_seconds: float = 15 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = {"user": max_failures_per_user, "ip": max_failures_per_ip}
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[tuple[str, ...], deque[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(ip: str, username: str) -> dict[str, tuple[str, ...]]:
        return {"user": ("user", ip, username.strip().lower()), "ip": ("ip", ip)}

    def _prune(self, key: tuple[str, ...], now: float) -> deque[float]:
        entries = self._failures.get(key)
        if entries is None:
            return deque()
        while entries and entries[0] <= now - self._window:
            entries.popleft()
        if not entries:
            del self._failures[key]
        return entries

    def retry_after(self, ip: str, username: str) -> int:
        """Seconds until a new attempt is allowed; 0 if not blocked."""
        now = self._clock()
        wait = 0.0
        with self._lock:
            for kind, key in self._keys(ip, username).items():
                entries = self._prune(key, now)
                limit = self._limits[kind]
                if len(entries) >= limit:
                    # The attempt that brings the count below the limit expires first.
                    oldest_relevant = entries[len(entries) - limit]
                    wait = max(wait, oldest_relevant + self._window - now)
        return math.ceil(wait)

    def register_failure(self, ip: str, username: str) -> None:
        now = self._clock()
        with self._lock:
            for key in self._keys(ip, username).values():
                self._failures.setdefault(key, deque()).append(now)

    def register_success(self, ip: str, username: str) -> None:
        with self._lock:
            self._failures.pop(self._keys(ip, username)["user"], None)
