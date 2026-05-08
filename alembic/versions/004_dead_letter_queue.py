"""Add dead_letter_queue table and Item.retry_count.

Revision ID: 004_dlq
Revises: 003_indexes
Create Date: 2025-01-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_dlq"
down_revision: Union[str, Sequence[str], None] = "003_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dead_letter_queue",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dlq_item_id", "dead_letter_queue", ["item_id"], unique=False)
    op.create_index("ix_dlq_stage", "dead_letter_queue", ["stage"], unique=False)

    op.execute(sa.text("ALTER TABLE items ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0"))


def downgrade() -> None:
    op.drop_index("ix_dlq_stage", table_name="dead_letter_queue")
    op.drop_index("ix_dlq_item_id", table_name="dead_letter_queue")
    op.drop_table("dead_letter_queue")
    op.execute(sa.text("ALTER TABLE items DROP COLUMN IF EXISTS retry_count"))
