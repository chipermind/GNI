"""
CLI to run pipeline for specific item IDs.
Usage: python -m apps.worker.run_pipeline --item-ids 1,2 --publish
Reuses real pipeline functions (no duplicated logic).
"""
import argparse
import os
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from apps.shared.config import ConfigError
from apps.shared.env_validation import EnvValidationError, validate_env
from apps.worker.tasks import run_pipeline, _dry_run, _log_info


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pipeline for specific item IDs")
    parser.add_argument("--item-ids", type=str, required=True, help="Comma-separated item IDs (e.g. 1,2,3)")
    parser.add_argument("--publish", action="store_true", help="Publish for real (DRY_RUN=0)")
    parser.add_argument("--dry-run", action="store_true", help="Publish in dry_run mode (default)")
    args = parser.parse_args()

    item_ids = [int(x.strip()) for x in args.item_ids.split(",") if x.strip()]
    if not item_ids:
        print("No item IDs provided", file=sys.stderr)
        return 1

    try:
        validate_env(role="worker")
    except (ConfigError, EnvValidationError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.publish:
        os.environ["DRY_RUN"] = "0"
    elif args.dry_run:
        os.environ["DRY_RUN"] = "1"

    result = run_pipeline(dry_run=not args.publish, item_ids=item_ids)
    _log_info("Pipeline run for items", item_ids=item_ids, **result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
