"""V1 editorial queue orchestrator.

Reads the current-day drafts JSON, materializes queue items per V1 routing
rules, deduplicates against the existing queue file, and persists the
merged queue. Same write-safety pattern as ingestion / drafting:
``fcntl.flock`` + ``tempfile + os.replace``.

Exit codes:
  0 = success
  1 = lock held / no drafts file / hard failure
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
import time
from datetime import datetime, timezone
from pathlib import Path

from gni.editorial_queue import queue_manager

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAFTS_DIR = REPO_ROOT / "gni" / "data" / "drafts"
QUEUE_DIR = REPO_ROOT / "gni" / "data" / "queue"
LOG_DIR = REPO_ROOT / "gni" / "logs"
LOCK_PATH = REPO_ROOT / "gni" / "editorial_queue" / ".lock"


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


_DRAFTS_RE = re.compile(r"^drafts_(\d{8})\.json$")


def _find_drafts_file(day: str) -> Path | None:
    """Prefer current-day drafts file; fall back to most recent ``drafts_*.json``."""
    primary = DRAFTS_DIR / f"drafts_{day}.json"
    if primary.exists():
        return primary
    if not DRAFTS_DIR.exists():
        return None
    matches: list[tuple[str, Path]] = []
    for p in DRAFTS_DIR.iterdir():
        m = _DRAFTS_RE.match(p.name)
        if m:
            matches.append((m.group(1), p))
    if not matches:
        return None
    matches.sort(key=lambda t: t[0], reverse=True)
    return matches[0][1]


def run() -> int:
    started_iso = _now_utc_iso()
    started_t = time.monotonic()
    day = _today_utc_str()

    queue_path = QUEUE_DIR / f"queue_{day}.json"
    log_path = LOG_DIR / f"queue_{day}.log"

    _setup_logging(log_path)
    logger = logging.getLogger("gni.editorial_queue")

    lock_fp = _acquire_lock(LOCK_PATH)
    if lock_fp is None:
        logger.warning("another queue run holds the lock; exiting")
        return 1

    try:
        drafts_path = _find_drafts_file(day)
        if drafts_path is None:
            logger.error("no drafts_*.json found under %s", DRAFTS_DIR)
            return 1
        logger.info("reading drafts from %s", drafts_path)

        drafts = queue_manager.load_drafts(drafts_path)
        existing = queue_manager.load_queue(queue_path)
        existing_draft_ids = {
            q.get("draft_id") for q in existing if q.get("draft_id")
        }

        new_items, duplicate_count = queue_manager.build_queue_items(
            drafts, existing_draft_ids=existing_draft_ids
        )
        merged = existing + new_items

        queue_manager.save_queue(queue_path, merged, day_utc=day)

        finished_iso = _now_utc_iso()
        duration_s = round(time.monotonic() - started_t, 3)

        status_counts = queue_manager.summarize_queue(merged)
        new_status_counts = queue_manager.summarize_queue(new_items)

        summary = {
            "run_started_at": started_iso,
            "run_finished_at": finished_iso,
            "duration_seconds": duration_s,
            "input_file": str(drafts_path),
            "output_file": str(queue_path),
            "drafts_read": len(drafts),
            "queue_new": len(new_items),
            "queue_duplicates": duplicate_count,
            "queue_total": status_counts.get("total", 0),
            "new_by_status": {
                k: v for k, v in new_status_counts.items() if k != "total"
            },
            "queue_by_status": {
                k: v for k, v in status_counts.items() if k != "total"
            },
        }

        logger.info(
            "run done duration=%.3fs drafts_read=%d new=%d dups=%d total=%d "
            "ready=%d needs_review=%d failed_guard=%d needs_editorial=%d",
            duration_s,
            summary["drafts_read"],
            summary["queue_new"],
            summary["queue_duplicates"],
            summary["queue_total"],
            status_counts.get("ready_to_publish", 0),
            status_counts.get("needs_review", 0),
            status_counts.get("failed_guard", 0),
            status_counts.get("needs_editorial_build", 0),
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
    parser = argparse.ArgumentParser(description="GNI V1 editorial queue runner")
    parser.parse_args()
    return run()


if __name__ == "__main__":
    sys.exit(main())
