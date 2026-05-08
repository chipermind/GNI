"""Dead Letter Queue API: list, retry, drop."""
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.db import get_db_dependency
from apps.api.db.models import DeadLetterQueue as DLQ, Item

router = APIRouter(prefix="/dlq", tags=["dlq"])


class DLQOut(BaseModel):
    id: int
    item_id: int
    stage: str
    error: Optional[str]
    attempts: int
    last_seen: str
    created_at: str

    class Config:
        from_attributes = True


def _dlq_to_out(row: DLQ) -> dict[str, Any]:
    return {
        "id": row.id,
        "item_id": row.item_id,
        "stage": row.stage,
        "error": row.error,
        "attempts": row.attempts,
        "last_seen": row.last_seen.isoformat() if row.last_seen else "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


@router.get("", response_model=List[DLQOut])
def list_dlq(
    limit: int = 100,
    db: Session = Depends(get_db_dependency),
) -> List[dict[str, Any]]:
    """List DLQ entries, newest first."""
    rows = (
        db.query(DLQ)
        .order_by(DLQ.created_at.desc())
        .limit(min(limit, 500))
        .all()
    )
    return [_dlq_to_out(r) for r in rows]


@router.post("/{dlq_id}/retry")
def retry_dlq(dlq_id: int, db: Session = Depends(get_db_dependency)) -> dict[str, Any]:
    """
    Retry: reset item status and retry_count, delete DLQ entry.
    Item goes back to appropriate stage (drafted for publish failures).
    """
    row = db.query(DLQ).filter(DLQ.id == dlq_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="DLQ entry not found")

    item = db.query(Item).filter(Item.id == row.item_id).first()
    if not item:
        db.delete(row)
        db.commit()
        return {"status": "ok", "message": "Item deleted; DLQ entry removed"}

    # Reset item for retry
    item.retry_count = 0
    item.last_error = None
    # Set status based on stage: scoring->new, llm_draft->scored, publish->drafted
    if row.stage == "scoring":
        item.status = "new"
    elif row.stage == "llm_draft":
        item.status = "scored"
    else:
        item.status = "drafted"

    db.delete(row)
    db.commit()
    return {"status": "ok", "message": f"Item {row.item_id} reset to {item.status} for retry"}


@router.post("/{dlq_id}/drop")
def drop_dlq(dlq_id: int, db: Session = Depends(get_db_dependency)) -> dict[str, Any]:
    """Drop: delete DLQ entry and optionally mark item as failed (kept for audit)."""
    row = db.query(DLQ).filter(DLQ.id == dlq_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="DLQ entry not found")

    item = db.query(Item).filter(Item.id == row.item_id).first()
    if item:
        item.status = "failed"
        item.last_error = (item.last_error or "") + " [dropped from DLQ]"

    db.delete(row)
    db.commit()
    return {"status": "ok", "message": f"DLQ entry {dlq_id} dropped"}
