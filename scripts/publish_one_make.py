#!/usr/bin/env python3
"""
Manual run: publish one rendered message to Make webhook.
Usage:
  MAKE_WEBHOOK_URL=https://... python scripts/publish_one_make.py
  # Simulate failure (invalid URL):
  MAKE_WEBHOOK_URL=https://invalid-url-404.example.com python scripts/publish_one_make.py
"""
import os
import sys
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
if str(repo) not in sys.path:
    sys.path.insert(0, str(repo))

os.environ.setdefault("DRY_RUN", "0")

from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Item
from apps.publisher.whatsapp_make import send_whatsapp_via_make


def main():
    init_db()
    session = SessionLocal()
    try:
        item = session.query(Item).filter(Item.status.in_(["drafted", "published"])).order_by(Item.id.desc()).first()
        if not item:
            print("No drafted/published item found. Run pipeline first.")
            sys.exit(1)
        rendered_text = "Test message from publish_one_make\n---\nManual run to verify Make webhook."
        template = item.template or "ANALISE_INTEL"
        priority = f"P{item.priority}" if item.priority is not None else "P2"
        webhook_url = os.environ.get("MAKE_WEBHOOK_URL", "").strip()
        if not webhook_url:
            print("Set MAKE_WEBHOOK_URL in environment.")
            sys.exit(1)
        result = send_whatsapp_via_make(
            session,
            item,
            rendered_text=rendered_text,
            template=template,
            priority=priority,
            dry_run=False,
        )
        session.commit()
        print(f"Result: status={result.status}, attempts={result.attempts}, publication_id={result.publication_id}")
        if result.last_error:
            print(f"Error: {result.last_error}")
        if result.status == "sent":
            print("SUCCESS: Publication logged.")
        else:
            print("FAILED: Check events_log for details.")
            sys.exit(1)
    except Exception as e:
        session.rollback()
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
