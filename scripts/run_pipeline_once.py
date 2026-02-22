#!/usr/bin/env python3
"""Run pipeline once with DRY_RUN=0. Usage: DRY_RUN=0 python scripts/run_pipeline_once.py"""
import os
import sys
sys.path.insert(0, ".")
os.environ.setdefault("DRY_RUN", "0")
from apps.worker.tasks import run_pipeline
r = run_pipeline(dry_run=False)
print("Result:", r)
