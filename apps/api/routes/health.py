from fastapi import APIRouter
from fastapi.responses import JSONResponse

from apps.api.db import check_db

router = APIRouter(tags=["health"])


def _check_redis() -> bool:
    """Return True if Redis is reachable."""
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
    """Return True if Ollama is reachable."""
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


@router.get("/health/live")
def liveness():
    """Liveness probe: process is running. Always 200. Safe for restarts."""
    return {"status": "ok"}


@router.get("/health/ready")
def readiness():
    """Readiness: DB, Redis, Ollama must be reachable. 503 (status=fail) if any critical dependency down."""
    db_ok = check_db()
    redis_ok = _check_redis()
    ollama_ok = _check_ollama()
    ready = db_ok and redis_ok and ollama_ok
    if not ready:
        return JSONResponse(
            status_code=503,
            content={
                "status": "fail",
                "db": "ok" if db_ok else "unreachable",
                "redis": "ok" if redis_ok else "unreachable",
                "ollama": "ok" if ollama_ok else "unreachable",
            },
        )
    return {"status": "ok", "db": "ok", "redis": "ok", "ollama": "ok"}


@router.get("/health")
def health():
    """Legacy: same as readiness. Returns 200 with degraded status when ollama down (so Monitoring UI stays reachable)."""
    db_ok = check_db()
    redis_ok = _check_redis()
    ollama_ok = _check_ollama()
    content = {
        "status": "ok" if (db_ok and redis_ok and ollama_ok) else "degraded",
        "db": "ok" if db_ok else "unreachable",
        "redis": "ok" if redis_ok else "unreachable",
        "ollama": "ok" if ollama_ok else "unreachable",
    }
    # 503 only when db or redis down; 200 when ollama down (degraded)
    if not db_ok or not redis_ok:
        return JSONResponse(status_code=503, content={**content, "status": "fail"})
    return content


@router.get("/health/detailed")
def health_detailed():
    """Extended health: DB, Redis, Ollama reachability."""
    db_ok = check_db()
    redis_ok = _check_redis()
    ollama_ok = _check_ollama()
    return {
        "status": "ok" if db_ok and redis_ok else "degraded",
        "db": "ok" if db_ok else "unreachable",
        "redis": "ok" if redis_ok else "unreachable",
        "ollama": "ok" if ollama_ok else "unreachable",
    }
