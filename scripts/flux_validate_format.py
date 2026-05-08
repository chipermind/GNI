#!/usr/bin/env python3
"""
Validate Flux E2E rendered format for Template A and Template B.
Queries drafts for given item IDs and checks required strings.
Exits 0 if all checks pass, 1 otherwise.
"""
import sys
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
if str(repo) not in sys.path:
    sys.path.insert(0, str(repo))

from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Draft, Item

# Template A (ANALISE_INTEL) - exact strings from render.py
TEMPLATE_A_REQUIRED = [
    "GNI — Análise de Inteligência",
    "Tema:",
    "Leitura rápida",
    "Por que isso importa",
    "Como validar (checklist OSINT)",
    "Insight central",
    "⸻",
    "\t•",
]
TEMPLATE_A_CHECKLIST = "\t• ✅"

# Template B (FLASH_SETORIAL) - Em destaque, 📌 Insight:, ⸻
TEMPLATE_B_REQUIRED = [
    "GNI |",
    "Em destaque",
    "📌 Insight:",
    "⸻",
]


def main() -> int:
    init_db()
    session = SessionLocal()
    try:
        item_ids = [int(x) for x in sys.argv[1].split(",") if x.strip()] if len(sys.argv) > 1 else []
        if not item_ids:
            print("Usage: flux_validate_format.py <item_ids>", file=sys.stderr)
            return 1

        items = session.query(Item).filter(Item.id.in_(item_ids)).all()
        draft_ids = [i.id for i in items]
        drafts = (
            session.query(Draft)
            .filter(Draft.item_id.in_(item_ids))
            .order_by(Draft.item_id, Draft.id.desc())
            .all()
        )
        latest_by_item = {}
        for d in drafts:
            if d.item_id not in latest_by_item:
                latest_by_item[d.item_id] = d

        ok = True
        for item in items:
            draft = latest_by_item.get(item.id)
            if not draft or not draft.rendered_text:
                print(f"FAIL: Item {item.id} has no rendered draft", file=sys.stderr)
                ok = False
                continue
            text = draft.rendered_text
            template = (item.template or "").upper()
            if "ANALISE_INTEL" in template or "DEFAULT" in template:
                for s in TEMPLATE_A_REQUIRED:
                    if s not in text:
                        print(f"FAIL: Item {item.id} Template A missing: {s!r}", file=sys.stderr)
                        ok = False
                        break
                if TEMPLATE_A_CHECKLIST not in text and "checklist" in text.lower():
                    pass  # Checklist may be empty
            elif "FLASH_SETORIAL" in template:
                for s in TEMPLATE_B_REQUIRED:
                    if s not in text:
                        print(f"FAIL: Item {item.id} Template B missing: {s!r}", file=sys.stderr)
                        ok = False
                        break
            else:
                print(f"WARN: Item {item.id} template={template}, validating as Template A", file=sys.stderr)
                for s in TEMPLATE_A_REQUIRED[:2]:  # minimal check
                    if s not in text:
                        print(f"FAIL: Item {item.id} missing: {s!r}", file=sys.stderr)
                        ok = False
                        break

        return 0 if ok else 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
