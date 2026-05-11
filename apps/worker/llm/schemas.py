"""Pydantic schemas for classify and generate JSON outputs. Exact bullet counts enforced."""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# --- Classifier ---

class ClassifyResult(BaseModel):
    """Strict JSON from classify_prompt."""
    template: str = Field(..., description="Template name: ANALISE_INTEL or FLASH_SETORIAL")
    reason: Optional[str] = Field(None, description="Reason for classification")
    risk: Optional[str] = Field(None, description="risk level e.g. high, medium, low")
    priority: Optional[str] = Field(None, description="P0, P1, P2")
    sector: Optional[str] = Field(None, description="Sector or category")
    flag: Optional[str] = Field(None, description="Optional flag")
    requires_review: bool = Field(False, description="Needs human review")


# --- Template A: ANALISE_INTEL ---

StatusConfirmacao = Literal["confirmado", "alegação — não confirmada", "em apuração"]


class AnaliseIntelPayload(BaseModel):
    """Template A (ANALISE_INTEL) output. Exact counts enforced."""
    tema: str = Field(..., description="Tema principal")
    status_confirmacao: StatusConfirmacao = Field(
        ...,
        description="confirmado | alegação — não confirmada | em apuração",
    )
    leitura_rapida: list[str] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exatamente 3 strings",
    )
    por_que_importa: list[str] = Field(
        ...,
        min_length=2,
        max_length=2,
        description="Exatamente 2 strings",
    )
    checklist_osint: list[str] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exatamente 3 strings",
    )
    insight_central: str = Field(..., description="1–2 linhas")


# --- Template B: FLASH_SETORIAL ---

class FlashSetorialPayload(BaseModel):
    """Template B (FLASH_SETORIAL) output. Exact counts enforced."""
    setor: str = Field(..., description="Setor")
    flag_emoji: str = Field(..., description="Emoji de bandeira")
    linha_1: str = Field(..., description="Primeira linha")
    em_destaque: list[str] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exatamente 3 strings",
    )
    insight: str = Field(..., description="1 linha")


# --- Template GNI_ALERTA ---

class GniAlertaPayload(BaseModel):
    """Template GNI_ALERTA: short breaking-news alert."""
    headline: str = Field(..., description="1 linha forte")
    o_que_aconteceu: str = Field(..., description="1-2 linhas")
    por_que_importa: str = Field(..., description="1 linha de impacto")
    impacto_provavel: str = Field(..., description="1 linha de efeito esperado")


# --- Generator result (wrapper) ---

class GenerateResult(BaseModel):
    """Strict JSON from generate_prompt. payload validated by template (AnaliseIntel or FlashSetorial)."""
    payload: dict[str, Any] = Field(default_factory=dict, description="Template payload (validated by template)")


def validate_generate_payload(payload: dict[str, Any], template: str) -> dict[str, Any]:
    """
    Validate payload against template schema. Enforces exact bullet counts.
    Returns validated payload as dict; raises pydantic.ValidationError if invalid.
    """
    if template == "ANALISE_INTEL":
        m = AnaliseIntelPayload.model_validate(payload)
        return m.model_dump()
    if template == "FLASH_SETORIAL":
        m = FlashSetorialPayload.model_validate(payload)
        return m.model_dump()
    if template == "GNI_ALERTA":
        m = GniAlertaPayload.model_validate(payload)
        return m.model_dump()
    # DEFAULT or unknown: accept any dict (no strict schema)
    return payload
