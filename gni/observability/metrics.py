"""Pure metric extractors for the GNI V1 pipeline.

All functions are tolerant of missing / empty / corrupt inputs — they return
zero-valued counts rather than raising. The orchestrator
(``run_report.py``) is the only side-effecting layer.

Authoritative source per metric:
  headlines_collected      <- gni/data/raw/headlines_YYYYMMDD_UTC.json
  drafts_*                 <- gni/data/drafts/drafts_YYYYMMDD.json
  queue_*                  <- gni/data/queue/queue_YYYYMMDD.json
  published_count          <- gni/data/published/published_YYYYMMDD.json
  failed_publish_count     <- queue items still ready_to_publish carrying
                              "publish_failed" in their review_notes
  critical_pending_count   <- queue items where priority=="critical" AND
                              status=="needs_review"
  duplicate_count          <- sum across stages, computed as:
                              ingestion manifest items_duplicates
                              + drafting log "duplicates=N"
                              + queue log "dups=N"
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Defensive readers
# ---------------------------------------------------------------------------


def safe_load_json(path: Path | None) -> Any:
    """Return parsed JSON, or ``None`` for any missing/empty/corrupt input."""
    if path is None:
        return None
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _items_from_envelope(data: Any, key: str) -> list[dict]:
    """Accept either a versioned envelope ({key: [...]}) or a bare list."""
    if data is None:
        return []
    if isinstance(data, dict) and key in data:
        v = data.get(key) or []
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


# ---------------------------------------------------------------------------
# Stage extractors
# ---------------------------------------------------------------------------


def count_headlines(headlines_path: Path | None) -> int:
    return len(_items_from_envelope(safe_load_json(headlines_path), "items"))


def drafts_breakdown(drafts_path: Path | None) -> dict[str, int]:
    """Return {created, validated, failed_guard, needs_review, needs_editorial_build}."""
    items = _items_from_envelope(safe_load_json(drafts_path), "drafts")
    out = {
        "created": len(items),
        "validated": 0,
        "failed_guard": 0,
        "needs_review": 0,
        "needs_editorial_build": 0,
    }
    for d in items:
        s = (d.get("draft_status") or "").strip()
        if s in out and s != "created":
            out[s] += 1
    return out


_QUEUE_STATUSES = (
    "validated", "needs_review", "failed_guard", "needs_editorial_build",
    "approved", "rejected", "ready_to_publish", "published",
)


def queue_breakdown(queue_path: Path | None) -> dict[str, Any]:
    """Return per-status counts plus derived: critical_pending, failed_publish."""
    items = _items_from_envelope(safe_load_json(queue_path), "queue")
    by_status = {s: 0 for s in _QUEUE_STATUSES}
    critical_pending = 0
    failed_publish = 0
    for q in items:
        status = (q.get("status") or "").strip()
        priority = (q.get("priority") or "").strip().lower()
        notes = q.get("review_notes") or ""
        if status in by_status:
            by_status[status] += 1
        if status == "needs_review" and priority == "critical":
            critical_pending += 1
        # A failure note plus the item still ready_to_publish == stuck.
        if status == "ready_to_publish" and "publish_failed" in notes:
            failed_publish += 1
    return {
        "total": len(items),
        "by_status": by_status,
        "critical_pending": critical_pending,
        "failed_publish": failed_publish,
    }


def count_published(published_path: Path | None) -> int:
    data = safe_load_json(published_path)
    if isinstance(data, list):
        return sum(1 for x in data if isinstance(x, dict))
    if isinstance(data, dict) and "published" in data:
        v = data.get("published") or []
        return sum(1 for x in v if isinstance(x, dict)) if isinstance(v, list) else 0
    return 0


# ---------------------------------------------------------------------------
# Manifest + log scrapes (supplementary; never fatal)
# ---------------------------------------------------------------------------


def ingestion_manifest_duplicates(manifest_path: Path | None) -> int:
    data = safe_load_json(manifest_path)
    if isinstance(data, dict):
        v = data.get("items_duplicates")
        if isinstance(v, int) and v >= 0:
            return v
    return 0


_DRAFTING_DUP_RE = re.compile(r"duplicates=(\d+)")
_QUEUE_DUP_RE = re.compile(r"dups=(\d+)")
_RUN_DONE_RE = re.compile(r"run done.*$", re.MULTILINE)


def _last_int_match(log_path: Path | None, pattern: re.Pattern) -> int:
    """Return last int captured by ``pattern`` in any 'run done' line of the log."""
    if log_path is None or not log_path.exists():
        return 0
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if not text:
        return 0
    last = 0
    for line in _RUN_DONE_RE.findall(text):
        m = pattern.search(line)
        if m:
            try:
                last = int(m.group(1))
            except ValueError:
                continue
    return last


def drafting_log_duplicates(log_path: Path | None) -> int:
    return _last_int_match(log_path, _DRAFTING_DUP_RE)


def queue_log_duplicates(log_path: Path | None) -> int:
    return _last_int_match(log_path, _QUEUE_DUP_RE)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate(
    *,
    headlines_path: Path | None,
    drafts_path: Path | None,
    queue_path: Path | None,
    published_path: Path | None,
    manifest_path: Path | None = None,
    drafting_log: Path | None = None,
    queue_log: Path | None = None,
) -> dict[str, Any]:
    """Build the full V1 metrics dict from whichever inputs are present.

    Missing inputs degrade silently to 0; ``inputs_seen`` records which
    files were actually loadable, useful for the human-readable summary.
    """
    headlines = count_headlines(headlines_path)
    drafts = drafts_breakdown(drafts_path)
    queue = queue_breakdown(queue_path)
    published = count_published(published_path)

    ingest_dups = ingestion_manifest_duplicates(manifest_path)
    drf_dups = drafting_log_duplicates(drafting_log)
    q_dups = queue_log_duplicates(queue_log)

    metrics = {
        "headlines_collected":   headlines,
        "drafts_created":        drafts["created"],
        "drafts_validated":      drafts["validated"],
        "drafts_failed_guard":   drafts["failed_guard"],
        "queue_ready_to_publish": queue["by_status"]["ready_to_publish"],
        "queue_needs_review":    queue["by_status"]["needs_review"],
        "published_count":       published,
        "failed_publish_count":  queue["failed_publish"],
        "duplicate_count":       ingest_dups + drf_dups + q_dups,
        "critical_pending_count": queue["critical_pending"],
    }

    inputs_seen = {
        "headlines": headlines_path is not None and headlines_path.exists(),
        "drafts":    drafts_path is not None and drafts_path.exists(),
        "queue":     queue_path is not None and queue_path.exists(),
        "published": published_path is not None and published_path.exists(),
        "manifest":  manifest_path is not None and manifest_path.exists(),
        "drafting_log": drafting_log is not None and drafting_log.exists(),
        "queue_log":  queue_log is not None and queue_log.exists(),
    }

    breakdown = {
        "drafts":    drafts,
        "queue":     queue,
        "duplicates": {
            "ingestion_manifest": ingest_dups,
            "drafting_log":       drf_dups,
            "queue_log":          q_dups,
        },
    }

    return {
        "metrics": metrics,
        "breakdown": breakdown,
        "inputs_seen": inputs_seen,
    }
