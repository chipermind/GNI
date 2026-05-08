"""
Startup configuration validation. Fails fast on bad config.
VM-first: defaults use Docker service DNS (postgres, redis, ollama, whatsapp-bot).
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .secrets import get_secret

# VM-first defaults: use service hostnames for in-container inter-service calls
DATABASE_URL_DEFAULT = "postgresql://gni:gni@postgres:5432/gni"
REDIS_URL_DEFAULT = "redis://redis:6379/0"
OLLAMA_BASE_URL_DEFAULT = "http://ollama:11434"
WHATSAPP_BOT_BASE_URL_DEFAULT = "http://whatsapp-bot:3100"


class ConfigError(Exception):
    """Raised when startup config is invalid."""

    def __init__(self, message: str, key: Optional[str] = None):
        self.key = key
        super().__init__(message)


def _in_docker() -> bool:
    """True if process is running inside a Docker container."""
    try:
        return os.path.exists("/.dockerenv")
    except Exception:
        return False


def _url_contains_localhost(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    u = url.lower().strip()
    return "localhost" in u or "127.0.0.1" in u


def _fail_if_localhost_in_docker() -> None:
    """
    If running in Docker, require that critical URLs use service DNS, not localhost.
    Raises ConfigError on violation (fail fast).
    """
    if not _in_docker():
        return
    # Only check URLs that are set (WHATSAPP_BOT_BASE_URL is optional)
    db_url = get_secret("DATABASE_URL", DATABASE_URL_DEFAULT)
    redis_url = get_secret("REDIS_URL", REDIS_URL_DEFAULT)
    ollama_url = get_secret("OLLAMA_BASE_URL", OLLAMA_BASE_URL_DEFAULT)
    wa_url = (get_secret("WHATSAPP_BOT_BASE_URL", "") or "").strip()
    checks = [
        ("DATABASE_URL", db_url),
        ("REDIS_URL", redis_url),
        ("OLLAMA_BASE_URL", ollama_url),
        ("WHATSAPP_BOT_BASE_URL", wa_url or None),
    ]
    for key, value in checks:
        if value and _url_contains_localhost(value):
            raise ConfigError(
                f"{key} must not contain localhost/127.0.0.1 when running in Docker. "
                "Use service DNS: postgres:5432, redis:6379, ollama:11434, whatsapp-bot:3100",
                key=key,
            )


def _valid_postgres_url(url: str) -> bool:
    if not url or len(url) < 10:
        return False
    return bool(re.match(r"^postgres(?:ql)?(\+[^/]+)?://[^/]+/\w+", url))


def _valid_redis_url(url: str) -> bool:
    if not url or len(url) < 5:
        return False
    return url.startswith("redis://")


def validate_config(required: bool = True) -> None:
    """
    Validate startup config. Raises ConfigError on invalid config.
    When required=True (default), DATABASE_URL and REDIS_URL must be present and valid.
    When running in Docker, any critical URL containing localhost/127.0.0.1 causes immediate exit.
    """
    db_url = get_secret("DATABASE_URL", DATABASE_URL_DEFAULT)
    redis_url = get_secret("REDIS_URL", REDIS_URL_DEFAULT)
    if required:
        if not db_url:
            raise ConfigError("DATABASE_URL is required", "DATABASE_URL")
        if not _valid_postgres_url(db_url):
            raise ConfigError("DATABASE_URL must be a valid postgresql:// URL", "DATABASE_URL")
        if not redis_url:
            raise ConfigError("REDIS_URL is required", "REDIS_URL")
        if not _valid_redis_url(redis_url):
            raise ConfigError("REDIS_URL must be a valid redis:// URL", "REDIS_URL")
    _fail_if_localhost_in_docker()
