"""Source management: add, list, delete sources."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.db import get_db_dependency
from apps.api.db.models import Source

router = APIRouter(prefix="/sources", tags=["sources"])


def _is_valid_url(url: Optional[str]) -> bool:
    if not url or not url.strip():
        return False
    s = url.strip()
    if not s.startswith(("http://", "https://")):
        return False
    try:
        from urllib.parse import urlparse
        p = urlparse(s)
        return bool(p.netloc)
    except Exception:
        return False


class SourceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: Optional[str] = None
    type: str = Field(default="rss", pattern="^(rss|telegram|api)$")
    tier: int = Field(default=2, ge=1, le=3)
    chat_id: Optional[str] = None


class SourceOut(BaseModel):
    id: int
    name: Optional[str] = None
    url: Optional[str] = None
    type: str = "rss"
    tier: int = 2
    chat_id: Optional[str] = None
    created_at: Optional[str] = None


@router.get("", response_model=list[SourceOut])
def list_sources(session: Session = Depends(get_db_dependency)):
    """List all sources."""
    rows = session.query(Source).order_by(Source.id).all()
    return [
        SourceOut(
            id=r.id,
            name=r.name,
            url=r.url,
            type=r.type or "rss",
            tier=r.tier or 2,
            chat_id=r.chat_id,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.post("", response_model=SourceOut, status_code=201)
def add_source(body: SourceIn, session: Session = Depends(get_db_dependency)):
    """Add a source. Validates format: rss=valid URL, telegram=chat_id required, tier 1..3."""
    if body.type == "rss":
        if not _is_valid_url(body.url):
            raise HTTPException(
                status_code=400,
                detail="rss source must have a valid url",
            )
    elif body.type == "telegram":
        if not body.chat_id or not str(body.chat_id).strip():
            raise HTTPException(
                status_code=400,
                detail="telegram source must have chat_id",
            )
    elif body.type == "api":
        pass  # api may have optional url

    row = Source(
        name=body.name.strip(),
        url=body.url.strip() if body.url else None,
        type=body.type,
        tier=body.tier,
        chat_id=body.chat_id.strip() if body.chat_id else None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return SourceOut(
        id=row.id,
        name=row.name,
        url=row.url,
        type=row.type or "rss",
        tier=row.tier or 2,
        chat_id=row.chat_id,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: int, session: Session = Depends(get_db_dependency)):
    """Delete a source by id."""
    row = session.query(Source).filter(Source.id == source_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="source not found")
    session.delete(row)
    session.commit()
    return None
