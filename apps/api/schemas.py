"""Pydantic schemas for API request/response validation."""
from typing import Any, Optional

from pydantic import BaseModel, Field


class PauseResponse(BaseModel):
    paused: bool = True


class ResumeResponse(BaseModel):
    paused: bool = False


class PublicationOut(BaseModel):
    id: int
    channel: Optional[str] = None
    status: Optional[str] = None
    external_id: Optional[str] = None
    created_at: Optional[str] = None


class StatusStats(BaseModel):
    failed_items: int = 0
    failed_publications: int = 0
    publish_blocked_count: int = 0
    publications_sent: int = 0
    items_last_hour: int = 0
    drafts_last_hour: int = 0
    publications_last_hour: int = 0


class DependencyStatus(BaseModel):
    db: str = "unknown"
    redis: str = "unknown"
    ollama: str = "unknown"


class FailureEventOut(BaseModel):
    id: int
    event_type: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None


class StatusResponse(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    stats: StatusStats = Field(default_factory=StatusStats)
    dependencies: DependencyStatus = Field(default_factory=DependencyStatus)
    last_failures: list[FailureEventOut] = Field(default_factory=list)
    last_publications: list[PublicationOut] = Field(default_factory=list)


# --- Source management ---


class SourceOut(BaseModel):
    id: int
    name: Optional[str] = None
    url: Optional[str] = None
    type: str = "rss"
    tier: int = 2
    chat_id: Optional[str] = None
    created_at: Optional[str] = None


class SourceIn(BaseModel):
    name: str
    url: Optional[str] = None
    type: str = "rss"  # rss | telegram | api
    tier: int = 2
    chat_id: Optional[str] = None


# --- Review queue ---


class ReviewItemOut(BaseModel):
    id: int
    title: Optional[str] = None
    summary: Optional[str] = None
    source_name: Optional[str] = None
    status: str = "drafted"
    needs_review: bool = True
    created_at: Optional[str] = None
