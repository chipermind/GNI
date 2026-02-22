#!/usr/bin/env python3
"""
Run a single desk window manually.
Respects DESK_DRY_RUN (default 1 = dry-run, no Telegram).
Usage: python scripts/run_desk_once.py PANORAMA_0900
"""
import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from desk.scheduler import run_window


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_desk_once.py <DESK_TYPE>")
        print("Example: python scripts/run_desk_once.py PANORAMA_0900")
        return 1
    desk_type = sys.argv[1].strip()
    summary = run_window(desk_type)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
