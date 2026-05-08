#!/usr/bin/env python3
"""Print all configured RSS sources. Run from repo root: python scripts/list_sources.py"""
import sys
from pathlib import Path

# Add apps/collector so we can import config
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "apps" / "collector"))

from config import print_sources

if __name__ == "__main__":
    print("Configured RSS sources:")
    print_sources()
