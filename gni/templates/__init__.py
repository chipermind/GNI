"""
GNI editorial template loader. Maps format_mode to contract .md files.
Used by the generator to enforce fixed output structure (anti-drift).
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

_THIS_DIR = Path(__file__).resolve().parent

# Format modes supported by the generator. Default when format_mode is not provided.
FORMAT_MODE_BRIEFING_LONG: Final[str] = "BRIEFING_LONG"
FORMAT_MODE_RADAR_SHORT: Final[str] = "RADAR_SHORT"
FORMAT_MODE_FLASH_BREAKING: Final[str] = "FLASH_BREAKING"

VALID_FORMAT_MODES: Final[tuple[str, ...]] = (
    FORMAT_MODE_BRIEFING_LONG,
    FORMAT_MODE_RADAR_SHORT,
    FORMAT_MODE_FLASH_BREAKING,
)

# Default format when caller does not pass format_mode (e.g. scheduler unchanged).
DEFAULT_FORMAT_MODE: Final[str] = FORMAT_MODE_BRIEFING_LONG

_TEMPLATE_FILES: Final[dict[str, str]] = {
    FORMAT_MODE_BRIEFING_LONG: "briefing_long.md",
    FORMAT_MODE_RADAR_SHORT: "radar_short.md",
    FORMAT_MODE_FLASH_BREAKING: "flash_breaking.md",
}


def get_template_path(format_mode: str) -> Path:
    """Return Path to template file for format_mode. Raises if unknown or file missing."""
    mode = (format_mode or "").strip().upper()
    if mode not in _TEMPLATE_FILES:
        raise ValueError(
            f"Unknown format_mode={format_mode!r}; expected one of: {', '.join(VALID_FORMAT_MODES)}"
        )
    path = _THIS_DIR / _TEMPLATE_FILES[mode]
    if not path.exists():
        raise FileNotFoundError(f"Template file not found: {path}")
    return path


def load_template(format_mode: str) -> str:
    """Load template content for format_mode. Raises if unknown or file missing."""
    path = get_template_path(format_mode)
    return path.read_text(encoding="utf-8")
