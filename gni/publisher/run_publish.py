"""V1 Telegram publisher orchestrator.

Reads the current-day editorial queue, publishes ONLY items whose
``status == "ready_to_publish"`` to Telegram, applies safe-mode rules
(critical priority blocked unless ``manual_approval == True``), and
persists publication metadata.

Updates queue items in place: ``status -> "published"`` plus a
``publication`` block carrying ``message_id``, ``topic_id``, ``topic_env``,
``chat_id``, ``published_at``, ``http_status``.

Failures keep ``status == "ready_to_publish"`` and append the error to
``review_notes``. No infinite retries — the underlying client is bounded.

Exit codes:
  0 = success (per-item failures isolated, not fatal)
  1 = lock held / no queue file / hard failure
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import logging.handlers
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from gni.editorial_queue import queue_manager
from gni.publisher import formatter, telegram_publisher
from gni.publisher.guards import get_editorial_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE_DIR = REPO_ROOT / "gni" / "data" / "queue"
PUBLISHED_DIR = REPO_ROOT / "gni" / "data" / "published"
LOG_DIR = REPO_ROOT / "gni" / "logs"
LOCK_PATH = REPO_ROOT / "gni" / "publisher" / ".lock"

# Spec safety rules:
ALLOWED_STATUS = "ready_to_publish"
BLOCK_PRIORITY_CRITICAL = "critical"  # blocked unless manual_approval=True

# Statuses the publisher MUST refuse to send, even if it somehow received them.
# This is a defense-in-depth allowlist mirror of queue_manager.
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


def _today_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s")
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)
    root.addHandler(stream)


def _atomic_write_json(path: Path, payload: dict | list) -> None:
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


def _append_published_log(path: Path, entries: list[dict]) -> None:
    if not entries:
        return
    existing: list[dict] = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                existing = json.load(f) or []
        except (json.JSONDecodeError, OSError):
            existing = []
    if isinstance(existing, dict):
        existing = existing.get("published") or []
    _atomic_write_json(path, existing + entries)


def _load_published_hash_keys(path: Path) -> set[str]:
    """Read the per-day published log and return the set of headline_hash_keys
    already published. Used to skip duplicates within a day."""
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f) or []
    except (json.JSONDecodeError, OSError):
        return set()
    if isinstance(data, dict):
        data = data.get("published") or []
    keys: set[str] = set()
    for entry in data or []:
        if not isinstance(entry, dict):
            continue
        hk = entry.get("headline_hash_key")
        if hk:
            keys.add(hk)
    return keys


def _validate_payload_via_guards(payload: dict) -> tuple[bool, str]:
    """Run the editorial validator. Returns (ok, first_reason)."""
    try:
        result = get_editorial_validator().validate(payload or {})
    except Exception as exc:  # noqa: BLE001 — guard layer must never crash publisher
        return False, f"guard_exception:{type(exc).__name__}"
    if result.ok:
        return True, ""
    reason = getattr(result, "first_reason", "") or ""
    if not reason and result.violations:
        v = result.violations[0]
        reason = getattr(v, "code", "") or "guard_violation"
    return False, reason or "guard_violation"


def _acquire_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(lock_path, "w")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fp.close()
        return None
    fp.write(str(os.getpid()))
    fp.flush()
    return fp


_QUEUE_RE = re.compile(r"^queue_(\d{8})\.json$")


def _find_queue_file(day: str) -> Path | None:
    primary = QUEUE_DIR / f"queue_{day}.json"
    if primary.exists():
        return primary
    if not QUEUE_DIR.exists():
        return None
    matches: list[tuple[str, Path]] = []
    for p in QUEUE_DIR.iterdir():
        m = _QUEUE_RE.match(p.name)
        if m:
            matches.append((m.group(1), p))
    if not matches:
        return None
    matches.sort(key=lambda t: t[0], reverse=True)
    return matches[0][1]


def _is_publishable(item: dict) -> tuple[bool, str | None]:
    """Return (publishable, skip_reason). Skip reasons never log secrets.

    Status gate is a strict allowlist: only ``ready_to_publish`` may pass.
    Forbidden statuses are reported with a ``forbidden_status_*`` reason so
    the orchestrator can count them separately from generic "wrong status".
    """
    status = (item.get("status") or "").strip()
    if status in PUBLISH_FORBIDDEN_STATUSES:
        return False, f"forbidden_status_{status}"
    if status != ALLOWED_STATUS:
        return False, f"status_{status or 'empty'}"
    priority = (item.get("priority") or "").strip().lower()
    if priority == BLOCK_PRIORITY_CRITICAL and not item.get("manual_approval"):
        return False, "critical_requires_manual_approval"
    if not item.get("template"):
        return False, "missing_template"
    if not item.get("payload"):
        return False, "empty_payload"
    return True, None


def _redact_token_url(s: str) -> str:
    """Defensive: never let a bot token slip into logs."""
    return re.sub(r"/bot[0-9A-Za-z:_\-]+/", "/bot<redacted>/", s)


def run(prod_mode: bool = False) -> int:
    started_iso = _now_utc_iso()
    started_t = time.monotonic()
    day = _today_utc_str()

    log_path = LOG_DIR / f"publish_{day}.log"
    published_path = PUBLISHED_DIR / f"published_{day}.json"

    _setup_logging(log_path)
    logger = logging.getLogger("gni.publisher.run_publish")

    lock_fp = _acquire_lock(LOCK_PATH)
    if lock_fp is None:
        logger.warning("another publish run holds the lock; exiting")
        return 1

    try:
        queue_path = _find_queue_file(day)
        if queue_path is None:
            logger.error("no queue_*.json found under %s", QUEUE_DIR)
            return 1
        logger.info("reading queue from %s", queue_path)

        token = os.environ.get(telegram_publisher.ENV_TOKEN) or ""
        chat_id = os.environ.get(telegram_publisher.ENV_CHAT) or ""
        # --prod forces real publishing; otherwise the existing dry-run env applies.
        dry_run = False if prod_mode else telegram_publisher.is_dry_run()

        if not dry_run and (not token or not chat_id):
            logger.error(
                "telegram credentials missing (set %s and %s, or %s=1 for dry-run)",
                telegram_publisher.ENV_TOKEN,
                telegram_publisher.ENV_CHAT,
                telegram_publisher.ENV_DRY_RUN,
            )
            return 1

        items = queue_manager.load_queue(queue_path)
        if not items:
            logger.info("queue is empty; nothing to publish")
            return 0

        # Build the per-day published-hash-key set BEFORE the loop so we can
        # detect duplicates persisted from earlier runs of the same day.
        published_hash_keys = _load_published_hash_keys(published_path)

        published_entries: list[dict] = []
        # Spec counters (governance contract).
        counters = {
            "items_checked": 0,
            "items_published": 0,
            "items_blocked_status": 0,
            "items_failed_guard": 0,
            "items_duplicate_blocked": 0,
            "items_failed_telegram": 0,
            # Auxiliary counters kept for ops visibility (not required by spec).
            "items_blocked_critical": 0,
            "items_topic_fallback": 0,
            "items_dry_run": 0,
        }

        for item in items:
            counters["items_checked"] += 1

            ok_to_publish, skip_reason = _is_publishable(item)
            if not ok_to_publish:
                if skip_reason and skip_reason.startswith("forbidden_status_"):
                    counters["items_blocked_status"] += 1
                    logger.warning(
                        "publish_blocked queue_id=%s reason=%s",
                        item.get("queue_id"),
                        skip_reason,
                    )
                elif skip_reason == "critical_requires_manual_approval":
                    counters["items_blocked_critical"] += 1
                    logger.info(
                        "skipped queue_id=%s reason=%s",
                        item.get("queue_id"),
                        skip_reason,
                    )
                # Other status mismatches (e.g. validated, approved, …) are
                # normal skips — they simply aren't ready_to_publish yet.
                continue

            # Duplicate protection: a headline_hash_key already in the
            # per-day published log must NEVER be sent again, even if it
            # somehow re-entered the queue.
            hk = item.get("headline_hash_key") or ""
            if hk and hk in published_hash_keys:
                counters["items_duplicate_blocked"] += 1
                queue_manager.update_status(
                    item, "published",
                    note="duplicate_blocked: headline_hash_key already published today",
                )
                logger.warning(
                    "duplicate_blocked queue_id=%s headline_hash_key=%s",
                    item.get("queue_id"), hk,
                )
                continue

            template = item.get("template") or ""
            payload = item.get("payload") or {}

            # Guard validation runs immediately before send. A validated draft
            # may still fail here if downstream mutations corrupted the payload.
            guard_ok, guard_reason = _validate_payload_via_guards(payload)
            if not guard_ok:
                counters["items_failed_guard"] += 1
                queue_manager.update_status(
                    item, "failed_guard",
                    note=f"publish_blocked: guard_failed reason={guard_reason}",
                )
                logger.error(
                    "guard_failed queue_id=%s template=%s reason=%s",
                    item.get("queue_id"), template, guard_reason,
                )
                continue

            text = formatter.format_payload(template, payload)
            if not text:
                counters["items_failed_telegram"] += 1
                queue_manager.update_status(
                    item, "ready_to_publish",
                    note=f"publish_failed: formatter_empty_text template={template}",
                )
                logger.error(
                    "format_failed queue_id=%s template=%s",
                    item.get("queue_id"), template,
                )
                continue

            topic_env, topic_id = telegram_publisher.resolve_topic(item)
            # If the topic env-var isn't configured, fall back to the main
            # channel (no message_thread_id). Operators can still see which
            # routing slot would have been used via topic_env.
            if not topic_id:
                counters["items_topic_fallback"] += 1
                logger.info(
                    "topic_unset_fallback queue_id=%s topic_env=%s "
                    "(publishing to main channel)",
                    item.get("queue_id"), topic_env,
                )
                topic_id = None

            if dry_run:
                counters["items_dry_run"] += 1
                logger.info(
                    "DRY_RUN queue_id=%s template=%s priority=%s topic_env=%s "
                    "preview_chars=%d",
                    item.get("queue_id"), template,
                    item.get("priority"), topic_env, len(text),
                )
                continue

            result = telegram_publisher.send_to_telegram(
                text,
                token=token,
                chat_id=chat_id,
                message_thread_id=topic_id,
            )

            if result["ok"]:
                counters["items_published"] += 1
                published_at = _now_utc_iso()
                pub_meta = {
                    "message_id": result["message_id"],
                    "topic_env": topic_env,
                    "topic_id": topic_id,
                    "chat_id": chat_id,
                    "published_at": published_at,
                    "http_status": result["http_status"],
                    "attempts": result["attempts"],
                }
                item["publication"] = pub_meta
                queue_manager.update_status(item, "published")
                # Track in-process so a same-run double appearance can't slip.
                if hk:
                    published_hash_keys.add(hk)
                logger.info(
                    "telegram_published queue_id=%s template=%s priority=%s "
                    "topic_env=%s message_id=%s attempts=%d",
                    item.get("queue_id"),
                    template,
                    item.get("priority"),
                    topic_env,
                    result["message_id"],
                    result["attempts"],
                )
                published_entries.append(
                    {
                        "queue_id": item.get("queue_id"),
                        "draft_id": item.get("draft_id"),
                        "headline_hash_key": hk,
                        "template": template,
                        "priority": item.get("priority"),
                        "category": (
                            (item.get("source_item") or {}).get("category") or ""
                        ),
                        "topic_env": topic_env,
                        "topic_id": topic_id,
                        "message_id": result["message_id"],
                        "published_at": published_at,
                        "text_preview": text[:200],
                    }
                )
            else:
                counters["items_failed_telegram"] += 1
                err = _redact_token_url(str(result.get("error") or "unknown_error"))
                queue_manager.update_status(
                    item, "ready_to_publish",
                    note=(
                        f"publish_failed: http={result.get('http_status')} "
                        f"attempts={result.get('attempts')} reason={err}"
                    ),
                )
                logger.error(
                    "telegram_failed queue_id=%s http=%s attempts=%s reason=%s",
                    item.get("queue_id"),
                    result.get("http_status"),
                    result.get("attempts"),
                    err,
                )

        # Persist updated queue (in place) and append to published log.
        queue_manager.save_queue(queue_path, items, day_utc=day)
        if published_entries:
            _append_published_log(published_path, published_entries)

        finished_iso = _now_utc_iso()
        duration_s = round(time.monotonic() - started_t, 3)

        summary = {
            "run_started_at": started_iso,
            "run_finished_at": finished_iso,
            "duration_seconds": duration_s,
            "input_file": str(queue_path),
            "queue_file_updated": str(queue_path),
            "published_log_file": str(published_path) if published_entries else None,
            "prod_mode": prod_mode,
            "dry_run": dry_run,
            **counters,
        }

        logger.info(
            "run done duration=%.3fs checked=%d published=%d "
            "blocked_status=%d failed_guard=%d duplicate_blocked=%d "
            "failed_telegram=%d blocked_critical=%d topic_fallback=%d dry_run=%d",
            duration_s,
            counters["items_checked"],
            counters["items_published"],
            counters["items_blocked_status"],
            counters["items_failed_guard"],
            counters["items_duplicate_blocked"],
            counters["items_failed_telegram"],
            counters["items_blocked_critical"],
            counters["items_topic_fallback"],
            counters["items_dry_run"],
        )
        sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0
    finally:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fp.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="GNI V1 Telegram publisher")
    parser.add_argument(
        "--prod",
        action="store_true",
        help=(
            "Force real publishing to Telegram (overrides TELEGRAM_DRY_RUN). "
            "Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        ),
    )
    args = parser.parse_args()
    return run(prod_mode=bool(args.prod))


if __name__ == "__main__":
    sys.exit(main())
