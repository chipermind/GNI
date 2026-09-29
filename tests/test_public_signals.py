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
        "public_visible": True,
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


def test_public_query_requires_published_and_explicit_visibility():
    from apps.api.routes.public import PUBLIC_SIGNAL_FILTERS

    status_filter, visibility_filter = PUBLIC_SIGNAL_FILTERS
    assert status_filter.left.name == "status"
    assert getattr(status_filter.right, "value", None) == "published"
    assert visibility_filter.left.name == "public_visible"
    assert "IS true" in str(visibility_filter)


def test_item_public_visibility_defaults_fail_closed():
    from apps.api.db.models import Item

    column = Item.__table__.c.public_visible
    assert column.nullable is False
    assert column.default is not None and column.default.arg is False
    assert column.server_default is not None
    assert str(column.server_default.arg).lower() == "false"


class _FakeQuery:
    def __init__(self, *, row=None, count_value=0, rows=None):
        self._row = row
        self._count_value = count_value
        self._rows = [] if rows is None else rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def offset(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def count(self):
        return self._count_value

    def all(self):
        return self._rows

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, *, row=None, count_value=0, rows=None):
        self.query_obj = _FakeQuery(row=row, count_value=count_value, rows=rows)
        self.commits = 0

    def query(self, *args, **kwargs):
        return self.query_obj

    def commit(self):
        self.commits += 1


def test_posts_list_computes_total_before_pagination():
    from apps.api.routes.posts import list_posts

    session = _FakeSession(count_value=7, rows=[])
    payload = list_posts(session=session, status="published", limit=2, offset=4)

    assert payload["total"] == 7
    assert payload["items"] == []
    assert payload["limit"] == 2
    assert payload["offset"] == 4


def test_public_visibility_rejects_non_published_item():
    from fastapi import HTTPException
    from apps.api.routes.posts import set_public_visibility

    row = SimpleNamespace(id=9, status="drafted", public_visible=False)
    session = _FakeSession(row=row)

    try:
        set_public_visibility(item_id=9, visible=True, session=session)
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 409

    assert row.public_visible is False
    assert session.commits == 0


def test_public_visibility_allows_published_item_and_revocation():
    from apps.api.routes.posts import set_public_visibility

    row = SimpleNamespace(id=10, status="published", public_visible=False)
    session = _FakeSession(row=row)

    enabled = set_public_visibility(item_id=10, visible=True, session=session)
    assert enabled == {"id": 10, "status": "published", "public_visible": True}
    assert row.public_visible is True
    assert session.commits == 1

    disabled = set_public_visibility(item_id=10, visible=False, session=session)
    assert disabled == {"id": 10, "status": "published", "public_visible": False}
    assert row.public_visible is False
    assert session.commits == 2
