"""
Editorial contract guards: validate text before publish. If validation fails, do NOT post.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# Contract markers (must match gni/templates)
LONG_HEADER = "🌐 GNI — BRIEFING GLOBAL"
LONG_FOOTER = "🔐 GNI — Um passo à frente."
SHORT_RADAR = "🔎 Radar Ativo"
SHORT_LEITURA = "📌 Leitura GNI"
SHORT_SIGNATURE = "— Equipe GNI"
FLASH_HEADER = "🚨 GNI — FLASH"
FLASH_IMPACTO = "📌 Impacto"

FormatKind = Literal["BRIEFING_LONG", "RADAR_SHORT", "FLASH_BREAKING"]


def validate_long(text: str) -> tuple[bool, str]:
    """
    LONG must contain header and footer. Returns (ok, reason).
    """
    if not text or not text.strip():
        return False, "empty_text"
    t = text.strip()
    if LONG_HEADER not in t:
        return False, "missing_long_header"
    if LONG_FOOTER not in t:
        return False, "missing_long_footer"
    return True, ""


def validate_short(text: str) -> tuple[bool, str]:
    """
    SHORT must contain Radar Ativo, Leitura GNI, and signature. Returns (ok, reason).
    """
    if not text or not text.strip():
        return False, "empty_text"
    t = text.strip()
    if SHORT_RADAR not in t:
        return False, "missing_short_radar"
    if SHORT_LEITURA not in t:
        return False, "missing_short_leitura"
    if SHORT_SIGNATURE not in t:
        return False, "missing_short_signature"
    return True, ""


def validate_flash(text: str) -> tuple[bool, str]:
    """
    FLASH must contain flash header and Impacto. Returns (ok, reason).
    """
    if not text or not text.strip():
        return False, "empty_text"
    t = text.strip()
    if FLASH_HEADER not in t:
        return False, "missing_flash_header"
    if FLASH_IMPACTO not in t:
        return False, "missing_flash_impacto"
    return True, ""


def validate_for_format(text: str, format_mode: str) -> tuple[bool, str]:
    """
    Dispatch by format_mode. Returns (ok, reason). reason empty if ok.
    """
    mode = (format_mode or "").strip().upper()
    if mode == "BRIEFING_LONG":
        return validate_long(text)
    if mode == "RADAR_SHORT":
        return validate_short(text)
    if mode == "FLASH_BREAKING":
        return validate_flash(text)
    return False, f"unknown_format_mode_{format_mode!r}"


def _logs_dir() -> Path:
    """Directory for failed payload dumps (logs/ under repo or cwd)."""
    for base in [Path(__file__).resolve().parent.parent.parent, Path.cwd()]:
        d = base / "logs"
        if d.is_dir() or base == Path.cwd():
            return d
    return Path.cwd() / "logs"


def save_failed_payload_for_debug(text: str, format_mode: str, reason: str) -> str | None:
    """
    Save payload to logs/ for debug when guard fails. No DB. Returns path if written.
    """
    try:
        log_dir = _logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_mode = (format_mode or "unknown").replace("/", "_")[:32]
        path = log_dir / f"failed_guard_{safe_mode}_{ts}.txt"
        payload = f"reason={reason}\nformat_mode={format_mode}\n---\n{text}"
        path.write_text(payload, encoding="utf-8")
        return str(path)
    except Exception as e:
        logger.warning("guard_save_failed_payload error=%s", e)
        return None


def guard_and_validate(
    text: str,
    format_mode: str,
) -> tuple[bool, str]:
    """
    Validate text for format_mode. If invalid: log error, save payload to logs/, return (False, reason).
    Caller must NOT post when False.
    """
    ok, reason = validate_for_format(text, format_mode)
    if ok:
        return True, ""
    logger.error(
        "guard_failed format_mode=%s reason=%s text_len=%s",
        format_mode,
        reason,
        len(text) if text else 0,
    )
    path = save_failed_payload_for_debug(text, format_mode, reason)
    if path:
        logger.info("guard_failed payload_saved path=%s", path)
    return False, reason
