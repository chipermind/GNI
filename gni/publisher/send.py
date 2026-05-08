"""
GNI editorial send: guards + optional split. send_message for SHORT/FLASH;
send_long_message for LONG (split, then send parts). Do not post if guard fails.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from apps.publisher.gni_sender import gni_send
from apps.publisher.telegram import PublicationResult

from gni.publisher.guards import guard_and_validate
from gni.publisher.splitter import DEFAULT_MAX_CHARS, split_briefing_long

logger = logging.getLogger(__name__)


def send_message(
    text: str,
    format_mode: str,
    *,
    meta: Optional[dict[str, Any]] = None,
    dry_run: bool = True,
    session: Optional[Any] = None,
) -> PublicationResult:
    """
    Send SHORT or FLASH (single message). Validates with guard; if invalid, does NOT post.
    Returns PublicationResult (status='guard_failed' on validation failure).
    """
    ok, reason = guard_and_validate(text, format_mode)
    if not ok:
        return PublicationResult(status="guard_failed", dry_run=dry_run, attempts=0)
    result = gni_send(
        text,
        meta=meta or {},
        dry_run=dry_run,
        session=session,
    )
    if result.status == "sent" and not result.dry_run:
        logger.info("telegram_sent_ok format_mode=%s", format_mode)
    return result


def send_long_message(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    *,
    meta: Optional[dict[str, Any]] = None,
    dry_run: bool = True,
    session: Optional[Any] = None,
) -> PublicationResult:
    """
    Validate LONG contract, split with split_briefing_long, send each part.
    If guard fails, does NOT post. Logs split_parts=N, part sizes, and send confirmation per part.
    """
    ok, reason = guard_and_validate(text, "BRIEFING_LONG")
    if not ok:
        return PublicationResult(status="guard_failed", dry_run=dry_run, attempts=0)

    parts = split_briefing_long(text, max_chars=max_chars)
    if not parts:
        logger.warning("send_long_message split produced no parts")
        return PublicationResult(status="no_parts", dry_run=dry_run, attempts=0)

    part_sizes = [len(p) for p in parts]
    logger.info("split_parts=%s part_sizes=%s max_chars=%s", len(parts), part_sizes, max_chars)

    result = gni_send(
        parts,
        meta=meta or {},
        dry_run=dry_run,
        session=session,
    )
    if result.status == "sent" and not result.dry_run:
        logger.info("telegram_sent_ok format_mode=BRIEFING_LONG")
        for i, size in enumerate(part_sizes, 1):
            logger.info("send_long_message part %s/%s sent len=%s", i, len(parts), size)
    return result
