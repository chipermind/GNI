"""
Fetch recent Telegram messages via Telethon; normalize to raw_item and insert into DB.
Reads sources from DB where type=telegram (use chat_id). Session from TELETHON_SESSION_PATH.
CLI: python -m apps.collector.telegram_ingest --since-minutes 60
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Item, RawItem, Source
from apps.collector.normalize import canonicalize_url, normalize_title, normalize_summary
from apps.worker.dedupe import (
    DEDUPE_DAYS,
    build_fingerprint,
    created_at_in_window,
    find_item,
)


TITLE_TRUNCATE = 500


def _message_link(client: TelegramClient, entity, message) -> str:
    """Return permalink for the message if available, else empty string."""
    try:
        if getattr(entity, "username", None):
            return f"https://t.me/{entity.username}/{message.id}"
        if isinstance(entity, (Channel, Chat)):
            # Private channel: t.me/c/<channel_id>/<msg_id>
            cid = getattr(entity, "id", None)
            if cid is not None:
                return f"https://t.me/c/{abs(cid)}/{message.id}"
    except Exception:
        pass
    return ""


def _message_to_record(entity, message, source_name: str, chat_id: str) -> dict:
    """Normalize a Telethon message to our record: title, summary, url, source_name, source_type, fingerprint, published_at."""
    text = (message.text or "").strip()
    summary = normalize_summary(text)
    first_line = text.split("\n")[0] if text else ""
    title = normalize_title(first_line)
    if title and len(title) > TITLE_TRUNCATE:
        title = title[: TITLE_TRUNCATE - 3] + "..."
    # url and fingerprint: we need a stable unique id; link is set in caller after we have client
    published_at = message.date
    if published_at and published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return {
        "title": title or None,
        "summary": summary or None,
        "url": None,  # set below with client
        "source_name": source_name,
        "source_type": "telegram",
        "published_at": published_at,
        "raw_payload": {
            "chat_id": str(chat_id),
            "message_id": message.id,
            "date": message.date.isoformat() if message.date else None,
            "text": text[: 5000],
        },
    }


def run(since_minutes: int = 60, limit_per_source: int = 200) -> int:
    """
    Fetch Telegram sources from DB (type=telegram), get recent messages, normalize and insert.
    Dedup by fingerprint; idempotent.
    """
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    session_path = os.environ.get("TELETHON_SESSION_PATH", "/data/telethon/session")

    if not api_id or not api_hash:
        return 0  # skip silently if credentials not set

    init_db()
    session = SessionLocal()
    try:
        sources = session.query(Source).filter(Source.type == "telegram").all()
        if not sources:
            return 0

        since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        total = 0
        now = datetime.now(timezone.utc)

        client = TelegramClient(session_path, int(api_id), api_hash.strip())
        with client:
            for src in sources:
                chat_id = (src.chat_id or "").strip()
                if not chat_id:
                    continue
                try:
                    entity = client.get_entity(chat_id)
                except Exception:
                    continue
                source_name = (src.name or getattr(entity, "title", None) or chat_id) or "telegram"
                if hasattr(entity, "title") and entity.title and not src.name:
                    source_name = entity.title

                for message in client.iter_messages(
                    entity,
                    offset_date=since,
                    reverse=True,
                    limit=limit_per_source,
                ):
                    if not message.text or not message.text.strip():
                        continue
                    rec = _message_to_record(entity, message, source_name, chat_id)
                    rec["url"] = _message_link(client, entity, message) or ""
                    raw_url = rec["url"] or f"telegram:{chat_id}:{message.id}"
                    canonical_url_str = canonicalize_url(rec["url"]) if rec["url"] else raw_url
                    norm_title = (rec["title"] or "").strip() or "(no title)"
                    fingerprint = build_fingerprint("telegram", canonical_url_str, norm_title)
                    rec["fingerprint"] = fingerprint

                    raw_content = json.dumps(rec.get("raw_payload") or {}, default=str)
                    raw_row = RawItem(
                        source_id=src.id,
                        raw_content=raw_content,
                        fetched_at=now,
                    )
                    session.add(raw_row)
                    session.flush()

                    # Centralized dedupe: 7-day window (configurable DEDUPE_DAYS)
                    item = find_item(session, fingerprint, title=rec.get("title"))
                    if item:
                        if created_at_in_window(item, DEDUPE_DAYS, now):
                            item.updated_at = now
                        else:
                            item.title = rec.get("title")
                            item.url = rec.get("url") or None
                            item.published_at = rec.get("published_at")
                            item.summary = rec.get("summary")
                            item.source_name = rec["source_name"]
                            item.source_type = "telegram"
                            item.updated_at = now
                    else:
                        item = Item(
                            fingerprint=fingerprint,
                            title=rec.get("title"),
                            url=rec.get("url") or None,
                            published_at=rec.get("published_at"),
                            summary=rec.get("summary"),
                            source_name=rec["source_name"],
                            source_type="telegram",
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest recent Telegram messages into raw_items + items (dedup by fingerprint)"
    )
    parser.add_argument(
        "--since-minutes",
        type=int,
        default=60,
        help="Fetch messages from the last N minutes (default: 60)",
    )
    parser.add_argument(
        "--limit-per-source",
        type=int,
        default=200,
        help="Max messages per Telegram source (default: 200)",
    )
    args = parser.parse_args()
    n = run(since_minutes=args.since_minutes, limit_per_source=args.limit_per_source)
    print(f"Ingested {n} Telegram items.")


if __name__ == "__main__":
    main()
