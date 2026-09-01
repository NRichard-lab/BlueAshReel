from __future__ import annotations

import hashlib
import hmac
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _AttemptState:
    failures: int
    window_started: float
    blocked_until: float


class LoginRateLimiter:
    """Bounded process-local backoff cache; identifiers are keyed digests."""

    def __init__(
        self,
        *,
        max_entries: int = 2048,
        failure_threshold: int = 5,
        window_seconds: int = 15 * 60,
        max_backoff_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max_entries
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.clock = clock
        self._states: OrderedDict[str, _AttemptState] = OrderedDict()
        self._lock = threading.Lock()

    def retry_after(self, identifier: str) -> int | None:
        now = self.clock()
        with self._lock:
            state = self._states.get(identifier)
            if state is None:
                return None
            if now - state.window_started >= self.window_seconds:
                self._states.pop(identifier, None)
                return None
            self._states.move_to_end(identifier)
            if state.blocked_until <= now:
                return None
            return max(1, math.ceil(state.blocked_until - now))

    def record_failure(self, identifier: str) -> None:
        now = self.clock()
        with self._lock:
            state = self._states.get(identifier)
            if state is None or now - state.window_started >= self.window_seconds:
                state = _AttemptState(failures=0, window_started=now, blocked_until=0)
                self._states[identifier] = state
            state.failures += 1
            if state.failures >= self.failure_threshold:
                delay = min(
                    self.max_backoff_seconds,
                    2 ** (state.failures - self.failure_threshold),
                )
                state.blocked_until = now + delay
            self._states.move_to_end(identifier)
            while len(self._states) > self.max_entries:
                self._states.popitem(last=False)

    def record_success(self, identifier: str) -> None:
        with self._lock:
            self._states.pop(identifier, None)

    def clear(self) -> None:
        with self._lock:
            self._states.clear()


def _keyed_identifier(secret_key: str, namespace: str, *parts: str) -> str:
    """Return a non-reversible cache key without retaining login identifiers."""
    normalized = "\0".join((namespace, *(part.strip().casefold() for part in parts))).encode()
    return hmac.new(secret_key.encode("utf-8"), normalized, hashlib.sha256).hexdigest()


def login_attempt_key(secret_key: str, username: str, client_host: str) -> str:
    return _keyed_identifier(secret_key, "login-user-host-v1", username, client_host)


def login_host_key(secret_key: str, client_host: str) -> str:
    """Key a host-wide budget so rotating usernames cannot evade throttling."""
    return _keyed_identifier(secret_key, "login-host-v1", client_host)


login_rate_limiter = LoginRateLimiter()
