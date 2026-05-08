from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.db import check_db, get_db_dependency
from apps.api.db.models import Draft, EventsLog, Item, Publication
from apps.api.schemas import (
    DependencyStatus,
    FailureEventOut,
    PauseResponse,
    PublicationOut,
    ResumeResponse,
    StatusResponse,
    StatusStats,
)
from apps.api.settings import get_feature_flag, get_settings, set_feature_flag, set_settings

router = APIRouter(prefix="/control", tags=["control"])


def _check_redis() -> bool:
    try:
        import redis
        from apps.shared.config import REDIS_URL_DEFAULT
        from apps.shared.secrets import get_secret
        url = get_secret("REDIS_URL", REDIS_URL_DEFAULT)
        r = redis.Redis.from_url(url)
        r.ping()
        return True
    except Exception:
        return False


def _check_ollama() -> bool:
    try:
        import urllib.request
        from apps.shared.config import OLLAMA_BASE_URL_DEFAULT
        from apps.shared.secrets import get_secret
        url = get_secret("OLLAMA_BASE_URL", OLLAMA_BASE_URL_DEFAULT)
        req = urllib.request.Request(f"{url.rstrip('/')}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


@router.post("/pause", response_model=PauseResponse)
def pause(session: Session = Depends(get_db_dependency)):
    """Set pause_all_publish=true in Settings."""
    set_settings(session, pause_all_publish=True)
    session.commit()
    return PauseResponse(paused=True)


@router.post("/resume", response_model=ResumeResponse)
def resume(session: Session = Depends(get_db_dependency)):
    """Set pause_all_publish=false in Settings."""
    set_settings(session, pause_all_publish=False)
    session.commit()
    return ResumeResponse(paused=False)


@router.get("/features")
def get_features(session: Session = Depends(get_db_dependency)):
    """Return current feature flags (runtime toggles)."""
    settings = get_settings(session)
    return {"feature_flags": settings.get("feature_flags") or {}}


@router.post("/features/{name}")
def set_feature(name: str, enabled: bool = True, session: Session = Depends(get_db_dependency)):
    """Set feature flag. Query param: enabled=true|false."""
    set_feature_flag(session, name, enabled)
    session.commit()
    return {"name": name, "enabled": enabled}


@router.get("/status", response_model=StatusResponse)
def status(session: Session = Depends(get_db_dependency)):
    """Return settings, pipeline counters, dependency status, last failures."""
    settings = get_settings(session)
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)

    # Pipeline counters (last hour)
    items_last_hour = session.query(Item).filter(Item.created_at >= hour_ago).count()
    drafts_last_hour = session.query(Draft).filter(Draft.created_at >= hour_ago).count()
    pubs_q = session.query(Publication).filter(Publication.created_at >= hour_ago)
    publications_last_hour = pubs_q.count()

    # Stats
    failed_items = session.query(Item).filter(Item.status == "failed").count()
    failed_publications = (
        session.query(Publication)
        .filter(Publication.status.in_(["failed", "dead_letter"]))
        .count()
    )
    publish_blocked_count = (
        session.query(EventsLog).filter(EventsLog.event_type == "publish_blocked").count()
    )
    publications_sent = (
        session.query(Publication).filter(Publication.status == "sent").count()
    )

    # Last 10 failures from events_log
    failure_types = ("make_publish_failure", "make_dead_letter", "rate_limit_exceeded")
    failures_q = (
        session.query(EventsLog)
        .filter(EventsLog.event_type.in_(failure_types))
        .order_by(EventsLog.id.desc())
        .limit(10)
    )
    last_failures = [
        FailureEventOut(
            id=e.id,
            event_type=e.event_type,
            payload=e.payload,
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e in failures_q.all()
    ]

    # Last 10 publications
    last_pubs = (
        session.query(Publication)
        .order_by(Publication.id.desc())
        .limit(10)
        .all()
    )
    publications = [
        PublicationOut(
            id=p.id,
            channel=p.channel,
            status=p.status,
            external_id=p.external_id,
            created_at=p.created_at.isoformat() if p.created_at else None,
        )
        for p in last_pubs
    ]

    # Dependency status
    deps = DependencyStatus(
        db="reachable" if check_db() else "unreachable",
        redis="reachable" if _check_redis() else "unreachable",
        ollama="reachable" if _check_ollama() else "unreachable",
    )

    return StatusResponse(
        settings=settings,
        stats=StatusStats(
            failed_items=failed_items,
            failed_publications=failed_publications,
            publish_blocked_count=publish_blocked_count,
            publications_sent=publications_sent,
            items_last_hour=items_last_hour,
            drafts_last_hour=drafts_last_hour,
            publications_last_hour=publications_last_hour,
        ),
        dependencies=deps,
        last_failures=last_failures,
        last_publications=publications,
    )
