"""
JWT + API key authentication for control/admin endpoints.
When neither JWT_SECRET nor API_KEY is set, auth is disabled (backward compat).
Uses API settings (Pydantic); JWT_EXPIRY_SECONDS is always int, never raw string.
"""
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from apps.api.core.settings import get_api_settings

try:
    import jwt
except ImportError:
    jwt = None

_api_settings = get_api_settings()
JWT_SECRET = _api_settings.JWT_SECRET
API_KEY = _api_settings.API_KEY
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS: int = _api_settings.JWT_EXPIRY_SECONDS  # always int (86400 default)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
http_bearer = HTTPBearer(auto_error=False)


def auth_required() -> bool:
    """True if any auth is configured (JWT or API key)."""
    return bool(JWT_SECRET or API_KEY)


def _verify_api_key(key: Optional[str]) -> bool:
    if not API_KEY or not key:
        return False
    return key.strip() == API_KEY


def _verify_jwt(token: str) -> bool:
    if not JWT_SECRET or not jwt:
        return False
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except Exception:
        return False


async def require_auth(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
    credentials: Optional[HTTPAuthorizationCredentials] = Security(http_bearer),
) -> None:
    """
    Dependency: require valid API key or JWT Bearer token for control endpoints.
    When auth is disabled (no JWT_SECRET, no API_KEY), passes without check.
    """
    if not auth_required():
        return
    if api_key and _verify_api_key(api_key):
        return
    if credentials and credentials.credentials and _verify_jwt(credentials.credentials):
        return
    raise HTTPException(
        status_code=401,
        detail="Unauthorized: missing or invalid X-API-Key header or Bearer token"
    )


def create_token(subject: str = "api") -> str:
    """Create JWT for API key holder (e.g. for UI use). Requires JWT_SECRET and PyJWT."""
    if not JWT_SECRET or not jwt:
        raise ValueError("JWT_SECRET required and PyJWT must be installed")
    import time
    payload = {"sub": subject, "exp": int(time.time()) + JWT_EXPIRY_SECONDS}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
