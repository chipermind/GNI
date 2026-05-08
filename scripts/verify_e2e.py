#!/usr/bin/env python3
"""
E2E verification: ingest → score → draft → publish.
Outputs counts for scripts/verify_e2e.sh.
Exits 1 if required env missing or pipeline fails.
"""
import os
import sys
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
if str(repo) not in sys.path:
    sys.path.insert(0, str(repo))

# Late imports after path setup
from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Draft, EventsLog, Publication
from apps.collector.rss import run as run_rss_ingest
from apps.collector.telegram_ingest import run as run_telegram_ingest
from apps.worker.tasks import step_llm_draft, step_render_and_publish, step_scoring


def _check_publish_env() -> list[str]:
    """Return list of missing env vars for real publish."""
    missing = []
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip():
        missing.append("TELEGRAM_BOT_TOKEN")
    if not (os.environ.get("TELEGRAM_TARGET_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip():
        missing.append("TELEGRAM_TARGET_CHAT_ID or TELEGRAM_CHAT_ID")
    if not (os.environ.get("MAKE_WEBHOOK_URL") or "").strip():
        missing.append("MAKE_WEBHOOK_URL")
    return missing


def _telethon_session_exists() -> bool:
    """Check if Telethon session file exists."""
    path = os.environ.get("TELETHON_SESSION_PATH", "/data/telethon/session")
    # Telethon uses path + ".session" for SQLite
    session_file = Path(path + ".session")
    return session_file.exists()


def main() -> int:
    init_db()
    session = SessionLocal()

    # Check publish env before we start
    missing = _check_publish_env()
    if missing:
        print(f"Missing required env for publish: {', '.join(missing)}", file=sys.stderr)
        return 1

    # 1. RSS ingest (limit 5)
    rss_n = run_rss_ingest(limit=5)
    print(f"RSS ingested: {rss_n}")

    # 2. Optional Telegram ingest (10 min if session exists)
    tg_n = 0
    if _telethon_session_exists():
        tg_n = run_telegram_ingest(since_minutes=10)
        print(f"Telegram ingested: {tg_n}")
    else:
        print("Telegram ingest: skipped (no Telethon session; set TELEGRAM_API_ID/TELEGRAM_API_HASH and run telegram_login)")

    ingested_total = rss_n + tg_n

    # 3. Scoring
    scored = step_scoring(limit=20)
    print(f"Scored: {scored}")

    # 4. LLM draft
    drafted = step_llm_draft(limit=5)
    print(f"Drafts generated: {drafted}")

    if drafted == 0:
        print("No drafts generated; pipeline cannot verify publish.", file=sys.stderr)
        return 1

    # 5. Publish (no dry_run)
    os.environ["DRY_RUN"] = "0"
    published = step_render_and_publish(limit=5, dry_run=False)
    print(f"Published: {published}")

    # Summary counts
    drafts_count = session.query(Draft).count()
    pub_sent = session.query(Publication).filter(Publication.status == "sent").count()
    pub_failed = session.query(Publication).filter(
        Publication.status.in_(["failed", "dead_letter"])
    ).count()
    pub_blocked = session.query(EventsLog).filter(EventsLog.event_type == "publish_blocked").count()

    print("VERIFY_SUMMARY:ITEMS_INGESTED=" + str(ingested_total))
    print("VERIFY_SUMMARY:DRAFTS=" + str(drafts_count))
    print("VERIFY_SUMMARY:PUBLICATIONS_SUCCESS=" + str(pub_sent))
    print("VERIFY_SUMMARY:PUBLICATIONS_FAILED=" + str(pub_failed))
    print("VERIFY_SUMMARY:PUBLICATIONS_BLOCKED=" + str(pub_blocked))

    # Assert Telegram and Make both succeeded (at least 1 sent each)
    tg_sent = session.query(Publication).filter(
        Publication.channel == "telegram", Publication.status == "sent"
    ).count()
    make_sent = session.query(Publication).filter(
        Publication.channel == "make", Publication.status == "sent"
    ).count()
    if published > 0 and tg_sent >= 1 and make_sent >= 1:
        return 0
    if published == 0:
        print("Publish step returned 0; check logs.", file=sys.stderr)
    else:
        print(
            f"Expected Telegram and Make success (tg_sent={tg_sent}, make_sent={make_sent})",
            file=sys.stderr,
        )
    return 1


def run_publish_only() -> int:
    """Run only publish step (for blocked test after pause)."""
    os.environ["DRY_RUN"] = "0"
    return step_render_and_publish(limit=5, dry_run=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish-only", action="store_true", help="Run only publish step (for blocked test)")
    args = parser.parse_args()
    try:
        if args.publish_only:
            n = run_publish_only()
            print(f"Publish returned: {n}")
            sys.exit(0)
        sys.exit(main())
    except Exception as e:
        print(f"Verify failed: {e}", file=sys.stderr)
        sys.exit(1)
