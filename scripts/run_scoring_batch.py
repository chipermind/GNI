#!/usr/bin/env python3
"""Run scoring on up to N items with status=new; fill priority, risk, template, needs_review; set status=scored."""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Item
from apps.worker.scoring import apply_score_to_item, score_item


def run(limit: int = 50) -> int:
    init_db()
    session = SessionLocal()
    try:
        items = session.query(Item).filter(Item.status == "new").limit(limit).all()
        for item in items:
            score = score_item(
                title=item.title,
                summary=item.summary,
                source_name=item.source_name,
            )
            apply_score_to_item(item, score)
            item.status = "scored"
        session.commit()
        return len(items)
    finally:
        session.close()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    count = run(limit=n)
    print(f"Scored {count} items.")
