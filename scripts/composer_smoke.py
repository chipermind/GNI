#!/usr/bin/env python3
"""
Smoke test: compose_post for PANORAMA_0900.
Requires local Ollama. Does not send Telegram.
Exits 0 if compose_post returns a dict (success or fallback).
"""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from desk.composer import compose_post


def main() -> int:
    snapshot = {
        "ts": None,
        "markets": {},
        "intel": [],
        "flow": {},
    }
    context = {"last_posts": []}
    result = compose_post("PANORAMA_0900", snapshot, context)
    if not isinstance(result, dict):
        print("FAIL: compose_post did not return a dict")
        return 1
    print("keys:", list(result.keys()))
    text = result.get("text") or ""
    preview = text[:200] + ("..." if len(text) > 200 else "")
    print("text (first 200 chars):", repr(preview))
    return 0


if __name__ == "__main__":
    sys.exit(main())
