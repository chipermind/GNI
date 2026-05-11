"""
Collector entrypoint: runs RSS + Telegram ingest on a configurable interval.
Unified with worker: collector ingests (writes items to DB), worker processes (scoring → LLM → publish).
Exits cleanly on SIGTERM.
CLI: python -m apps.collector
"""
import os
import signal
import sys
import time
from pathlib import Path

# Ensure repo root on path when run as __main__
_repo = Path(__file__).resolve().parent.parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Source
from apps.collector.rss import run as run_rss_ingest
from apps.collector.telegram_ingest import run as run_telegram_ingest
from apps.shared.config import validate_config
from apps.shared.env_helpers import get_int_env

# COLLECTOR_INTERVAL_MINUTES can come from COLLECTOR_INTERVAL_MINUTES or COLLECTOR_INTERVAL
_collector_interval_raw = os.environ.get("COLLECTOR_INTERVAL_MINUTES") or os.environ.get("COLLECTOR_INTERVAL", "")
if _collector_interval_raw and _collector_interval_raw.strip():
    # Use the first non-empty value found
    if os.environ.get("COLLECTOR_INTERVAL_MINUTES"):
        COLLECTOR_INTERVAL_MINUTES = get_int_env("COLLECTOR_INTERVAL_MINUTES", default=15)
    else:
        COLLECTOR_INTERVAL_MINUTES = get_int_env("COLLECTOR_INTERVAL", default=15)
else:
    COLLECTOR_INTERVAL_MINUTES = 15
INGEST_LIMIT = get_int_env("INGEST_LIMIT", default=50)
TELEGRAM_SINCE_MINUTES = get_int_env("TELEGRAM_SINCE_MINUTES", default=60)

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    _shutdown = True


def _sync_telegram_sources() -> None:
    """Sync TELEGRAM_SOURCES env var to sources table. Format: Name:chat_id,..."""
    raw = os.environ.get("TELEGRAM_SOURCES", "").strip()
    if not raw:
        return
    session = SessionLocal()
    try:
        for entry in raw.split(","):
            entry = entry.strip()
            if ":" not in entry:
                continue
            name, chat_id = entry.rsplit(":", 1)
            name, chat_id = name.strip(), chat_id.strip()
            if not name or not chat_id:
                continue
            existing = session.query(Source).filter(Source.chat_id == chat_id).first()
            if not existing:
                session.add(Source(name=name, type="telegram", chat_id=chat_id, tier=1))
        session.commit()
        print("[collector] Telegram sources synced from TELEGRAM_SOURCES")
    except Exception as e:
        session.rollback()
        print(f"[collector] Source sync error: {e}", file=sys.stderr)
    finally:
        session.close()


def run_once() -> tuple[int, int]:
    """Run RSS + Telegram ingest once. Returns (rss_count, telegram_count)."""
    init_db()
    _sync_telegram_sources()
    rss_n = run_rss_ingest(limit=INGEST_LIMIT)
    tg_n = run_telegram_ingest(since_minutes=TELEGRAM_SINCE_MINUTES)
    return (rss_n, tg_n)


def main() -> None:
    global _shutdown
    validate_config(required=True)
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    interval_sec = max(1, COLLECTOR_INTERVAL_MINUTES * 60)
    print(f"Collector started: interval={COLLECTOR_INTERVAL_MINUTES}m, limit={INGEST_LIMIT}, telegram_since={TELEGRAM_SINCE_MINUTES}m")

    while not _shutdown:
        try:
            rss_n, tg_n = run_once()
            total = rss_n + tg_n
            if total > 0:
                try:
                    from apps.observability.metrics import record_items_ingested
                    record_items_ingested(total)
                except ImportError:
                    pass
            print(f"Collector ingest: rss={rss_n} telegram={tg_n} total={total}")
        except Exception as e:
            print(f"Collector error: {e}", file=sys.stderr)

        # Sleep in small chunks to allow quick shutdown on SIGTERM
        for _ in range(interval_sec):
            if _shutdown:
                break
            time.sleep(1)

    print("Collector shutdown")


if __name__ == "__main__":
    main()
