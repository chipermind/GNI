#!/usr/bin/env python3
"""
List your Telegram chats (groups, channels, private) using the Telethon session.

Uses TELEGRAM_API_ID, TELEGRAM_API_HASH, TELETHON_SESSION_PATH from .env.
Requires a valid session (run telegram_convert_desktop_session.py or telegram_login first).

Usage: python scripts/telegram_list_chats_telethon.py
"""
import asyncio
import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Load .env
_env = repo_root / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env)
    except ImportError:
        for line in _env.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELETHON_SESSION_PATH"):
                    os.environ.setdefault(k.strip(), v.strip())

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat


async def main() -> None:
    api_id = (os.environ.get("TELEGRAM_API_ID") or "").strip()
    api_hash = (os.environ.get("TELEGRAM_API_HASH") or "").strip()
    raw = (os.environ.get("TELETHON_SESSION_PATH") or "").strip()
    if raw:
        session_path = str(Path(raw).resolve() if not Path(raw).is_absolute() else Path(raw))
    else:
        session_path = str(repo_root / "data" / "telethon" / "session")

    if not api_id or not api_hash:
        print("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env (from my.telegram.org)", file=sys.stderr)
        sys.exit(1)

    client = TelegramClient(session_path, int(api_id), api_hash.strip())
    await client.connect()
    if not await client.is_user_authorized():
        print("Session not authorized. Run telegram_login or telegram_convert_desktop_session first.", file=sys.stderr)
        await client.disconnect()
        sys.exit(1)

    groups = []
    channels = []
    private = []

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        name = dialog.name or getattr(entity, "title", None) or "?"
        eid = getattr(entity, "id", None) or 0
        if isinstance(entity, Channel):
            if entity.megagroup:
                groups.append((eid, name, "group"))
            else:
                channels.append((eid, name, "channel"))
        elif isinstance(entity, Chat):
            groups.append((eid, name, "chat"))
        else:
            private.append((eid, name, "private"))

    await client.disconnect()

    def _p(s: str) -> None:
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))

    _p("=== GROUPS / SUPERGROUPS ===")
    for eid, name, _ in sorted(groups, key=lambda x: (x[1] or "").lower()):
        _p(f"  {eid}  {name}")

    _p("\n=== CHANNELS ===")
    for eid, name, _ in sorted(channels, key=lambda x: (x[1] or "").lower()):
        _p(f"  {eid}  {name}")

    _p("\n=== PRIVATE CHATS ===")
    for eid, name, _ in sorted(private[:20], key=lambda x: (x[1] or "").lower()):
        _p(f"  {eid}  {name}")
    if len(private) > 20:
        _p(f"  ... and {len(private) - 20} more")

    _p(f"\nTotal: {len(groups)} groups, {len(channels)} channels, {len(private)} private")


if __name__ == "__main__":
    asyncio.run(main())
