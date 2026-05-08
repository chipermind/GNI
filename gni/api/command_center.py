"""Agent Command Center — unified read-only aggregator.

Public API
----------
- :func:`build_command_center` — pure-stdlib builder returning a dict that
  matches the Command Center UI contract. No framework dependency. Sub-100ms
  on typical local data (reads at most three small JSON files for the current
  UTC day plus a heartbeat file).
- :func:`heartbeat` — write/refresh the runner heartbeat file. Called by the
  worker loop on each tick.
- ``router`` — optional FastAPI ``APIRouter`` that mounts ``GET
  /api/command-center``. Only available when FastAPI is installed; importing
  this module never fails if FastAPI is missing.

Response contract (frozen)
--------------------------
::

    {
      "system_status": "ok" | "degraded" | "down",
      "queue": {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0},
      "runner": {
        "status": "running" | "idle" | "down",
        "last_heartbeat": "<iso8601 utc | empty string>",
        "active_workers": 0
      },
      "tasks": {
        "last_task": {} | {"id","template","priority","status","headline","updated_at"},
        "running_tasks": [...],
        "recent_failures": [{"id","template","priority","short_error","occurred_at"}, ...]
      },
      "cost": {"current_usage": 0.0, "daily_estimate": 0.0},
      "audit_mode": <bool>
    }

Design choices
--------------
- Pure stdlib + ``re``. No DB, no shell, no network.
- Reads only the *current UTC day's* files
  (``queue_YYYYMMDD.json`` / ``drafts_YYYYMMDD.json`` /
  ``published_YYYYMMDD.json``). No history scan. Missing files → safe defaults.
- Reuses :func:`gni.observability.metrics.safe_load_json` for tolerant
  parsing.
- Sanitization: every string field that crosses the API surface goes through
  :func:`_sanitize_text`, which strips URLs, absolute paths, and known
  secret-bearing tokens (``Bearer`` / ``api_key`` / ``token``) and clamps to
  ``MAX_TEXT_CHARS``.
- Backward compatible: this module is additive. It does not modify any
  existing endpoint, model, or data file.
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gni.observability.metrics import safe_load_json

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Resolve project root: ``<repo>/gni/api/command_center.py`` → repo root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "gni" / "data"

QUEUE_DIR = _DATA_DIR / "queue"
DRAFTS_DIR = _DATA_DIR / "drafts"
PUBLISHED_DIR = _DATA_DIR / "published"
STATE_DIR = _DATA_DIR / "state"
HEARTBEAT_PATH = STATE_DIR / "runner_heartbeat.json"

# Heartbeat freshness windows (seconds).
HEARTBEAT_RUNNING_WINDOW = 60
HEARTBEAT_IDLE_WINDOW = 300

# Output caps.
MAX_TEXT_CHARS = 160
MAX_RECENT_FAILURES = 10
MAX_RUNNING_TASKS = 10

# Cost model (V1 simple): static per-item cost in USD, derived from
# environment so deployments can tune without code change.
DEFAULT_COST_PER_ITEM_USD = 0.001
COST_PER_ITEM_ENV = "GNI_COST_PER_ITEM_USD"
AUDIT_MODE_ENV = "GNI_AUDIT_MODE"

# Queue status buckets (mirrors gni/editorial_queue/queue_manager.py).
_PENDING_STATUSES = frozenset({
    "validated",
    "needs_review",
    "needs_editorial_build",
    "approved",
    "ready_to_publish",
})
_IN_PROGRESS_STATUSES = frozenset({"ready_to_publish"})
_COMPLETED_STATUSES = frozenset({"published"})
_FAILED_STATUSES = frozenset({"failed_guard", "failed_processing", "rejected"})

# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------

# URLs (http/https/file/redis/postgres). Replace with ``[url]``.
_URL_RE = re.compile(
    r"\b(?:https?|file|redis|postgres(?:ql)?|amqp|s3)://\S+",
    re.IGNORECASE,
)
# Absolute Unix paths longer than 4 chars containing a separator and a dot or
# subdirectory. Replaced with ``[path]``. Conservative — we do not strip
# bare ``/tmp`` to avoid false positives in human messages.
_PATH_RE = re.compile(r"(?:^|\s)(/[A-Za-z0-9_./\-]{8,})")
# Bearer / token / api_key patterns.
_SECRET_KV_RE = re.compile(
    r"\b(?:bearer|api[_\-]?key|token|secret|password|passwd)\b\s*[:=]?\s*[A-Za-z0-9_\-./+=]{4,}",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)
# Long opaque hex/base64 strings (≥24 chars) — likely tokens.
_OPAQUE_TOKEN_RE = re.compile(r"\b[A-Fa-f0-9]{24,}\b")

_NEWLINE_RE = re.compile(r"\s*[\r\n]+\s*")


def _sanitize_text(text: Any, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Defensive scrubber for any string crossing the API surface.

    - Coerces to ``str``.
    - Collapses newlines (avoid traceback frames).
    - Strips URLs, absolute paths, bearer tokens, and long opaque strings.
    - Clamps to ``max_chars`` at a word boundary.
    """
    if text is None:
        return ""
    s = str(text)
    if not s:
        return ""
    s = _NEWLINE_RE.sub(" ", s)
    s = _SECRET_KV_RE.sub("[redacted]", s)
    s = _BEARER_RE.sub("[redacted]", s)
    s = _URL_RE.sub("[url]", s)
    s = _PATH_RE.sub(" [path]", s)
    s = _OPAQUE_TOKEN_RE.sub("[redacted]", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" .,;:!?-")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_today_compact() -> str:
    return _now_utc().strftime("%Y%m%d")


def _parse_iso(s: str) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        # Accept both "Z" suffix and "+00:00".
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Path resolution (overridable for tests)
# ---------------------------------------------------------------------------


def _today_paths(
    *,
    data_dir: Path | None = None,
    today: str | None = None,
) -> dict[str, Path]:
    base = data_dir or _DATA_DIR
    day = today or _utc_today_compact()
    return {
        "queue":     base / "queue" / f"queue_{day}.json",
        "drafts":    base / "drafts" / f"drafts_{day}.json",
        "published": base / "published" / f"published_{day}.json",
        "heartbeat": base / "state" / "runner_heartbeat.json",
    }


# ---------------------------------------------------------------------------
# Loaders (defensive — never raise)
# ---------------------------------------------------------------------------


def _load_items(path: Path, key: str) -> list[dict]:
    data = safe_load_json(path)
    if isinstance(data, dict):
        v = data.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _load_heartbeat(path: Path) -> dict[str, Any]:
    data = safe_load_json(path)
    if isinstance(data, dict):
        return data
    return {}


# ---------------------------------------------------------------------------
# Heartbeat write API
# ---------------------------------------------------------------------------


def heartbeat(
    *,
    status: str = "running",
    active_workers: int = 1,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Write/refresh the runner heartbeat file (atomic).

    Called by the worker on each tick (or by ad-hoc scripts for smoke tests).
    Status values: ``running``, ``idle``. (``down`` is computed by the
    aggregator from heartbeat staleness; callers do not write ``down``.)
    """
    base = (data_dir or _DATA_DIR) / "state"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "runner_heartbeat.json"
    payload = {
        "last_heartbeat": _utc_iso(_now_utc()),
        "status":         status if status in {"running", "idle"} else "running",
        "active_workers": int(active_workers) if active_workers > 0 else 0,
    }
    # Atomic write: tmpfile in same dir, fsync, replace.
    fd, tmp_path = tempfile.mkstemp(prefix="hb_", suffix=".json", dir=str(base))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            import json as _json
            f.write(_json.dumps(payload, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return payload
    return payload


# ---------------------------------------------------------------------------
# Per-section builders
# ---------------------------------------------------------------------------


def _bucket_queue(items: list[dict]) -> dict[str, int]:
    counts = {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0}
    for it in items:
        st = it.get("status")
        if not isinstance(st, str):
            continue
        if st in _COMPLETED_STATUSES:
            counts["completed"] += 1
        elif st in _FAILED_STATUSES:
            counts["failed"] += 1
        elif st in _PENDING_STATUSES:
            counts["pending"] += 1
        if st in _IN_PROGRESS_STATUSES:
            counts["in_progress"] += 1
    return counts


def _runner_view(hb: dict[str, Any], now: datetime) -> dict[str, Any]:
    last_iso = hb.get("last_heartbeat") if isinstance(hb, dict) else None
    last_dt = _parse_iso(last_iso) if isinstance(last_iso, str) else None

    if last_dt is None:
        return {"status": "down", "last_heartbeat": "", "active_workers": 0}

    age = (now - last_dt).total_seconds()
    if age <= HEARTBEAT_RUNNING_WINDOW:
        status = "running"
    elif age <= HEARTBEAT_IDLE_WINDOW:
        status = "idle"
    else:
        status = "down"

    workers_raw = hb.get("active_workers", 0)
    try:
        workers = max(0, int(workers_raw))
    except (TypeError, ValueError):
        workers = 0

    return {
        "status":         status,
        "last_heartbeat": _sanitize_text(last_iso, max_chars=40),
        "active_workers": workers,
    }


def _summarize_task(item: dict) -> dict[str, Any]:
    headline = item.get("headline") or item.get("title") or ""
    return {
        "id":         _sanitize_text(item.get("id") or item.get("hash_key") or "", max_chars=64),
        "template":   _sanitize_text(item.get("template") or "", max_chars=20),
        "priority":   _sanitize_text(item.get("priority") or "", max_chars=12),
        "status":     _sanitize_text(item.get("status") or "", max_chars=32),
        "headline":   _sanitize_text(headline, max_chars=120),
        "updated_at": _sanitize_text(item.get("updated_at") or item.get("created_at") or "", max_chars=40),
    }


def _summarize_failure(item: dict) -> dict[str, Any]:
    notes = item.get("review_notes") or item.get("last_error") or ""
    if isinstance(notes, list):
        # Keep only the last note (most recent).
        notes = notes[-1] if notes else ""
    return {
        "id":          _sanitize_text(item.get("id") or item.get("hash_key") or "", max_chars=64),
        "template":    _sanitize_text(item.get("template") or "", max_chars=20),
        "priority":    _sanitize_text(item.get("priority") or "", max_chars=12),
        "status":      _sanitize_text(item.get("status") or "", max_chars=32),
        "short_error": _sanitize_text(notes, max_chars=120),
        "occurred_at": _sanitize_text(item.get("updated_at") or item.get("created_at") or "", max_chars=40),
    }


def _last_task(queue_items: list[dict], published_items: list[dict]) -> dict[str, Any]:
    """Most recent processed item across queue + published. Empty dict if none."""
    candidates: list[dict] = []
    for it in queue_items + published_items:
        ts = it.get("updated_at") or it.get("published_at") or it.get("created_at")
        if isinstance(ts, str) and ts:
            candidates.append((ts, it))  # type: ignore[arg-type]
    if not candidates:
        return {}
    candidates.sort(key=lambda p: p[0], reverse=True)  # type: ignore[index]
    _, latest = candidates[0]
    return _summarize_task(latest)


def _running_tasks(
    queue_items: list[dict], runner_status: str
) -> list[dict[str, Any]]:
    """Items currently traversing the publish stage.

    Definition for V1: queue rows with status ``ready_to_publish`` while the
    runner heartbeat reports ``running``. If the runner is idle/down, no task
    is in flight.
    """
    if runner_status != "running":
        return []
    out: list[dict[str, Any]] = []
    for it in queue_items:
        if it.get("status") == "ready_to_publish":
            out.append(_summarize_task(it))
            if len(out) >= MAX_RUNNING_TASKS:
                break
    return out


def _recent_failures(
    queue_items: list[dict], drafts_items: list[dict], limit: int = MAX_RECENT_FAILURES
) -> list[dict[str, Any]]:
    pool: list[dict] = []
    for it in queue_items:
        if it.get("status") in _FAILED_STATUSES:
            pool.append(it)
    for it in drafts_items:
        if it.get("draft_status") in {"failed_guard", "failed_processing"}:
            pool.append({**it, "status": it.get("draft_status")})
    pool.sort(
        key=lambda x: (x.get("updated_at") or x.get("created_at") or ""),
        reverse=True,
    )
    return [_summarize_failure(it) for it in pool[:limit]]


def _cost_view(
    queue_items: list[dict],
    drafts_items: list[dict],
    published_items: list[dict],
) -> dict[str, float]:
    """Approximate cost. ``current_usage`` = items processed today × per-item
    static cost. ``daily_estimate`` = current_usage scaled to 24h based on
    elapsed UTC hours. Both clamped to non-negative floats with two decimals.
    """
    try:
        per_item = float(os.environ.get(COST_PER_ITEM_ENV, DEFAULT_COST_PER_ITEM_USD))
    except (TypeError, ValueError):
        per_item = DEFAULT_COST_PER_ITEM_USD
    if per_item < 0:
        per_item = 0.0

    # Count distinct items processed today across stages. We deliberately
    # double-count drafts + queue + published because each represents an LLM
    # / pipeline pass; tune via per-item cost.
    items_processed = len(drafts_items) + len(queue_items) + len(published_items)
    current = round(items_processed * per_item, 4)

    now = _now_utc()
    elapsed_hours = max(now.hour + now.minute / 60.0, 1 / 60)
    estimate = round(current * (24.0 / elapsed_hours), 4) if current > 0 else 0.0

    return {"current_usage": current, "daily_estimate": estimate}


def _audit_mode_flag() -> bool:
    val = os.environ.get(AUDIT_MODE_ENV, "")
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _system_status(
    runner_status: str,
    queue_counts: dict[str, int],
    paths: dict[str, Path],
) -> str:
    """Roll-up status.

    - "down": no data files present at all (system never ran today) AND
      runner is down. The latter alone is "degraded" — files may simply be
      from a previous day.
    - "ok": runner running AND failures are not the dominant outcome.
    - "degraded": everything else.
    """
    files_present = any(p.exists() for k, p in paths.items() if k != "heartbeat")
    if not files_present and runner_status == "down":
        return "down"

    failed = queue_counts.get("failed", 0)
    completed = queue_counts.get("completed", 0)
    pending = queue_counts.get("pending", 0)
    total = failed + completed + pending

    failure_dominant = total > 0 and failed > (completed + pending)

    if runner_status == "running" and not failure_dominant:
        return "ok"
    return "degraded"


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_command_center(
    *,
    data_dir: Path | None = None,
    today: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the Command Center response payload.

    All parameters are optional and exist purely for testability. The
    production entry point passes none of them.

    The function never raises. On any unexpected error (e.g. corrupt JSON),
    it falls back to safe defaults — an empty dict for ``last_task``, empty
    arrays, zero counts — and reports ``system_status = "degraded"`` or
    ``"down"`` based on what was reachable.
    """
    paths = _today_paths(data_dir=data_dir, today=today)
    n = now or _now_utc()

    # Defensive loads — never raise.
    try:
        queue_items = _load_items(paths["queue"], "items")
    except Exception:
        queue_items = []
    try:
        drafts_items = _load_items(paths["drafts"], "drafts")
    except Exception:
        drafts_items = []
    try:
        published_items = _load_items(paths["published"], "items")
    except Exception:
        published_items = []
    try:
        hb = _load_heartbeat(paths["heartbeat"])
    except Exception:
        hb = {}

    queue_counts = _bucket_queue(queue_items)
    runner = _runner_view(hb, n)
    last_task = _last_task(queue_items, published_items)
    running = _running_tasks(queue_items, runner["status"])
    failures = _recent_failures(queue_items, drafts_items)
    cost = _cost_view(queue_items, drafts_items, published_items)
    status = _system_status(runner["status"], queue_counts, paths)
    audit = _audit_mode_flag()

    return {
        "system_status": status,
        "queue":         queue_counts,
        "runner":        runner,
        "tasks": {
            "last_task":        last_task,
            "running_tasks":    running,
            "recent_failures":  failures,
        },
        "cost":          cost,
        "audit_mode":    audit,
    }


# ---------------------------------------------------------------------------
# Optional FastAPI adapter (only registered if FastAPI is installed)
# ---------------------------------------------------------------------------

try:  # pragma: no cover — framework-level wiring
    from fastapi import APIRouter

    router = APIRouter(prefix="/api", tags=["command-center"])

    @router.get("/command-center")
    def get_command_center() -> dict[str, Any]:
        """Unified Command Center snapshot. Read-only, sanitized, sub-100ms."""
        return build_command_center()
except Exception:  # FastAPI not installed — adapter unavailable.
    router = None  # type: ignore[assignment]


__all__ = [
    "build_command_center",
    "heartbeat",
    "router",
    "MAX_TEXT_CHARS",
    "MAX_RECENT_FAILURES",
    "MAX_RUNNING_TASKS",
    "HEARTBEAT_RUNNING_WINDOW",
    "HEARTBEAT_IDLE_WINDOW",
]
