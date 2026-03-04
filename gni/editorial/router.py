"""
Editorial format router: pure function mapping (job_name, event_score, category) -> format_mode.
No side effects; caller is responsible for logging router_decision.
"""
from __future__ import annotations

from gni.templates import (
    FORMAT_MODE_BRIEFING_LONG,
    FORMAT_MODE_FLASH_BREAKING,
    FORMAT_MODE_RADAR_SHORT,
)

# Jobs that always get BRIEFING_LONG (scheduled briefings / premium / closing).
JOBS_BRIEFING_LONG: frozenset[str] = frozenset({
    "briefing_0530",
    "briefing_0900",
    "premium_1200",
    "closing_2200",
})

# Jobs that always get RADAR_SHORT (interval radar, intel flash).
JOBS_RADAR_SHORT: frozenset[str] = frozenset({
    "radar_interval",
    "intel_flash",
})

# event_score >= FLASH_THRESHOLD => FLASH_BREAKING (overrides job_name when applicable).
FLASH_THRESHOLD: float = 0.9


def select_format(
    job_name: str | None,
    event_score: float | None,
    category: str | None,
) -> str:
    """
    Pure: select editorial format from job context. No I/O, no logging.

    Rules (evaluated in order):
    1. If event_score is not None and event_score >= FLASH_THRESHOLD => FLASH_BREAKING.
    2. If job_name is in briefing/premium/closing set => BRIEFING_LONG.
    3. If job_name is in radar/intel set => RADAR_SHORT.
    4. Fallback => RADAR_SHORT (explicit default for unknown/None job_name).

    Edge cases:
    - job_name None, event_score None => RADAR_SHORT.
    - job_name "", event_score 0.0 => RADAR_SHORT.
    - job_name "unknown_job" => RADAR_SHORT.
    """
    # 1. Breaking event overrides job type
    if event_score is not None and event_score >= FLASH_THRESHOLD:
        return FORMAT_MODE_FLASH_BREAKING

    # 2. Job-based routing (normalize to lowercase for comparison)
    name = (job_name or "").strip().lower()
    if name in JOBS_BRIEFING_LONG:
        return FORMAT_MODE_BRIEFING_LONG
    if name in JOBS_RADAR_SHORT:
        return FORMAT_MODE_RADAR_SHORT

    # 3. Explicit fallback (documented)
    return FORMAT_MODE_RADAR_SHORT
