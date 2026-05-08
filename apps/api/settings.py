"""
Load and update Settings from DB. Single row (id=1) holds flags and limits.
Feature flags: runtime toggles in feature_flags JSON.
"""
from typing import Any, Optional

from sqlalchemy.orm import Session

from apps.api.db.models import Settings


def get_settings(session: Session) -> dict[str, Any]:
    """Return current settings as dict. Uses first row or defaults."""
    row = session.query(Settings).first()
    if row is None:
        return {
            "pause_all_publish": False,
            "autopilot_enabled": False,
            "rate_limits": None,
            "feature_flags": None,
        }
    return {
        "pause_all_publish": row.pause_all_publish,
        "autopilot_enabled": row.autopilot_enabled,
        "rate_limits": row.rate_limits,
        "feature_flags": row.feature_flags,
    }


def get_feature_flag(session: Session, name: str, default: bool = False) -> bool:
    """Return feature flag value. Default False if not set."""
    settings = get_settings(session)
    flags = settings.get("feature_flags") or {}
    if not isinstance(flags, dict):
        return default
    val = flags.get(name)
    if val is None:
        return default
    return bool(val)


def set_feature_flag(session: Session, name: str, value: bool) -> None:
    """Set feature flag. Caller should commit."""
    row = session.query(Settings).first()
    if row is None:
        row = Settings()
        session.add(row)
        session.flush()
    flags = dict(row.feature_flags) if row.feature_flags else {}
    flags[name] = value
    row.feature_flags = flags
    session.flush()


def set_settings(
    session: Session,
    *,
    pause_all_publish: Optional[bool] = None,
    autopilot_enabled: Optional[bool] = None,
    rate_limits: Optional[dict] = None,
) -> dict[str, Any]:
    """Update one or more settings. Gets or creates first row. Caller should commit."""
    row = session.query(Settings).first()
    if row is None:
        row = Settings()
        session.add(row)
        session.flush()
    if pause_all_publish is not None:
        row.pause_all_publish = pause_all_publish
    if autopilot_enabled is not None:
        row.autopilot_enabled = autopilot_enabled
    if rate_limits is not None:
        row.rate_limits = rate_limits
    session.flush()
    return get_settings(session)
