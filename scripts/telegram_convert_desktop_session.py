#!/usr/bin/env python3
"""
Convert Telegram Desktop tdata to a Telethon session file (no verification code needed).

Use this when the 5-digit code never arrives: log in to Telegram Desktop via QR code,
then run this script to get a session file the project can use.

Steps:
  1. Install Telegram Desktop, log in with QR code (no code).
  2. Close Telegram Desktop.
  3. pip install opentele
  4. Run: python scripts/telegram_convert_desktop_session.py

Session is saved to TELETHON_SESSION_PATH or data/telethon/session.
"""
from __future__ import annotations

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
                if k.strip() == "TELETHON_SESSION_PATH":
                    os.environ.setdefault(k.strip(), v.strip())
                    break

try:
    from opentele.td import TDesktop
    from opentele.api import UseCurrentSession
    from opentele.exception import OpenTeleException
except ImportError:
    print("opentele is required. Install it with:", file=sys.stderr)
    print("  pip install opentele", file=sys.stderr)
    sys.exit(1)


def default_tdata_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "Telegram Desktop" / "tdata"
    else:
        home = os.environ.get("HOME", "")
        if home:
            return Path(home) / ".local" / "share" / "TelegramDesktop" / "tdata"
    return Path("")


def get_session_path() -> Path:
    raw = (os.environ.get("TELETHON_SESSION_PATH") or "").strip()
    if raw:
        p = Path(raw)
    else:
        p = repo_root / "data" / "telethon" / "session"
    return p.resolve()


async def main() -> None:
    tdata_path = default_tdata_path()
    if not tdata_path or not tdata_path.exists():
        print("Telegram Desktop tdata folder not found.", file=sys.stderr)
        if sys.platform == "win32":
            print("Default path: %APPDATA%\\Telegram Desktop\\tdata", file=sys.stderr)
        print("Make sure Telegram Desktop is installed and you have logged in (e.g. via QR).", file=sys.stderr)
        sys.exit(1)

    session_path = get_session_path()
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_str = str(session_path)

    print("Loading tdata from:", tdata_path)
    try:
        tdesk = TDesktop(str(tdata_path))
    except OpenTeleException as e:
        print("Error loading session:", str(e), file=sys.stderr)
        print(file=sys.stderr)
        print("pip may install an old opentele. Install the latest from GitHub:", file=sys.stderr)
        print("  pip uninstall opentele -y", file=sys.stderr)
        print("  pip install git+https://github.com/thedemons/opentele.git", file=sys.stderr)
        print(file=sys.stderr)
        print("Other tips:", file=sys.stderr)
        print("  - Telegram Desktop must be fully closed before running.", file=sys.stderr)
        print("  - If Desktop has a local passcode, opentele may need it (check docs).", file=sys.stderr)
        print("  - Or use older Telegram portable: tportable-x64.4.14.2 from official site.", file=sys.stderr)
        sys.exit(1)
    if not tdesk.isLoaded():
        print("No authorized account in tdata. Log in to Telegram Desktop first (e.g. via QR code).", file=sys.stderr)
        sys.exit(1)

    print("Converting to Telethon session (this may take a moment)...")
    client = await tdesk.ToTelethon(session=session_str, flag=UseCurrentSession)
    await client.connect()
    await client.disconnect()
    print("Session saved to:", session_str)
    print("You can now run: python scripts/telegram_list_chats_telethon.py")
    print("Or: python -m apps.collector.telegram_ingest --since-minutes 60")


if __name__ == "__main__":
    asyncio.run(main())
