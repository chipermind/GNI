"""
Fetch RSS with feedparser; normalize; store raw_items + items with fingerprint dedup.
CLI: python -m apps.collector.rss --limit 20
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root on path for apps.api
_repo = Path(__file__).resolve().parent.parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

import feedparser  # type: ignore

from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Item, RawItem, Source

from apps.collector.config import list_sources
from apps.collector.normalize import normalized_record
from apps.worker.dedupe import (
    DEDUPE_DAYS,
    build_fingerprint,
    created_at_in_window,
    find_item,
)


def fetch_feed(url: str, limit: int = 50):
    """Fetch feed URL; return list of entries (up to limit)."""
    if not url or not url.strip():
        return []
    try:
        fp = feedparser.parse(url)
        entries = getattr(fp, "entries", [])[:limit]
        return entries
    except Exception:
        return []


def get_or_create_source(session, name: str, url: str) -> Source:
    """Get or create Source by (name, url)."""
    row = session.query(Source).filter(Source.name == name, Source.url == url).first()
    if row:
        return row
    row = Source(name=name, url=url)
    session.add(row)
    session.flush()
    return row


def run(limit: int = 20) -> int:
    """
    Ingest up to `limit` items: fetch from configured sources, normalize, store.
    Dedup by fingerprint; idempotent (rerun does not duplicate, only updates updated_at if exists).
    """
    init_db()
    session = SessionLocal()
    try:
        sources = list_sources()
        total = 0
        now = datetime.now(timezone.utc)
        for src in sources:
            if total >= limit:
                break
            name, url = src["name"], src["url"]
            if not url:
                continue
            entries = fetch_feed(url, limit=limit * 2)
            source = get_or_create_source(session, name, url)
            session.flush()
            for entry in entries:
                if total >= limit:
                    break
                try:
                    rec = normalized_record(entry, source_name=name)
                except Exception:
                    continue
                canonical_url = rec.get("url") or ""
                normalized_title = (rec.get("title") or "").strip()
                fingerprint = build_fingerprint("rss", canonical_url, normalized_title)
                rec["fingerprint"] = fingerprint
                # Store raw
                raw_content = json.dumps(rec.get("raw_payload") or {}, default=str)
                raw_row = RawItem(
                    source_id=source.id,
                    raw_content=raw_content,
                    fetched_at=now,
                )
                session.add(raw_row)
                session.flush()
                # Centralized dedupe: 7-day window (configurable DEDUPE_DAYS); relaxed policy uses title
                item = find_item(session, fingerprint, title=normalized_title)
                if item:
                    if created_at_in_window(item, DEDUPE_DAYS, now):
                        item.updated_at = now
                    else:
                        item.title = rec.get("title")
                        item.url = rec.get("url")
                        item.published_at = rec.get("published_at")
                        item.summary = rec.get("summary")
                        item.source_name = name
                        item.source_type = "rss"
                        item.updated_at = now
                else:
                    item = Item(
                        fingerprint=fingerprint,
                        title=rec.get("title"),
                        url=rec.get("url"),
                        published_at=rec.get("published_at"),
                        summary=rec.get("summary"),
                        source_name=name,
                        source_type="rss",
                        status="new",
                    )
                    session.add(item)
                total += 1
        session.commit()
        return total
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="Ingest RSS into raw_items + items (dedup by fingerprint)")
    parser.add_argument("--limit", type=int, default=20, help="Max items to ingest")
    args = parser.parse_args()
    n = run(limit=args.limit)
    print(f"Ingested {n} items.")


if __name__ == "__main__":
    main()
