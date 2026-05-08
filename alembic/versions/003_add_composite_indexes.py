"""Add composite indexes for query patterns.

Revision ID: 003_indexes
Revises: 002_ensure
Create Date: 2025-01-30

Indexes: items (status, created_at DESC), (fingerprint, created_at DESC);
publications (channel, created_at DESC), (status, created_at DESC);
drafts (item_id). Uses IF NOT EXISTS for idempotency.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "003_indexes"
down_revision: Union[str, Sequence[str], None] = "002_ensure"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # items
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_items_status_created_at_desc ON items (status, created_at DESC)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_items_fingerprint_created_at_desc ON items (fingerprint, created_at DESC)"))
    # publications
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_publications_channel_created_at_desc ON publications (channel, created_at DESC)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_publications_status_created_at_desc ON publications (status, created_at DESC)"))
    # drafts (item_id - may already exist from column index)
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_drafts_item_id ON drafts (item_id)"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP INDEX IF EXISTS ix_drafts_item_id"))
    conn.execute(text("DROP INDEX IF EXISTS ix_publications_status_created_at_desc"))
    conn.execute(text("DROP INDEX IF EXISTS ix_publications_channel_created_at_desc"))
    conn.execute(text("DROP INDEX IF EXISTS ix_items_fingerprint_created_at_desc"))
    conn.execute(text("DROP INDEX IF EXISTS ix_items_status_created_at_desc"))
