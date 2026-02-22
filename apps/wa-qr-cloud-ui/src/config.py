"""
Config: optional API base URL from secrets/env. No required secrets; app never blocks.
GNI_API_BASE_URL can be set in Streamlit Cloud Secrets or env; if empty, user pastes URL in UI (st.session_state).
"""
import os
from typing import Any

import streamlit as st

OPTIONAL_KEYS = (
    "SEED_CLIENT_EMAIL",
    "SEED_CLIENT_PASSWORD",
    "SEED_CLIENT_ROLE",
    "WA_QR_BRIDGE_TOKEN",
    "WA_API_PREFIX",
    "API_KEY",
    "ADMIN_API_KEY",
    "AUTO_REFRESH_SECONDS",
)


def _get(key: str, default: str = "") -> str:
    """Read from st.secrets first, then os.environ. Stripped string."""
    val = ""
    try:
        if hasattr(st, "secrets") and st.secrets is not None:
            val = st.secrets.get(key, "") or ""
    except (TypeError, KeyError, AttributeError):
        pass
    if not (val and str(val).strip()):
        val = os.environ.get(key, default) or ""
    return str(val).strip()


def has_seed_for_legacy() -> bool:
    """True if SEED_CLIENT_EMAIL and SEED_CLIENT_PASSWORD are set (legacy in-app login fallback)."""
    c = get_config()
    return bool((c.get("SEED_CLIENT_EMAIL") or "").strip() and (c.get("SEED_CLIENT_PASSWORD") or "").strip())


def get_config() -> dict[str, Any]:
    """Return full config dict. GNI_API_BASE_URL optional (default http://api:8000)."""
    base_url = _get("GNI_API_BASE_URL", "http://api:8000").rstrip("/")
    # WA Connect UI uses /wa/* with X-API-Key only (WA_API_PREFIX kept for reference)
    wa_prefix = "/wa"
    token = _get("WA_QR_BRIDGE_TOKEN")
    seed_email = _get("SEED_CLIENT_EMAIL")
    seed_password = _get("SEED_CLIENT_PASSWORD")
    seed_role = _get("SEED_CLIENT_ROLE") or "client"
    api_key = _get("API_KEY") or _get("ADMIN_API_KEY")
    try:
        auto_refresh = int(_get("AUTO_REFRESH_SECONDS") or "3")
    except ValueError:
        auto_refresh = 3
    return {
        "GNI_API_BASE_URL": base_url,
        "WA_API_PREFIX": wa_prefix,
        "WA_QR_BRIDGE_TOKEN": token,
        "SEED_CLIENT_EMAIL": seed_email,
        "SEED_CLIENT_PASSWORD": seed_password,
        "SEED_CLIENT_ROLE": seed_role.strip().lower(),
        "API_KEY": api_key,
        "AUTO_REFRESH_SECONDS": auto_refresh,
    }


def validate_config() -> None:
    """No-op. No required config; app never blocks. User can paste Backend URL in UI."""
    pass
