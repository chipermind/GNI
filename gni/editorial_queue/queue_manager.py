"""Editorial queue manager: drafts -> queue items + status transitions.

V1 status routing (draft_status + priority -> queue_status):

  draft.draft_status == "validated"  + priority in {medium, low, info}
      -> "ready_to_publish"
  draft.draft_status == "validated"  + priority == "critical"
      -> "needs_review"
  draft.draft_status == "validated"  + priority == "high"
      -> "needs_review"
  draft.draft_status == "failed_guard"          -> "failed_guard"
  draft.draft_status == "needs_editorial_build" -> "needs_editorial_build"
  draft.draft_status == "needs_review"          -> "needs_review"  (preserved)

Operator-driven transitions (post V1 build):
  approved        (operator action; precondition: needs_review)
  rejected        (operator action; precondition: any non-published)
  ready_to_publish (auto for medium/low/info validated, OR after approval)
  published        (set by publisher V2; queue manager only validates target)

This module is pure (no I/O at import time). The orchestrator
(``run_queue.py``) is the only side-effecting layer.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# All legal queue statuses.
ALLOWED_STATUSES: frozenset[str] = frozenset(
    {
        "validated",
        "needs_review",
        "failed_guard",
        "needs_editorial_build",
        "approved",
        "rejected",
        "ready_to_publish",
        "published",
        "ignored",
        "failed_processing",
    }
)

# Statuses that block further automated transitions.
# "ignored" and "failed_processing" are terminal: never approvable, never
# publishable. Operator must regenerate the draft to recover.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"published", "rejected", "ignored", "failed_processing"}
)

# The single status the publisher is allowed to act on.
PUBLISHABLE_STATUSES: frozenset[str] = frozenset({"ready_to_publish"})

# Statuses that must be blocked from publishing even if mis-routed there.
PUBLISH_FORBIDDEN_STATUSES: frozenset[str] = frozenset(
    {
        "ignored",
        "needs_review",
        "failed_guard",
        "needs_editorial_build",
        "failed_processing",
        "rejected",
        "published",
    }
)

# Priorities that may auto-flow to ready_to_publish from a validated draft.
AUTO_PUBLISH_PRIORITIES: frozenset[str] = frozenset({"medium", "low", "info"})

# Priorities that always require human review even when validated.
REVIEW_REQUIRED_PRIORITIES: frozenset[str] = frozenset({"critical", "high"})


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# I/O helpers (pure read; orchestrator does writes via save_queue)
# ---------------------------------------------------------------------------


def load_drafts(path: Path) -> list[dict]:
    """Load drafts JSON. Returns [] on missing/corrupt input.

    Accepts either ``{"drafts": [...]}`` (V1 wrapper) or a bare list.
    """
    if not path.exists():
        logger.error("drafts file not found: %s", path)
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("drafts file unreadable %s: %r", path, exc)
        return []
    if isinstance(data, dict) and "drafts" in data:
        items = data.get("drafts") or []
        return [d for d in items if isinstance(d, dict)]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def load_queue(path: Path) -> list[dict]:
    """Load existing queue JSON. Returns [] on missing/corrupt input."""
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("queue file unreadable %s: %r", path, exc)
        return []
    if isinstance(data, dict) and "queue" in data:
        items = data.get("queue") or []
        return [q for q in items if isinstance(q, dict)]
    if isinstance(data, list):
        return [q for q in data if isinstance(q, dict)]
    return []


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def map_draft_status_to_queue_status(draft_status: str, priority: str) -> str:
    """Apply V1 routing rules. Returns one of ALLOWED_STATUSES."""
    ds = (draft_status or "").strip()
    pr = (priority or "").strip().lower()

    if ds == "validated":
        if pr in REVIEW_REQUIRED_PRIORITIES:
            return "needs_review"
        if pr in AUTO_PUBLISH_PRIORITIES:
            return "ready_to_publish"
        # Unknown priority on a validated draft → human review.
        return "needs_review"
    if ds == "failed_guard":
        return "failed_guard"
    if ds == "needs_editorial_build":
        return "needs_editorial_build"
    if ds == "needs_review":
        return "needs_review"
    if ds == "ignored":
        return "ignored"
    if ds == "failed_processing":
        return "failed_processing"
    # Defensive: unknown draft_status falls through to review.
    logger.warning("unknown draft_status=%r priority=%r → needs_review", ds, pr)
    return "needs_review"


# ---------------------------------------------------------------------------
# Build queue items
# ---------------------------------------------------------------------------


def _build_queue_id(draft_id: str) -> str:
    """Deterministic queue_id: q_<draft_id>. Stable across re-runs → enables dedup."""
    return f"q_{draft_id}" if draft_id else ""


def build_queue_items(
    drafts: Iterable[dict],
    existing_draft_ids: set[str] | None = None,
) -> tuple[list[dict], int]:
    """Convert drafts to queue items (skipping any whose draft_id is already
    present in ``existing_draft_ids``). Returns ``(new_items, dup_count)``.

    Mutates ``existing_draft_ids`` in place if provided so callers can chain
    multiple drafts files in one run.
    """
    seen = existing_draft_ids if existing_draft_ids is not None else set()
    out: list[dict] = []
    dups = 0
    now = _now_utc_iso()

    for d in drafts:
        if not isinstance(d, dict):
            continue
        draft_id = d.get("draft_id") or ""
        if not draft_id:
            logger.warning("draft missing draft_id; skipped headline_hash_key=%r",
                           d.get("headline_hash_key"))
            continue
        if draft_id in seen:
            dups += 1
            continue

        priority = d.get("priority", "medium")
        draft_status = d.get("draft_status", "")
        queue_status = map_draft_status_to_queue_status(draft_status, priority)

        item = {
            "queue_id": _build_queue_id(draft_id),
            "draft_id": draft_id,
            "headline_hash_key": d.get("headline_hash_key", ""),
            "template": d.get("template", ""),
            "priority": priority,
            "status": queue_status,
            "payload": d.get("payload", {}) or {},
            "source_item": d.get("source_item", {}) or {},
            "created_at": now,
            "updated_at": now,
            "review_notes": "",
        }
        out.append(item)
        seen.add(draft_id)

    return out, dups


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def update_status(item: dict, new_status: str, note: str | None = None) -> dict:
    """Transition a queue item to ``new_status``. Mutates and returns ``item``.

    Validates that:
      - ``new_status`` is a member of ALLOWED_STATUSES
      - current status is not terminal (published / rejected)

    Any failure raises ``ValueError`` so callers (operator tooling) can
    surface the problem.
    """
    if not isinstance(item, dict):
        raise ValueError("queue item must be a dict")
    target = (new_status or "").strip()
    if target not in ALLOWED_STATUSES:
        raise ValueError(f"unknown queue status: {new_status!r}")
    current = item.get("status", "")
    if current in TERMINAL_STATUSES and target != current:
        raise ValueError(
            f"cannot transition from terminal status {current!r} to {target!r}"
        )
    item["status"] = target
    item["updated_at"] = _now_utc_iso()
    if note:
        existing = item.get("review_notes") or ""
        item["review_notes"] = f"{existing}\n{note}".strip() if existing else note
    return item


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_queue(path: Path, items: list[dict], day_utc: str | None = None) -> None:
    """Atomic write of queue JSON. Wraps items in a versioned envelope."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "day_utc": day_utc or "",
        "last_updated_at": _now_utc_iso(),
        "count": len(items),
        "queue": items,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def summarize_queue(items: Iterable[dict]) -> dict[str, int]:
    """Return per-status counts (every ALLOWED_STATUSES key present, even at 0)."""
    counts: dict[str, int] = {s: 0 for s in ALLOWED_STATUSES}
    counts["total"] = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        counts["total"] += 1
        s = it.get("status", "")
        if s in counts:
            counts[s] += 1
    return counts
