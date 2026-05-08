"""Tests for rate limit module: Redis counters, block + log when exceeded."""
from unittest.mock import MagicMock, patch

import pytest

from apps.publisher.rate_limit import (
    RateLimitExceededError,
    check_rate_limit,
    DEFAULT_PER_HOUR,
    DEFAULT_PER_MINUTE,
    log_rate_limit_event,
)


def test_rate_limit_exceeded_error():
    """RateLimitExceededError holds channel, limit_type, current, limit."""
    e = RateLimitExceededError("telegram", "per_minute", 6, 5)
    assert e.channel == "telegram"
    assert e.limit_type == "per_minute"
    assert e.current == 6
    assert e.limit == 5


def test_get_limits_uses_defaults_when_no_settings():
    """When settings empty, default limits used."""
    limits = None
    from apps.publisher.rate_limit import _get_limits_for_channel
    pm, ph = _get_limits_for_channel({}, "telegram")
    assert pm == DEFAULT_PER_MINUTE
    assert ph == DEFAULT_PER_HOUR


def test_check_rate_limit_raises_when_over_limit():
    """check_rate_limit raises RateLimitExceededError when count >= limit."""
    r = MagicMock()
    r.get.side_effect = ["5", "50"]  # min 5, hour 50
    with patch("apps.publisher.rate_limit._get_redis", return_value=r):
        with pytest.raises(RateLimitExceededError) as exc_info:
            check_rate_limit("telegram", settings={"rate_limits": {"telegram": {"per_minute": 5, "per_hour": 100}}})
    assert exc_info.value.limit_type == "per_minute"
    assert exc_info.value.current == 5
    assert exc_info.value.limit == 5


def test_log_rate_limit_event_adds_events_log():
    """log_rate_limit_event adds EventsLog row to session."""
    from apps.api.db.models import EventsLog
    session = MagicMock()
    added = []

    def capture_add(row):
        added.append(row)

    session.add = capture_add
    log_rate_limit_event(session, "make", "per_hour", 101, 100)
    assert len(added) == 1
    assert isinstance(added[0], EventsLog)
    assert added[0].event_type == "rate_limit_exceeded"
    assert added[0].payload["channel"] == "make"
    assert added[0].payload["limit_type"] == "per_hour"
    assert added[0].payload["current"] == 101
    assert added[0].payload["limit"] == 100
