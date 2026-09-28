"""Tests for the public read-only intelligence surface."""
from datetime import datetime, timezone
from types import SimpleNamespace


def _item(**overrides):
    values = {
        "id": 42,
        "title": "Published signal",
        "summary": "Sanitized summary",
        "source_name": "Public Source",
        "url": "https://example.com/story",
        "source_type": "rss",
        "risk": "high",
        "priority": 4,
        "published_at": datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 9, 24, 12, 5, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_public_signal_serializer_exposes_only_allowlisted_fields():
    from apps.api.routes.public import _serialize_signal

    payload = _serialize_signal(_item())
    assert set(payload) == {
        "id",
        "title",
        "summary",
        "source_name",
        "source_url",
        "source_type",
        "risk",
        "priority",
        "source_published_at",
        "processed_at",
    }
    assert payload["source_url"] == "https://example.com/story"
    assert payload["source_published_at"].startswith("2026-09-24T12:00:00")


def test_public_signal_serializer_rejects_non_http_source_url():
    from apps.api.routes.public import _serialize_signal

    assert _serialize_signal(_item(url="tg://private-channel/123"))["source_url"] is None
    assert _serialize_signal(_item(url="javascript:alert(1)"))["source_url"] is None
    assert _serialize_signal(_item(url="/internal/path"))["source_url"] is None


def test_public_signal_serializer_allows_https_and_http_only():
    from apps.api.routes.public import _safe_http_url

    assert _safe_http_url("https://example.com/a") == "https://example.com/a"
    assert _safe_http_url("http://example.com/a") == "http://example.com/a"
    assert _safe_http_url("ftp://example.com/a") is None
    assert _safe_http_url(None) is None
