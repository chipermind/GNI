"""
Source-tier + keyword-based scoring. Fills priority (P0/P1/P2 as int 0/1/2),
risk, template, needs_review.
"""
import re
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None

# Default rules if YAML not loaded
RUMOR_INTEL_KEYWORDS = ("rumor", "rumours", "unconfirmed", "allegedly", "alleged")
FLASH_EDITORIAL_KEYWORDS = ("announcement", "launch", "partnership", "capability", "unveiled", "released")
TEMPLATE_ANALISE_INTEL = "ANALISE_INTEL"
TEMPLATE_FLASH_SETORIAL = "FLASH_SETORIAL"


def _keywords_path() -> Path:
    path = Path(__file__).resolve().parent.parent.parent / "data" / "keywords.yaml"
    env_path = __import__("os").environ.get("DATA_KEYWORDS_PATH")
    if env_path:
        return Path(env_path)
    return path


def load_keywords() -> dict[str, Any]:
    """Load data/keywords.yaml."""
    path = _keywords_path()
    if not path.exists() or yaml is None:
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_keywords_list(data: dict, key: str, default: tuple[str, ...]) -> list[str]:
    lst = (data or {}).get(key)
    if isinstance(lst, list):
        return [str(x).strip().lower() for x in lst if x]
    return list(default)


def _get_source_tier(data: dict, source_name: Optional[str]) -> int:
    """Return tier 1 (best), 2, or 3 (lowest). Unknown source => 3."""
    tiers = (data or {}).get("source_tiers") or {}
    name = (source_name or "").strip()
    for tier_key in ("tier1", "tier2", "tier3"):
        names = tiers.get(tier_key)
        if isinstance(names, list) and name in names:
            return int(tier_key.replace("tier", ""))
    return 3


def _text_contains_any(text: str, keywords: list[str]) -> bool:
    if not text or not keywords:
        return False
    lower = text.lower()
    return any(re.search(rf"\b{re.escape(k)}\b", lower) for k in keywords)


def score_item(
    title: Optional[str] = None,
    summary: Optional[str] = None,
    source_name: Optional[str] = None,
    keywords_data: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Return scoring result: priority (0=P0, 1=P1, 2=P2), risk, template, needs_review.
    Rules: rumor/unconfirmed/allegedly => risk=high, template=ANALISE_INTEL;
           announcement/launch/partnership/capability => template=FLASH_SETORIAL.
    """
    data = keywords_data if keywords_data is not None else load_keywords()
    rumor_kw = _get_keywords_list(data, "rumor_intel", RUMOR_INTEL_KEYWORDS)
    flash_kw = _get_keywords_list(data, "flash_editorial", FLASH_EDITORIAL_KEYWORDS)

    combined = f"{title or ''} {summary or ''}"
    risk: Optional[str] = None
    template: Optional[str] = None
    needs_review = False

    if _text_contains_any(combined, rumor_kw):
        risk = "high"
        template = TEMPLATE_ANALISE_INTEL
        needs_review = True

    if _text_contains_any(combined, flash_kw) and template is None:
        template = TEMPLATE_FLASH_SETORIAL

    if template is None:
        template = "DEFAULT"

    source_tier = _get_source_tier(data, source_name)
    # P0 = tier1 + high impact (risk or flash); P1 = tier1/tier2 else; P2 = tier3 or default
    if source_tier == 1 and (risk == "high" or template == TEMPLATE_FLASH_SETORIAL):
        priority = 0  # P0
    elif source_tier <= 2:
        priority = 1  # P1
    else:
        priority = 2  # P2

    return {
        "priority": priority,
        "risk": risk,
        "template": template,
        "needs_review": needs_review,
    }


def apply_score_to_item(item: Any, score: dict[str, Any]) -> None:
    """Set item.priority, item.risk, item.template, item.needs_review from score dict."""
    item.priority = score.get("priority", 2)
    item.risk = score.get("risk")
    item.template = score.get("template")
    item.needs_review = score.get("needs_review", False)
