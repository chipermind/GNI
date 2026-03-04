"""Unit tests for editorial router: pure function, edge cases (None, unknown job, threshold)."""
from __future__ import annotations

import pytest

from gni.editorial.router import (
    FLASH_THRESHOLD,
    JOBS_BRIEFING_LONG,
    JOBS_RADAR_SHORT,
    select_format,
)
from gni.templates import (
    FORMAT_MODE_BRIEFING_LONG,
    FORMAT_MODE_FLASH_BREAKING,
    FORMAT_MODE_RADAR_SHORT,
)


def test_select_format_all_none_returns_radar_short():
    """job_name=None, event_score=None, category=None => fallback RADAR_SHORT."""
    assert select_format(None, None, None) == FORMAT_MODE_RADAR_SHORT


def test_select_format_empty_job_returns_radar_short():
    """job_name='', event_score=None => fallback RADAR_SHORT."""
    assert select_format("", None, None) == FORMAT_MODE_RADAR_SHORT


def test_select_format_unknown_job_returns_radar_short():
    """Unknown job_name => fallback RADAR_SHORT."""
    assert select_format("unknown_job", None, None) == FORMAT_MODE_RADAR_SHORT
    assert select_format("random_123", None, "crypto") == FORMAT_MODE_RADAR_SHORT


def test_select_format_briefing_jobs_return_briefing_long():
    """briefing_0530, briefing_0900, premium_1200, closing_2200 => BRIEFING_LONG."""
    for job in JOBS_BRIEFING_LONG:
        assert select_format(job, None, None) == FORMAT_MODE_BRIEFING_LONG
    assert select_format("briefing_0530", None, None) == FORMAT_MODE_BRIEFING_LONG
    assert select_format("BRIEFING_0900", None, None) == FORMAT_MODE_BRIEFING_LONG
    assert select_format("premium_1200", None, None) == FORMAT_MODE_BRIEFING_LONG
    assert select_format("closing_2200", None, None) == FORMAT_MODE_BRIEFING_LONG


def test_select_format_radar_jobs_return_radar_short():
    """radar_interval, intel_flash => RADAR_SHORT."""
    for job in JOBS_RADAR_SHORT:
        assert select_format(job, None, None) == FORMAT_MODE_RADAR_SHORT
    assert select_format("radar_interval", None, None) == FORMAT_MODE_RADAR_SHORT
    assert select_format("intel_flash", None, None) == FORMAT_MODE_RADAR_SHORT


def test_select_format_event_score_at_or_above_threshold_returns_flash():
    """event_score >= FLASH_THRESHOLD => FLASH_BREAKING (overrides job_name)."""
    assert select_format(None, FLASH_THRESHOLD, None) == FORMAT_MODE_FLASH_BREAKING
    assert select_format(None, 1.0, None) == FORMAT_MODE_FLASH_BREAKING
    assert select_format("briefing_0900", 0.95, None) == FORMAT_MODE_FLASH_BREAKING
    assert select_format("radar_interval", 0.9, "geo") == FORMAT_MODE_FLASH_BREAKING


def test_select_format_event_score_below_threshold_no_flash():
    """event_score < FLASH_THRESHOLD => job-based or fallback, not FLASH."""
    assert select_format(None, 0.0, None) == FORMAT_MODE_RADAR_SHORT
    assert select_format(None, FLASH_THRESHOLD - 0.01, None) == FORMAT_MODE_RADAR_SHORT
    assert select_format("briefing_0530", 0.5, None) == FORMAT_MODE_BRIEFING_LONG


def test_select_format_event_score_none_uses_job_only():
    """event_score=None => no flash path; job or fallback."""
    assert select_format("briefing_0530", None, None) == FORMAT_MODE_BRIEFING_LONG
    assert select_format(None, None, "crypto") == FORMAT_MODE_RADAR_SHORT


def test_flash_threshold_value():
    """FLASH_THRESHOLD is a float in (0, 1] for breaking events."""
    assert isinstance(FLASH_THRESHOLD, float)
    assert 0 < FLASH_THRESHOLD <= 1.0
