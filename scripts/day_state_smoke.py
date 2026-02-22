#!/usr/bin/env python3
"""
Smoke test: update day_state and print EXEC 20:30 closure text.
Does not send Telegram. Exit 0.
"""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from desk.day_state import build_exec_closure, day_key, load_day_state, update_and_persist


def main() -> int:
    snapshot1 = {
        "ts": None,
        "intel": [{"title": "Smoke test signal A", "impact": "high", "category": "geo"}],
        "deltas": {},
    }
    post1 = {"type": "PANORAMA_0900", "text": "Smoke post", "tags": ["geo"], "reasons": ["confirmed: smoke A"], "meta": {}}
    update_and_persist("PANORAMA_0900", snapshot1, post1)

    snapshot2 = {"ts": None, "intel": [], "lost_strength": ["smoke:reversal"], "deltas": {}}
    post2 = {"type": "FLOW_1330", "text": "Smoke post 2", "tags": ["cyber"], "reasons": [], "meta": {}}
    update_and_persist("FLOW_1330", snapshot2, post2)

    snapshot3 = {"ts": None, "intel": [{"title": "Smoke watch item", "impact": "high"}], "deltas": {}}
    post3 = {"type": "EXEC_SUMMARY_2030", "text": "Smoke exec", "tags": ["macro"], "reasons": ["confirmed: smoke B"], "meta": {}}
    update_and_persist("EXEC_SUMMARY_2030", snapshot3, post3)

    day_state = load_day_state(day_key(tz="America/Recife"))
    closure = build_exec_closure(day_state, last_posts=[])
    txt = closure["text"]
    try:
        print(txt)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(txt.encode("utf-8", errors="replace") + b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
