#!/usr/bin/env python3
"""
Preview and test GNI editorial formats locally: validate guard + splitter without Telegram.
Optionally send to a test channel when TELEGRAM_TEST_CHAT_ID is set.

Usage:
  # Dry-run (default): print format_mode, guard result, chunks (LONG), no send
  python scripts/gni_preview.py --mode LONG
  python scripts/gni_preview.py --mode SHORT
  python scripts/gni_preview.py --mode FLASH
  python scripts/gni_preview.py --job-name briefing_0900
  python scripts/gni_preview.py --job-name radar_interval

  # Actually send to test channel (requires TELEGRAM_TEST_CHAT_ID)
  python scripts/gni_preview.py --mode SHORT --send

Safe: --send only sends when TELEGRAM_TEST_CHAT_ID is set; otherwise exits with error.
Requires repo root on PYTHONPATH (run from repo root or set PYTHONPATH=.).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

CHUNK_SEP = "\n" + "=" * 60 + "\n"


# Map CLI --mode to internal format_mode (guards/send use full names)
MODE_TO_FORMAT = {
    "LONG": "BRIEFING_LONG",
    "SHORT": "RADAR_SHORT",
    "FLASH": "FLASH_BREAKING",
}


def _resolve_mode(mode: str | None, job_name: str | None) -> str:
    """Return internal format_mode (BRIEFING_LONG, RADAR_SHORT, FLASH_BREAKING)."""
    if mode:
        return MODE_TO_FORMAT.get(mode.strip().upper(), "BRIEFING_LONG")
    if job_name:
        from gni.editorial.router import select_format
        return select_format(job_name.strip(), None, None)
    return "BRIEFING_LONG"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Preview GNI format (guard + splitter) or send to test channel"
    )
    ap.add_argument(
        "--mode",
        choices=["LONG", "SHORT", "FLASH"],
        help="Format mode (overrides --job-name if set)",
    )
    ap.add_argument(
        "--job-name",
        type=str,
        help="Job name to derive mode: e.g. briefing_0900, radar_interval, intel_flash",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Only validate and print; do not send (default: True)",
    )
    ap.add_argument(
        "--send",
        action="store_true",
        help="Send to test channel. Safe: exits with error if TELEGRAM_TEST_CHAT_ID is not set.",
    )
    ap.add_argument(
        "--geopolitics",
        type=str,
        default="",
        help="Optional radar input for Geopolitics",
    )
    args = ap.parse_args()

    dry_run = not args.send
    if args.send:
        test_chat = (os.environ.get("TELEGRAM_TEST_CHAT_ID") or "").strip()
        if not test_chat:
            print("ERROR: --send requires TELEGRAM_TEST_CHAT_ID to be set.", file=sys.stderr)
            print("Set it in .env or export TELEGRAM_TEST_CHAT_ID=your_test_chat_id", file=sys.stderr)
            return 1
        # Send to test channel only (override target for this process)
        os.environ["TELEGRAM_TARGET_CHAT_ID"] = test_chat

    format_mode = _resolve_mode(args.mode, args.job_name)

    radar_data = {
        "geopolitics": (args.geopolitics or "").strip() or "Preview run. Sem dados externos.",
        "cyber": "",
        "crypto": "",
        "ai": "",
    }

    from gni.analysis.llm_formatter import generate_report
    from gni.publisher.guards import guard_and_validate
    from gni.publisher.splitter import split_briefing_long, DEFAULT_MAX_CHARS

    text = generate_report(radar_data, format_mode=format_mode)
    if not text:
        print("ERROR: No content generated.")
        return 1

    guard_ok, guard_reason = guard_and_validate(text, format_mode)
    print("format_mode:", format_mode, flush=True)
    print("guard:", "pass" if guard_ok else f"fail ({guard_reason})", flush=True)

    def _safe_print(s: str) -> None:
        try:
            print(s)
        except UnicodeEncodeError:
            sys.stdout.buffer.write((s + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()

    if format_mode == "BRIEFING_LONG":
        chunks = split_briefing_long(text, max_chars=DEFAULT_MAX_CHARS)
        print("chunks:", len(chunks), flush=True)
        if chunks:
            for i, c in enumerate(chunks, 1):
                print(f"  part {i}: {len(c)} chars", flush=True)
        print(flush=True)
        print("--- CHUNKS (separator below) ---", flush=True)
        for i, c in enumerate(chunks, 1):
            _safe_print(c)
            if i < len(chunks):
                print(CHUNK_SEP)
    else:
        print("chunks: 1 (single message)", flush=True)
        print(flush=True)
        print("--- CONTENT ---", flush=True)
        _safe_print(text)

    if dry_run:
        print()
        print("(dry-run: nothing sent). Use --send to send to TELEGRAM_TEST_CHAT_ID.")
        return 0

    # --send path
    from gni.publisher.send import send_long_message, send_message

    if format_mode == "BRIEFING_LONG":
        result = send_long_message(text, meta={"source": "gni_preview"}, dry_run=False)
    else:
        result = send_message(text, format_mode=format_mode, meta={"source": "gni_preview"}, dry_run=False)

    if result.status == "guard_failed":
        print("NOT SENT: guard failed.", file=sys.stderr)
        return 1
    if result.status == "sent":
        print("OK: Sent to test channel (TELEGRAM_TEST_CHAT_ID).")
        return 0
    print("Send failed:", result.status, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
