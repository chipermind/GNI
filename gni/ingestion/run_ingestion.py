"""V1 ingestion orchestrator.

Single-process, cron-driven. One run:
  1. Acquire flock (prevents cron overlap; P0-01 mitigation)
  2. Load sources.json
  3. Per-source fetch (per-source try/except + timeout; P0-02 mitigation)
  4. Normalize + schema-validate (P0-03 mitigation)
  5. Dedup against today's JSON
  6. Atomic write of merged day-file (temp + os.replace; P0-04 mitigation)
  7. Append DLQ + write run-manifest

Exit codes:
  0 = success (even if some sources failed; per-source errors are isolated)
  1 = lock held / hard failure
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import logging.handlers
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from gni.ingestion import collector, dedup, normalizer

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_PATH = Path(__file__).resolve().parent / "sources.json"
DATA_DIR = REPO_ROOT / "gni" / "data" / "raw"
STATE_DIR = REPO_ROOT / "gni" / "data" / "state"
STATE_PATH = STATE_DIR / "seen_hashes.json"
LOG_DIR = REPO_ROOT / "gni" / "logs"
LOCK_PATH = REPO_ROOT / "gni" / "ingestion" / ".lock"

SCHEMA_VERSION = 1
SILENT_FEED_THRESHOLD = 3  # consecutive 0-entry runs -> source_warning


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


def _append_dlq(dlq_path: Path, entries: list[dict]) -> None:
    if not entries:
        return
    existing: list[dict] = []
    if dlq_path.exists():
        try:
            with dlq_path.open("r", encoding="utf-8") as f:
                existing = json.load(f) or []
        except (json.JSONDecodeError, OSError):
            existing = []
    merged = existing + entries
    _atomic_write_json(dlq_path, merged)


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


def run() -> int:
    started_iso = _now_utc_iso()
    started_t = time.monotonic()
    day = _today_utc_str()

    headlines_path = DATA_DIR / f"headlines_{day}_UTC.json"
    dlq_path = DATA_DIR / f"dlq_{day}_UTC.json"
    manifest_path = DATA_DIR / f"manifest_{day}_UTC.json"
    log_path = LOG_DIR / f"ingestion_{day}_UTC.log"

    _setup_logging(log_path)
    logger = logging.getLogger("gni.ingestion")

    lock_fp = _acquire_lock(LOCK_PATH)
    if lock_fp is None:
        logger.warning("another ingestion run holds the lock; exiting")
        return 1

    try:
        sources = collector.load_sources(SOURCES_PATH)
        logger.info("loaded %d source definitions", len(sources))

        results = collector.collect_all(sources)

        # Cross-day state: seed dedup sets and prepare zero-streak tracking.
        state = dedup.load_state(STATE_PATH)
        state = dedup.prune_seen(state, days=dedup.STATE_RETENTION_DAYS)
        state_hashes, state_urls = dedup.state_keys(state)
        zero_streaks: dict = state.setdefault("source_zero_streaks", {})

        existing = dedup.load_existing(headlines_path)
        seen_hashes, seen_urls = dedup.existing_keys(existing)
        seen_hashes |= state_hashes
        seen_urls |= state_urls

        all_new: list[dict] = []
        all_dlq: list[dict] = []
        per_source_summary: list[dict] = []
        silent_feed_warnings: list[str] = []

        collected_at = _now_utc_iso()

        for r in results:
            src = r["source"]
            name = src.get("source_name", "<unnamed>")
            if not r["ok"]:
                # Network/parse failure: do NOT touch zero_streak (different signal).
                per_source_summary.append(
                    {
                        "source_name": name,
                        "ok": False,
                        "error": r["error"],
                        "fetched": 0,
                        "normalized": 0,
                        "new": 0,
                        "duplicates": 0,
                        "dlq": 0,
                        "zero_streak": int(zero_streaks.get(name, 0)),
                        "silent_warning": False,
                    }
                )
                continue

            entries = r["entries"]
            ok_items, dlq_items = normalizer.normalize_batch(
                entries, src, collected_at=collected_at
            )
            new_items, dup_count = dedup.filter_new(
                ok_items, seen_hashes, seen_urls
            )
            all_new.extend(new_items)
            all_dlq.extend(dlq_items)

            # Silent-feed detection: ok response but 0 entries N runs in a row.
            silent_warning = False
            if len(entries) == 0:
                streak = int(zero_streaks.get(name, 0)) + 1
                zero_streaks[name] = streak
                if streak >= SILENT_FEED_THRESHOLD:
                    silent_warning = True
                    silent_feed_warnings.append(name)
                    logger.warning(
                        "source_warning name=%s consecutive_zero_runs=%d "
                        "threshold=%d",
                        name,
                        streak,
                        SILENT_FEED_THRESHOLD,
                    )
            else:
                zero_streaks[name] = 0

            per_source_summary.append(
                {
                    "source_name": name,
                    "ok": True,
                    "error": None,
                    "fetched": len(entries),
                    "normalized": len(ok_items),
                    "new": len(new_items),
                    "duplicates": dup_count,
                    "dlq": len(dlq_items),
                    "zero_streak": int(zero_streaks.get(name, 0)),
                    "silent_warning": silent_warning,
                }
            )

        merged_items = existing + all_new
        payload = {
            "schema_version": SCHEMA_VERSION,
            "day_utc": day,
            "last_updated_at": _now_utc_iso(),
            "count": len(merged_items),
            "items": merged_items,
        }
        _atomic_write_json(headlines_path, payload)

        if all_dlq:
            _append_dlq(dlq_path, all_dlq)

        # Persist cross-day dedup state.
        now_iso = _now_utc_iso()
        dedup.append_to_state(state, all_new, now_iso)
        state["updated_at"] = now_iso
        state["schema_version"] = dedup.STATE_SCHEMA_VERSION
        _atomic_write_json(STATE_PATH, state)

        finished_iso = _now_utc_iso()
        duration_s = round(time.monotonic() - started_t, 3)

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "day_utc": day,
            "started_at": started_iso,
            "finished_at": finished_iso,
            "duration_seconds": duration_s,
            "sources_total": len(sources),
            "sources_ok": sum(1 for s in per_source_summary if s["ok"]),
            "sources_failed": sum(1 for s in per_source_summary if not s["ok"]),
            "items_fetched": sum(s["fetched"] for s in per_source_summary),
            "items_new": sum(s["new"] for s in per_source_summary),
            "items_duplicates": sum(s["duplicates"] for s in per_source_summary),
            "items_dlq": sum(s["dlq"] for s in per_source_summary),
            "items_in_dayfile": len(merged_items),
            "silent_feed_warnings": silent_feed_warnings,
            "state_seen_count": len(state.get("seen_items", [])),
            "per_source": per_source_summary,
        }
        _atomic_write_json(manifest_path, manifest)

        logger.info(
            "run done duration=%.3fs sources_ok=%d/%d new=%d dups=%d dlq=%d total=%d",
            duration_s,
            manifest["sources_ok"],
            manifest["sources_total"],
            manifest["items_new"],
            manifest["items_duplicates"],
            manifest["items_dlq"],
            manifest["items_in_dayfile"],
        )
        return 0
    finally:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fp.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="GNI V1 ingestion runner")
    parser.parse_args()
    return run()


if __name__ == "__main__":
    sys.exit(main())
