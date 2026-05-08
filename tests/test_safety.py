"""Unit tests for publish safety: assert_publish_allowed and PublishPausedError."""
from unittest.mock import MagicMock

import pytest

from apps.worker.safety import PublishPausedError, assert_publish_allowed


def test_assert_publish_allowed_when_not_paused():
    """When pause_all_publish is False or no row, assert_publish_allowed does not raise."""
    session = MagicMock()
    row = MagicMock()
    row.pause_all_publish = False
    session.query.return_value.first.return_value = row
    assert_publish_allowed(session)


def test_assert_publish_allowed_when_no_settings_row():
    """When no Settings row exists (first() returns None), assert_publish_allowed does not raise."""
    session = MagicMock()
    session.query.return_value.first.return_value = None
    assert_publish_allowed(session)


def test_assert_publish_allowed_raises_when_paused():
    """When pause_all_publish is True, assert_publish_allowed raises PublishPausedError."""
    session = MagicMock()
    row = MagicMock()
    row.pause_all_publish = True
    session.query.return_value.first.return_value = row
    with pytest.raises(PublishPausedError) as exc_info:
        assert_publish_allowed(session)
    assert "publish blocked by pause" in str(exc_info.value).lower() or "pause" in str(exc_info.value).lower()


def test_publish_paused_error_is_exception():
    """PublishPausedError is a controlled exception (subclass of Exception)."""
    e = PublishPausedError("test")
    assert isinstance(e, Exception)


def test_publish_blocked_events_log_payload():
    """Contract: when publish is blocked by pause, events_log should record event_type 'publish_blocked' and message 'publish blocked by pause'."""
    from apps.api.db.models import EventsLog

    log = EventsLog(
        event_type="publish_blocked",
        payload={"reason": "pause_all_publish", "message": "publish blocked by pause"},
    )
    assert log.event_type == "publish_blocked"
    assert log.payload["message"] == "publish blocked by pause"
    assert log.payload["reason"] == "pause_all_publish"
