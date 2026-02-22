#!/usr/bin/env python3
"""Test LLM draft step. Usage: python scripts/test_llm_draft.py"""
import sys
sys.path.insert(0, ".")
from apps.worker.tasks import step_llm_draft

try:
    n = step_llm_draft(limit=2)
    print("Drafted:", n)
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
