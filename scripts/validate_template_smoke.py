#!/usr/bin/env python3
"""
Smoke test: raw templates should fail validation (unfilled_placeholders).
Exits 0 when expected FAIL occurs.
"""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from desk.templates import load_template
from desk.validators import validate_text


def main() -> int:
    types_to_check = ("PANORAMA_0900", "EXEC_SUMMARY_2030")
    for desk_type in types_to_check:
        text = load_template(desk_type)
        ok, reason = validate_text(desk_type, text)
        if ok:
            print(f"FAIL: {desk_type} passed validation (expected unfilled_placeholders)")
            return 1
        if reason != "unfilled_placeholders":
            print(f"FAIL: {desk_type} got reason={reason!r} (expected unfilled_placeholders)")
            return 1
        print(f"OK: {desk_type} -> {reason} (expected)")
    print("All templates correctly rejected (unfilled_placeholders).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
