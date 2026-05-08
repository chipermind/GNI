"""Unit tests for LLM schemas and JSON validation (no Ollama required)."""
import pytest
from pydantic import ValidationError

from apps.worker.llm.schemas import (
    AnaliseIntelPayload,
    ClassifyResult,
    FlashSetorialPayload,
    GenerateResult,
    validate_generate_payload,
)


def test_classify_result_valid():
    """Valid ClassifyResult from JSON including reason."""
    data = {
        "template": "ANALISE_INTEL",
        "reason": "Item sobre rumor não confirmado.",
        "risk": "high",
        "priority": "P0",
        "sector": "crypto",
        "flag": None,
        "requires_review": True,
    }
    r = ClassifyResult.model_validate(data)
    assert r.template == "ANALISE_INTEL"
    assert r.reason == "Item sobre rumor não confirmado."
    assert r.risk == "high"
    assert r.priority == "P0"
    assert r.requires_review is True


def test_classify_result_minimal():
    """Minimal ClassifyResult (template only); reason optional."""
    r = ClassifyResult.model_validate({"template": "FLASH_SETORIAL"})
    assert r.template == "FLASH_SETORIAL"
    assert r.reason is None
    assert r.requires_review is False


def test_classify_result_invalid_missing_template():
    """ClassifyResult requires template."""
    with pytest.raises(ValidationError):
        ClassifyResult.model_validate({"risk": "high"})


# --- Template A (ANALISE_INTEL) ---

def test_analise_intel_payload_valid():
    """AnaliseIntelPayload validates with exact counts."""
    data = {
        "tema": "Possível acordo SEC",
        "status_confirmacao": "em apuração",
        "leitura_rapida": ["Ponto 1", "Ponto 2", "Ponto 3"],
        "por_que_importa": ["Razão 1", "Razão 2"],
        "checklist_osint": ["Check 1", "Check 2", "Check 3"],
        "insight_central": "Uma ou duas linhas de insight.",
    }
    r = AnaliseIntelPayload.model_validate(data)
    assert r.tema == "Possível acordo SEC"
    assert r.status_confirmacao == "em apuração"
    assert len(r.leitura_rapida) == 3
    assert len(r.por_que_importa) == 2
    assert len(r.checklist_osint) == 3


def test_analise_intel_payload_status_values():
    """status_confirmacao accepts only the three allowed values."""
    base = {
        "tema": "T",
        "leitura_rapida": ["a", "b", "c"],
        "por_que_importa": ["x", "y"],
        "checklist_osint": ["1", "2", "3"],
        "insight_central": "I",
    }
    for status in ("confirmado", "alegação — não confirmada", "em apuração"):
        r = AnaliseIntelPayload.model_validate({**base, "status_confirmacao": status})
        assert r.status_confirmacao == status
    with pytest.raises(ValidationError):
        AnaliseIntelPayload.model_validate({**base, "status_confirmacao": "invalid"})


def test_analise_intel_payload_wrong_count_rejected():
    """AnaliseIntelPayload rejects wrong list lengths."""
    data = {
        "tema": "T",
        "status_confirmacao": "em apuração",
        "leitura_rapida": ["a", "b"],  # must be 3
        "por_que_importa": ["x", "y"],
        "checklist_osint": ["1", "2", "3"],
        "insight_central": "I",
    }
    with pytest.raises(ValidationError):
        AnaliseIntelPayload.model_validate(data)


# --- Template B (FLASH_SETORIAL) ---

def test_flash_setorial_payload_valid():
    """FlashSetorialPayload validates with exact counts."""
    data = {
        "setor": "Crypto",
        "flag_emoji": "📌",
        "linha_1": "Primeira linha",
        "em_destaque": ["Destaque 1", "Destaque 2", "Destaque 3"],
        "insight": "Uma linha de insight.",
    }
    r = FlashSetorialPayload.model_validate(data)
    assert r.setor == "Crypto"
    assert r.flag_emoji == "📌"
    assert len(r.em_destaque) == 3


def test_flash_setorial_payload_wrong_count_rejected():
    """FlashSetorialPayload rejects em_destaque with != 3 items."""
    data = {
        "setor": "S",
        "flag_emoji": "🔴",
        "linha_1": "L1",
        "em_destaque": ["a", "b"],  # must be 3
        "insight": "I",
    }
    with pytest.raises(ValidationError):
        FlashSetorialPayload.model_validate(data)


# --- GenerateResult + validate_generate_payload ---

def test_generate_result_valid():
    """Valid GenerateResult with payload."""
    data = {"payload": {"headline": "Test", "body": "Content"}}
    r = GenerateResult.model_validate(data)
    assert r.payload["headline"] == "Test"


def test_generate_result_empty_payload():
    """GenerateResult with empty payload."""
    r = GenerateResult.model_validate({})
    assert r.payload == {}


def test_validate_generate_payload_analise_intel():
    """validate_generate_payload enforces ANALISE_INTEL schema."""
    payload = {
        "tema": "Tema",
        "status_confirmacao": "confirmado",
        "leitura_rapida": ["L1", "L2", "L3"],
        "por_que_importa": ["P1", "P2"],
        "checklist_osint": ["C1", "C2", "C3"],
        "insight_central": "Insight.",
    }
    out = validate_generate_payload(payload, "ANALISE_INTEL")
    assert out["tema"] == "Tema"
    assert len(out["leitura_rapida"]) == 3


def test_validate_generate_payload_flash_setorial():
    """validate_generate_payload enforces FLASH_SETORIAL schema."""
    payload = {
        "setor": "Setor",
        "flag_emoji": "🚨",
        "linha_1": "Linha",
        "em_destaque": ["E1", "E2", "E3"],
        "insight": "Uma linha.",
    }
    out = validate_generate_payload(payload, "FLASH_SETORIAL")
    assert out["setor"] == "Setor"
    assert len(out["em_destaque"]) == 3


def test_validate_generate_payload_invalid_analise_raises():
    """validate_generate_payload raises for invalid ANALISE_INTEL payload."""
    with pytest.raises(ValidationError):
        validate_generate_payload({"tema": "T", "leitura_rapida": ["a", "b"]}, "ANALISE_INTEL")


def test_validate_generate_payload_default_passthrough():
    """validate_generate_payload passes through unknown template as-is."""
    payload = {"headline": "H", "body": "B"}
    assert validate_generate_payload(payload, "DEFAULT") == payload


# --- Generator returns valid JSON per template (mocked); one retry works ---

def test_generate_returns_valid_json_analise_intel_after_retry():
    """Given invalid then valid JSON, generate() returns valid ANALISE_INTEL payload after one retry."""
    from unittest.mock import AsyncMock, patch

    from apps.worker.llm.ollama_client import generate

    valid_payload = {
        "tema": "Acordo regulatório",
        "status_confirmacao": "em apuração",
        "leitura_rapida": ["A", "B", "C"],
        "por_que_importa": ["X", "Y"],
        "checklist_osint": ["1", "2", "3"],
        "insight_central": "Insight central.",
    }
    invalid_resp = "not json at all"
    valid_resp = '{"payload": ' + __import__("json").dumps(valid_payload) + "}"

    with patch("apps.worker.llm.ollama_client._chat_async", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = [invalid_resp, valid_resp]
        result = generate("Title", "Summary", template="ANALISE_INTEL", base_url="http://ollama:11434")
    assert result.payload["tema"] == "Acordo regulatório"
    assert len(result.payload["leitura_rapida"]) == 3
    assert mock_chat.call_count == 2


def test_generate_returns_valid_json_flash_setorial():
    """Generator returns valid FLASH_SETORIAL JSON that validates with Pydantic."""
    from unittest.mock import AsyncMock, patch

    from apps.worker.llm.ollama_client import generate

    valid_payload = {
        "setor": "Crypto",
        "flag_emoji": "📌",
        "linha_1": "Linha um",
        "em_destaque": ["D1", "D2", "D3"],
        "insight": "Uma linha.",
    }
    valid_resp = '{"payload": ' + __import__("json").dumps(valid_payload) + "}"

    with patch("apps.worker.llm.ollama_client._chat_async", new_callable=AsyncMock, return_value=valid_resp):
        result = generate("Title", "Summary", template="FLASH_SETORIAL", base_url="http://ollama:11434")
    assert result.payload["setor"] == "Crypto"
    assert len(result.payload["em_destaque"]) == 3
    from apps.worker.llm.schemas import FlashSetorialPayload
    FlashSetorialPayload.model_validate(result.payload)
