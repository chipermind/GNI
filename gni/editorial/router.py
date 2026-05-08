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


# ---------------------------------------------------------------------------
# V1 content router (text -> 5-template editorial space)
# ---------------------------------------------------------------------------

URGENCY_KEYWORDS: tuple[str, ...] = (
    "breaking", "urgente", "urgent", "guerra", "war",
    "ataque", "attack", "crash", "hack", "breach", "exploit",
    "killed", "morto", "morte", "dead", "explosion", "explosão",
    "emergency", "emergência", "default", "collapse", "colapso",
)
HIGH_PRIORITY_KEYWORDS: tuple[str, ...] = (
    "fed", "rate cut", "rate hike", "inflation", "inflação",
    "gdp", "pib", "sanctions", "sanção", "sanctions",
    "tariff", "tarifa", "downgrade", "recession", "recessão",
    "ipca", "selic", "boe", "ecb", "bce", "fomc", "cpi",
    "outage", "ransomware", "zero-day", "0day", "cve-",
)
SIGNAL_KEYWORDS: tuple[str, ...] = (
    "rally", "surge", "plunge", "drop", "rise", "fall",
    "alta", "queda", "sobe", "cai", "despenca", "record", "recorde",
    "spike", "selloff", "sell-off",
)


def route_content(text: str) -> dict:
    """V1 heuristic router: text -> {"template": str, "confidence": float}.

    Templates returned: FLASH | ALERTA | RADAR.
    BRIEFING and FECHAMENTO are scheduled editorial builds and are NOT
    auto-routed from a single headline.

    Confidence:
      0.90 if urgency keyword matched      -> FLASH
      0.80 if high-priority keyword matched -> ALERTA
      0.70 if signal keyword matched        -> RADAR
      0.50 fallback                          -> RADAR
      0.00 if text is empty                  -> RADAR
    """
    if not text or not text.strip():
        return {"template": "RADAR", "confidence": 0.0}
    t = text.lower()
    if any(k in t for k in URGENCY_KEYWORDS):
        return {"template": "FLASH", "confidence": 0.90}
    if any(k in t for k in HIGH_PRIORITY_KEYWORDS):
        return {"template": "ALERTA", "confidence": 0.80}
    if any(k in t for k in SIGNAL_KEYWORDS):
        return {"template": "RADAR", "confidence": 0.70}
    return {"template": "RADAR", "confidence": 0.50}
