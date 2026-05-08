"""
Settings utilities: safe environment variable parsing.
Never crashes on startup due to bad env values.
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def env_int(name: str, default: int) -> int:
    """
    Get integer from environment variable. Never crashes on startup.
    
    Args:
        name: Environment variable name
        default: Default value if missing/empty/invalid
    
    Returns:
        Parsed integer value, or default if missing/empty/invalid
    
    Behavior:
        - Missing env var -> return default (no warning)
        - Empty string -> log warning and return default
        - Whitespace-only -> log warning and return default
        - Invalid int (e.g., "abc") -> log warning and return default
        - Valid integer -> parsed value
    
    This function is production-safe and will never raise exceptions.
    All warnings include variable name and the provided value for debugging.
    """
    raw = os.getenv(name)
    
    # Missing env var -> return default (no warning, expected behavior)
    if raw is None:
        return default
    
    # Empty string or whitespace-only -> log warning and return default
    if not raw.strip():
        logger.warning(
            "Environment variable %s is empty or whitespace-only (value: '%s'), using default: %d",
            name,
            repr(raw),  # Shows empty string as '' or whitespace as '   '
            default,
        )
        return default
    
    s = raw.strip()
    
    # Try to parse as integer
    try:
        n = int(s)
        return n
    except ValueError:
        # Invalid int -> log warning and return default (never crash)
        logger.warning(
            "Environment variable %s has invalid integer value '%s' (raw: '%s'), using default: %d",
            name,
            s,
            raw,  # Include original raw value for debugging
            default,
        )
        return default


def env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get string from environment variable. Strips whitespace.
    
    Args:
        name: Environment variable name
        default: Default value if missing/empty (default: None)
    
    Returns:
        Stripped string value, or default if missing/empty
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    s = raw.strip()
    return s if s else default
