"""Public, read-only intelligence surface for market-facing GNI clients.

Only already-published Item fields are exposed. Draft payloads, retry state,
fingerprints, internal errors, templates and control-plane metadata never leave
this route.
"""
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from apps.api.db import get_db_dependency
from apps.api.db.models import Item

router = APIRouter(prefix="/public", tags=["public"])

# Delivery state and public visibility are intentionally separate. An item may be
# delivered to restricted channels while remaining ineligible for this surface.
PUBLIC_SIGNAL_FILTERS = (
    Item.status == "published",
    Item.public_visible.is_(True),
)


def _safe_http_url(value: Optional[str]) -> Optional[str]:
    """Return only absolute HTTP(S) URLs; suppress internal/non-web schemes."""
    if not value:
        return None
    candidate = value.strip()
    try:
        parsed = urlparse(candidate)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _serialize_signal(item: Item) -> dict:
    """Sanitized public representation of one explicitly public intelligence item."""
    return {
        "id": item.id,
        "title": item.title,
        "summary": item.summary,
        "source_name": item.source_name,
        "source_url": _safe_http_url(item.url),
        "source_type": item.source_type,
        "risk": item.risk,
        "priority": item.priority,
        "source_published_at": _iso(item.published_at),
        "processed_at": _iso(item.updated_at),
    }


@router.get("/signals")
def list_public_signals(
    response: Response,
    session: Session = Depends(get_db_dependency),
    limit: int = Query(8, ge=1, le=20),
) -> dict:
    """Return the latest explicitly public, published intelligence signals."""
    rows = (
        session.query(Item)
        .filter(*PUBLIC_SIGNAL_FILTERS)
        .order_by(Item.updated_at.desc().nullslast(), Item.id.desc())
        .limit(limit)
        .all()
    )
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"
    items = [_serialize_signal(row) for row in rows]
    return {"items": items, "count": len(items)}
