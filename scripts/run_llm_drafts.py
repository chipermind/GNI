#!/usr/bin/env python3
"""
Local test: run 3 items through classify -> generate and produce valid JSON drafts.
Requires Ollama running (e.g. docker compose up ollama). Usage: python scripts/run_llm_drafts.py
"""
import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from apps.worker.llm import run_classify_then_generate

SAMPLES = [
    {"title": "Bitcoin hits new high amid institutional demand", "summary": "Price surge continues.", "source_name": "CoinDesk"},
    {"title": "Unconfirmed reports of SEC settlement", "summary": "Rumor suggests deal close.", "source_name": "Reuters Markets"},
    {"title": "Partnership announcement: Bank X and Crypto Y", "summary": "New capability unveiled.", "source_name": "Cointelegraph"},
]


def main():
    base_url = __import__("os").environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    model = __import__("os").environ.get("OLLAMA_MODEL", "qwen2.5:7b")
    print(f"Ollama: {base_url} model={model}")
    drafts = []
    for i, item in enumerate(SAMPLES, 1):
        print(f"\n--- Item {i}: {item['title'][:50]}...")
        try:
            c, g = run_classify_then_generate(
                title=item["title"],
                summary=item.get("summary", ""),
                source_name=item.get("source_name", ""),
                model=model,
                base_url=base_url,
            )
            draft = {
                "classify": c.model_dump(),
                "generate": g.model_dump(),
            }
            drafts.append(draft)
            print("  Classify:", json.dumps(c.model_dump(), default=str))
            print("  Generate payload keys:", list(g.payload.keys()) if g.payload else [])
        except Exception as e:
            print(f"  FAIL: {e}")
            drafts.append({"error": str(e)})
    print("\n--- Result: valid JSON drafts =", sum(1 for d in drafts if "error" not in d))
    return 0 if all("error" not in d for d in drafts) else 1


if __name__ == "__main__":
    sys.exit(main())
