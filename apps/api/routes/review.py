"""Review queue: pending items, approve, reject."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.db import get_db_dependency
from apps.api.db.models import Item

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/pending")
def get_pending(session: Session = Depends(get_db_dependency)):
    """Return items with needs_review=true and status=drafted."""
    rows = (
        session.query(Item)
        .filter(Item.needs_review == True, Item.status == "drafted")  # noqa: E712
        .order_by(Item.id.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": r.id,
            "title": r.title,
            "summary": r.summary,
            "source_name": r.source_name,
            "status": r.status,
            "needs_review": r.needs_review,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/{item_id}/approve")
def approve_item(item_id: int, session: Session = Depends(get_db_dependency)):
    """Approve item: triggers publish (moves to published). Rejects if not drafted/needs_review."""
    row = session.query(Item).filter(Item.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="item not found")
    if row.status != "drafted":
        raise HTTPException(status_code=400, detail=f"item status is {row.status}, expected drafted")
    row.needs_review = False
    row.status = "drafted"  # keep drafted so pipeline will pick it up for publish
    session.commit()
    return {"id": item_id, "status": "approved", "message": "item will be published by pipeline"}


@router.post("/{item_id}/reject")
def reject_item(item_id: int, session: Session = Depends(get_db_dependency)):
    """Reject item: mark status=failed, last_error=rejected. Item will not be published."""
    row = session.query(Item).filter(Item.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="item not found")
    row.status = "failed"
    row.last_error = "rejected"
    row.needs_review = False
    session.commit()
    return {"id": item_id, "status": "rejected"}
