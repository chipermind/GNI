"""Tests for intelligent caching: score by fingerprint, LLM by prompt hash."""
from unittest.mock import MagicMock, patch

import pytest

from apps.worker.cache import (
    get_score_cached,
    prompt_hash,
    set_score_cached,
)


def test_prompt_hash_deterministic():
    """Same inputs produce same hash."""
    h1 = prompt_hash("a", "b", "c")
    h2 = prompt_hash("a", "b", "c")
    assert h1 == h2


def test_prompt_hash_different_inputs():
    """Different inputs produce different hash."""
    h1 = prompt_hash("a", "b")
    h2 = prompt_hash("a", "b", "c")
    assert h1 != h2


def test_prompt_hash_normalizes_whitespace():
    """Excessive whitespace is normalized."""
    h1 = prompt_hash("a  b", "c")
    h2 = prompt_hash("a b", "c")
    assert h1 == h2


def test_score_cache_miss_returns_none():
    """get_score_cached returns None for unknown fingerprint."""
    assert get_score_cached("unknown_fp_xyz123") is None


def test_score_cache_hit_returns_value():
    """set then get returns same value."""
    fp = "test_fp_abc"
    score = {"priority": 1, "risk": "high", "template": "ANALISE_INTEL", "needs_review": True}
    set_score_cached(fp, score)
    cached = get_score_cached(fp)
    assert cached is not None
    assert cached["priority"] == 1
    assert cached["template"] == "ANALISE_INTEL"
