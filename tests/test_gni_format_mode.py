"""Unit/smoke tests: each format_mode loads the correct GNI template (no LLM required)."""
from __future__ import annotations

import pytest

from gni.templates import (
    DEFAULT_FORMAT_MODE,
    FORMAT_MODE_BRIEFING_LONG,
    FORMAT_MODE_FLASH_BREAKING,
    FORMAT_MODE_RADAR_SHORT,
    get_template_path,
    load_template,
    VALID_FORMAT_MODES,
)


def test_default_format_mode_is_briefing_long():
    """Default when format_mode is not provided is BRIEFING_LONG (documented behavior)."""
    assert DEFAULT_FORMAT_MODE == FORMAT_MODE_BRIEFING_LONG
    assert DEFAULT_FORMAT_MODE in VALID_FORMAT_MODES


def test_template_path_briefing_long():
    """BRIEFING_LONG maps to gni/templates/briefing_long.md."""
    path = get_template_path(FORMAT_MODE_BRIEFING_LONG)
    assert path.name == "briefing_long.md"
    assert path.exists()
    assert "gni" in str(path) and "templates" in str(path)


def test_template_path_radar_short():
    """RADAR_SHORT maps to gni/templates/radar_short.md."""
    path = get_template_path(FORMAT_MODE_RADAR_SHORT)
    assert path.name == "radar_short.md"
    assert path.exists()


def test_template_path_flash_breaking():
    """FLASH_BREAKING maps to gni/templates/flash_breaking.md."""
    path = get_template_path(FORMAT_MODE_FLASH_BREAKING)
    assert path.name == "flash_breaking.md"
    assert path.exists()


def test_load_template_briefing_long_contains_contract():
    """Loaded BRIEFING_LONG template contains contract header/footer and anti-drift rules."""
    content = load_template(FORMAT_MODE_BRIEFING_LONG)
    assert "BRIEFING GLOBAL" in content
    assert "Um passo à frente" in content
    assert "anti-desvio" in content or "Nunca inventar" in content


def test_load_template_radar_short_contains_contract():
    """Loaded RADAR_SHORT template contains Radar Ativo, Leitura GNI, Equipe GNI."""
    content = load_template(FORMAT_MODE_RADAR_SHORT)
    assert "Radar Ativo" in content
    assert "Leitura GNI" in content
    assert "Equipe GNI" in content
    assert "Desk" in content


def test_load_template_flash_breaking_contains_contract():
    """Loaded FLASH_BREAKING template contains FLASH and Impacto."""
    content = load_template(FORMAT_MODE_FLASH_BREAKING)
    assert "FLASH" in content
    assert "Impacto" in content
    assert "3 bullets" in content or "bullets" in content


def test_unknown_format_mode_raises():
    """Unknown format_mode raises ValueError."""
    with pytest.raises(ValueError, match="Unknown format_mode"):
        get_template_path("INVALID_MODE")
    with pytest.raises(ValueError, match="Unknown format_mode"):
        load_template("INVALID_MODE")


def test_generate_report_default_returns_string():
    """generate_report(radar_data) with no format_mode returns a string (fallback path)."""
    from gni.analysis.llm_formatter import generate_report

    result = generate_report({"geopolitics": "Test."})
    assert isinstance(result, str)
    assert len(result) > 20
    # Default is long format; should contain GNI branding
    assert "GNI" in result or "Global" in result


def test_generate_report_radar_short_returns_short_format():
    """generate_report(..., format_mode=RADAR_SHORT) returns short-format content (fallback)."""
    from gni.analysis.llm_formatter import generate_report

    result = generate_report({}, format_mode=FORMAT_MODE_RADAR_SHORT)
    assert isinstance(result, str)
    assert "Equipe GNI" in result
    assert "Radar Ativo" in result or "Desk" in result


def test_generate_report_flash_breaking_returns_flash_format():
    """generate_report(..., format_mode=FLASH_BREAKING) returns flash-format content (fallback)."""
    from gni.analysis.llm_formatter import generate_report

    result = generate_report({}, format_mode=FORMAT_MODE_FLASH_BREAKING)
    assert isinstance(result, str)
    assert "FLASH" in result
    assert "Impacto" in result
