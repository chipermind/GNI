"""Tests for GNI publisher: splitter (deterministic), guards (contract validation)."""
from __future__ import annotations

import pytest

from gni.publisher.guards import (
    validate_flash,
    validate_for_format,
    validate_long,
    validate_short,
)
from gni.publisher.splitter import (
    DEFAULT_MAX_CHARS,
    split_briefing_long,
)


# --- Splitter ---

def test_split_briefing_long_empty_returns_empty_list():
    assert split_briefing_long("") == []
    assert split_briefing_long("   ") == []


def test_split_briefing_long_under_limit_returns_single_chunk():
    short = "🌐 GNI — BRIEFING GLOBAL Segunda, 04 Mar 2025 | 14h30\n\nBody\n\n🔐 GNI — Um passo à frente."
    assert len(short) < DEFAULT_MAX_CHARS
    result = split_briefing_long(short)
    assert len(result) == 1
    assert result[0] == short.strip()


def test_split_briefing_long_header_only_first_footer_only_last():
    header = "🌐 GNI — BRIEFING GLOBAL Segunda, 04 Mar 2025 | 14h30"
    footer = "🔐 GNI — Um passo à frente."
    body = "X" * (DEFAULT_MAX_CHARS + 100)
    text = f"{header}\n\n{body}\n\n{footer}"
    parts = split_briefing_long(text, max_chars=500)
    assert len(parts) >= 2
    assert header in parts[0]
    assert footer not in parts[0]
    assert footer in parts[-1]
    assert header not in parts[-1]
    for p in parts:
        assert len(p) <= 500


def test_split_briefing_long_splits_by_flag_blocks():
    header = "🌐 GNI — BRIEFING GLOBAL Segunda, 04 Mar 2025 | 14h30"
    footer = "🔐 GNI — Um passo à frente."
    # Two flag blocks (🇦🇺 🇺🇸) so we get at least two blocks
    block1 = "🇦🇺 Australia section with some content here."
    block2 = "🇺🇸 US section with more content."
    body = f"\n\n{block1}\n\n{block2}"
    text = f"{header}\n{body}\n\n{footer}"
    # Force small max so we get multiple chunks
    parts = split_briefing_long(text, max_chars=80)
    assert len(parts) >= 1
    assert header in parts[0]
    assert footer in parts[-1]


def test_split_briefing_long_deterministic_same_input_same_output():
    header = "🌐 GNI — BRIEFING GLOBAL Segunda, 04 Mar 2025 | 14h30"
    footer = "🔐 GNI — Um passo à frente."
    body = "A" * (DEFAULT_MAX_CHARS + 1)
    text = f"{header}\n\n{body}\n\n{footer}"
    a = split_briefing_long(text, max_chars=1000)
    b = split_briefing_long(text, max_chars=1000)
    assert a == b


# --- Guards ---

def test_validate_long_ok():
    text = "🌐 GNI — BRIEFING GLOBAL Segunda, 04 Mar 2025 | 14h30\n\nBody\n\n🔐 GNI — Um passo à frente."
    ok, reason = validate_long(text)
    assert ok is True
    assert reason == ""


def test_validate_long_missing_header():
    text = "Some other header\n\n🔐 GNI — Um passo à frente."
    ok, reason = validate_long(text)
    assert ok is False
    assert "header" in reason


def test_validate_long_missing_footer():
    text = "🌐 GNI — BRIEFING GLOBAL Segunda, 04 Mar 2025 | 14h30\n\nBody only."
    ok, reason = validate_long(text)
    assert ok is False
    assert "footer" in reason


def test_validate_long_empty():
    ok, reason = validate_long("")
    assert ok is False
    assert reason


def test_validate_short_ok():
    text = "🌐 GNI — Desk 14h30\n\n🔎 Radar Ativo\n• x\n\n📌 Leitura GNI\nY.\n\n— Equipe GNI"
    ok, reason = validate_short(text)
    assert ok is True
    assert reason == ""


def test_validate_short_missing_radar():
    text = "📌 Leitura GNI\nY.\n\n— Equipe GNI"
    ok, reason = validate_short(text)
    assert ok is False
    assert "radar" in reason.lower() or "Radar" in reason


def test_validate_short_missing_signature():
    text = "🔎 Radar Ativo\n• x\n\n📌 Leitura GNI\nY."
    ok, reason = validate_short(text)
    assert ok is False
    assert "signature" in reason.lower() or "Equipe" in reason


def test_validate_flash_ok():
    text = "🚨 GNI — FLASH\n• a\n• b\n• c\n\n📌 Impacto\nTwo sentences."
    ok, reason = validate_flash(text)
    assert ok is True
    assert reason == ""


def test_validate_flash_missing_header():
    text = "📌 Impacto\nTwo sentences."
    ok, reason = validate_flash(text)
    assert ok is False
    assert "header" in reason or "FLASH" in reason


def test_validate_for_format_dispatches():
    long_ok = "🌐 GNI — BRIEFING GLOBAL x\n\n🔐 GNI — Um passo à frente."
    assert validate_for_format(long_ok, "BRIEFING_LONG") == (True, "")
    assert validate_for_format(long_ok, "RADAR_SHORT")[0] is False
    short_ok = "🔎 Radar Ativo\n📌 Leitura GNI\n— Equipe GNI"
    assert validate_for_format(short_ok, "RADAR_SHORT") == (True, "")
    assert validate_for_format("x", "UNKNOWN")[0] is False
