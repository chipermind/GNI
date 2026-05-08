"""Posts: drafted (pending) and published items for Streamlit Posts page."""
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from apps.api.db import get_db_dependency
from apps.api.db.models import Draft, Item

router = APIRouter(prefix="/posts", tags=["posts"])


def _render_preview(template: str, payload: dict, source_name: str) -> str:
    """Render draft payload for preview. Returns formatted text."""
    try:
        from apps.worker.render import render
        sector = (source_name or "").strip() or "Setor"
        messages = render(template=template or "DEFAULT", payload=payload, sector=sector, flag="")
        return "\n---\n".join(messages) if messages else ""
    except Exception:
        return ""


def _latest_draft_subq(session: Session):
    """Subquery: (item_id, max_id) for latest draft per item."""
    return (
        session.query(Draft.item_id, func.max(Draft.id).label("max_id"))
        .filter(Draft.item_id.isnot(None))
        .group_by(Draft.item_id)
    ).subquery()


@router.get("")
def list_posts(
    session: Session = Depends(get_db_dependency),
    status: str = Query("pending", description="pending | published"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List posts. status=pending: drafted items (needs_review or all). status=published: items with status=published."""
    if status == "pending":
        rows = (
            session.query(Item)
            .filter(Item.status == "drafted")
            .order_by(Item.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    else:
        rows = (
            session.query(Item)
            .filter(Item.status == "published")
            .order_by(Item.updated_at.desc().nullslast(), Item.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    item_ids = [r.id for r in rows]
    draft_map: dict[int, Draft] = {}
    if item_ids:
        subq = _latest_draft_subq(session)
        drafts = (
            session.query(Draft)
            .join(subq, (Draft.item_id == subq.c.item_id) & (Draft.id == subq.c.max_id))
            .filter(Draft.item_id.in_(item_ids))
            .all()
        )
        draft_map = {d.item_id: d for d in drafts if d.item_id}
    out = []
    for r in rows:
        d = draft_map.get(r.id)
        payload = (d.data if d else {}) or {}
        rendered = (d.rendered_text if d and d.rendered_text else None) or _render_preview(
            r.template or "DEFAULT", payload, r.source_name or ""
        )
        out.append({
            "id": r.id,
            "title": r.title,
            "summary": r.summary,
            "source_name": r.source_name,
            "status": r.status,
            "needs_review": r.needs_review,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "rendered_text": rendered,
            "draft_payload": payload,
        })
    return {"items": out, "total": total, "limit": limit, "offset": offset}
