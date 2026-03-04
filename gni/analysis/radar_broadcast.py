"""
Radar broadcast: assemble radar data, generate LLM report, dispatch via Telegram.
Uses editorial router when format_mode not provided; uses guards + split for LONG.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from gni.analysis.llm_formatter import generate_report
from gni.editorial.router import select_format
from gni.templates import DEFAULT_FORMAT_MODE

logger = logging.getLogger(__name__)


def run_radar_broadcast(
    radar_data: dict[str, Any],
    dry_run: bool = True,
    session: Optional[Any] = None,
    format_mode: Optional[str] = None,
    job_name: Optional[str] = None,
    event_score: Optional[float] = None,
    category: Optional[str] = None,
) -> tuple[str, bool]:
    """
    Generate strategic report from radar data and send via Telegram.
    format_mode: optional BRIEFING_LONG | RADAR_SHORT | FLASH_BREAKING; if None and any of
    job_name/event_score/category is provided, format is chosen by editorial router.
    LONG: guard + split (Telegram limit), then send parts. SHORT/FLASH: guard + single send.
    Returns (formatted_message, sent_success). On guard failure, does not post.
    """
    if format_mode is None and (job_name is not None or event_score is not None or category is not None):
        format_mode = select_format(job_name, event_score, category)
        logger.info(
            "router_decision job_name=%s event_score=%s category=%s => format_mode=%s",
            job_name,
            event_score,
            category,
            format_mode,
        )
    formatted_message = generate_report(radar_data, format_mode=format_mode)
    if not formatted_message:
        return "", False

    if dry_run:
        return formatted_message, False

    mode = format_mode or DEFAULT_FORMAT_MODE
    mode = mode.strip().upper()

    if mode == "BRIEFING_LONG":
        from gni.publisher.send import send_long_message
        result = send_long_message(
            formatted_message,
            meta={},
            dry_run=False,
            session=session,
        )
    else:
        from gni.publisher.send import send_message
        result = send_message(
            formatted_message,
            format_mode=mode,
            meta={},
            dry_run=False,
            session=session,
        )

    sent = result.status == "sent"
    return formatted_message, sent
