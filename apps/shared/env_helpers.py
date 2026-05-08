"""Shared helpers for env parsing: treat empty as missing, safe int parsing."""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_int_env(name: str, default: int) -> int:
    """
    Get integer from environment variable with safe parsing. Never crashes on startup.
    
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
    raw = os.environ.get(name)
    
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


def parse_int(
    raw: str,
    default: int,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
    name: Optional[str] = None,
    raise_on_invalid: bool = False,
) -> int:
    """
    Parse int from environment variable value.
    
    Args:
        raw: Raw string value from env (can be empty/whitespace)
        default: Default value if missing/empty/invalid
        min_val: Optional minimum (clamps or errors if below)
        max_val: Optional maximum (clamps or errors if above)
        name: Variable name (for logging, optional)
        raise_on_invalid: If True, raise ConfigError on invalid value; if False, use default + warn
    
    Returns:
        Parsed int value, or default if missing/empty/invalid
    
    Behavior:
        - None/empty/whitespace -> default (no warning)
        - Invalid string (e.g., "abc") -> default + warning (or ConfigError if raise_on_invalid=True)
        - Valid int -> parsed value
        - If min_val/max_val set and value out of range -> clamp to range + warning
        - Zero/negative values are valid unless min_val > 0
    """
    from apps.shared.config import ConfigError
    
    if not raw:
        return default
    
    s = raw.strip() if isinstance(raw, str) else str(raw).strip()
    
    # Empty/whitespace -> default (silent, expected)
    if not s:
        return default
    
    var_name = name or "env_var"
    
    # Try to parse
    try:
        n = int(s)
    except ValueError:
        if raise_on_invalid:
            raise ConfigError(f"Invalid integer value for {var_name}: '{s}'", key=var_name)
        logger.warning("Invalid integer value for %s: '%s' (using default: %d)", var_name, s, default)
        return default
    
    # Range validation
    if min_val is not None and n < min_val:
        if raise_on_invalid:
            raise ConfigError(f"Value for {var_name} ({n}) below minimum ({min_val})", key=var_name)
        logger.warning("Value for %s (%d) below minimum (%d), clamping to %d", var_name, n, min_val, min_val)
        n = min_val
    
    if max_val is not None and n > max_val:
        if raise_on_invalid:
            raise ConfigError(f"Value for {var_name} ({n}) above maximum ({max_val})", key=var_name)
        logger.warning("Value for %s (%d) above maximum (%d), clamping to %d", var_name, n, max_val, max_val)
        n = max_val
    
    return n


def parse_int_default(raw: str, default: int, min_val: int, max_val: int) -> int:
    """
    Legacy function for backward compatibility.
    Parse int from string; empty or invalid -> default; clamp to [min_val, max_val].
    """
    s = (raw or "").strip()
    if not s:
        return default
    try:
        n = int(s)
        # Clamp to range
        if n < min_val:
            return min_val
        if n > max_val:
            return max_val
        return n
    except ValueError:
        return default
