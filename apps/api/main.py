import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.auth import require_auth
from apps.api.db import init_db
from apps.api.middleware import RateLimitMiddleware, RequestIdMiddleware, RequestSizeLimitMiddleware
from apps.api.routes.admin import router as admin_router
from apps.api.routes.auth_routes import router as auth_router
from apps.api.routes.control import router as control_router
from apps.api.routes.dlq import router as dlq_router
from apps.api.routes.health import router as health_router
from apps.api.routes.metrics import router as metrics_router
from apps.api.routes.monitoring import router as monitoring_router
from apps.api.routes.posts import router as posts_router
from apps.api.routes.review import router as review_router
from apps.api.routes.sources import router as sources_router
from apps.api.routes.wa_bridge import router as wa_bridge_router
from apps.api.routes.wa_public import wa_public_router

from apps.shared.config import ConfigError, validate_config
from apps.shared.env_validation import EnvValidationError, validate_env
from apps.shared.secrets import get_secret

_CORS_ORIGINS = [
    o.strip() for o in (get_secret("CORS_ALLOWED_ORIGINS") or "").split(",") if o.strip()
]
_streamlit_origin = (get_secret("STREAMLIT_ORIGIN") or "").strip()
if _streamlit_origin:
    _CORS_ORIGINS = [*_CORS_ORIGINS, _streamlit_origin]


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    import logging
    logger = logging.getLogger(__name__)
    try:
        validate_env(role="api")
    except (ConfigError, EnvValidationError) as e:
        logger.error("Startup env validation failed: %s", e)
        raise
    from apps.api.core.settings import get_api_settings
    settings = get_api_settings()
    # Safe one-line summary (no secrets)
    db_mask = "postgresql://***" if (settings.DATABASE_URL or "").startswith("postgresql://") else "(not set)"
    logger.info("Config loaded: DB=%s, JWT_EXPIRY_SECONDS=%s", db_mask, settings.JWT_EXPIRY_SECONDS)
    init_db()

    # Start WhatsApp QR keepalive background task (checks status, reconnect, cache QR)
    from apps.api.wa_keepalive import run_keepalive_loop
    _wa_keepalive_task = asyncio.create_task(run_keepalive_loop())

    # Desk 24H scheduler (optional, off by default)
    if os.getenv("DESK24H_ENABLED", "0") == "1":
        try:
            from desk.scheduler import start_scheduler
            start_scheduler(app)
        except Exception as e:
            logger.warning("Desk scheduler start failed: %s", e)

    yield

    # Shutdown: cancel keepalive, desk scheduler (if enabled), drain in-flight
    if os.getenv("DESK24H_ENABLED", "0") == "1":
        try:
            from desk.scheduler import shutdown_scheduler
            shutdown_scheduler()
        except Exception:
            pass
    _wa_keepalive_task.cancel()
    try:
        await _wa_keepalive_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="gni-bot-creator API", lifespan=lifespan)

app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS if _CORS_ORIGINS else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "X-API-Key", "Content-Type"],
)

app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(admin_router)
app.include_router(wa_bridge_router)
app.include_router(wa_public_router)
app.include_router(auth_router)
app.include_router(control_router, dependencies=[Depends(require_auth)])
app.include_router(dlq_router, dependencies=[Depends(require_auth)])
app.include_router(monitoring_router, dependencies=[Depends(require_auth)])
app.include_router(posts_router, dependencies=[Depends(require_auth)])
app.include_router(sources_router, dependencies=[Depends(require_auth)])
app.include_router(review_router, dependencies=[Depends(require_auth)])
