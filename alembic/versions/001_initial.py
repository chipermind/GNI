"""Initial schema: all tables.

Revision ID: 001_initial
Revises:
Create Date: 2025-01-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("type", sa.String(32), nullable=False, server_default="rss"),
        sa.Column("tier", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("chat_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sources_name_url", "sources", ["name", "url"], unique=False)

    op.create_table(
        "raw_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_raw_items_source_id", "raw_items", ["source_id"], unique=False)

    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.String(1024), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_name", sa.String(255), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="rss"),
        sa.Column("risk", sa.String(32), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("template", sa.String(255), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_items_fingerprint", "items", ["fingerprint"], unique=True)
    op.create_index("ix_items_source_name", "items", ["source_name"], unique=False)
    op.create_index("ix_items_source_type_created_at", "items", ["source_type", "created_at"], unique=False)
    op.create_index("ix_items_status_id", "items", ["status", "id"], unique=False)
    op.create_index("ix_items_status_created_at_desc", "items", ["status", "created_at"], unique=False, postgresql_ops={"created_at": "DESC"})
    op.create_index("ix_items_fingerprint_created_at_desc", "items", ["fingerprint", "created_at"], unique=False, postgresql_ops={"created_at": "DESC"})
    op.create_index("ix_items_url", "items", ["url"], unique=False)

    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rendered_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_drafts_item_id", "drafts", ["item_id"], unique=False)
    op.create_index("ix_drafts_item_id_id_desc", "drafts", ["item_id", "id"], unique=False, postgresql_ops={"id": "DESC"})

    op.create_table(
        "publications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_publications_channel_status", "publications", ["channel", "status"], unique=False)
    op.create_index("ix_publications_channel_created_at_desc", "publications", ["channel", "created_at"], unique=False, postgresql_ops={"created_at": "DESC"})
    op.create_index("ix_publications_status_created_at_desc", "publications", ["status", "created_at"], unique=False, postgresql_ops={"created_at": "DESC"})
    op.create_index("ix_publications_channel", "publications", ["channel"], unique=False)
    op.create_index("ix_publications_external_id", "publications", ["external_id"], unique=False)
    op.create_index("ix_publications_status", "publications", ["status"], unique=False)

    op.create_table(
        "events_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_log_event_type", "events_log", ["event_type"], unique=False)

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("autopilot_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pause_all_publish", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rate_limits", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("feature_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_index("ix_events_log_event_type", table_name="events_log")
    op.drop_table("events_log")
    op.drop_index("ix_publications_status", table_name="publications")
    op.drop_index("ix_publications_external_id", table_name="publications")
    op.drop_index("ix_publications_channel", table_name="publications")
    op.drop_index("ix_publications_status_created_at_desc", table_name="publications")
    op.drop_index("ix_publications_channel_created_at_desc", table_name="publications")
    op.drop_index("ix_publications_channel_status", table_name="publications")
    op.drop_table("publications")
    op.drop_index("ix_drafts_item_id_id_desc", table_name="drafts")
    op.drop_index("ix_drafts_item_id", table_name="drafts")
    op.drop_table("drafts")
    op.drop_index("ix_items_url", table_name="items")
    op.drop_index("ix_items_fingerprint_created_at_desc", table_name="items")
    op.drop_index("ix_items_status_created_at_desc", table_name="items")
    op.drop_index("ix_items_status_id", table_name="items")
    op.drop_index("ix_items_source_type_created_at", table_name="items")
    op.drop_index("ix_items_source_name", table_name="items")
    op.drop_index("ix_items_fingerprint", table_name="items")
    op.drop_table("items")
    op.drop_index("ix_raw_items_source_id", table_name="raw_items")
    op.drop_table("raw_items")
    op.drop_index("ix_sources_name_url", table_name="sources")
    op.drop_table("sources")
