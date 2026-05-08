"""
WhatsApp Web channel: POST to whatsapp-bot /send (internal service).
Logs publication status (success/fail/dry_run) and attempts in DB.
Respects pause_all_publish at pipeline level (caller does not invoke when paused).
"""
from __future__ import annotations

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

CHANNEL = "whatsapp_web"
DEFAULT_TIMEOUT = 30.0


def _get_base_url() -> str:
    """WHATSAPP_BOT_BASE_URL (e.g. http://whatsapp-bot:3100). Empty = skip send."""
    return (get_secret("WHATSAPP_BOT_BASE_URL", "") or "").strip().rstrip("/")


@dataclass
class WhatsAppWebResult:
    """Result of a whatsapp_web publish attempt."""

    publication_id: Optional[int] = None
    status: str = "pending"  # sent | failed | dry_run | blocked
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


def _post_send(
    base_url: str,
    text: str,
    idempotency_key: str,
    meta: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[bool, Optional[str], Optional[str], int]:
    """
    POST to whatsapp-bot /send. Returns (ok, message_ids_str, error, status_code).
    message_ids_str: first message_id or comma-separated for external_id storage.
    """
    if not httpx:
        raise RuntimeError("httpx not installed")
    url = f"{base_url}/send"
    payload = {
        "text": text,
        "idempotency_key": idempotency_key,
        "meta": meta,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)
        try:
            data = resp.json() if resp.content else {}
        except Exception:
            data = {}
        ok = data.get("ok") is True and resp.status_code == 200
        message_ids = data.get("message_ids")
        if isinstance(message_ids, list) and message_ids:
            external_id = message_ids[0] if len(message_ids) == 1 else ",".join(str(x) for x in message_ids)
        else:
            external_id = None
        err = data.get("error") or (None if ok else resp.text[:500])
        return ok, external_id, err, resp.status_code


def send_whatsapp_web(
    session: Session,
    item: Any,
    rendered_text: str,
    template: str,
    dry_run: bool = False,
) -> WhatsAppWebResult:
    """
    Send rendered message to WhatsApp group via whatsapp-bot.
    POST /send with text, idempotency_key = "whatsapp_web:<item_id>:<template>", meta.
    Logs Publication row (channel=whatsapp_web, status=sent|failed|dry_run), including attempts.
    """
    base_url = _get_base_url()
    now = datetime.now(timezone.utc)
    item_id = item.id if hasattr(item, "id") else None
    source = (item.source_name or "").strip() if hasattr(item, "source_name") else ""
    url = (item.url or "").strip() if hasattr(item, "url") else ""
    meta = {"source": source, "url": url, "item_id": item_id}
    idempotency_key = f"whatsapp_web:{item_id}:{template or 'DEFAULT'}"

    if dry_run or not base_url:
        pub_id = _log_publication(
            session, CHANNEL, "dry_run", external_id=None, published_at=now, attempts=0
        )
        return WhatsAppWebResult(
            publication_id=pub_id,
            status="dry_run",
            dry_run=True,
            attempts=0,
        )

    attempts = 1
    ok, external_id, err, status_code = _post_send(
        base_url, rendered_text, idempotency_key, meta
    )

    if ok:
        pub_id = _log_publication(
            session, CHANNEL, "sent",
            external_id=external_id,
            published_at=now,
            attempts=attempts,
        )
        return WhatsAppWebResult(
            publication_id=pub_id,
            status="sent",
            external_id=external_id,
            dry_run=False,
            attempts=attempts,
        )

    err_str = err or f"HTTP {status_code}"
    pub_id = _log_publication(
        session, CHANNEL, "failed",
        external_id=None,
        published_at=now,
        attempts=attempts,
    )
    return WhatsAppWebResult(
        publication_id=pub_id,
        status="failed",
        dry_run=False,
        attempts=attempts,
        last_error=err_str,
    )
