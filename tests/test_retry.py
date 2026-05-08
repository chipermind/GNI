"""Unit tests for shared retry helper (exponential backoff)."""
from unittest.mock import MagicMock

import pytest

from apps.worker.retry import run_with_retry


def test_run_with_retry_success_first_try():
    """run_with_retry returns (True, result, 1) when callable succeeds on first try."""
    ok, result, attempts = run_with_retry(lambda: 42, max_attempts=3)
    assert ok is True
    assert result == 42
    assert attempts == 1


def test_run_with_retry_success_after_failures():
    """run_with_retry retries and returns success when callable eventually succeeds."""
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("fail")
        return "ok"

    ok, result, attempts = run_with_retry(fn, max_attempts=3)
    assert ok is True
    assert result == "ok"
    assert attempts == 2
    assert len(calls) == 2


def test_run_with_retry_exhausted_returns_last_error():
    """run_with_retry returns (False, last_exception, max_attempts) when all attempts fail."""
    def fn():
        raise ValueError("always fail")

    ok, result, attempts = run_with_retry(fn, max_attempts=2)
    assert ok is False
    assert isinstance(result, ValueError)
    assert str(result) == "always fail"
    assert attempts == 2


def test_run_with_retry_no_retry_on_circuit_open():
    """run_with_retry does not retry on CircuitOpenError; fails immediately."""
    from apps.worker.circuit_breaker import CircuitOpenError

    calls = []

    def fn():
        calls.append(1)
        raise CircuitOpenError("test")

    ok, result, attempts = run_with_retry(fn, max_attempts=5)
    assert ok is False
    assert isinstance(result, CircuitOpenError)
    assert result.service == "test"
    assert attempts == 1
    assert len(calls) == 1
