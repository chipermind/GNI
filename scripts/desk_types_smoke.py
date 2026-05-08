#!/usr/bin/env python3
"""Smoke test for desk types. Prints ordered types and limits. No dependencies."""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from desk.types import ALL_DESK_TYPES, DeskType, LIMITS, get_limits


def main() -> None:
    for dt in ALL_DESK_TYPES:
        max_lines, max_chars = get_limits(dt)
        print(f"{dt.value}: max_lines={max_lines}, max_chars={max_chars}")


if __name__ == "__main__":
    main()
    sys.exit(0)
