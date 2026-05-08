"""Tests for circuit breaker."""
from unittest.mock import MagicMock, patch

import pytest

from apps.worker.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    get_circuit_breaker,
)


def test_circuit_closed_initially():
    """Circuit starts closed."""
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=1.0)
    assert cb.state == CircuitState.CLOSED


def _fail():
    raise ValueError("fail")


def test_circuit_opens_after_threshold():
    """Circuit opens after failure_threshold failures."""
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=10.0)
    cb._redis = None
    for _ in range(3):
        try:
            cb.call(_fail)
        except ValueError:
            pass
    assert cb.state == CircuitState.OPEN


def test_circuit_raises_when_open():
    """Circuit raises CircuitOpenError when open."""
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=10.0)
    cb._redis = None
    for _ in range(2):
        try:
            cb.call(_fail)
        except ValueError:
            pass
    with pytest.raises(CircuitOpenError) as exc_info:
        cb.call(lambda: 42)
    assert exc_info.value.service == "test"


def test_circuit_resets_on_success():
    """Circuit resets failure count on success."""
    cb = CircuitBreaker("test", failure_threshold=5, recovery_timeout=10.0)
    cb._redis = None
    cb.call(lambda: 1 + 1)
    cb._record_failure()
    cb._record_failure()
    cb.call(lambda: 42)
    assert cb._failures == 0
    assert cb.state == CircuitState.CLOSED


def test_get_circuit_breaker_returns_singletons():
    """get_circuit_breaker returns same instance per service."""
    a = get_circuit_breaker("ollama")
    b = get_circuit_breaker("ollama")
    assert a is b
    c = get_circuit_breaker("telegram")
    assert c is not a
