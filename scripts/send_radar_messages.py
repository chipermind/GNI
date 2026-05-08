#!/usr/bin/env python3
"""
Send one or more radar-format messages (ACTIVE RADAR, GNI STRATEGIC READ) to Telegram.
Same format as Global News Intel channel. Use for testing or to send multiple updates.

Usage:
  python scripts/send_radar_messages.py                  # 1 message, dry-run (no send)
  python scripts/send_radar_messages.py --count 5        # 5 messages, dry-run
  python scripts/send_radar_messages.py --count 3 --send # 3 messages, actually send to Telegram

Requires repo root on PYTHONPATH (e.g. run from repo root or set PYTHONPATH=.).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root on path
_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from gni.analysis.llm_formatter import generate_report
from gni.analysis.radar_broadcast import run_radar_broadcast


def main() -> None:
    ap = argparse.ArgumentParser(description="Send radar-format messages to Telegram")
    ap.add_argument("--count", type=int, default=1, help="Number of messages to send (default 1)")
    ap.add_argument("--send", action="store_true", help="Actually send (default is dry-run)")
    ap.add_argument("--geopolitics", type=str, default="", help="Optional text for Geopolitics section")
    ap.add_argument("--cyber", type=str, default="", help="Optional text for Cyber Activity section")
    ap.add_argument("--crypto", type=str, default="", help="Optional text for Institutional Flows section")
    ap.add_argument("--ai", type=str, default="", help="Optional text for AI & Tech section")
    args = ap.parse_args()

    dry_run = not args.send
    if args.send and args.count > 10:
        print("Limiting to 10 messages when --send is used.")
        args.count = 10

    base_data: dict = {
        "geopolitics": (args.geopolitics or "").strip(),
        "cyber": (args.cyber or "").strip(),
        "crypto": (args.crypto or "").strip(),
        "ai": (args.ai or "").strip(),
    }

    sent = 0
    for i in range(args.count):
        # Vary input so cache doesn't return same report for every message
        radar_data = {**base_data, "_run": i}
        msg, ok = run_radar_broadcast(radar_data, dry_run=dry_run)
        if msg:
            if ok:
                sent += 1
            print(f"[{i+1}/{args.count}] {'SENT' if ok else 'dry-run'} ({len(msg)} chars)")
        else:
            print(f"[{i+1}/{args.count}] no message generated")

    mode = "dry-run" if dry_run else "send"
    print(f"Done: {sent} sent, {args.count} total ({mode})")


if __name__ == "__main__":
    main()
