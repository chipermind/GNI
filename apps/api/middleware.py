"""
API middleware: request_id/correlation_id, rate limiting per IP/token, request size limit.
Uses secrets provider for REDIS_URL.
"""
import os
import uuid
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from apps.api.settings_utils import env_int
from apps.shared.secrets import get_secret

# Paths that skip rate limiting (health, metrics)
_SKIP_RATE_LIMIT_PATHS = frozenset(
    ("/health", "/health/live", "/health/ready", "/health/detailed", "/metrics", "")
)


def _get_redis():
    try:
        import redis
        from apps.shared.config import REDIS_URL_DEFAULT
        url = get_secret("REDIS_URL", REDIS_URL_DEFAULT)
        return redis.Redis.from_url(url)
    except Exception:
        return None


API_RATE_LIMIT_PER_MINUTE = env_int("API_RATE_LIMIT_PER_MINUTE", default=60)
API_RATE_LIMIT_PER_HOUR = env_int("API_RATE_LIMIT_PER_HOUR", default=1000)
API_MAX_BODY_SIZE = env_int("API_MAX_BODY_SIZE", default=65536)  # 64KB default


def _client_identifier(request: Request) -> str:
    """IP or X-Forwarded-For, or auth token hash for per-token limiting."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"
    api_key = request.headers.get("X-API-Key")
    if api_key:
        import hashlib
        token_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
        return f"token:{token_hash}"
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        import hashlib
        token_hash = hashlib.sha256(auth.encode()).hexdigest()[:16]
        return f"token:{token_hash}"
    return f"ip:{ip}"


def _minute_key(identifier: str) -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return f"api:rate:{identifier}:min:{now.strftime('%Y-%m-%d-%H-%M')}"


def _hour_key(identifier: str) -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return f"api:rate:{identifier}:hr:{now.strftime('%Y-%m-%d-%H')}"


def _should_skip_rate_limit(path: str) -> bool:
    """Skip rate limit for health and metrics (Docker, load balancers, Prometheus)."""
    return path.rstrip("/") in _SKIP_RATE_LIMIT_PATHS


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Set request_id/correlation_id and bind to structured logging context. Logs one line per request when structured logging is used."""

    async def dispatch(self, request: Request, call_next):
        request_id = (
            request.headers.get("X-Request-ID")
            or request.headers.get("X-Correlation-ID")
            or str(uuid.uuid4())
        )
        request.state.request_id = request_id
        request.state.correlation_id = request_id
        try:
            try:
                import structlog
                structlog.contextvars.bind_contextvars(
                    request_id=request_id,
                    correlation_id=request_id,
                )
            except ImportError:
                pass
            response = await call_next(request)
            if hasattr(response, "headers"):
                response.headers["X-Request-ID"] = request_id
            try:
                from apps.observability.logging import get_logger
                get_logger("api.request").info(
                    "request",
                    method=request.method,
                    path=request.url.path,
                    status_code=getattr(response, "status_code", None),
                )
            except Exception:
                pass
            return response
        finally:
            try:
                import structlog
                structlog.contextvars.clear_contextvars()
            except ImportError:
                pass


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-based rate limit per IP or token."""

    async def dispatch(self, request: Request, call_next):
        if _should_skip_rate_limit(request.url.path):
            return await call_next(request)
        r = _get_redis()
        if not r:
            return await call_next(request)
        ident = _client_identifier(request)
        mk = _minute_key(ident)
        hk = _hour_key(ident)
        try:
            min_count = int(r.get(mk) or 0)
            hour_count = int(r.get(hk) or 0)
            if min_count >= API_RATE_LIMIT_PER_MINUTE:
                return Response(
                    content='{"detail":"Rate limit exceeded (per minute)"}',
                    status_code=429,
                    media_type="application/json",
                )
            if hour_count >= API_RATE_LIMIT_PER_HOUR:
                return Response(
                    content='{"detail":"Rate limit exceeded (per hour)"}',
                    status_code=429,
                    media_type="application/json",
                )
            pipe = r.pipeline()
            pipe.incr(mk)
            pipe.expire(mk, 120)
            pipe.incr(hk)
            pipe.expire(hk, 7200)
            pipe.execute()
        except Exception:
            pass
        return await call_next(request)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject request bodies exceeding API_MAX_BODY_SIZE."""

    async def dispatch(self, request: Request, call_next):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        content_length = request.headers.get("Content-Length")
        if content_length:
            try:
                size = int(content_length)
                if size > API_MAX_BODY_SIZE:
                    return Response(
                        content='{"detail":"Request body too large"}',
                        status_code=413,
                        media_type="application/json",
                    )
            except ValueError:
                pass
        return await call_next(request)
