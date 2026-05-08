#!/usr/bin/env python3
"""
List desk scheduler job IDs (requires APScheduler).
Starts scheduler, prints jobs, then shuts down.
Does not use FastAPI.
"""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from desk.scheduler import start_scheduler, shutdown_scheduler


def main() -> int:
    try:
        sched = start_scheduler()
    except ImportError as e:
        print("APScheduler not installed:", e)
        return 1
    jobs = sched.get_jobs()
    print("Job IDs:")
    for j in jobs:
        print(" ", j.id)
    shutdown_scheduler()
    return 0


if __name__ == "__main__":
    sys.exit(main())
