"""Simulate news workflow: validate format and pipeline flow."""
import pytest

from apps.worker.render import render, render_intelligence, render_sector_flash
from apps.publisher.whatsapp_make import MakePayload


FIXTURE_TEMPLATE_A = {
    "tema": "Acordo regulatório em apuração",
    "status_confirmacao": "em apuração",
    "leitura_rapida": ["A", "B", "C"],
    "por_que_importa": ["X", "Y"],
    "checklist_osint": ["1", "2", "3"],
    "insight_central": "Resumo central.",
}

FIXTURE_TEMPLATE_B = {
    "setor": "Crypto",
    "flag_emoji": "🇺🇸",
    "linha_1": "Bitcoin atinge nova máxima.",
    "em_destaque": ["ETF aprovado", "Fluxo positivo", "Correlação tech"],
    "insight": "Momentum mantém.",
}


def test_template_a_workflow_format():
    """Template A (ANALISE_INTEL) renders in correct Portuguese format."""
    out = render_intelligence(FIXTURE_TEMPLATE_A)
    assert "🚨 GNI — Análise de Inteligência" in out
    assert "Tema:" in out
    assert "Leitura rápida" in out
    assert "Por que isso importa" in out
    assert "Como validar (checklist OSINT)" in out
    assert "Insight central" in out
    assert "\t• " in out
    assert "\t• ✅ " in out
    assert out.strip().endswith("⸻")


def test_template_b_workflow_format():
    """Template B (FLASH_SETORIAL) renders in correct Portuguese format."""
    out = render_sector_flash("Crypto", "🇺🇸", FIXTURE_TEMPLATE_B)
    assert "🚨 GNI |" in out
    assert "Crypto" in out
    assert "Em destaque:" in out
    assert "📌 Insight:" in out
    assert "\t• " in out
    assert out.strip().endswith("⸻")


def test_make_payload_workflow_format():
    """Make webhook payload matches spec: channel, text, template, priority, meta."""
    p = MakePayload(
        text="Test",
        template="ANALISE_INTEL",
        priority="P1",
        source="CoinDesk",
        url="https://x.com/1",
        item_id=42,
    )
    j = p.to_json()
    assert j["channel"] == "whatsapp"
    assert "text" in j
    assert j["template"] == "ANALISE_INTEL"
    assert j["priority"] == "P1"
    assert "meta" in j
    assert j["meta"]["source"] == "CoinDesk"
    assert j["meta"]["url"] == "https://x.com/1"
    assert j["meta"]["item_id"] == 42


def test_render_workflow_dispatches():
    """render() produces messages for both templates."""
    msgs_a = render(template="ANALISE_INTEL", payload=FIXTURE_TEMPLATE_A, sector="", flag="")
    msgs_b = render(template="FLASH_SETORIAL", payload=FIXTURE_TEMPLATE_B, sector="Crypto", flag="🇺🇸")
    assert len(msgs_a) >= 1
    assert len(msgs_b) >= 1
