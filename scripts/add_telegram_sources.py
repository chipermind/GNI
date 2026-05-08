#!/usr/bin/env python3
"""
Add Telegram channel/group sources to the DB for ingest.
Get chat_ids from: python scripts/telegram_list_chats_telethon.py

Usage:
  python scripts/add_telegram_sources.py
  # Uses TELEGRAM_SOURCES env: "name1:chat_id1,name2:chat_id2,..."
  # Or pass as args:
  python scripts/add_telegram_sources.py "Euro Intel Mais:-1001234567890" "Coin Sauce:coinsauce" "RU:-1009876543210"

chat_id can be: @username, username, or numeric ID (e.g. -1001234567890).
"""
import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

_env = repo_root / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env)
    except ImportError:
        pass

from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Source


# Default sources for Global News Intel pipeline (replace chat_id with your IDs from telegram_list_chats_telethon)
DEFAULT_TELEGRAM_SOURCES = [
    ("Euro Intel Mais", ""),   # Fill: username or -100... ID
    ("Coin Sauce", ""),        # Fill: username or -100... ID
    ("RU", ""),                # Fill: username or -100... ID
]


def add_sources(sources: list[tuple[str, str]]) -> int:
    init_db()
    session = SessionLocal()
    added = 0
    try:
        for name, chat_id in sources:
            chat_id = (chat_id or "").strip()
            if not chat_id:
                print(f"Skip {name}: chat_id empty. Get from: python scripts/telegram_list_chats_telethon.py")
                continue
            existing = session.query(Source).filter(
                Source.type == "telegram",
                Source.chat_id == chat_id,
            ).first()
            if existing:
                print(f"Already exists: {name} ({chat_id})")
                continue
            row = Source(name=name, url=None, type="telegram", chat_id=chat_id)
            session.add(row)
            added += 1
            print(f"Added: {name} ({chat_id})")
        session.commit()
        return added
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    sources: list[tuple[str, str]] = []

    env_val = (os.environ.get("TELEGRAM_SOURCES") or "").strip()
    if env_val:
        for part in env_val.split(","):
            part = part.strip()
            if ":" in part:
                name, cid = part.split(":", 1)
                sources.append((name.strip(), cid.strip()))

    if not sources and len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if ":" in arg:
                name, cid = arg.split(":", 1)
                sources.append((name.strip(), cid.strip()))

    if not sources:
        sources = DEFAULT_TELEGRAM_SOURCES
        print("Using defaults (fill chat_ids). Run: python scripts/telegram_list_chats_telethon.py")
        print("Then: TELEGRAM_SOURCES='Euro Intel Mais:-100xxx,Coin Sauce:username,RU:-100yyy' python scripts/add_telegram_sources.py")
        print()

    n = add_sources(sources)
    print(f"\nAdded {n} Telegram source(s).")


if __name__ == "__main__":
    main()
