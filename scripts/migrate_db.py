#!/usr/bin/env python3
"""Initialize desk DB: create tables and ensure data/ exists. Optionally run cleanup."""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from desk.storage import DB_PATH, cleanup, init_db


def main() -> None:
    data_dir = DB_PATH.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    print("DB_PATH:", DB_PATH)
    print("migrate ok")

    if len(sys.argv) > 1 and sys.argv[1] == "--cleanup":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        counts = cleanup(days=days)
        print("cleanup:", counts)


if __name__ == "__main__":
    main()
    sys.exit(0)
