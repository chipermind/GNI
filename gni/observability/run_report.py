"""GNI V1 observability orchestrator.

Resolves today's (or ``--day YYYYMMDD``) artefact paths, aggregates the
ten V1 metrics via :mod:`gni.observability.metrics`, atomically writes
``gni/data/metrics/metrics_<DAY>.json`` and prints a human-readable
summary to stdout.

Acceptance contract (per spec):
  * Runs even if any/all input files are missing — silent zero-fill.
  * Never raises on empty / corrupt input.
  * stdout summary is human-readable; JSON file is the machine surface.

Usage:
    python -m gni.observability.run_report
    python -m gni.observability.run_report --day 20260505
    python -m gni.observability.run_report --root /path/to/repo
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gni.observability.metrics import aggregate

# Repo root (gni/observability/run_report.py -> gni/.. -> repo root).
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _validate_day(day: str) -> str:
    """Reject malformed day strings before touching the filesystem."""
    try:
        datetime.strptime(day, "%Y%m%d")
    except ValueError as e:
        raise SystemExit(f"--day must be YYYYMMDD, got {day!r}: {e}") from None
    return day


def resolve_paths(root: Path, day: str) -> dict[str, Path]:
    """Return all known artefact paths for ``day`` under ``root``.

    Existence is *not* checked here; the metrics layer tolerates missing
    files. Logs and the ingestion manifest are supplementary inputs used
    only to enrich ``duplicate_count``.
    """
    raw = root / "gni" / "data" / "raw"
    drafts = root / "gni" / "data" / "drafts"
    queue = root / "gni" / "data" / "queue"
    published = root / "gni" / "data" / "published"
    logs = root / "gni" / "logs"
    return {
        "headlines": raw / f"headlines_{day}_UTC.json",
        "drafts":    drafts / f"drafts_{day}.json",
        "queue":     queue / f"queue_{day}.json",
        "published": published / f"published_{day}.json",
        "manifest":  raw / f"manifest_{day}_UTC.json",
        "drafting_log": logs / f"drafting_{day}.log",
        "queue_log":    logs / f"queue_{day}.log",
    }


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def _atomic_write_json(target: Path, payload: Any) -> None:
    """Write JSON via temp-file + os.replace so a crash never leaves a half file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Human-readable summary
# ---------------------------------------------------------------------------


def _fmt_bool(b: bool) -> str:
    return "yes" if b else "no "


def render_summary(day: str, report: dict[str, Any], out_path: Path) -> str:
    """Build the stdout summary block. No I/O here — pure string."""
    m = report["metrics"]
    seen = report["inputs_seen"]
    bd = report["breakdown"]

    lines: list[str] = []
    lines.append("=" * 64)
    lines.append(f"GNI V1 metrics — day={day} (UTC)")
    lines.append("=" * 64)
    lines.append("")
    lines.append("inputs seen")
    lines.append("-" * 64)
    for k in ("headlines", "drafts", "queue", "published",
              "manifest", "drafting_log", "queue_log"):
        lines.append(f"  [{_fmt_bool(seen.get(k, False))}] {k}")
    lines.append("")
    lines.append("pipeline stages")
    lines.append("-" * 64)
    lines.append(f"  ingestion : headlines_collected     = {m['headlines_collected']:>6}")
    lines.append(f"  drafting  : drafts_created          = {m['drafts_created']:>6}")
    lines.append(f"              drafts_validated        = {m['drafts_validated']:>6}")
    lines.append(f"              drafts_failed_guard     = {m['drafts_failed_guard']:>6}")
    lines.append(f"  queue     : queue_ready_to_publish  = {m['queue_ready_to_publish']:>6}")
    lines.append(f"              queue_needs_review      = {m['queue_needs_review']:>6}")
    lines.append(f"              critical_pending_count  = {m['critical_pending_count']:>6}")
    lines.append(f"  publish   : published_count         = {m['published_count']:>6}")
    lines.append(f"              failed_publish_count    = {m['failed_publish_count']:>6}")
    lines.append(f"  cross    : duplicate_count          = {m['duplicate_count']:>6}")
    lines.append("")
    lines.append("breakdown")
    lines.append("-" * 64)
    qstatus = bd["queue"]["by_status"]
    lines.append(f"  queue.total = {bd['queue']['total']}")
    for s in (
        "validated", "needs_review", "failed_guard", "needs_editorial_build",
        "approved", "rejected", "ready_to_publish", "published",
    ):
        lines.append(f"    {s:<24} = {qstatus.get(s, 0)}")
    dups = bd["duplicates"]
    lines.append(
        f"  duplicate sources: ingestion={dups['ingestion_manifest']} "
        f"drafting={dups['drafting_log']} queue={dups['queue_log']}"
    )
    lines.append("")
    lines.append(f"wrote {out_path}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="gni.observability.run_report",
        description="Aggregate GNI V1 pipeline metrics for a UTC day.",
    )
    p.add_argument(
        "--day",
        default=None,
        help="UTC day in YYYYMMDD form. Default: today (UTC).",
    )
    p.add_argument(
        "--root",
        default=str(_DEFAULT_ROOT),
        help="Repository root. Default: derived from this file's location.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable summary on stdout.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    day = _validate_day(args.day) if args.day else _today_utc()
    root = Path(args.root).resolve()

    paths = resolve_paths(root, day)
    report = aggregate(
        headlines_path=paths["headlines"],
        drafts_path=paths["drafts"],
        queue_path=paths["queue"],
        published_path=paths["published"],
        manifest_path=paths["manifest"],
        drafting_log=paths["drafting_log"],
        queue_log=paths["queue_log"],
    )

    # Stamp the report so consumers know what window it covers.
    report["day_utc"] = day
    report["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report["schema_version"] = 1

    out_path = root / "gni" / "data" / "metrics" / f"metrics_{day}.json"
    _atomic_write_json(out_path, report)

    if not args.quiet:
        print(render_summary(day, report, out_path))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
