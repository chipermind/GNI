"""
Circuit breaker for external calls: Ollama, Telegram, Make.
Prevents cascading failures; opens after repeated failures; recovers automatically.
"""
from __future__ import annotations

import os
import threading
import time
from enum import Enum
from typing import Callable, TypeVar

from apps.shared.env_helpers import get_int_env, parse_int

T = TypeVar("T")

# Config
FAILURE_THRESHOLD = get_int_env("CIRCUIT_FAILURE_THRESHOLD", default=5)
def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


RECOVERY_TIMEOUT = _float_env("CIRCUIT_RECOVERY_TIMEOUT", 60.0)


class CircuitState(str, Enum):
    CLOSED = "closed"      # Normal; failures increment counter
    OPEN = "open"          # Failing fast; no calls
    HALF_OPEN = "half_open"  # Test call allowed


class CircuitOpenError(Exception):
    """Raised when circuit is open and call is rejected."""

    def __init__(self, service: str, message: str = ""):
        self.service = service
        super().__init__(message or f"Circuit open for {service}")


def _get_redis():
    try:
        import redis
        url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        return redis.Redis.from_url(url)
    except Exception:
        return None


class CircuitBreaker:
    """
    In-memory circuit breaker with optional Redis backend.
    Thread-safe; shared across workers when Redis used.
    """

    def __init__(
        self,
        service: str,
        failure_threshold: int = FAILURE_THRESHOLD,
        recovery_timeout: float = RECOVERY_TIMEOUT,
    ):
        self.service = service
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._lock = threading.Lock()
        self._failures = 0
        self._last_failure_time: float | None = None
        self._state = CircuitState.CLOSED
        self._redis = _get_redis()
        self._key_prefix = f"cb:{service}"

    def _load_from_redis(self) -> None:
        if not self._redis:
            return
        try:
            failures = self._redis.get(f"{self._key_prefix}:failures")
            opened_at = self._redis.get(f"{self._key_prefix}:opened_at")
            if failures is not None:
                self._failures = int(failures)
            if opened_at is not None:
                self._last_failure_time = float(opened_at)
            state = self._redis.get(f"{self._key_prefix}:state")
            if state:
                self._state = CircuitState(state.decode())
        except Exception:
            pass

    def _save_to_redis(self) -> None:
        if not self._redis:
            return
        try:
            pipe = self._redis.pipeline()
            pipe.set(f"{self._key_prefix}:failures", self._failures, ex=3600)
            pipe.set(f"{self._key_prefix}:state", self._state.value, ex=3600)
            if self._last_failure_time is not None:
                pipe.set(f"{self._key_prefix}:opened_at", str(self._last_failure_time), ex=3600)
            else:
                pipe.delete(f"{self._key_prefix}:opened_at")
            pipe.execute()
        except Exception:
            pass

    def _reset_to_redis(self) -> None:
        if not self._redis:
            return
        try:
            self._redis.delete(
                f"{self._key_prefix}:failures",
                f"{self._key_prefix}:opened_at",
                f"{self._key_prefix}:state",
            )
        except Exception:
            pass

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._load_from_redis()
            now = time.time()
            if self._state == CircuitState.OPEN:
                if self._last_failure_time is not None:
                    if now - self._last_failure_time >= self.recovery_timeout:
                        self._state = CircuitState.HALF_OPEN
                        self._save_to_redis()
            return self._state

    def call(self, fn: Callable[[], T]) -> T:
        """
        Execute fn through circuit. On open: raise CircuitOpenError.
        On success: record success (reset). On failure: record failure.
        """
        state = self.state
        if state == CircuitState.OPEN:
            raise CircuitOpenError(self.service)
        try:
            result = fn()
            self._record_success()
            return result
        except Exception as e:
            self._record_failure()
            raise

    def _record_success(self) -> None:
        with self._lock:
            self._load_from_redis()
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._last_failure_time = None
            self._save_to_redis()

    def _record_failure(self) -> None:
        with self._lock:
            self._load_from_redis()
            self._failures += 1
            self._last_failure_time = time.time()
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
            self._save_to_redis()


# Per-service instances
_ollama_cb: CircuitBreaker | None = None
_telegram_cb: CircuitBreaker | None = None
_make_cb: CircuitBreaker | None = None
_cb_lock = threading.Lock()


def get_circuit_breaker(service: str) -> CircuitBreaker:
    global _ollama_cb, _telegram_cb, _make_cb
    with _cb_lock:
        if service == "ollama":
            if _ollama_cb is None:
                _ollama_cb = CircuitBreaker("ollama")
            return _ollama_cb
        if service == "telegram":
            if _telegram_cb is None:
                _telegram_cb = CircuitBreaker("telegram")
            return _telegram_cb
        if service == "make":
            if _make_cb is None:
                _make_cb = CircuitBreaker("make")
            return _make_cb
    return CircuitBreaker(service)
