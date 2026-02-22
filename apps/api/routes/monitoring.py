"""
Monitoring: scraping/job status and recent items for Streamlit Monitoring page.
GET /monitoring — status + recent items (RSS/collector pipeline).
POST /monitoring/run — trigger one collector ingest run (optional; VM runs 24/7).
Auth: X-API-Key or Bearer JWT (same as control/review).
"""
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.auth import require_auth
from apps.api.db import check_db, get_db_dependency
from apps.api.db.models import Item

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


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


@router.get("")
def get_monitoring(
    tenant: Optional[str] = None,
    limit: int = 20,
    session: Session = Depends(get_db_dependency),
    _auth=Depends(require_auth),
) -> dict[str, Any]:
    """
    Status and recent pipeline jobs. Tenant is optional (reserved for multi-tenant).
    Returns: status, db, redis, ollama, items_last_24h, recent (list of {id, status, created_at, source_name}).
    """
    db_ok = check_db()
    redis_ok = _check_redis()
    ollama_ok = _check_ollama()
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    items_last_24h = session.query(Item).filter(Item.created_at >= day_ago).count()

    recent_q = (
        session.query(Item)
        .order_by(Item.id.desc())
        .limit(max(1, min(limit, 100)))
    )
    recent = [
        {
            "id": i.id,
            "status": i.status or "—",
            "source_type": i.source_type or "rss",
            "created_at": i.created_at.isoformat() if i.created_at else None,
            "updated_at": i.updated_at.isoformat() if i.updated_at else None,
            "source_name": i.source_name or "—",
        }
        for i in recent_q.all()
    ]

    return {
        "status": "ok",
        "db": "ok" if db_ok else "unreachable",
        "redis": "ok" if redis_ok else "unreachable",
        "ollama": "ok" if ollama_ok else "unreachable",
        "items_last_24h": items_last_24h,
        "recent": recent,
    }


@router.post("/run")
def post_monitoring_run(
    tenant: Optional[str] = None,
    _auth=Depends(require_auth),
) -> dict[str, Any]:
    """
    Trigger one collector ingest run (RSS + optional Telegram). Runs in background; returns immediately.
    On VM, collector and worker already run 24/7 via docker compose (COLLECTOR_INTERVAL_MINUTES / RUN_EVERY_MINUTES).
    """
    def _run_once() -> None:
        try:
            from apps.api.db import init_db
            from apps.collector.rss import run as run_rss_ingest
            from apps.collector.telegram_ingest import run as run_telegram_ingest
            init_db()
            run_rss_ingest(limit=50)
            run_telegram_ingest(since_minutes=60)
        except Exception:
            pass

    t = threading.Thread(target=_run_once, daemon=True)
    t.start()
    return {"status": "accepted", "message": "Collector run triggered. Data will appear in recent jobs shortly."}
