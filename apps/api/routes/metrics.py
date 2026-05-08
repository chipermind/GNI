"""Prometheus metrics endpoint."""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

from apps.api.db import SessionLocal

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
def metrics():
    """Prometheus exposition format. Exposes items_ingested_total, drafts_generated_total, publications_*, llm_latency_seconds, queue_depth."""
    try:
        from apps.observability.metrics import get_metrics, update_queue_depth
        try:
            session = SessionLocal()
            try:
                update_queue_depth(session)
            finally:
                session.close()
        except Exception:
            pass
        body = get_metrics()
        if not body:
            return Response(content=b"", media_type="text/plain")
        return Response(content=body, media_type="text/plain; charset=utf-8")
    except ImportError:
        return Response(content=b"", media_type="text/plain")
