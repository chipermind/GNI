"""
Shared exponential backoff retry helper for publish (Telegram and Make).
Max attempts configurable via PUBLISH_MAX_ATTEMPTS env (default 3).
CircuitOpenError: no retry (circuit open, service unavailable).
"""
import os
import time
from typing import Callable, TypeVar

from apps.shared.env_helpers import get_int_env, parse_int

T = TypeVar("T")

PUBLISH_MAX_ATTEMPTS = get_int_env("PUBLISH_MAX_ATTEMPTS", default=3)


def _float_env(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


BACKOFF_BASE = _float_env("PUBLISH_BACKOFF_BASE", 1.0)

# Import for "no retry" check; avoid circular import
def _is_circuit_open(e: Exception) -> bool:
    try:
        from apps.worker.circuit_breaker import CircuitOpenError
        return isinstance(e, CircuitOpenError)
    except ImportError:
        return False


def run_with_retry(
    fn: Callable[[], T],
    max_attempts: int | None = None,
    backoff_base: float | None = None,
) -> tuple[bool, T | Exception, int]:
    """
    Run callable with exponential backoff on exception.
    Returns (success, result_or_exception, attempts_used).
    CircuitOpenError: fail immediately, no retry (service circuit open).
    """
    attempts = max_attempts if max_attempts is not None else PUBLISH_MAX_ATTEMPTS
    base = backoff_base if backoff_base is not None else BACKOFF_BASE
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = fn()
            return True, result, attempt
        except Exception as e:
            last_error = e
            if _is_circuit_open(e):
                return False, e, attempt
        if attempt < attempts:
            time.sleep(base * (2 ** (attempt - 1)))
    return False, last_error or RuntimeError("retry exhausted"), attempts
