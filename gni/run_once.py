"""
Run-once entrypoint for VM/production: load news, router, generate, guards, publish.
Usage: python -m gni.run_once --job briefing_0900
       python -m gni.run_once --job radar_interval [--dry-run]
No DB/schema change. No scheduler change.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("gni.run_once")


def _get_latest_raw() -> dict[str, Any]:
    """Load raw pipeline data (same as desk scheduler). Returns {} if unavailable."""
    for mod_path in ("desk.pipeline", "desk.data", "apps.worker.desk_ingest"):
        try:
            mod = __import__(mod_path, fromlist=["get_latest_raw"])
            fn = getattr(mod, "get_latest_raw", None)
            if callable(fn):
                return fn() or {}
        except ImportError:
            continue
    logger.debug("get_latest_raw unavailable")
    return {}


def _snapshot_to_radar_data(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Map snapshot intel to radar_data keys (geopolitics, cyber, crypto, ai, energy)."""
    intel = snapshot.get("intel") or []
    out: dict[str, str] = {
        "geopolitics": "",
        "cyber": "",
        "crypto": "",
        "ai": "",
        "energy": "",
    }
    category_to_key = {
        "geo": "geopolitics",
        "geopolitics": "geopolitics",
        "cyber": "cyber",
        "ai": "ai",
        "macro": "crypto",
        "crypto": "crypto",
        "energy": "energy",
    }
    for item in intel:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        if not title:
            continue
        source = item.get("source") or item.get("source_name")
        line = f"{title}"
        if source:
            line += f" ({source})"
        cat = (item.get("category") or "").strip().lower()
        key = category_to_key.get(cat, "geopolitics")
        if out[key]:
            out[key] += "\n• "
        else:
            out[key] = "• "
        out[key] += line
    return {k: v.strip() for k, v in out.items() if v.strip()}


def _load_radar_data_from_db(limit: int = 25) -> dict[str, Any]:
    """Fallback: build radar_data from recent items in DB (collector pipeline)."""
    try:
        from apps.api.db import SessionLocal, init_db
        from apps.api.db.models import Item

        init_db()
        session = SessionLocal()
        try:
            rows = (
                session.query(Item)
                .filter(Item.title.isnot(None), Item.title != "")
                .order_by(Item.created_at.desc())
                .limit(limit)
                .all()
            )
        finally:
            session.close()

        if not rows:
            return {}
        out: dict[str, str] = {
            "geopolitics": "",
            "cyber": "",
            "crypto": "",
            "ai": "",
            "energy": "",
        }
        for item in rows:
            title = (item.title or "").strip()
            if not title:
                continue
            source = (getattr(item, "source_name", None) or "").strip()
            line = f"• {title}"
            if source:
                line += f" ({source})"
            out["geopolitics"] += ("\n" if out["geopolitics"] else "") + line
        return {k: v.strip() for k, v in out.items() if v.strip()}
    except Exception as e:
        logger.debug("load_radar_data_from_db failed: %s", e)
        return {}


def load_radar_data() -> dict[str, Any]:
    """
    Load news: get_latest_raw -> build_snapshot -> radar_data; if empty, fallback to recent items from DB.
    Returns dict with keys geopolitics, cyber, crypto, ai, energy (strings).
    """
    raw = _get_latest_raw()
    if raw:
        try:
            from desk.snapshot import build_snapshot
            snapshot = build_snapshot("run_once", raw, prev_snapshot=None)
            data = _snapshot_to_radar_data(snapshot)
            if data:
                return data
        except Exception as e:
            logger.warning("load_radar_data snapshot failed: %s", e)

    data = _load_radar_data_from_db()
    if data:
        logger.info("radar_data loaded from DB (fallback)")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="GNI run-once: load news, router, generate, guards, publish")
    ap.add_argument("--job", required=True, help="Job name: e.g. briefing_0900, radar_interval, intel_flash")
    ap.add_argument("--dry-run", action="store_true", help="Do not publish; only generate and validate")
    args = ap.parse_args()

    job_name = (args.job or "").strip()
    if not job_name:
        logger.error("--job is required")
        return 1

    from gni.editorial.router import select_format

    event_score: float | None = None  # run_once has no event score; scheduler could pass it later
    category: str | None = None
    format_mode = select_format(job_name, event_score, category)
    logger.info(
        "router_decision job_name=%s event_score=%s category=%s => format_mode=%s",
        job_name,
        event_score,
        category,
        format_mode,
    )

    radar_data = load_radar_data()
    if not radar_data:
        logger.info("no radar data loaded; using empty (fallback content)")
    else:
        logger.info("radar_data keys=%s", list(radar_data.keys()))

    from gni.analysis.radar_broadcast import run_radar_broadcast

    dry_run = args.dry_run
    msg, sent = run_radar_broadcast(
        radar_data,
        dry_run=dry_run,
        format_mode=format_mode,
        job_name=job_name,
    )

    if not msg:
        logger.error("run_once no message generated")
        return 1

    if dry_run:
        logger.info("run_once dry_run done format_mode=%s msg_len=%s", format_mode, len(msg))
        return 0

    if sent:
        logger.info("telegram_sent_ok format_mode=%s", format_mode)
        return 0
    logger.error("run_once publish failed format_mode=%s", format_mode)
    return 1


if __name__ == "__main__":
    sys.exit(main())
