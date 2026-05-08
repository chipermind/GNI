#!/usr/bin/env python3
"""
Run Telegram publisher in dry_run: prints messages and writes one row to publications.
Requires DATABASE_URL (e.g. from .env). Usage: python scripts/run_publisher_dry_run.py
"""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from apps.api.db import SessionLocal, init_db
from apps.api.db.models import Publication
from apps.publisher.telegram import publish_telegram


def main():
    init_db()
    session = SessionLocal()
    try:
        result = publish_telegram(
            messages=["[dry_run] Message one", "[dry_run] Message two"],
            channel="telegram",
            dry_run=True,
            session=session,
        )
        session.commit()
        print(f"\nPublication logged: id={result.publication_id} status={result.status}")
        row = session.query(Publication).filter(Publication.id == result.publication_id).first()
        if row:
            print(f"  DB row: channel={row.channel} status={row.status}")
        return 0
    except Exception as e:
        session.rollback()
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
