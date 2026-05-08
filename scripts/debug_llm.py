#!/usr/bin/env python3
"""Debug LLM draft: check scored items and try one classify+generate."""
import sys
sys.path.insert(0, ".")
from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Item
from apps.worker.llm import run_classify_then_generate

init_db()
s = SessionLocal()
items = s.query(Item).filter(Item.status == "scored").limit(2).all()
print("Scored items count:", len(items))
for item in items:
    print("  id:", item.id, "title:", (item.title or "")[:60])
    try:
        c, g = run_classify_then_generate(
            title=item.title or "",
            summary=item.summary or "",
            source_name=item.source_name or "",
        )
        print("  -> OK template:", c.template, "payload keys:", list((g.payload or {}).keys()))
    except Exception as e:
        print("  -> FAIL:", type(e).__name__, str(e)[:200])
s.close()
