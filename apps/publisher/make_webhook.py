"""
Make.com webhook channel: POST JSON {phone, message, source, item_id} to external webhook.
Runs when MAKE_WEBHOOK_ENABLED=1 and MAKE_WEBHOOK_URL set. 3 retries, configurable timeout.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = None  # type: ignore

from apps.shared.secrets import get_secret

CHANNEL = "make_webhook"
DEFAULT_TIMEOUT = 15.0
MAX_RETRIES = 3


def _is_enabled() -> bool:
    return (get_secret("MAKE_WEBHOOK_ENABLED", "false") or "").lower() in ("1", "true", "yes")


def _get_url() -> str:
    return (get_secret("MAKE_WEBHOOK_URL", "") or "").strip().rstrip("/")


def _get_timeout() -> float:
    try:
        return float(get_secret("MAKE_WEBHOOK_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def _get_phone() -> str:
    return (get_secret("MAKE_WEBHOOK_PHONE", "") or "").strip()


@dataclass
class MakeWebhookResult:
    publication_id: Optional[int] = None
    status: str = "pending"  # sent | failed | dry_run | skipped
    external_id: Optional[str] = None
    dry_run: bool = False
    attempts: int = 0
    last_error: Optional[str] = None


def _log_publication(
    session: Session,
    channel: str,
    status: str,
    external_id: Optional[str] = None,
    published_at: Optional[datetime] = None,
    attempts: int = 0,
) -> int:
    from apps.api.db.models import Publication

    row = Publication(
        channel=channel,
        status=status,
        external_id=external_id,
        published_at=published_at,
        attempts=attempts,
    )
    session.add(row)
    session.flush()
    return row.id


def _post_with_retries(
    url: str,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[bool, Optional[str], int, Optional[str]]:
    """POST JSON with up to 3 retries. Returns (ok, external_id_or_none, attempts, error)."""
    if not httpx:
        return False, None, 0, "httpx not installed"
    last_error: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload)
            if 200 <= resp.status_code < 300:
                try:
                    data = resp.json()
                    ext_id = str(data.get("id", "")) if isinstance(data, dict) and data.get("id") else None
                except Exception:
                    ext_id = None
                return True, ext_id, attempt, None
            last_error = f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"
        except Exception as e:
            last_error = str(e)[:500]
        if attempt < MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))
    return False, None, MAX_RETRIES, last_error


def send_make_webhook(
    session: Session,
    item: Any,
    rendered_text: str,
    dry_run: bool = False,
) -> MakeWebhookResult:
    """
    Send to Make.com webhook. Payload: {phone, message, source, item_id}.
    When disabled or dry_run, returns dry_run/skipped. 3 retries, timeout from env.
    """
    now = datetime.now(timezone.utc)
    item_id = item.id if hasattr(item, "id") else None
    url = _get_url()
    enabled = _is_enabled()

    if dry_run or not enabled or not url:
        pub_id = _log_publication(
            session, CHANNEL, "dry_run" if dry_run else "skipped",
            external_id=None, published_at=now, attempts=0
        )
        return MakeWebhookResult(
            publication_id=pub_id,
            status="dry_run" if dry_run else "skipped",
            dry_run=True,
            attempts=0,
        )

    payload = {
        "phone": _get_phone(),
        "message": rendered_text,
        "source": "gni",
        "item_id": item_id,
    }
    timeout = _get_timeout()
    ok, external_id, attempts, err = _post_with_retries(url, payload, timeout)

    if ok:
        pub_id = _log_publication(
            session, CHANNEL, "sent",
            external_id=external_id,
            published_at=now,
            attempts=attempts,
        )
        return MakeWebhookResult(
            publication_id=pub_id,
            status="sent",
            external_id=external_id,
            dry_run=False,
            attempts=attempts,
        )

    _log_publication(
        session, CHANNEL, "failed",
        external_id=None,
        published_at=now,
        attempts=attempts,
    )
    return MakeWebhookResult(
        status="failed",
        dry_run=False,
        attempts=attempts,
        last_error=err,
    )
