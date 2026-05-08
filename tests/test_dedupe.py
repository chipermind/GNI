"""Unit tests for centralized dedupe: fingerprint (source_type + url + title) and 7-day window."""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from apps.worker.dedupe import (
    build_fingerprint,
    canonicalize_url,
    created_at_in_window,
    get_window_cutoff,
    is_duplicate_in_window,
)


def test_fingerprint_same_title_url_different_source_type_produces_different_fingerprint():
    """Same title and url but different source_type must produce different fingerprints."""
    url = "https://example.com/article"
    title = "Same Headline"
    fp_rss = build_fingerprint("rss", url, title)
    fp_telegram = build_fingerprint("telegram", url, title)
    assert fp_rss != fp_telegram


def test_fingerprint_same_source_type_url_title_reproducible():
    """Same source_type + canonical_url + normalized_title yields same fingerprint."""
    url = "https://example.com/path"
    title = "Normalized Title"
    assert build_fingerprint("rss", url, title) == build_fingerprint("rss", url, title)


def test_canonical_url_strips_utm():
    """canonical_url strips utm params (from normalize)."""
    u = "https://example.com/article?utm_source=twitter&foo=bar"
    assert "utm_source" not in canonicalize_url(u)
    assert "foo=bar" in canonicalize_url(u)


def test_duplicate_older_than_window_is_allowed():
    """Duplicate with created_at older than window: is_duplicate_in_window returns False (allowed)."""
    session = MagicMock()
    old_item = MagicMock()
    now = datetime.now(timezone.utc)
    old_item.created_at = now - timedelta(days=8)
    session.query.return_value.filter.return_value.first.return_value = old_item

    assert is_duplicate_in_window(session, "abc123", window_days=7, now=now) is False


def test_duplicate_within_window_is_dropped():
    """Duplicate with created_at within window: is_duplicate_in_window returns True (dropped)."""
    session = MagicMock()
    recent_item = MagicMock()
    now = datetime.now(timezone.utc)
    recent_item.created_at = now - timedelta(days=3)
    session.query.return_value.filter.return_value.first.return_value = recent_item

    assert is_duplicate_in_window(session, "abc123", window_days=7, now=now) is True


def test_created_at_in_window_older_than_window():
    """created_at_in_window returns False when item is older than window."""
    item = MagicMock()
    now = datetime.now(timezone.utc)
    item.created_at = now - timedelta(days=10)
    assert created_at_in_window(item, 7, now=now) is False


def test_created_at_in_window_within_window():
    """created_at_in_window returns True when item is within window."""
    item = MagicMock()
    now = datetime.now(timezone.utc)
    item.created_at = now - timedelta(days=2)
    assert created_at_in_window(item, 7, now=now) is True


def test_get_window_cutoff():
    """get_window_cutoff returns now - window_days."""
    now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    cutoff = get_window_cutoff(7, now)
    assert cutoff == datetime(2025, 1, 8, 12, 0, 0, tzinfo=timezone.utc)
