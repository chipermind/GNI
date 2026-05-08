"""V1 human approval gate for the editorial queue (local CLI).

Commands:
  list                 list items requiring operator action
  approve              approve a queue item (-> ready_to_publish)
  reject               reject a queue item (-> rejected)

All commands hold the same flock used by ``run_queue.py`` so the queue file
is never corrupted by concurrent edits. Audit events are appended to
``gni/data/queue/approvals_YYYYMMDD.json``.

Exit codes:
  0 = success
  1 = lock held / queue not found / item not found / hard failure
  2 = invalid arguments / illegal transition
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from gni.editorial_queue import queue_manager

REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE_DIR = REPO_ROOT / "gni" / "data" / "queue"
LOCK_PATH = REPO_ROOT / "gni" / "editorial_queue" / ".lock"

PRIORITIES_REQUIRING_APPROVAL = frozenset({"critical", "high"})

logger = logging.getLogger("gni.editorial_queue.approve")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _setup_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


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


_QUEUE_RE = re.compile(r"^queue_(\d{8})\.json$")


def _resolve_queue_file(day: str | None) -> Path | None:
    if day:
        candidate = QUEUE_DIR / f"queue_{day}.json"
        return candidate if candidate.exists() else None
    today = QUEUE_DIR / f"queue_{_today_utc_str()}.json"
    if today.exists():
        return today
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


def _audit_path_for(queue_path: Path) -> Path:
    """approvals_YYYYMMDD.json beside queue_YYYYMMDD.json."""
    name = queue_path.name.replace("queue_", "approvals_", 1)
    return queue_path.parent / name


def _append_audit(audit_path: Path, event: dict) -> None:
    existing: list[dict] = []
    if audit_path.exists():
        try:
            with audit_path.open("r", encoding="utf-8") as f:
                existing = json.load(f) or []
        except (json.JSONDecodeError, OSError):
            existing = []
    if isinstance(existing, dict):
        existing = existing.get("events") or []
    _atomic_write_json(audit_path, existing + [event])


def _find_item(items: list[dict], queue_id: str) -> dict | None:
    for it in items:
        if it.get("queue_id") == queue_id:
            return it
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    queue_path = _resolve_queue_file(args.day)
    if queue_path is None:
        print("ERROR: no queue file found", file=sys.stderr)
        return 1

    items = queue_manager.load_queue(queue_path)
    pending = [
        it for it in items
        if (it.get("status") or "") == "needs_review"
    ]

    print(f"queue_file: {queue_path}")
    print(f"total: {len(items)} | needs_review: {len(pending)}")
    print("-" * 88)
    if not pending:
        print("(no items requiring review)")
        return 0

    print(f"{'QUEUE_ID':40s} {'TEMPLATE':10s} {'PRIO':9s}  TITLE")
    print("-" * 88)
    for it in pending:
        qid = (it.get("queue_id") or "")[:40]
        tmpl = (it.get("template") or "")[:10]
        prio = (it.get("priority") or "")[:9]
        payload = it.get("payload") or {}
        title = payload.get("title") or payload.get("text") or (
            (it.get("source_item") or {}).get("title") or ""
        )
        title = re.sub(r"\s+", " ", title).strip()[:90]
        marker = "*" if prio in PRIORITIES_REQUIRING_APPROVAL else " "
        print(f"{qid:40s} {tmpl:10s} {prio:9s}{marker} {title}")
    print("-" * 88)
    print("* = approval required (high/critical)")
    return 0


def _do_transition(
    queue_id: str,
    operator: str,
    *,
    action: str,                 # "approve" | "reject"
    reason: str | None,
    day: str | None,
) -> int:
    if not operator.strip():
        print("ERROR: --operator must be non-empty", file=sys.stderr)
        return 2
    if action == "reject" and not (reason and reason.strip()):
        print("ERROR: --reason required for reject", file=sys.stderr)
        return 2

    queue_path = _resolve_queue_file(day)
    if queue_path is None:
        print("ERROR: no queue file found", file=sys.stderr)
        return 1

    lock_fp = _acquire_lock(LOCK_PATH)
    if lock_fp is None:
        print("ERROR: another queue process holds the lock", file=sys.stderr)
        return 1

    try:
        items = queue_manager.load_queue(queue_path)
        item = _find_item(items, queue_id)
        if item is None:
            print(f"ERROR: queue_id not found: {queue_id}", file=sys.stderr)
            return 1

        prev_status = item.get("status", "")
        priority = (item.get("priority") or "").strip().lower()
        now = _now_utc_iso()

        if action == "approve":
            try:
                queue_manager.update_status(
                    item, "ready_to_publish",
                    note=f"approved_by={operator} at={now}",
                )
            except ValueError as exc:
                print(f"ERROR: illegal transition: {exc}", file=sys.stderr)
                return 2
            item["approved_by"] = operator
            item["approved_at"] = now
            # Critical needs the explicit safety flag the publisher checks.
            if priority == "critical":
                item["manual_approval"] = True
            new_status = "ready_to_publish"
            event = {
                "event": "approve",
                "queue_id": queue_id,
                "operator": operator,
                "at": now,
                "prev_status": prev_status,
                "new_status": new_status,
                "priority": priority,
                "manual_approval": bool(item.get("manual_approval")),
            }

        elif action == "reject":
            try:
                queue_manager.update_status(
                    item, "rejected",
                    note=(
                        f"rejected_by={operator} at={now} "
                        f"reason={reason.strip()}"
                    ),
                )
            except ValueError as exc:
                print(f"ERROR: illegal transition: {exc}", file=sys.stderr)
                return 2
            item["rejected_by"] = operator
            item["rejected_at"] = now
            item["rejection_reason"] = reason.strip()
            new_status = "rejected"
            event = {
                "event": "reject",
                "queue_id": queue_id,
                "operator": operator,
                "at": now,
                "prev_status": prev_status,
                "new_status": new_status,
                "priority": priority,
                "reason": reason.strip(),
            }
        else:
            print(f"ERROR: unknown action {action!r}", file=sys.stderr)
            return 2

        # Persist queue + audit.
        day_utc = queue_path.name[len("queue_"):-len(".json")]
        queue_manager.save_queue(queue_path, items, day_utc=day_utc)
        _append_audit(_audit_path_for(queue_path), event)

        print(json.dumps(event, ensure_ascii=False))
        return 0
    finally:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fp.close()


def cmd_approve(args: argparse.Namespace) -> int:
    return _do_transition(
        queue_id=args.queue_id,
        operator=args.operator,
        action="approve",
        reason=None,
        day=args.day,
    )


def cmd_reject(args: argparse.Namespace) -> int:
    return _do_transition(
        queue_id=args.queue_id,
        operator=args.operator,
        action="reject",
        reason=args.reason,
        day=args.day,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m gni.editorial_queue.approve",
        description="GNI V1 human approval gate (local CLI, no DB).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List items needing review")
    p_list.add_argument("--day", help="UTC day YYYYMMDD (default: today)")
    p_list.set_defaults(func=cmd_list)

    p_app = sub.add_parser("approve", help="Approve a queue item")
    p_app.add_argument("--queue-id", required=True)
    p_app.add_argument("--operator", required=True)
    p_app.add_argument("--day", help="UTC day YYYYMMDD (default: today)")
    p_app.set_defaults(func=cmd_approve)

    p_rej = sub.add_parser("reject", help="Reject a queue item")
    p_rej.add_argument("--queue-id", required=True)
    p_rej.add_argument("--operator", required=True)
    p_rej.add_argument("--reason", required=True)
    p_rej.add_argument("--day", help="UTC day YYYYMMDD (default: today)")
    p_rej.set_defaults(func=cmd_reject)

    return p


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
