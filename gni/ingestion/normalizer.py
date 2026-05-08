"""Normalize raw RSS entries into the canonical V1 headline schema.

Canonical schema (per item):
    {
      "id": str,                 # SHA-256 hash_key (also used as primary key)
      "schema_version": 1,
      "source_name": str,
      "source_type": "rss",
      "category": str,
      "tier": str,
      "title": str,
      "url": str,                # canonicalized
      "published_at": str,       # ISO 8601 UTC, "" if unknown
      "collected_at": str,       # ISO 8601 UTC (always set)
      "raw_text": str,           # RSS summary
      "hash_key": str            # SHA-256(source_name|canonical_url|normalized_title)
    }
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

REQUIRED_FIELDS = ("source_name", "title", "url", "collected_at", "hash_key")

_TRACKING_PARAM_PATTERNS = (
    re.compile(r"^utm_"),
    re.compile(r"^fbclid$"),
    re.compile(r"^gclid$"),
    re.compile(r"^mc_(cid|eid)$"),
    re.compile(r"^ref$"),
    re.compile(r"^ref_src$"),
    re.compile(r"^igshid$"),
)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_tracking_param(name: str) -> bool:
    n = name.lower()
    return any(p.match(n) for p in _TRACKING_PARAM_PATTERNS)


def _canonical_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    try:
        parts = urlsplit(raw_url.strip())
    except Exception:
        return raw_url.strip()
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = re.sub(r"/+$", "", parts.path) or "/"
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(k)
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "")).strip().lower()


def _hash_key(source_name: str, canonical_url: str, title: str) -> str:
    payload = f"{source_name}|{canonical_url}|{_normalize_title(title)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pseudo_url(source_name: str, title: str) -> str:
    """Deterministic fallback URL when the source has no <link>.

    Format: ``source://{source_name_no_spaces}/{sha256(normalized_title)}``.
    Bypasses canonicalization so uniqueness is preserved.
    """
    digest = hashlib.sha256(_normalize_title(title).encode("utf-8")).hexdigest()
    safe_name = (source_name or "unknown").strip().replace(" ", "_") or "unknown"
    return f"source://{safe_name}/{digest}"


def _coerce_to_utc_iso(raw: str) -> str | None:
    """Try to coerce ``raw`` (an already-parsed ISO-Z or empty) into a UTC ISO Z string."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sanitize_published_at(parsed_iso: str, collected_at_iso: str) -> str:
    """Sanity check parsed published_at against ``collected_at``.

    Rules:
      - empty / unparseable -> collected_at
      - year < 2000          -> collected_at
      - more than 1h in the future relative to collected_at -> collected_at
      - else                 -> parsed_iso (already UTC ISO Z)
    """
    coerced = _coerce_to_utc_iso(parsed_iso)
    if coerced is None:
        return collected_at_iso
    try:
        dt = datetime.strptime(coerced, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return collected_at_iso
    if dt.year < 2000:
        return collected_at_iso
    try:
        col_dt = datetime.strptime(
            collected_at_iso, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return coerced
    if dt > col_dt + timedelta(hours=1):
        return collected_at_iso
    return coerced


def _parse_published(entry: dict[str, Any]) -> str:
    parsed = entry.get("published_parsed")
    if parsed is not None:
        try:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", parsed)
            return ts
        except Exception:
            pass
    raw = entry.get("published") or ""
    if raw:
        for fmt in (
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
        ):
            try:
                dt = datetime.strptime(raw, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
    return ""


def _validate(item: dict[str, Any]) -> tuple[bool, str | None]:
    for field in REQUIRED_FIELDS:
        v = item.get(field)
        if v is None or (isinstance(v, str) and not v.strip()):
            return False, f"missing_required_field:{field}"
    return True, None


def normalize(
    entry: dict[str, Any],
    source_meta: dict[str, Any],
    collected_at: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize one raw RSS entry. Returns (item, None) or (None, error_code)."""
    title = (entry.get("title") or "").strip()
    raw_url = (entry.get("link") or "").strip()
    if not title:
        return None, "missing_title"

    source_name = source_meta.get("source_name", "")
    if not source_name:
        return None, "missing_source_name"

    canonical = _canonical_url(raw_url) if raw_url else ""
    url_synthetic = False
    if not canonical:
        canonical = _pseudo_url(source_name, title)
        url_synthetic = True

    collected_at_final = collected_at or _now_utc_iso()
    published_raw = _parse_published(entry)
    published_at = _sanitize_published_at(published_raw, collected_at_final)

    hk = _hash_key(source_name, canonical, title)
    item = {
        "id": hk,
        "schema_version": SCHEMA_VERSION,
        "source_name": source_name,
        "source_type": source_meta.get("source_type", "rss"),
        "category": source_meta.get("category", ""),
        "tier": source_meta.get("tier", ""),
        "title": title,
        "url": canonical,
        "url_synthetic": url_synthetic,
        "published_at": published_at,
        "collected_at": collected_at_final,
        "raw_text": (entry.get("summary") or "").strip(),
        "hash_key": hk,
    }
    ok, err = _validate(item)
    if not ok:
        return None, err
    return item, None


def normalize_batch(
    entries: list[dict[str, Any]],
    source_meta: dict[str, Any],
    collected_at: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize a batch. Returns (items_ok, dlq_entries).

    DLQ entry shape: {"reason": str, "source_name": str, "raw": <entry>}.
    """
    ok_items: list[dict[str, Any]] = []
    dlq: list[dict[str, Any]] = []
    for e in entries:
        item, err = normalize(e, source_meta, collected_at=collected_at)
        if item is not None:
            ok_items.append(item)
        else:
            dlq.append(
                {
                    "reason": err or "unknown",
                    "source_name": source_meta.get("source_name", ""),
                    "raw": {
                        "title": e.get("title", ""),
                        "link": e.get("link", ""),
                        "published": e.get("published", ""),
                    },
                }
            )
    return ok_items, dlq
