"""
API env config via Pydantic Settings. Treats empty string as missing -> use default.
Used at startup; validators ensure ints never get raw empty strings.
"""
from __future__ import annotations

from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from apps.shared.config import DATABASE_URL_DEFAULT, REDIS_URL_DEFAULT
from apps.shared.secrets import get_secret

JWT_EXPIRY_MIN = 1
JWT_EXPIRY_MAX = 604800  # 7 days
JWT_EXPIRY_DEFAULT = 86400  # 24h


def _get(key: str, default: str = "") -> str:
    """Env lookup: empty string is treated as missing (get_secret already does this)."""
    return get_secret(key, default) or ""


class ApiSettings(BaseSettings):
    """API configuration from env. Empty values fall back to defaults; invalid int raises."""

    model_config = SettingsConfigDict(extra="ignore")

    DATABASE_URL: str = DATABASE_URL_DEFAULT
    REDIS_URL: str = REDIS_URL_DEFAULT
    JWT_SECRET: str = ""
    JWT_EXPIRY_SECONDS: int = JWT_EXPIRY_DEFAULT
    API_KEY: str = ""

    @field_validator("JWT_EXPIRY_SECONDS", mode="before")
    @classmethod
    def _coerce_jwt_expiry(cls, v: object) -> int:
        if v is None:
            return JWT_EXPIRY_DEFAULT
        if isinstance(v, int):
            return max(JWT_EXPIRY_MIN, min(v, JWT_EXPIRY_MAX)) if v else JWT_EXPIRY_DEFAULT
        s = (v or "").strip()
        if not s:
            return JWT_EXPIRY_DEFAULT
        try:
            n = int(s)
            return max(JWT_EXPIRY_MIN, min(n, JWT_EXPIRY_MAX)) if n else JWT_EXPIRY_DEFAULT
        except ValueError:
            raise ValueError(
                "JWT_EXPIRY_SECONDS must be an integer between 1 and 604800 (e.g. 86400 for 24h). "
                "Fix or remove the env var to use default 86400."
            )

    @classmethod
    def from_env(cls) -> "ApiSettings":
        """Build from current env. Empty JWT_EXPIRY_SECONDS -> default; non-numeric -> clear error."""
        return cls(
            DATABASE_URL=_get("DATABASE_URL", DATABASE_URL_DEFAULT),
            REDIS_URL=_get("REDIS_URL", REDIS_URL_DEFAULT),
            JWT_SECRET=_get("JWT_SECRET"),
            JWT_EXPIRY_SECONDS=_get("JWT_EXPIRY_SECONDS", "86400"),  # validator: empty->default, invalid->raise
            API_KEY=_get("API_KEY") or _get("ADMIN_API_KEY"),
        )


_settings: Optional[ApiSettings] = None


def get_api_settings() -> ApiSettings:
    """Return cached API settings. Call once at startup after env is loaded."""
    global _settings
    if _settings is None:
        _settings = ApiSettings.from_env()
    return _settings
