"""
Make webhook publisher for WhatsApp: POST JSON to MAKE_WEBHOOK_URL.
Runs in dry_run when URL not set. Retries with exponential backoff; logs success/failure in events_log.
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

from apps.shared.env_helpers import parse_int
from apps.shared.secrets import get_secret

MAKE_CHANNEL = "make"
MAKE_SUCCESS_EVENT = "make_publish_success"
MAKE_FAILURE_EVENT = "make_publish_failure"
DEAD_LETTER_EVENT = "make_dead_letter"


def _get_webhook_url() -> str:
    return get_secret("MAKE_WEBHOOK_URL")


def _get_timeout() -> float:
    return float(get_secret("MAKE_WEBHOOK_TIMEOUT_SECONDS", "15"))


def _get_max_attempts() -> int:
    return parse_int(get_secret("MAKE_WEBHOOK_MAX_ATTEMPTS", ""), default=5, min_val=1, name="MAKE_WEBHOOK_MAX_ATTEMPTS")


def _get_backoff_base() -> float:
    return float(get_secret("MAKE_WEBHOOK_BACKOFF_BASE_SECONDS", "2"))


@dataclass
class MakePayload:
    """Payload sent to Make webhook per spec: channel, text, template, priority, meta."""

    text: str
    template: str
    priority: str
    source: str
    url: str
    item_id: Optional[int] = None

    def to_json(self) -> dict[str, Any]:
        """Produce payload exactly: {channel, text, template, priority, meta}."""
        return {
            "channel": "whatsapp",
            "text": self.text,
            "template": self.template or "ANALISE_INTEL",
            "priority": self.priority or "P2",
            "meta": {
                "source": self.source or "",
                "url": self.url or "",
                "item_id": self.item_id,
            },
        }


@dataclass
class MakePublishResult:
    """Result of a Make publish attempt."""

    publication_id: Optional[int] = None
    status: str = "pending"  # dry_run | sent | failed | dead_letter
    external_id: Optional[str] = None
    dry_run: bool = False
    attempts: int = 0
    last_error: Optional[str] = None


def _get_session():
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from apps.api.db import SessionLocal

    return SessionLocal()


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


def _log_event(session: Session, event_type: str, payload: dict[str, Any]) -> None:
    from apps.api.db.models import EventsLog

    row = EventsLog(event_type=event_type, payload=payload)
    session.add(row)


def _do_post(url: str, payload: dict[str, Any], timeout: float) -> Optional[str]:
    """POST JSON; return external_id from response or None. Raises on non-2xx or connection error."""
    if not httpx:
        raise RuntimeError("httpx not installed")
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            if isinstance(data, dict) and "id" in data:
                return str(data["id"])
        except Exception:
            pass
        return None


def _post_with_retries(
    url: str,
    payload: dict[str, Any],
) -> tuple[bool, Optional[str], int]:
    """
    POST JSON with exponential backoff. Uses MAKE_WEBHOOK_MAX_ATTEMPTS and MAKE_WEBHOOK_BACKOFF_BASE_SECONDS.
    Returns (success, external_id_or_error, attempts).
    """
    from apps.worker.circuit_breaker import get_circuit_breaker

    max_attempts = _get_max_attempts()
    backoff_base = _get_backoff_base()
    timeout = _get_timeout()
    cb = get_circuit_breaker("make")

    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = cb.call(lambda: _do_post(url, payload, timeout))
            return True, result, attempt
        except Exception as e:
            last_error = e
            from apps.worker.circuit_breaker import CircuitOpenError

            if isinstance(e, CircuitOpenError):
                return False, e, attempt
        if attempt < max_attempts:
            time.sleep(backoff_base * (2 ** (attempt - 1)))
    return False, last_error or RuntimeError("retry exhausted"), max_attempts


def send_whatsapp_via_make(
    session: Session,
    item: Any,
    rendered_text: str,
    template: str,
    priority: str,
    dry_run: bool = False,
    messages: Optional[list[str]] = None,
) -> MakePublishResult:
    """
    Send WhatsApp message via Make webhook. POST with exact payload spec.
    When messages (list) provided, sends one POST per part (character limit handling).
    Increments attempts on each retry; logs success/failure in events_log.
    """
    webhook_url = _get_webhook_url()
    now = datetime.now(timezone.utc)

    # Use messages list if provided (split for char limit); else single text
    parts = messages if messages else [rendered_text]
    meta = {
        "source": (item.source_name or "").strip() if hasattr(item, "source_name") else "",
        "url": (item.url or "").strip() if hasattr(item, "url") else "",
        "item_id": item.id if hasattr(item, "id") else None,
    }

    if dry_run or not webhook_url:
        for i, part in enumerate(parts):
            payload = {
                "channel": "whatsapp",
                "text": part,
                "template": template or "ANALISE_INTEL",
                "priority": priority or "P2",
                "meta": meta,
            }
            print(f"[make dry_run] part {i+1}/{len(parts)} payload: {payload}")
        pub_id = _log_publication(session, MAKE_CHANNEL, "dry_run", external_id=None, published_at=now, attempts=0)
        _log_event(
            session,
            "make_dry_run",
            {"item_id": item.id if hasattr(item, "id") else None, "parts": len(parts)},
        )
        return MakePublishResult(
            publication_id=pub_id,
            status="dry_run",
            dry_run=True,
            attempts=0,
        )

    # Send one POST per part (message splitting; each part under char limit)
    ok = True
    ext_or_err = None
    attempts = 0
    for part in parts:
        payload = {
            "channel": "whatsapp",
            "text": part,
            "template": template or "ANALISE_INTEL",
            "priority": priority or "P2",
            "meta": meta,
        }
        ok_part, ext_or_err, attempts = _post_with_retries(webhook_url, payload)
        if not ok_part:
            ok = False
            break

    try:
        from apps.observability.metrics import record_publish

        record_publish("make", "sent" if ok else "failed")
    except ImportError:
        pass

    if ok:
        pub_id = _log_publication(
            session,
            MAKE_CHANNEL,
            "sent",
            external_id=ext_or_err,
            published_at=now,
            attempts=attempts,
        )
        _log_event(
            session,
            MAKE_SUCCESS_EVENT,
            {
                "item_id": item.id if hasattr(item, "id") else None,
                "publication_id": pub_id,
                "attempts": attempts,
                "external_id": ext_or_err,
            },
        )
        return MakePublishResult(
            publication_id=pub_id,
            status="sent",
            external_id=ext_or_err,
            dry_run=False,
            attempts=attempts,
        )

    err_str = str(ext_or_err) if isinstance(ext_or_err, Exception) else (ext_or_err or "unknown")
    _log_publication(session, MAKE_CHANNEL, "dead_letter", external_id=None, published_at=now, attempts=attempts)
    _log_event(
        session,
        MAKE_FAILURE_EVENT,
        {
            "item_id": item.id if hasattr(item, "id") else None,
            "attempts": attempts,
            "error": err_str,
            "payload": payload,
        },
    )
    _log_event(
        session,
        DEAD_LETTER_EVENT,
        {"payload": payload, "attempts": attempts, "last_error": err_str},
    )
    return MakePublishResult(
        publication_id=None,
        status="dead_letter",
        dry_run=False,
        attempts=attempts,
        last_error=err_str,
    )


def publish_make(
    payload: "MakePayload",
    dry_run: Optional[bool] = None,
    session: Optional[Session] = None,
) -> MakePublishResult:
    """
    Publish to Make webhook (legacy). Builds minimal item-like object and calls send_whatsapp_via_make.
    """
    webhook_url = _get_webhook_url()
    if dry_run is None:
        dry_run = not bool(webhook_url)
    own_session = False
    if session is None:
        session = _get_session()
        own_session = True

    class _FakeItem:
        id = payload.item_id
        source_name = payload.source
        url = payload.url

    try:
        result = send_whatsapp_via_make(
            session,
            _FakeItem(),
            payload.text,
            payload.template,
            payload.priority,
            dry_run=dry_run,
        )
        if own_session:
            session.commit()
        return result
    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session and session:
            session.close()


def publish_make_simple(
    text: str,
    template: str = "",
    priority: str = "P2",
    source: str = "",
    url: str = "",
    item_id: Optional[int] = None,
    dry_run: Optional[bool] = None,
    session: Optional[Session] = None,
) -> MakePublishResult:
    """Convenience: build MakePayload and call publish_make."""
    payload = MakePayload(
        text=text,
        template=template,
        priority=priority,
        source=source,
        url=url,
        item_id=item_id,
    )
    return publish_make(payload, dry_run=dry_run, session=session)
