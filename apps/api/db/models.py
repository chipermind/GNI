"""
Minimal table definitions. Use create_all() on startup (MVP);
structured so Alembic can take over migrations later (single Base.metadata).
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Single declarative base for all models — Alembic uses Base.metadata."""
    pass


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        Index("ix_sources_name_url", "name", "url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    type: Mapped[str] = mapped_column(String(32), default="rss", index=True)  # rss | telegram | api
    tier: Mapped[int] = mapped_column(Integer, default=2)
    chat_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    raw_items: Mapped[list["RawItem"]] = relationship("RawItem", back_populates="source")


class RawItem(Base):
    __tablename__ = "raw_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sources.id"), nullable=True, index=True)
    raw_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    source: Mapped[Optional["Source"]] = relationship("Source", back_populates="raw_items")


class Item(Base):
    """Lifecycle status: new -> scored -> drafted -> published | failed."""
    __tablename__ = "items"
    __table_args__ = (
        Index("ix_items_fingerprint_created_at", "fingerprint", "created_at"),
        Index("ix_items_source_type_created_at", "source_type", "created_at"),
        Index("ix_items_status_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True, index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="rss", index=True)  # rss | telegram | api
    risk: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    priority: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    template: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)  # new | scored | drafted | published | failed | dlq
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (
        Index("ix_drafts_item_id_id_desc", "item_id", "id", postgresql_ops={"id": "DESC"}),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("items.id"), nullable=True, index=True)
    data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    rendered_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        Index("ix_publications_channel_status", "channel", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class EventsLog(Base):
    __tablename__ = "events_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class DeadLetterQueue(Base):
    """Failed items after max attempts: item_id, stage, error, attempts, last_seen."""

    __tablename__ = "dead_letter_queue"
    __table_args__ = (Index("ix_dlq_item_id", "item_id"), Index("ix_dlq_stage", "stage"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # scoring | llm_draft | publish
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    autopilot_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_all_publish: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limits: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # e.g. {"per_minute": 5, "per_hour": 100}
    feature_flags: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # runtime toggles: {"flag_name": true}
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
