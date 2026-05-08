"""
Secure WhatsApp QR Bridge: proxy status and QR from internal whatsapp-bot to a remote UI
(Streamlit Cloud) without exposing the bot service. All endpoints require Bearer token.

Routes:
  /admin/wa/* — admin routes (primary)
  /wa/*       — public aliases (same auth) for backward compatibility with clients
                expecting /wa/status, /wa/connect, /wa/qr
"""
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from apps.api.settings_utils import env_int
from apps.shared.config import WHATSAPP_BOT_BASE_URL_DEFAULT
from apps.shared.secrets import get_secret

logger = logging.getLogger(__name__)

# Ensure bot base URL uses http://whatsapp-bot:3100 (not localhost/127.0.0.1)
WA_BOT_BASE_URL = get_secret("WA_BOT_BASE_URL", WHATSAPP_BOT_BASE_URL_DEFAULT).rstrip("/")
if not WA_BOT_BASE_URL or "localhost" in WA_BOT_BASE_URL or "127.0.0.1" in WA_BOT_BASE_URL:
    WA_BOT_BASE_URL = WHATSAPP_BOT_BASE_URL_DEFAULT.rstrip("/")
    logger.warning("wa_bridge: WA_BOT_BASE_URL contained localhost, using default: %s", WA_BOT_BASE_URL)

WA_QR_BRIDGE_TOKEN = get_secret("WA_QR_BRIDGE_TOKEN", "").strip()
WA_QR_TTL_SECONDS = env_int("WA_QR_TTL_SECONDS", default=120)
WA_QR_RATE_LIMIT_PER_MINUTE = env_int("WA_QR_RATE_LIMIT_PER_MINUTE", default=90)
WA_BRIDGE_CACHE_TTL_SECONDS = env_int("WA_BRIDGE_CACHE_TTL_SECONDS", default=3)
WA_BOT_TIMEOUT_SECONDS = 20.0  # Bot can take 10–30s to generate QR after reconnect

http_bearer = HTTPBearer(auto_error=False)

# In-memory rate limit for /admin/wa/qr: IP -> list of request timestamps (last minute)
_qr_rate: dict[str, list[float]] = defaultdict(list)
_bridge_cache: dict[str, tuple[float, dict]] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune_and_count(ip: str) -> int:
    now = time.monotonic()
    cutoff = now - 60.0
    _qr_rate[ip] = [t for t in _qr_rate[ip] if t > cutoff]
    return len(_qr_rate[ip])


async def require_wa_bridge_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
) -> None:
    """Require Authorization: Bearer <WA_QR_BRIDGE_TOKEN>. 401 if missing or invalid."""
    if not WA_QR_BRIDGE_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="WhatsApp QR Bridge is not configured (WA_QR_BRIDGE_TOKEN not set)",
        )
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if credentials.credentials.strip() != WA_QR_BRIDGE_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# --- Shared handlers (no router path): used by both /admin/wa and /wa ---
# Security: both use same Bearer auth. No weakening of auth for /wa/*.


async def _fetch_netcheck() -> dict:
    """Proxy to whatsapp-bot GET /netcheck. Returns {ok, status_code, error, server_time}."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{WA_BOT_BASE_URL}/netcheck")
            data = r.json() if r.content else {}
    except Exception as e:
        logger.warning("wa_bridge: netcheck error: %s", type(e).__name__)
        return {"ok": False, "status_code": None, "error": str(e)[:200], "server_time": now}
    return {
        "ok": data.get("ok", False),
        "status_code": data.get("status_code"),
        "error": data.get("error"),
        "server_time": data.get("server_time", now),
    }


async def _fetch_status() -> dict:
    """
    Proxy to whatsapp-bot /status (not /health). 
    Returns connected, status ("not_ready"|"qr_ready"|"connected"|"disconnected"), lastDisconnectReason, server_time.
    """
    now_mono = time.monotonic()
    cached = _bridge_cache.get("status")
    if cached and (now_mono - cached[0] < WA_BRIDGE_CACHE_TTL_SECONDS):
        return cached[1]

    now = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=WA_BOT_TIMEOUT_SECONDS) as client:
            r = await client.get(f"{WA_BOT_BASE_URL}/status")
            r.raise_for_status()
            data = r.json()
    except httpx.TimeoutException:
        logger.warning("wa_bridge: whatsapp-bot status timeout")
        out = {
            "connected": False,
            "status": "not_ready",
            "lastDisconnectReason": None,
            "server_time": now,
        }
        _bridge_cache["status"] = (now_mono, out)
        return out
    except Exception as e:
        logger.warning("wa_bridge: whatsapp-bot status error: %s", type(e).__name__)
        out = {
            "connected": False,
            "status": "not_ready",
            "lastDisconnectReason": None,
            "server_time": now,
        }
        _bridge_cache["status"] = (now_mono, out)
        return out
    
    # Bot returns: { connected: bool, status: "connected|qr_ready|not_ready|disconnected", lastDisconnectReason, server_time }
    # Use bot's status field directly (bot already maps it correctly)
    bot_status = data.get("status", "disconnected")
    bot_connected = data.get("connected", False)
    
    # Ensure status is one of the expected values
    if bot_status not in ("connected", "qr_ready", "not_ready", "disconnected"):
        # Fallback: use connected flag to determine status
        if bot_connected:
            status = "connected"
        else:
            status = "disconnected"
    else:
        status = bot_status
    
    out = {
        "connected": bot_connected,
        "status": status,
        "lastDisconnectReason": data.get("lastDisconnectReason"),
        "server_time": now,
    }
    _bridge_cache["status"] = (now_mono, out)
    return out


def _fetch_qr_sync() -> dict:
    """
    Fetch QR from bot with caching: Redis -> in-memory -> file -> bot.
    Returns: { "qr": str|null, "status": "qr_ready"|"not_ready"|"connected", "ts": unix_ts?, "expires_in", "server_time" }
    """
    import time
    from apps.api.wa_qr_cache import get_cached_qr, set_cached_qr

    now = datetime.now(timezone.utc).isoformat()
    now_ts = time.time()

    # 1) Check cache (Redis -> in-memory -> file)
    cached = get_cached_qr()
    if cached:
        qr_str, ts = cached
        return {
            "qr": qr_str,
            "status": "qr_ready",
            "ts": int(ts),
            "expires_in": WA_QR_TTL_SECONDS,
            "server_time": now,
        }

    # 2) Proxy to bot GET /qr
    try:
        import httpx
        with httpx.Client(timeout=WA_BOT_TIMEOUT_SECONDS) as client:
            r = client.get(f"{WA_BOT_BASE_URL}/qr")
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning("wa_bridge: whatsapp-bot qr error: %s", type(e).__name__)
        return {"qr": None, "status": "not_ready", "expires_in": 0, "server_time": now}

    bot_status = data.get("status", "not_ready")
    qr = data.get("qr")
    
    # Map bot status to our status
    if bot_status == "connected":
        # Bot is connected, no QR needed
        return {
            "qr": None,
            "status": "connected",
            "expires_in": 0,
            "server_time": now,
        }
    
    if bot_status == "qr_ready" and qr:
        # Bot has QR ready, cache it and return
        # ALWAYS cache QR when received from bot
        set_cached_qr(qr, ttl=WA_QR_TTL_SECONDS)
        logger.debug("wa_bridge: cached QR from bot (length: %d)", len(qr))
        return {
            "qr": qr,
            "status": "qr_ready",
            "ts": int(now_ts),
            "expires_in": WA_QR_TTL_SECONDS,
            "server_time": now,
        }
    
    # Bot says not_ready or disconnected, keep polling
    return {
        "qr": None,
        "status": bot_status if bot_status in ("not_ready", "disconnected") else "not_ready",
        "expires_in": 0,
        "server_time": now,
    }


async def _fetch_qr() -> dict:
    """Async wrapper: run sync _fetch_qr_sync in thread pool."""
    now_mono = time.monotonic()
    cached = _bridge_cache.get("qr")
    if cached and (now_mono - cached[0] < WA_BRIDGE_CACHE_TTL_SECONDS):
        return cached[1]
    import asyncio
    out = await asyncio.to_thread(_fetch_qr_sync)
    _bridge_cache["qr"] = (now_mono, out)
    return out


async def _do_reconnect(wipe_auth: bool = False) -> dict:
    """
    Trigger whatsapp-bot to logout and reconnect, generating a new QR.
    Non-blocking: triggers reconnect and returns quickly (< 2s).
    QR will be available via polling /admin/wa/qr.
    """
    now = datetime.now(timezone.utc).isoformat()
    _bridge_cache.pop("status", None)
    _bridge_cache.pop("qr", None)
    
    # Trigger reconnect (fire-and-forget with short timeout)
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            payload = {"wipe_auth": True} if wipe_auth else {}
            r = await client.post(f"{WA_BOT_BASE_URL}/reconnect", json=payload)
            r.raise_for_status()
            # Bot handles reconnect internally and returns quickly
            # No need to wait here - return immediately
            logger.info("wa_bridge: reconnect triggered successfully")
            return {"ok": True, "message": "Reconnect triggered. Poll /admin/wa/qr for QR code.", "server_time": now}
    except httpx.TimeoutException:
        # Even if timeout, reconnect may have been triggered
        logger.info("wa_bridge: reconnect request sent (timeout OK, bot may be processing)")
        return {"ok": True, "message": "Reconnect triggered. Poll /admin/wa/qr for QR code.", "server_time": now}
    except Exception as e:
        logger.warning("wa_bridge: whatsapp-bot reconnect error: %s", type(e).__name__)
        return {"ok": False, "error": str(e)[:100], "server_time": now}


# --- /admin/wa/* router: requires Bearer token (WA_QR_BRIDGE_TOKEN) ---
router = APIRouter(prefix="/admin/wa", tags=["wa-bridge"], dependencies=[Depends(require_wa_bridge_token)])


@router.get("/status")
async def wa_status() -> dict:
    """
    Get WhatsApp connection status.
    Returns: {connected: bool, status: "not_ready"|"qr_ready"|"connected", lastDisconnectReason: str|null, server_time: str}
    """
    return await _fetch_status()


@router.get("/qr")
async def wa_qr() -> dict:
    """Proxy to whatsapp-bot /qr."""
    return await _fetch_qr()


@router.get("/netcheck")
async def wa_netcheck() -> dict:
    """Proxy to whatsapp-bot /netcheck (network connectivity to WhatsApp)."""
    return await _fetch_netcheck()


@router.post("/reconnect")
async def wa_reconnect(payload: Optional[dict] = Body(default=None)) -> dict:
    """Trigger whatsapp-bot reconnect."""
    wipe_auth = bool(payload and payload.get("wipe_auth") is True)
    return await _do_reconnect(wipe_auth=wipe_auth)
