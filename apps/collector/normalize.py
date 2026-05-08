"""
Normalize feed entry to canonical fields. Fingerprint for dedup is built in apps.worker.dedupe.
"""
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


# Query params to strip for canonical URL (utm_*, fbclid, etc.)
STRIP_QUERY_PREFIXES = ("utm_", "fbclid", "gclid", "ref", "mc_", "_ga")
STRIP_QUERY_EXACT = frozenset(("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"))


def canonicalize_url(url: Optional[str]) -> str:
    """Remove tracking/utm params and normalize. Empty input -> ''."""
    if not url or not url.strip():
        return ""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip()
    query = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {
        k: v for k, v in query.items()
        if not any(k.lower().startswith(p) for p in STRIP_QUERY_PREFIXES)
        and k.lower() not in STRIP_QUERY_EXACT
    }
    new_query = urlencode(filtered, doseq=True)
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "/",
        parsed.params,
        new_query,
        "",  # fragment
    ))


def normalize_title(s: Optional[str]) -> str:
    if s is None:
        return ""
    return " ".join(s.split())


def normalize_summary(s: Optional[str]) -> str:
    if s is None:
        return ""
    return " ".join(s.split())[:10000]


def parse_published(entry: Any) -> Optional[datetime]:
    """Use published_parsed, updated_parsed, or published/updated string; fallback None."""
    # feedparser: published_parsed / updated_parsed are time.struct_time in UTC
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            from time import mktime
            from calendar import timegm
            ts = timegm(entry.published_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        try:
            from calendar import timegm
            ts = timegm(entry.updated_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
    # String fallback
    for attr in ("published", "updated"):
        val = getattr(entry, attr, None)
        if not val or not isinstance(val, str):
            continue
        s = val.strip()[:50]
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
            try:
                if fmt.endswith("%z"):
                    dt = datetime.strptime(s.replace("Z", "+00:00"), fmt)
                else:
                    dt = datetime.strptime(s[: len(fmt.replace("%z", "").replace("%Z", ""))], fmt.replace("%z", "").replace("%Z", ""))
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                continue
    return None


def normalized_record(
    entry: Any,
    source_name: str,
    raw_payload: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Return dict: title, url, published_at, summary, source_name, raw_payload.
    url is canonical; includes fingerprint key for dedup.
    """
    raw = raw_payload
    if raw is None:
        raw = _entry_to_payload(entry)
    link = getattr(entry, "link", None) or ""
    title = getattr(entry, "title", None) or ""
    summary = getattr(entry, "summary", None) or ""
    canonical_url = canonicalize_url(link)
    norm_title = normalize_title(title)
    norm_summary = normalize_summary(summary)
    published_at = parse_published(entry)
    # Fingerprint is built by pipeline dedupe (apps.worker.dedupe) using source_type + canonical_url + normalized_title
    return {
        "title": norm_title or None,
        "url": canonical_url or None,
        "published_at": published_at,
        "summary": norm_summary or None,
        "source_name": source_name,
        "raw_payload": raw,
    }


def _entry_to_payload(entry: Any) -> dict:
    """Serialize feedparser entry to JSON-safe dict (strip non-serializable)."""
    out: dict = {}
    for key in ("id", "link", "title", "summary", "published", "updated", "author"):
        if hasattr(entry, key):
            v = getattr(entry, key)
            if isinstance(v, (str, int, float, bool, type(None))):
                out[key] = v
            elif isinstance(v, (list, dict)):
                try:
                    out[key] = v
                except Exception:
                    pass
            elif hasattr(v, "__str__"):
                out[key] = str(v)
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            from time import strftime
            out["published_parsed"] = strftime("%Y-%m-%dT%H:%M:%SZ", entry.published_parsed)  # type: ignore[arg-type]
        except Exception:
            pass
    return out
