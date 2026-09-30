"""Add explicit public visibility gate to items.

Revision ID: 005_public_visibility
Revises: 004_dlq
Create Date: 2026-09-28

No existing item becomes public during this migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005_public_visibility"
down_revision: Union[str, Sequence[str], None] = "004_dlq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column(
            "public_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_items_public_visible_status_updated_at",
        "items",
        ["public_visible", "status", "updated_at"],
        unique=False,
        postgresql_ops={"updated_at": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("ix_items_public_visible_status_updated_at", table_name="items")
    op.drop_column("items", "public_visible")
