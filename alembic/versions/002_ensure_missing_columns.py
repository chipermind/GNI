"""Ensure missing columns exist (for DBs created before Alembic).

Revision ID: 002_ensure
Revises: 001_initial
Create Date: 2025-01-30

Adds columns if not exist: sources.type, tier, chat_id; items.source_type; publications.attempts.
Adds settings.feature_flags if missing.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "002_ensure"
down_revision: Union[str, Sequence[str], None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # sources (PostgreSQL ADD COLUMN IF NOT EXISTS)
    conn.execute(text("ALTER TABLE sources ADD COLUMN IF NOT EXISTS type VARCHAR(32) DEFAULT 'rss'"))
    conn.execute(text("ALTER TABLE sources ADD COLUMN IF NOT EXISTS tier INTEGER DEFAULT 2"))
    conn.execute(text("ALTER TABLE sources ADD COLUMN IF NOT EXISTS chat_id VARCHAR(255)"))
    # items
    conn.execute(text("ALTER TABLE items ADD COLUMN IF NOT EXISTS source_type VARCHAR(32) DEFAULT 'rss'"))
    # publications
    conn.execute(text("ALTER TABLE publications ADD COLUMN IF NOT EXISTS attempts INTEGER DEFAULT 0"))
    # settings
    conn.execute(text("ALTER TABLE settings ADD COLUMN IF NOT EXISTS feature_flags JSONB"))


def downgrade() -> None:
    pass
