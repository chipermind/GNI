"""
Centralized deduplication: fingerprint spec and 7-day window.
Fingerprint: sha256(source_type + canonical_url + normalized_title).
canonical_url strips utm params and normalizes domains (from collector.normalize).
Policy: strict (exact fingerprint) | relaxed (title similarity via rapidfuzz if available).
"""
import hashlib
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

# Re-export so pipeline and ingest use one place for canonical URL
from apps.collector.normalize import canonicalize_url  # noqa: F401

from apps.api.db.models import Item
from apps.shared.env_helpers import get_int_env

# Configurable window (days); only treat as duplicate if same fingerprint exists with created_at >= now - DEDUPE_DAYS
DEDUPE_DAYS = get_int_env("DEDUPE_DAYS", 7)
# Policy: strict (exact fingerprint only) | relaxed (title similarity, uses rapidfuzz if available)
DEDUPE_POLICY = (os.environ.get("DEDUPE_POLICY", "strict") or "strict").lower()

try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False


def build_fingerprint(source_type: str, canonical_url: str, normalized_title: str) -> str:
    """
    Deterministic fingerprint for dedup: sha256(source_type + canonical_url + normalized_title).
    Same title/url but different source_type yields different fingerprint.
    """
    raw = f"{source_type}\n{canonical_url}\n{normalized_title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_item(session, fingerprint: str, title: Optional[str] = None) -> Optional[Item]:
    """
    Return existing Item by fingerprint (strict) or title similarity (relaxed).
    In strict mode: exact fingerprint match.
    In relaxed mode: if rapidfuzz available, also check for similar titles within window.
    """
    exact = session.query(Item).filter(Item.fingerprint == fingerprint).first()
    if exact:
        return exact
    if DEDUPE_POLICY != "relaxed" or not _HAS_RAPIDFUZZ or not title or not title.strip():
        return None
    # Relaxed: find items in window with similar title (ratio >= 85)
    cutoff = get_window_cutoff(DEDUPE_DAYS)
    candidates = (
        session.query(Item)
        .filter(Item.created_at >= cutoff, Item.title.isnot(None))
        .all()
    )
    for c in candidates:
        if c.title and fuzz.ratio(title.strip().lower(), c.title.lower()) >= 85:
            return c
    return None


def get_window_cutoff(window_days: int, now: Optional[datetime] = None) -> datetime:
    """Return the cutoff time: items with created_at >= this are considered in-window duplicates."""
    if now is None:
        now = datetime.now(timezone.utc)
    return now - timedelta(days=window_days)


def _ensure_aware(dt: datetime) -> datetime:
    """Return datetime with tzinfo=timezone.utc if naive."""
    if dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def is_duplicate_in_window(
    session,
    fingerprint: str,
    window_days: int = DEDUPE_DAYS,
    now: Optional[datetime] = None,
) -> bool:
    """
    True if an item with this fingerprint exists and created_at >= now - window_days.
    Duplicate within window -> drop (don't insert new; caller may touch updated_at).
    Duplicate older than window -> allowed (caller may refresh that row).
    """
    item = find_item(session, fingerprint)
    if not item or not item.created_at:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = get_window_cutoff(window_days, _ensure_aware(now))
    created = _ensure_aware(item.created_at)
    return created >= cutoff


def created_at_in_window(item: Optional[Item], window_days: int, now: Optional[datetime] = None) -> bool:
    """True if item.created_at >= now - window_days. Used by ingest to decide update vs refresh."""
    if not item or not getattr(item, "created_at", None):
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = get_window_cutoff(window_days, _ensure_aware(now))
    created = _ensure_aware(item.created_at)
    return created >= cutoff
