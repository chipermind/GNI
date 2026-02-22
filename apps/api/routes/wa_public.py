"""
Public WhatsApp QR endpoints under /wa/* for UI access (X-API-Key auth).
Deterministic API contract: GET /wa/status unchanged; GET /wa/qr returns { qr, updated_at, connected }; POST /wa/connect triggers QR generation.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from apps.api.auth import require_auth
from apps.api.routes.wa_bridge import (
    _do_reconnect,
    _fetch_qr,
    _fetch_status,
)

wa_public_router = APIRouter(
    prefix="/wa",
    tags=["wa-public"],
    dependencies=[Depends(require_auth)],
)


@wa_public_router.get(
    "/status",
    summary="WhatsApp connection status",
    description="Returns connected, status, lastDisconnectReason, server_time. Use X-API-Key header.",
)
async def wa_public_status() -> dict:
    """GET /wa/status — proxy to whatsapp-bot health. Auth: X-API-Key or Bearer JWT."""
    return await _fetch_status()


@wa_public_router.post(
    "/connect",
    summary="Trigger WhatsApp QR generation",
    description="Triggers bot to start / ensure QR generation is active. Use X-API-Key header.",
)
async def wa_public_connect() -> dict:
    """POST /wa/connect — triggers bot to start or ensure QR generation. Auth: X-API-Key or Bearer JWT."""
    return await _do_reconnect()


@wa_public_router.get(
    "/qr",
    summary="Get WhatsApp QR code",
    description='Returns JSON: { "qr": "<string or null>", "updated_at": "<ISO8601>", "connected": <bool> }. Use X-API-Key header.',
)
async def wa_public_qr() -> dict:
    """GET /wa/qr — deterministic contract: qr (string or null), updated_at (ISO8601), connected (bool). Auth: X-API-Key or Bearer JWT."""
    status_out = await _fetch_status()
    if status_out.get("connected"):
        return {
            "qr": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "connected": True,
        }
    qr_out = await _fetch_qr()
    ts = qr_out.get("ts")
    updated_at = (
        datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if ts is not None
        else datetime.now(timezone.utc).isoformat()
    )
    qr_val = qr_out.get("qr")
    return {
        "qr": qr_val if qr_val else None,
        "updated_at": updated_at,
        "connected": False,
    }
