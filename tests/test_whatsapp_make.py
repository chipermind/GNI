"""Tests for Make webhook publisher: dry_run prints payload; with test URL performs POST and logs status."""
from unittest.mock import MagicMock, patch

import pytest

from apps.publisher.whatsapp_make import (
    MakePayload,
    MakePublishResult,
    publish_make,
    publish_make_simple,
    _post_with_retries,
    _get_webhook_url,
    send_whatsapp_via_make,
)


def test_make_payload_to_json():
    """MakePayload serializes to spec: channel, text, template, priority, meta."""
    p = MakePayload(
        text="Hello",
        template="FLASH_SETORIAL",
        priority="P1",
        source="CoinDesk",
        url="https://example.com/1",
        item_id=42,
    )
    j = p.to_json()
    assert j["channel"] == "whatsapp"
    assert j["text"] == "Hello"
    assert j["template"] == "FLASH_SETORIAL"
    assert j["priority"] == "P1"
    assert "meta" in j
    assert j["meta"]["source"] == "CoinDesk"
    assert j["meta"]["url"] == "https://example.com/1"
    assert j["meta"]["item_id"] == 42


def test_publish_make_dry_run_prints_payload(capsys):
    """In dry_run mode prints payload."""
    session = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    session.flush = MagicMock()
    session.add = MagicMock()

    from apps.publisher import whatsapp_make as mod
    original_log = mod._log_publication
    mod._log_publication = lambda s, ch, st, external_id=None, published_at=None, attempts=0: 99
    try:
        payload = MakePayload(
            text="Test message",
            template="ANALISE_INTEL",
            priority="P0",
            source="Reuters",
            url="https://example.com/item",
            item_id=1,
        )
        result = publish_make(payload, dry_run=True, session=session)
        assert result.status == "dry_run"
        assert result.dry_run is True
        out = capsys.readouterr().out
        assert "dry_run" in out
        assert "Test message" in out
        assert "ANALISE_INTEL" in out
    finally:
        mod._log_publication = original_log


def test_publish_make_no_url_uses_dry_run(capsys):
    """When MAKE_WEBHOOK_URL not set, behaves like dry_run (prints payload)."""
    with patch.dict("os.environ", {}, clear=False):
        # Ensure URL is unset for this test
        from apps.publisher import whatsapp_make as mod
        original_get = mod._get_webhook_url
        mod._get_webhook_url = lambda: ""
        session = MagicMock()
        session.commit = MagicMock()
        session.close = MagicMock()
        session.flush = MagicMock()
        session.add = MagicMock()
        original_log = mod._log_publication
        mod._log_publication = lambda s, ch, st, external_id=None, published_at=None, attempts=0: 100
        try:
            payload = MakePayload(text="No URL", template="X", priority="P2", source="", url="")
            result = publish_make(payload, dry_run=None, session=session)
            assert result.dry_run is True
            assert result.status == "dry_run"
            out = capsys.readouterr().out
            assert "payload" in out or "No URL" in out
        finally:
            mod._get_webhook_url = original_get
            mod._log_publication = original_log


def test_publish_make_with_test_url_performs_post_and_logs_status():
    """With a test URL, performs POST (mocked) and logs status to DB."""
    session = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    session.flush = MagicMock()
    added = []
    session.add = lambda row: added.append(row)

    from apps.publisher import whatsapp_make as mod
    original_get_url = mod._get_webhook_url
    mod._get_webhook_url = lambda: "https://example.com/webhook"
    original_log = mod._log_publication

    def fake_log(s, ch, st, external_id=None, published_at=None, attempts=0):
        added.append(MagicMock(status=st, channel=ch, external_id=external_id))
        return 999

    mod._log_publication = fake_log

    with patch("apps.publisher.whatsapp_make.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_resp.text = "{}"
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = lambda self: self
        mock_client.__exit__ = lambda self, *a: None
        mock_httpx.Client.return_value = mock_client
        try:
            payload = MakePayload(
                text="POST test",
                template="FLASH",
                priority="P1",
                source="Test",
                url="https://example.com",
                item_id=5,
            )
            result = publish_make(payload, dry_run=False, session=session)
            assert result.status == "sent"
            assert result.attempts == 1
            assert len(added) >= 1
            assert added[0].status == "sent"
        finally:
            mod._get_webhook_url = original_get_url
            mod._log_publication = original_log


def test_post_with_retries_success_mock():
    """_post_with_retries returns success when POST returns 200 (mocked)."""
    with patch("apps.publisher.whatsapp_make.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "abc123"}
        mock_resp.text = "{}"
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = lambda self: self
        mock_client.__exit__ = lambda self, *a: None
        mock_httpx.Client.return_value = mock_client
        ok, ext_id, attempts = _post_with_retries("https://example.com/webhook", {"text": "hi"})
        assert ok is True
        assert ext_id == "abc123"
        assert attempts == 1


@patch.dict("os.environ", {"MAKE_WEBHOOK_MAX_ATTEMPTS": "2"}, clear=False)
def test_post_with_retries_failure_then_dead_letter():
    """After N failures, dead_letter is logged (via publish_make)."""
    session = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    session.flush = MagicMock()
    events_added = []
    pub_added = []

    def capture_add(row):
        from apps.api.db.models import EventsLog, Publication
        if type(row).__name__ == "EventsLog":
            events_added.append(row)
        else:
            pub_added.append(row)

    session.add = capture_add

    with patch("apps.publisher.whatsapp_make.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Server Error"
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = lambda self: self
        mock_client.__exit__ = lambda self, *a: None
        mock_httpx.Client.return_value = mock_client

        from apps.publisher import whatsapp_make as mod
        original_get = mod._get_webhook_url
        mod._get_webhook_url = lambda: "https://example.com/webhook"
        original_log = mod._log_publication
        mod._log_publication = lambda s, ch, st, external_id=None, published_at=None, attempts=0: 1
        try:
            payload = MakePayload(text="Fail", template="X", priority="P2", source="", url="")
            result = publish_make(payload, dry_run=False, session=session)
            assert result.status == "dead_letter"
            assert result.attempts == 2
            assert result.last_error
            assert len(events_added) >= 1
            event_types = [e.event_type for e in events_added]
            assert "make_dead_letter" in event_types or "make_publish_failure" in event_types
        finally:
            mod._get_webhook_url = original_get
            mod._log_publication = original_log
