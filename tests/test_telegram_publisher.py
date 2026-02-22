"""Tests for Telegram publisher: interface, dry_run, and real Bot API send."""
from unittest.mock import MagicMock, patch

import pytest

from apps.publisher.telegram import (
    PublicationResult,
    TelegramPublisher,
    publish_telegram,
    PublisherProtocol,
)


def test_publication_result():
    """PublicationResult holds id, status, external_id, dry_run, attempts."""
    r = PublicationResult(publication_id=1, status="dry_run", dry_run=True, attempts=0)
    assert r.publication_id == 1
    assert r.status == "dry_run"
    assert r.dry_run is True


def test_telegram_publisher_implements_protocol():
    """TelegramPublisher implements PublisherProtocol."""
    assert isinstance(TelegramPublisher(), PublisherProtocol)


def test_publish_telegram_dry_run_writes_to_db(capsys):
    """Publishing pipeline writes to DB even in dry_run: _log_publication called with status='dry_run'."""
    session = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    session.flush = MagicMock()
    added = []

    def capture_add(row):
        added.append(row)

    session.add = capture_add
    session.query.return_value.filter.return_value.first.return_value = None

    from apps.publisher import telegram as mod
    original_log = mod._log_publication

    def fake_log(sess, channel, status, external_id=None, published_at=None, attempts=0):
        fake_row = MagicMock()
        fake_row.id = 42
        fake_row.channel = channel
        fake_row.status = status
        fake_row.external_id = external_id
        fake_row.published_at = published_at
        sess.add(fake_row)
        return 42

    mod._log_publication = fake_log
    try:
        result = publish_telegram(
            messages=["Hello", "World"],
            channel="telegram",
            dry_run=True,
            session=session,
        )
        assert result.status == "dry_run"
        assert result.dry_run is True
        assert result.publication_id == 42
        # Pipeline writes to DB: one row added with status dry_run (via fake_log)
        assert len(added) >= 1
        assert getattr(added[0], "status", None) == "dry_run"
        out = capsys.readouterr().out
        assert "Hello" in out
    finally:
        mod._log_publication = original_log


def test_publish_telegram_dry_run_prints_messages(capsys):
    """dry_run prints messages to stdout."""
    session = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    session.flush = MagicMock()
    session.add = MagicMock()

    from apps.publisher import telegram as mod
    original_log = mod._log_publication
    def _fake_log(s, ch, st, external_id=None, published_at=None, attempts=0):
        return 99
    mod._log_publication = _fake_log
    try:
        publish_telegram(
            messages=["Part one", "Part two"],
            channel="test",
            dry_run=True,
            session=session,
        )
        out = capsys.readouterr().out
        assert "Part one" in out
        assert "Part two" in out
        assert "dry_run" in out
    finally:
        mod._log_publication = original_log


def test_publish_telegram_real_send_writes_sent_status():
    """When dry_run=False and token set, real API sends; records status='sent', message_id as external_id."""
    session = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    session.flush = MagicMock()
    added = []

    def capture_add(row):
        added.append(row)

    session.add = capture_add

    from apps.publisher import telegram as mod

    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_TARGET_CHAT_ID": "123"}):
        with patch.object(mod, "_send_message", return_value="456"):
            result = publish_telegram(
                messages=["Sent"],
                channel="telegram",
                dry_run=False,
                session=session,
            )
    assert result.status == "sent"
    assert result.external_id == "456"
    assert result.dry_run is False
    assert result.attempts >= 1


def test_gni_send_calls_publish_telegram_once():
    """gni_send delegates to publish_telegram with same text."""
    from apps.publisher.gni_sender import gni_send

    with patch("apps.publisher.gni_sender.publish_telegram") as mock_pub:
        mock_pub.return_value = PublicationResult(
            publication_id=1, status="sent", external_id="99", dry_run=False, attempts=1
        )
        result = gni_send("test message", meta={}, dry_run=False)
    mock_pub.assert_called_once_with(
        messages=["test message"],
        channel="telegram",
        dry_run=False,
        session=None,
    )
    assert result.status == "sent"
