"""
Telegram publisher: real Bot API sendMessage.
Uses TELEGRAM_BOT_TOKEN, TELEGRAM_TARGET_CHAT_ID. Splits messages over 4096 chars; retry with backoff.
Stores returned message_id(s) in DB publications (comma-separated if multiple).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = None  # type: ignore

from apps.worker.retry import PUBLISH_MAX_ATTEMPTS, run_with_retry

# Telegram sendMessage limit (use slightly under to avoid edge cases)
TELEGRAM_MAX_MESSAGE_LENGTH = 4090


@runtime_checkable
class PublisherProtocol(Protocol):
    """Interface for publishers: publish messages to a channel and log to DB."""

    def publish(
        self,
        messages: list[str],
        channel: str,
        dry_run: bool = True,
        session: Optional["Session"] = None,
    ) -> "PublicationResult":
        """Publish messages; log attempt to DB. Returns result with publication id and status."""
        ...


class PublicationResult:
    """Result of a publication attempt: id, status, external_id (if sent), attempts."""
    __slots__ = ("publication_id", "status", "external_id", "dry_run", "attempts")

    def __init__(
        self,
        publication_id: Optional[int] = None,
        status: str = "pending",
        external_id: Optional[str] = None,
        dry_run: bool = False,
        attempts: int = 0,
    ):
        self.publication_id = publication_id
        self.status = status
        self.external_id = external_id
        self.dry_run = dry_run
        self.attempts = attempts


def _get_session():
    """Lazy import to avoid requiring apps.api at module load."""
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from apps.api.db import SessionLocal
    return SessionLocal()


def _get_bot_token() -> str:
    from apps.shared.secrets import get_secret
    return get_secret("TELEGRAM_BOT_TOKEN")


def _get_target_chat_id() -> str:
    from apps.shared.secrets import get_secret
    return get_secret("TELEGRAM_TARGET_CHAT_ID") or get_secret("TELEGRAM_CHAT_ID")


def _log_publication(
    session: "Session",
    channel: str,
    status: str,
    external_id: Optional[str] = None,
    published_at: Optional[datetime] = None,
    attempts: int = 0,
) -> int:
    """Create a Publication row; return id. Caller must commit."""
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


def _split_message(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    """
    Split text so no part exceeds max_len. Preserve first line as header on continuation parts.
    """
    if not text or len(text) <= max_len:
        return [text] if text else []
    parts = []
    first_newline = text.find("\n")
    has_header = first_newline >= 0
    header = (text[: first_newline + 1]).strip() + "\n" if has_header else ""
    rest = text[first_newline + 1 :].lstrip("\n") if has_header else text
    chunk_size = max_len - len(header) if header else max_len
    if chunk_size <= 0:
        # Header alone too long; split by max_len only
        for i in range(0, len(text), max_len):
            parts.append(text[i : i + max_len])
        return parts
    pos = 0
    while pos < len(rest):
        chunk = rest[pos : pos + chunk_size]
        parts.append(header + chunk if header else chunk)
        pos += chunk_size
    return parts


def _normalize_messages_for_telegram(messages: list[str]) -> list[str]:
    """Ensure no single message exceeds Telegram limit; split with header preserved."""
    out: list[str] = []
    for msg in messages:
        if not msg:
            continue
        for part in _split_message(msg, TELEGRAM_MAX_MESSAGE_LENGTH):
            if part:
                out.append(part)
    return out


def _send_message(token: str, chat_id: str, text: str) -> str:
    """POST sendMessage to Telegram Bot API (plain text). Returns message_id. Raises on error."""
    if not httpx:
        raise RuntimeError("httpx not installed")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Plain text (no parse_mode) so separators and bullets render correctly
    payload = {"chat_id": chat_id, "text": text}
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"Telegram API {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data.get('description', 'unknown')}")
        result = data.get("result") or {}
        msg_id = result.get("message_id")
        if msg_id is None:
            raise RuntimeError("Telegram API: no message_id in response")
        return str(msg_id)


class TelegramPublisher:
    """
    Telegram publisher: real Bot API sendMessage when token/chat_id set.
    dry_run: print only, no send. Uses shared retry; stores message_id as external_id; increments attempts.
    """

    def publish(
        self,
        messages: list[str],
        channel: str,
        dry_run: bool = True,
        session: Optional["Session"] = None,
    ) -> PublicationResult:
        """
        Publish messages to TELEGRAM_TARGET_CHAT_ID via Bot API.
        dry_run: print only. Real send: retry with backoff, store message_id, log attempts.
        """
        own_session = False
        if session is None:
            session = _get_session()
            own_session = True
        token = _get_bot_token()
        chat_id = _get_target_chat_id()
        now = datetime.now(timezone.utc)

        try:
            if dry_run:
                for i, msg in enumerate(messages, 1):
                    try:
                        print(f"[telegram dry_run] {channel} part {i}/{len(messages)}:\n{msg}\n")
                    except UnicodeEncodeError:
                        safe_msg = msg.encode("ascii", errors="replace").decode("ascii")
                        print(f"[telegram dry_run] {channel} part {i}/{len(messages)}:\n{safe_msg}\n")
                pub_id = _log_publication(session, channel, "dry_run", external_id=None, published_at=now, attempts=0)
                if own_session:
                    session.commit()
                return PublicationResult(publication_id=pub_id, status="dry_run", dry_run=True, attempts=0)

            if not token or not chat_id:
                pub_id = _log_publication(session, channel, "dry_run", external_id=None, published_at=now, attempts=0)
                if own_session:
                    session.commit()
                return PublicationResult(publication_id=pub_id, status="dry_run", dry_run=True, attempts=0)

            # Normalize: split any message over Telegram limit (preserve headers)
            to_send = _normalize_messages_for_telegram(messages)
            message_ids: list[str] = []

            def _send_all() -> list[str]:
                from apps.worker.circuit_breaker import get_circuit_breaker

                cb = get_circuit_breaker("telegram")
                ids: list[str] = []
                for msg in to_send:
                    mid = cb.call(lambda m=msg: _send_message(token, chat_id, m))
                    ids.append(mid)
                return ids

            ok, result_or_err, attempts = run_with_retry(_send_all, max_attempts=PUBLISH_MAX_ATTEMPTS)
            try:
                from apps.observability.metrics import record_publish
                record_publish("telegram", "sent" if ok else "failed")
            except ImportError:
                pass
            if ok and isinstance(result_or_err, list) and result_or_err:
                message_ids = result_or_err
                external_id = ",".join(message_ids)
                pub_id = _log_publication(
                    session, channel, "sent",
                    external_id=external_id,
                    published_at=now,
                    attempts=attempts,
                )
                if own_session:
                    session.commit()
                return PublicationResult(
                    publication_id=pub_id,
                    status="sent",
                    external_id=external_id,
                    dry_run=False,
                    attempts=attempts,
                )
            # Failed after retries
            err_str = str(result_or_err) if isinstance(result_or_err, Exception) else (result_or_err or "unknown")
            pub_id = _log_publication(session, channel, "failed", external_id=None, published_at=now, attempts=attempts)
            if own_session:
                session.commit()
            raise RuntimeError(f"Telegram send failed after {attempts} attempts: {err_str}")
        except Exception:
            if own_session:
                session.rollback()
            raise
        finally:
            if own_session and session:
                session.close()


def publish_telegram(
    messages: list[str],
    channel: str = "telegram",
    dry_run: bool = True,
    session: Optional["Session"] = None,
) -> PublicationResult:
    """Convenience: publish messages via Telegram Bot API. Logs to publications."""
    return TelegramPublisher().publish(messages, channel, dry_run=dry_run, session=session)
