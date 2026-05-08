#!/usr/bin/env python3
"""
Validate required env vars for VM deployment. Exit 1 if any required var is missing.
Load .env from repo root if python-dotenv is available; otherwise use current env.
Usage:
  python scripts/validate_env.py [api|worker|all]
  # Or with .env loaded:
  set -a && source .env && set +a && python scripts/validate_env.py api
"""
import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Optional: load .env from repo root
_env_file = repo_root / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        pass

os.chdir(repo_root)

from apps.shared.env_validation import main

if __name__ == "__main__":
    sys.exit(main())
