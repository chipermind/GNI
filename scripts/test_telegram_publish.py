#!/usr/bin/env python3
"""
Send ONE test message to Telegram (or dry-run if DRY_RUN=1 / no token).
Uses TELEGRAM_BOT_TOKEN + TELEGRAM_TARGET_CHAT_ID. Logs to publications table.
Usage:
  python scripts/test_telegram_publish.py
  DRY_RUN=1 python scripts/test_telegram_publish.py   # print only, no send
"""
import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Load .env if present
_env = repo_root / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env)
    except ImportError:
        pass

from apps.shared.secrets import get_secret
from apps.publisher.telegram import publish_telegram

TEST_MESSAGE = (
    "🧪 GNI — Test message\n"
    "This is a single test from test_telegram_publish.py.\n"
    "If you see this, Telegram publishing is working."
)


def main() -> int:
    token = (get_secret("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (get_secret("TELEGRAM_TARGET_CHAT_ID") or get_secret("TELEGRAM_CHAT_ID") or "").strip()
    dry_run_env = os.environ.get("DRY_RUN", "0").strip().lower() in ("1", "true", "yes")

    if not token or not chat_id:
        print("DRY_RUN (no TELEGRAM_BOT_TOKEN or TELEGRAM_TARGET_CHAT_ID): printing message only.", file=sys.stderr)
        try:
            print(TEST_MESSAGE)
        except UnicodeEncodeError:
            print(TEST_MESSAGE.encode("ascii", errors="replace").decode("ascii"))
        print("OK: no credentials; message printed (no send, no DB).")
        return 0

    dry_run = dry_run_env
    print(f"Sending 1 test message (dry_run={dry_run})...")
    result = publish_telegram([TEST_MESSAGE], channel="telegram", dry_run=dry_run)

    if result.dry_run:
        print("OK: dry-run completed (message not sent).")
        return 0
    if result.status == "sent":
        print(f"OK: message sent. publication_id={result.publication_id} external_id={result.external_id}")
        return 0
    print(f"FAIL: status={result.status}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
