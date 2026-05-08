"""Prompts em português: classificar (template/risk/priority) e gerar (payload por template). Apenas JSON válido, sem markdown."""

CLASSIFY_SYSTEM = """Você classifica itens de notícia em template, risk, priority, sector, flag e se requer revisão.
Responda APENAS com JSON válido, sem markdown nem texto extra.
Schema: {"template": string, "reason": string|null, "risk": string|null, "priority": "P0"|"P1"|"P2", "sector": string|null, "flag": string|null, "requires_review": boolean}
template deve ser "ANALISE_INTEL" ou "FLASH_SETORIAL". reason explica brevemente a classificação."""


def classify_prompt(title: str, summary: str, source_name: str = "") -> str:
    """Monta o prompt de usuário para classificação."""
    parts = [f"Título: {title}"]
    if summary:
        parts.append(f"Resumo: {summary}")
    if source_name:
        parts.append(f"Fonte: {source_name}")
    parts.append("\nRetorne somente JSON: template (ANALISE_INTEL ou FLASH_SETORIAL), reason, risk, priority (P0/P1/P2), sector, flag, requires_review.")
    return "\n".join(parts)


# --- Generate: schema por template ---

GENERATE_SYSTEM_ANALISE = """Você produz um payload de publicação no template ANALISE_INTEL a partir do item.
Responda APENAS com JSON válido no formato: {"payload": { ... }}. Sem markdown, sem cercas de código.
O objeto payload deve ter exatamente:
- "tema": string (tema principal)
- "status_confirmacao": "confirmado" | "alegação — não confirmada" | "em apuração"
- "leitura_rapida": array de exatamente 3 strings
- "por_que_importa": array de exatamente 2 strings
- "checklist_osint": array de exatamente 3 strings
- "insight_central": string (1 a 2 linhas)"""


GENERATE_SYSTEM_FLASH = """Você produz um payload de publicação no template FLASH_SETORIAL a partir do item.
Responda APENAS com JSON válido no formato: {"payload": { ... }}. Sem markdown, sem cercas de código.
O objeto payload deve ter exatamente:
- "setor": string
- "flag_emoji": string (um emoji)
- "linha_1": string
- "em_destaque": array de exatamente 3 strings
- "insight": string (1 linha)"""


GENERATE_SYSTEM_DEFAULT = """Você produz um payload de publicação a partir do item.
Responda APENAS com JSON válido: {"payload": { ... }}. Sem markdown, sem cercas de código."""


def get_generate_system(template: str) -> str:
    """Retorna o system prompt de geração conforme o template."""
    if template == "ANALISE_INTEL":
        return GENERATE_SYSTEM_ANALISE
    if template == "FLASH_SETORIAL":
        return GENERATE_SYSTEM_FLASH
    return GENERATE_SYSTEM_DEFAULT


def generate_prompt(title: str, summary: str, template: str, risk: str = "") -> str:
    """Monta o prompt de usuário para geração do draft."""
    parts = [
        f"Template: {template}",
        f"Título: {title}",
    ]
    if summary:
        parts.append(f"Resumo: {summary}")
    if risk:
        parts.append(f"Risk: {risk}")
    parts.append("\nRetorne somente JSON: {\"payload\": { ... }} com os campos exatos do template.")
    return "\n".join(parts)


STRICT_JSON_REPAIR = "\n\nCORREÇÃO: A saída deve ser exatamente um objeto JSON válido, sem outro texto."
