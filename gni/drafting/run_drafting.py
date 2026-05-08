"""V1 drafting orchestrator.

Reads the current-day (or latest) headlines JSON, generates draft payloads,
validates them with the editorial guards, and persists a per-day drafts JSON.

Single-process, cron-driven. Same write-safety pattern as ingestion:
flock + tempfile + os.replace.

Exit codes:
  0 = success
  1 = lock held / no headlines file found / hard failure
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

from gni.drafting import draft_builder

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "gni" / "data" / "raw"
DRAFTS_DIR = REPO_ROOT / "gni" / "data" / "drafts"
LOG_DIR = REPO_ROOT / "gni" / "logs"
LOCK_PATH = REPO_ROOT / "gni" / "drafting" / ".lock"

SCHEMA_VERSION = 1


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


_HEADLINES_RE = re.compile(r"^headlines_(\d{8})(?:_UTC)?\.json$")


def _find_headlines_file(day: str) -> Path | None:
    """Prefer current-day file, fall back to most recent headlines_*.json."""
    candidates = [
        RAW_DIR / f"headlines_{day}_UTC.json",
        RAW_DIR / f"headlines_{day}.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    if not RAW_DIR.exists():
        return None
    matches: list[tuple[str, Path]] = []
    for p in RAW_DIR.iterdir():
        m = _HEADLINES_RE.match(p.name)
        if m:
            matches.append((m.group(1), p))
    if not matches:
        return None
    matches.sort(key=lambda t: t[0], reverse=True)
    return matches[0][1]


def _load_headlines(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "items" in data:
        return list(data["items"])
    if isinstance(data, list):
        return data
    return []


def _load_existing_drafts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logging.getLogger(__name__).error(
            "drafts file unreadable %s: %r", path, exc
        )
        return []
    if isinstance(data, dict) and "drafts" in data:
        return list(data["drafts"])
    if isinstance(data, list):
        return data
    return []


def run() -> int:
    started_iso = _now_utc_iso()
    started_t = time.monotonic()
    day = _today_utc_str()

    drafts_path = DRAFTS_DIR / f"drafts_{day}.json"
    log_path = LOG_DIR / f"drafting_{day}.log"

    _setup_logging(log_path)
    logger = logging.getLogger("gni.drafting")

    lock_fp = _acquire_lock(LOCK_PATH)
    if lock_fp is None:
        logger.warning("another drafting run holds the lock; exiting")
        return 1

    try:
        headlines_path = _find_headlines_file(day)
        if headlines_path is None:
            logger.error("no headlines_*.json found under %s", RAW_DIR)
            return 1
        logger.info("reading headlines from %s", headlines_path)

        headlines = _load_headlines(headlines_path)
        existing = _load_existing_drafts(drafts_path)
        existing_keys = {
            d.get("headline_hash_key") for d in existing if d.get("headline_hash_key")
        }

        new_drafts: list[dict] = []
        counters = {
            "items_read": len(headlines),
            "items_classified": 0,
            "items_ignored": 0,
            "items_alerta": 0,
            "items_briefing": 0,
            "low_confidence_count": 0,
            "classifier_router_mismatch_count": 0,
            "drafts_created": 0,
            "drafts_validated": 0,
            "drafts_failed_guard": 0,
            "drafts_needs_review": 0,
            "drafts_needs_editorial_build": 0,
            "drafts_ignored": 0,
            "drafts_failed_processing": 0,
            "duplicate_drafts": 0,
        }

        for h in headlines:
            hk = h.get("hash_key") or h.get("id")
            if not hk:
                continue
            if hk in existing_keys:
                counters["duplicate_drafts"] += 1
                continue

            # Per-headline exception isolation: a single broken item must
            # never abort the whole batch. On exception we emit a terminal
            # "failed_processing" draft so the operator can investigate.
            try:
                draft = draft_builder.build_draft(h)
            except Exception as exc:  # noqa: BLE001 — isolation boundary
                logger.exception(
                    "build_draft failed for hash_key=%s; recording failed_processing",
                    hk,
                )
                draft = {
                    "draft_id": f"draft_{hk}",
                    "headline_hash_key": hk,
                    "template": "",
                    "route_confidence": 0.0,
                    "draft_status": "failed_processing",
                    "priority": "medium",
                    "payload": {},
                    "guard_errors": [],
                    "classifier_decision": "",
                    "classifier_confidence": 0.0,
                    "classifier_reasons": [],
                    "classifier_risk_flags": ["build_exception"],
                    "router_template": "",
                    "classifier_router_match": False,
                    "mismatch_reason": "",
                    "processing_error": f"{type(exc).__name__}: {exc}",
                    "source_item": h,
                    "created_at": _now_utc_iso(),
                }

            new_drafts.append(draft)
            existing_keys.add(hk)

            counters["drafts_created"] += 1
            counters["items_classified"] += 1

            decision = draft.get("classifier_decision")
            if decision == "ignore":
                counters["items_ignored"] += 1
            elif decision == "alerta":
                counters["items_alerta"] += 1
            elif decision == "briefing":
                counters["items_briefing"] += 1

            if not draft.get("classifier_router_match", True):
                counters["classifier_router_mismatch_count"] += 1

            try:
                if float(draft.get("classifier_confidence", 0.0)) < \
                        draft_builder.CONFIDENCE_REVIEW_THRESHOLD:
                    counters["low_confidence_count"] += 1
            except (TypeError, ValueError):
                counters["low_confidence_count"] += 1

            status = draft.get("draft_status")
            if status == "validated":
                counters["drafts_validated"] += 1
            elif status == "failed_guard":
                counters["drafts_failed_guard"] += 1
            elif status == "needs_review":
                counters["drafts_needs_review"] += 1
            elif status == "needs_editorial_build":
                counters["drafts_needs_editorial_build"] += 1
            elif status == "ignored":
                counters["drafts_ignored"] += 1
            elif status == "failed_processing":
                counters["drafts_failed_processing"] += 1

        merged = existing + new_drafts
        payload = {
            "schema_version": SCHEMA_VERSION,
            "day_utc": day,
            "last_updated_at": _now_utc_iso(),
            "count": len(merged),
            "drafts": merged,
        }
        _atomic_write_json(drafts_path, payload)

        finished_iso = _now_utc_iso()
        duration_s = round(time.monotonic() - started_t, 3)

        summary = {
            "run_started_at": started_iso,
            "run_finished_at": finished_iso,
            "duration_seconds": duration_s,
            "input_file": str(headlines_path),
            "output_file": str(drafts_path),
            **counters,
            "drafts_in_dayfile": len(merged),
        }

        logger.info(
            "run done duration=%.3fs read=%d classified=%d "
            "ignore=%d alerta=%d briefing=%d low_conf=%d mismatch=%d "
            "created=%d validated=%d failed_guard=%d "
            "needs_review=%d needs_editorial_build=%d ignored=%d "
            "failed_processing=%d duplicates=%d",
            duration_s,
            summary["items_read"],
            summary["items_classified"],
            summary["items_ignored"],
            summary["items_alerta"],
            summary["items_briefing"],
            summary["low_confidence_count"],
            summary["classifier_router_mismatch_count"],
            summary["drafts_created"],
            summary["drafts_validated"],
            summary["drafts_failed_guard"],
            summary["drafts_needs_review"],
            summary["drafts_needs_editorial_build"],
            summary["drafts_ignored"],
            summary["drafts_failed_processing"],
            summary["duplicate_drafts"],
        )

        # Print summary to stdout (cron tail / pipeline-friendly).
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
    parser = argparse.ArgumentParser(description="GNI V1 drafting runner")
    parser.parse_args()
    return run()


if __name__ == "__main__":
    sys.exit(main())
