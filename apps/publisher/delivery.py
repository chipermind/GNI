"""
Delivery fallback: try WhatsApp first when connected; else send via Telegram (webhook or Bot API).
Idempotency: message_id stored in Redis so retries do not send twice.
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

DELIVERY_SENT_TTL_DAYS = 7


@dataclass
class DeliveryResult:
    ok: bool
    channel: Optional[str] = None  # "wa" | "telegram" | None
    used_fallback: bool = False


def _get_wa_base_url() -> str:
    from apps.shared.config import WHATSAPP_BOT_BASE_URL_DEFAULT
    return (get_secret("WHATSAPP_BOT_BASE_URL", WHATSAPP_BOT_BASE_URL_DEFAULT) or "").strip().rstrip("/")


def _get_telegram_webhook_url() -> str:
    return (get_secret("TELEGRAM_WEBHOOK_URL", "") or "").strip().rstrip("/")


def get_wa_connected(timeout: float = 5.0) -> bool:
    """Return True if whatsapp-bot reports connected. Sync GET /status."""
    base = _get_wa_base_url()
    if not base:
        return False
    if not httpx:
        return False
    try:
        r = httpx.get(f"{base}/status", timeout=timeout)
        if r.status_code != 200:
            return False
        data = r.json() if r.content else {}
        return bool(data.get("connected"))
    except Exception:
        return False


def _redis_set_sent(message_id: str, channel: str) -> None:
    try:
        import os
        import redis
        url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        client = redis.Redis.from_url(url)
        key = f"delivery:sent:{message_id}"
        ttl = DELIVERY_SENT_TTL_DAYS * 86400
        client.setex(key, ttl, channel)
    except Exception:
        pass


def _redis_get_sent(message_id: str) -> Optional[str]:
    try:
        import os
        import redis
        url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        client = redis.Redis.from_url(url)
        key = f"delivery:sent:{message_id}"
        val = client.get(key)
        return val.decode("utf-8") if isinstance(val, bytes) else (val or None)
    except Exception:
        return None


def send_telegram_webhook(url: str, payload: dict[str, Any], timeout: float = 15.0) -> bool:
    """POST JSON to Telegram webhook. Returns True on 2xx."""
    if not httpx or not url:
        return False
    try:
        r = httpx.post(url, json=payload, timeout=timeout)
        return 200 <= r.status_code < 300
    except Exception:
        return False


def deliver_message(
    session: Session,
    *,
    message_id: Optional[str],
    messages: list[str],
    item: Any,
    template: str,
    dry_run: bool,
) -> DeliveryResult:
    """
    Try WhatsApp first only if connected; if not connected or send fails, send via Telegram.
    Uses TELEGRAM_WEBHOOK_URL if set, else TELEGRAM_BOT_TOKEN + TELEGRAM_TARGET_CHAT_ID.
    Idempotency: if message_id is set and already delivered (Redis), return success without sending.
    Records delivery channel in logs and Publication rows via underlying publishers.
    """
    from apps.publisher.whatsapp_web import send_whatsapp_web, WhatsAppWebResult
    from apps.publisher.gni_sender import gni_send

    rendered_text = "\n---\n".join(messages) if messages else ""

    # Idempotency: already sent this message_id
    if message_id:
        existing = _redis_get_sent(message_id)
        if existing:
            return DeliveryResult(ok=True, channel=existing, used_fallback=(existing == "telegram"))

    if dry_run:
        return DeliveryResult(ok=True, channel="dry_run", used_fallback=False)

    wa_connected = get_wa_connected()
    used_fallback = False

    # Try WhatsApp first only if connected
    if wa_connected:
        try:
            wa_result = send_whatsapp_web(
                session, item,
                rendered_text=rendered_text,
                template=template or "DEFAULT",
                dry_run=False,
            )
            if wa_result.status == "sent":
                if message_id:
                    _redis_set_sent(message_id, "wa")
                _log_delivery_channel("wa", message_id)
                return DeliveryResult(ok=True, channel="wa", used_fallback=False)
        except Exception as wa_err:
            _log_delivery_fallback("wa", str(wa_err)[:200], message_id)
            used_fallback = True

    # WA not connected or send failed: send via Telegram
    webhook_url = _get_telegram_webhook_url()
    if webhook_url:
        payload = {
            "text": rendered_text,
            "message_id": message_id,
            "source": getattr(item, "source_name", "") or "",
            "item_id": getattr(item, "id", None),
        }
        if send_telegram_webhook(webhook_url, payload):
            if message_id:
                _redis_set_sent(message_id, "telegram")
            _log_publication_telegram(session, "telegram")
            _log_delivery_channel("telegram", message_id)
            return DeliveryResult(ok=True, channel="telegram", used_fallback=used_fallback)
    else:
        try:
            tg_result = gni_send(
                messages,
                meta={"source": getattr(item, "source_name", "") or ""},
                dry_run=False,
                session=session,
            )
            if tg_result.status == "sent":
                if message_id:
                    _redis_set_sent(message_id, "telegram")
                _log_delivery_channel("telegram", message_id)
                return DeliveryResult(ok=True, channel="telegram", used_fallback=used_fallback)
        except Exception as tg_err:
            _log_delivery_fallback("telegram", str(tg_err)[:200], message_id)

    return DeliveryResult(ok=False, channel=None, used_fallback=used_fallback)


def _log_publication_telegram(session: Session, channel: str) -> None:
    """Record a successful Telegram (webhook) send in publications."""
    try:
        from apps.api.db.models import Publication
        now = datetime.now(timezone.utc)
        session.add(Publication(channel=channel, status="sent", attempts=1, published_at=now))
        session.flush()
    except Exception:
        pass


def _log_delivery_channel(channel: str, message_id: Optional[str]) -> None:
    try:
        from apps.observability.logging import get_logger
        get_logger("apps.publisher.delivery").info(
            "delivery_channel_used",
            channel=channel,
            message_id=message_id,
        )
    except Exception:
        print(f"delivery_channel_used channel={channel} message_id={message_id}")


def _log_delivery_fallback(primary: str, error: str, message_id: Optional[str]) -> None:
    try:
        from apps.observability.logging import get_logger
        get_logger("apps.publisher.delivery").info(
            "delivery_fallback",
            primary=primary,
            error=error[:200],
            message_id=message_id,
        )
    except Exception:
        print(f"delivery_fallback primary={primary} error={error} message_id={message_id}")
