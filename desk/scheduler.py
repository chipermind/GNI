"""
Desk 24H scheduler using APScheduler.
Runs jobs in America/Recife timezone.
Optional: pip install apscheduler
"""
import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError as e:
    BackgroundScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore
    IntervalTrigger = None  # type: ignore
    _APSCHEDULER_IMPORT_ERROR = e

DESK_SCHEDULER_ENABLED = os.environ.get("DESK_SCHEDULER_ENABLED", "0").strip().lower() in ("1", "true", "yes")
DESK_DRY_RUN = os.environ.get("DESK_DRY_RUN", "1").strip().lower() in ("1", "true", "yes")
DESK_TRIGGER_INTERVAL_MIN = int(os.environ.get("DESK_TRIGGER_INTERVAL_MIN", "5") or "5")
DESK_TRIGGER_THRESHOLD = float(os.environ.get("DESK_TRIGGER_THRESHOLD", "0.75") or "0.75")
DESK_TRIGGER_COOLDOWN_MIN = int(os.environ.get("DESK_TRIGGER_COOLDOWN_MIN", "45") or "45")

_TIMEZONE = "America/Recife"
_scheduler: "BackgroundScheduler | None" = None

# Cron mapping: desk_type -> (hour, minute) America/Recife
_CRON_MAP = {
    "OVERNIGHT_GLOBAL_0500": (5, 0),
    "PREMARKET_BR_0800": (8, 0),
    "PANORAMA_0900": (9, 0),
    "THREAT_MONITOR_1130": (11, 30),
    "ALERTA_TATICO_1200": (12, 0),
    "FLOW_1330": (13, 30),
    "REALTIME_VOL_1530": (15, 30),
    "RISK_MATRIX_1800": (18, 0),
    "EXEC_SUMMARY_2030": (20, 30),
    "OVERNIGHT_WATCH_2300": (23, 0),
}


def get_latest_raw() -> dict[str, Any]:
    """Try to import repo function for raw pipeline data; else return {} and log."""
    for mod_path in ("desk.pipeline", "desk.data", "apps.worker.desk_ingest"):
        try:
            mod = __import__(mod_path, fromlist=["get_latest_raw"])
            fn = getattr(mod, "get_latest_raw", None)
            if callable(fn):
                return fn()
        except ImportError:
            continue
    logger.debug("raw unavailable")
    return {}


def _get_latest_snapshot_from_db() -> dict[str, Any] | None:
    """Return latest snapshot payload from DB, or None if empty."""
    from desk.storage import get_conn, init_db

    init_db()
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT payload_json FROM snapshots ORDER BY created_at DESC LIMIT 1",
            (),
        )
        row = cur.fetchone()
        if row is None or not row[0]:
            return None
        return json.loads(row[0])
    finally:
        conn.close()


def detect_triggers(snapshot: dict) -> dict[str, Any]:
    """
    Deterministic trigger detection from snapshot only.
    Returns {score: 0..1, reasons: [str], desk_type: ALERTA_TATICO_1200 | REALTIME_VOL_1530}.
    """
    score = 0.0
    reasons: list[str] = []
    desk_type = "ALERTA_TATICO_1200"

    deltas = snapshot.get("deltas") or {}
    markets_deltas = deltas.get("markets") or {}
    flow_deltas = deltas.get("flow") or {}

    delta_score = 0.0
    for section, d in (("markets", markets_deltas), ("flow", flow_deltas)):
        if not isinstance(d, dict):
            continue
        for _k, v in d.items():
            if isinstance(v, dict) and "delta" in v:
                try:
                    mag = abs(float(v["delta"]))
                    if mag >= 5.0:
                        delta_score = max(delta_score, 0.4)
                        reasons.append(f"{section}_delta_large:{mag:.1f}")
                    elif mag >= 2.0:
                        delta_score = max(delta_score, 0.2)
                        reasons.append(f"{section}_delta:{mag:.1f}")
                except (TypeError, ValueError):
                    pass

    intel_score = 0.0
    intel_items = snapshot.get("intel") or []
    for item in intel_items:
        if isinstance(item, dict):
            impact = item.get("impact")
            if impact is not None and str(impact).strip().lower() == "high":
                intel_score = 0.5
                reasons.append("intel_impact_high")
                break

    if intel_score >= delta_score and intel_score > 0:
        score = min(1.0, intel_score + delta_score * 0.5)
        desk_type = "ALERTA_TATICO_1200"
    elif delta_score > 0:
        score = min(1.0, delta_score + intel_score * 0.5)
        desk_type = "REALTIME_VOL_1530"
    else:
        score = 0.0

    return {"score": score, "reasons": reasons, "desk_type": desk_type}


def _run_trigger_check() -> None:
    """Interval job: get snapshot, detect triggers, run_window if score >= threshold and cooldown ok."""
    from desk.snapshot import build_snapshot
    from desk.storage import cooldown_ok, init_db

    init_db()
    snapshot = _get_latest_snapshot_from_db()
    if snapshot is None:
        raw = get_latest_raw()
        snapshot = build_snapshot("ALERTA_TATICO_1200", raw, prev_snapshot=None)
    result = detect_triggers(snapshot)
    if result["score"] < DESK_TRIGGER_THRESHOLD:
        return
    desk_type = result["desk_type"]
    if desk_type not in ("ALERTA_TATICO_1200", "REALTIME_VOL_1530"):
        return
    key = f"trigger:{desk_type}"
    if not cooldown_ok(key, DESK_TRIGGER_COOLDOWN_MIN):
        return
    run_window(desk_type)


def run_window(desk_type: str) -> dict[str, Any]:
    """
    Compose, validate, store, optionally send for a desk window.
    Returns summary: {type, ok, reason, dry_run}.
    """
    from desk.composer import compose_post
    from desk.snapshot import build_snapshot
    from desk.storage import get_last_posts, init_db, save_post, save_snapshot
    from desk.validators import validate

    dry_run = DESK_DRY_RUN
    summary: dict[str, Any] = {"type": desk_type, "ok": False, "reason": "", "dry_run": dry_run}

    try:
        raw = get_latest_raw()
        snapshot = build_snapshot(desk_type, raw, prev_snapshot=None)
    except Exception as e:
        summary["reason"] = f"snapshot_error: {e}"
        logger.warning("run_window %s snapshot failed: %s", desk_type, e)
        return summary

    last_posts = get_last_posts(hours=24)
    prev_texts = [p["text"] for p in last_posts if p.get("text")]
    context = {"last_posts": [{"desk_type": p["desk_type"], "summary": (p.get("text") or "")[:300]} for p in last_posts]}
    from desk.evidence_pack import build_evidence_pack
    context["evidence_pack"] = build_evidence_pack(desk_type, raw)
    if desk_type == "EXEC_SUMMARY_2030":
        from desk.day_state import build_exec_closure, day_key, load_day_state
        day_state = load_day_state(day_key(tz="America/Recife"))
        closure = build_exec_closure(day_state, last_posts)
        context["day_state"] = day_state
        context["exec_closure"] = closure

    try:
        result = compose_post(desk_type, snapshot, context)
    except Exception as e:
        summary["reason"] = f"compose_error: {e}"
        logger.warning("run_window %s compose failed: %s", desk_type, e)
        return summary

    post = {"type": desk_type, "text": result["text"], "meta": result.get("meta", {})}
    ok, reason = validate(post, prev_texts=prev_texts, packs=context.get("evidence_pack"))
    if not ok:
        summary["reason"] = reason
        logger.info("run_window %s validation failed: %s", desk_type, reason)

    payload_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    init_db()
    try:
        snapshot_id = save_snapshot(desk_type, snapshot, payload_hash)
    except Exception as e:
        summary["reason"] = summary.get("reason") or f"save_snapshot_error: {e}"
        logger.warning("run_window %s save_snapshot failed: %s", desk_type, e)
        return summary

    meta = dict(result.get("meta", {}))
    meta["dry_run"] = dry_run
    if not ok:
        meta["validation_failed"] = reason
    try:
        save_post(desk_type, result["text"], meta=meta, snapshot_id=snapshot_id)
    except Exception as e:
        summary["reason"] = summary.get("reason") or f"save_post_error: {e}"
        logger.warning("run_window %s save_post failed: %s", desk_type, e)
        return summary

    try:
        from desk.day_state import update_and_persist
        post_for_state = {"type": desk_type, "text": result["text"], "tags": result.get("tags", []), "reasons": result.get("reasons", []), "meta": result.get("meta", {})}
        update_and_persist(desk_type, snapshot, post_for_state)
    except Exception as e:
        logger.warning("run_window %s update_and_persist failed: %s", desk_type, e)

    if ok and not dry_run:
        try:
            from apps.publisher.gni_sender import gni_send
            send_result = gni_send(result["text"], meta=meta, dry_run=False)
            sent_ok = getattr(send_result, "status", "") == "sent" if send_result else False
            logger.info("run_window %s sent_ok=%s", desk_type, sent_ok)
        except ImportError as ie:
            summary["reason"] = "gni_send_unavailable"
            logger.warning("run_window %s gni_send import failed: %s", desk_type, ie)
            return summary
        except Exception as e:
            summary["reason"] = f"send_error: {e}"
            logger.warning("run_window %s gni_send failed: %s", desk_type, e)
            return summary

    summary["ok"] = ok
    summary["reason"] = reason if not ok else ""
    return summary


def start_scheduler(app=None) -> "BackgroundScheduler":
    """
    Start Desk scheduler in America/Recife timezone.
    Idempotent: starts only once.
    Adds fixed cron jobs for all Desk types.
    """
    if BackgroundScheduler is None:
        raise ImportError(
            "APScheduler not installed. Install with: pip install apscheduler"
        ) from _APSCHEDULER_IMPORT_ERROR
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.debug("desk scheduler already running")
        return _scheduler
    _scheduler = BackgroundScheduler(timezone=_TIMEZONE)
    for desk_type, (hour, minute) in _CRON_MAP.items():
        job_id = f"desk:{desk_type}"
        _scheduler.add_job(
            run_window,
            CronTrigger(hour=hour, minute=minute, timezone=_TIMEZONE),
            id=job_id,
            args=[desk_type],
            replace_existing=True,
        )
    _scheduler.add_job(
        _run_trigger_check,
        IntervalTrigger(minutes=DESK_TRIGGER_INTERVAL_MIN),
        id="desk:trigger_check",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("desk scheduler started (timezone=%s, cron=%d, trigger_interval=%dm)", _TIMEZONE, len(_CRON_MAP), DESK_TRIGGER_INTERVAL_MIN)
    return _scheduler


def shutdown_scheduler() -> None:
    """Stop the Desk scheduler if running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("desk scheduler shutdown")
    else:
        _scheduler = None


# --- Smoke CLI (no Telegram, no Ollama) ---
# Usage: python -m desk.scheduler --dry-run --type PANORAMA_0900
#        python -m desk.scheduler --no-dry-run --type FLOW_1330
# CLI path: forced dry-run, never calls gni_send.

if __name__ == "__main__":
    import argparse
    import sys
    import re
    from pathlib import Path

    # Force smoke mode: no Telegram, no production scheduler
    os.environ["DESK24H_ENABLED"] = "0"
    print("[SMOKE] Desk24H dry-run mode — no Telegram send.")

    TYPE_TO_MD = {
        "OVERNIGHT_GLOBAL_0500": "0500_overnight.md",
        "PREMARKET_BR_0800": "0800_premarket.md",
        "PANORAMA_0900": "0900_panorama.md",
        "THREAT_MONITOR_1130": "1130_threats.md",
        "ALERTA_TATICO_1200": "1130_threats.md",
        "FLOW_1330": "1330_flow.md",
        "REALTIME_VOL_1530": "1330_flow.md",
        "RISK_MATRIX_1800": "1800_risk_matrix.md",
        "EXEC_SUMMARY_2030": "2030_exec_summary.md",
        "OVERNIGHT_WATCH_2300": "2300_overnight_watch.md",
    }

    parser = argparse.ArgumentParser(description="Desk 24H scheduler smoke CLI")
    parser.add_argument("--dry-run", action="store_true", default=True, help="No Telegram send (default: True)")
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    parser.add_argument("--type", required=True, dest="desk_type", help="Desk type, e.g. PANORAMA_0900")
    parser.add_argument("--compose", action="store_true", help="Run full compose (Ollama) + validate + save to DB")
    args = parser.parse_args()

    if args.compose:
        os.environ["DESK_DRY_RUN"] = "1" if args.dry_run else "0"
        summary = run_window(args.desk_type)
        print("[SMOKE] Compose result:", json.dumps(summary, indent=2, default=str))
        sys.exit(0 if summary.get("ok") else 1)

    templates_dir = Path(__file__).resolve().parent / "templates"
    md_file = TYPE_TO_MD.get(args.desk_type)
    if md_file and (templates_dir / md_file).exists():
        text = (templates_dir / md_file).read_text(encoding="utf-8")
    else:
        from desk.templates import load_template
        text = load_template(args.desk_type)

    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    line_count = len(text.splitlines())
    char_count = len(text)
    preview = (text[:300] + ("..." if len(text) > 300 else ""))
    print("[SMOKE] Desk type:", args.desk_type)
    print("[SMOKE] First 300 chars:", preview)
    print("[SMOKE] Line count:", line_count)
    print("[SMOKE] Char count:", char_count)
    print("[SMOKE] Dry-run: True")
