#!/usr/bin/env python3
"""
Insert two deterministic test items for Flux E2E verification.
Item A: alegação / não confirmada -> Template A (ANALISE_INTEL)
Item B: defesa / sistema / teste -> Template B (FLASH_SETORIAL)
No external internet. Prints comma-separated IDs to stdout.
"""
import sys
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
if str(repo) not in sys.path:
    sys.path.insert(0, str(repo))

from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Item
from apps.worker.dedupe import build_fingerprint

# Item A: alegação / não confirmada -> Template A (ANALISE_INTEL)
ITEM_A = {
    "title": "Alegação: suposto desvio de fundos não confirmada por fontes",
    "summary": "Alleged violation. Não confirmada. Rumores sobre sanções. Unconfirmed claim.",
    "url": "https://flux-test.example.com/item-a",
    "source_name": "Reuters Markets",
}

# Item B: defesa / sistema / teste -> Template B (FLASH_SETORIAL)
ITEM_B = {
    "title": "Defesa: novo sistema de teste e parceria anunciada",
    "summary": "Sistema de defesa em teste. Lançamento e demo. Partnership announced.",
    "url": "https://flux-test.example.com/item-b",
    "source_name": "Reuters Markets",
}


def main() -> int:
    init_db()
    session = SessionLocal()
    try:
        ids = []
        for label, data in [("A", ITEM_A), ("B", ITEM_B)]:
            fp = build_fingerprint("rss", data["url"], data["title"])
            existing = session.query(Item).filter(Item.fingerprint == fp).first()
            if existing:
                ids.append(existing.id)
                continue
            item = Item(
                fingerprint=fp,
                title=data["title"],
                url=data["url"],
                summary=data["summary"],
                source_name=data["source_name"],
                source_type="rss",
                status="new",
            )
            session.add(item)
            session.flush()
            ids.append(item.id)
        session.commit()
        print(",".join(str(i) for i in ids))
        return 0
    except Exception as e:
        session.rollback()
        print(str(e), file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
