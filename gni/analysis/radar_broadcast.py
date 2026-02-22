"""
Radar broadcast: assemble radar data, generate LLM report, dispatch via Telegram.
Integration layer — does NOT modify Telegram client. Uses existing publish_telegram.
"""
from __future__ import annotations

from typing import Any, Optional

from gni.analysis.llm_formatter import generate_report


def run_radar_broadcast(
    radar_data: dict[str, Any],
    dry_run: bool = True,
    session: Optional[Any] = None,
) -> tuple[str, bool]:
    """
    Generate strategic report from radar data and send via Telegram.
    Returns (formatted_message, sent_success).
    Does NOT modify Telegram send logic — only builds message and delegates.
    """
    formatted_message = generate_report(radar_data)
    if not formatted_message:
        return "", False

    if dry_run:
        return formatted_message, False

    # Delegate via gni_send wrapper
    from apps.publisher.gni_sender import gni_send

    result = gni_send(
        formatted_message,
        meta={},
        dry_run=False,
        session=session,
    )
    sent = result.status == "sent"
    return formatted_message, sent
