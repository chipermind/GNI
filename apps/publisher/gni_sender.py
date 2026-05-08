"""
Minimal wrapper around existing Telegram send. Zero behavior changes.
Adds one INFO log after successful send. Call sites unchanged.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from apps.observability.logging import get_logger
from apps.publisher.telegram import publish_telegram, PublicationResult

logger = get_logger(__name__)


def gni_send(
    text: str | list[str],
    meta: dict[str, Any] | None = None,
    *,
    dry_run: bool | None = None,
    session: Optional[Any] = None,
) -> PublicationResult:
    """
    Send text or messages to Telegram via existing publish_telegram. Same params, return, exceptions.
    meta is optional; dry_run/session pass through when provided.
    """
    if dry_run is None:
        dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    messages = [text] if isinstance(text, str) else text
    result = publish_telegram(
        messages=messages,
        channel="telegram",
        dry_run=dry_run,
        session=session,
    )
    if result.status == "sent" and not result.dry_run:
        if meta:
            logger.info("gni_send: sent ok", **meta)
        else:
            logger.info("gni_send: sent ok")
    return result
