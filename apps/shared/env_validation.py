"""
Production env validation: fail startup if required env vars are missing.
Conditional requirements: Telegram, Make webhook, QR bridge only when enabled.
"""
from __future__ import annotations

import os
import sys
from typing import List

from apps.shared.config import (
    ConfigError,
    OLLAMA_BASE_URL_DEFAULT,
    validate_config,
)
from apps.shared.secrets import get_secret


class EnvValidationError(Exception):
    """Raised when required env vars are missing or invalid."""

    def __init__(self, message: str, missing: List[str] | None = None):
        self.missing = missing or []
        super().__init__(message)


def _get(key: str, default: str = "") -> str:
    return (get_secret(key, default) or "").strip()


def _telegram_enabled() -> bool:
    token = _get("TELEGRAM_BOT_TOKEN")
    chat = _get("TELEGRAM_TARGET_CHAT_ID") or _get("TELEGRAM_CHAT_ID")
    return bool(token or chat)


def _make_webhook_enabled() -> bool:
    return bool(_get("MAKE_WEBHOOK_URL"))


def _qr_bridge_enabled() -> bool:
    """QR bridge is enabled if Streamlit origin is set (remote UI needs the bridge)."""
    return bool(_get("STREAMLIT_ORIGIN"))


def validate_env(role: str = "all") -> None:
    """
    Validate required env vars for the given role. Raises EnvValidationError or ConfigError on failure.
    role: "api" | "worker" | "all"
    - api: DB, Redis, OLLAMA_BASE_URL; if STREAMLIT_ORIGIN set then WA_QR_BRIDGE_TOKEN required.
    - worker: DB, Redis, OLLAMA_BASE_URL; if telegram enabled then TELEGRAM_BOT_TOKEN + target chat;
              if MAKE_WEBHOOK_URL set then non-empty; no QR bridge checks.
    """
    try:
        validate_config(required=True)
    except ConfigError:
        raise

    errors: List[str] = []
    missing: List[str] = []

    # OLLAMA_BASE_URL must be present (default is set in config; just ensure no empty override)
    ollama = _get("OLLAMA_BASE_URL", OLLAMA_BASE_URL_DEFAULT)
    if not ollama:
        missing.append("OLLAMA_BASE_URL")
        errors.append("OLLAMA_BASE_URL is required")

    if role in ("api", "all"):
        # Optional JWT_EXPIRY_SECONDS: if set and non-empty, must be valid int (1–604800)
        raw_expiry = _get("JWT_EXPIRY_SECONDS", "86400")
        if raw_expiry:
            try:
                n = int(raw_expiry)
                if n < 1 or n > 604800:
                    errors.append("JWT_EXPIRY_SECONDS must be between 1 and 604800")
                    missing.append("JWT_EXPIRY_SECONDS")
            except ValueError:
                errors.append("JWT_EXPIRY_SECONDS must be an integer")
                missing.append("JWT_EXPIRY_SECONDS")
        if _qr_bridge_enabled():
            token = _get("WA_QR_BRIDGE_TOKEN")
            if not token or token == "CHANGE_ME_LONG_RANDOM":
                missing.append("WA_QR_BRIDGE_TOKEN")
                errors.append(
                    "STREAMLIT_ORIGIN is set; WA_QR_BRIDGE_TOKEN must be set to a long random secret"
                )

    if role in ("worker", "all"):
        if _telegram_enabled():
            token = _get("TELEGRAM_BOT_TOKEN")
            chat = _get("TELEGRAM_TARGET_CHAT_ID") or _get("TELEGRAM_CHAT_ID")
            if not token:
                missing.append("TELEGRAM_BOT_TOKEN")
                errors.append("Telegram is enabled (TELEGRAM_TARGET_CHAT_ID or TELEGRAM_CHAT_ID set) but TELEGRAM_BOT_TOKEN is missing")
            elif not chat:
                missing.append("TELEGRAM_TARGET_CHAT_ID or TELEGRAM_CHAT_ID")
                errors.append("Telegram is enabled (TELEGRAM_BOT_TOKEN set) but TELEGRAM_TARGET_CHAT_ID or TELEGRAM_CHAT_ID is missing")
        if _make_webhook_enabled():
            url = _get("MAKE_WEBHOOK_URL")
            if not url:
                missing.append("MAKE_WEBHOOK_URL")
                errors.append("MAKE_WEBHOOK_URL is set but empty")

    if errors:
        msg = "Env validation failed: " + "; ".join(errors)
        raise EnvValidationError(msg, missing=missing)


def main() -> int:
    """CLI: validate env for role (default: all). Load .env via env_file; call from scripts."""
    role = "all"
    if len(sys.argv) > 1:
        role = sys.argv[1].lower()
        if role not in ("api", "worker", "all"):
            print(f"Usage: {sys.argv[0]} [api|worker|all]", file=sys.stderr)
            return 2
    try:
        validate_env(role=role)
        print("OK: env validation passed")
        return 0
    except EnvValidationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if e.missing:
            print(f"Missing or invalid: {', '.join(e.missing)}", file=sys.stderr)
        return 1
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
