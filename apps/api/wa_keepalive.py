"""
WhatsApp QR keepalive: background task that checks connection, triggers reconnect
when disconnected, polls for QR and caches in Redis. Runs inside API lifespan.
"""
import asyncio
import logging
import random
import time
from typing import Optional

import httpx
from apps.api.settings_utils import env_int
from apps.shared.config import REDIS_URL_DEFAULT, WHATSAPP_BOT_BASE_URL_DEFAULT
from apps.shared.secrets import get_secret

from apps.api.wa_qr_cache import set_cached_qr

logger = logging.getLogger(__name__)

WA_KEEPALIVE_INTERVAL = env_int("WA_KEEPALIVE_INTERVAL_SECONDS", default=25)
WA_RECONNECT_BACKOFF = env_int("WA_RECONNECT_BACKOFF_SECONDS", default=30)
WA_QR_POLL_TIMEOUT = 90
WA_QR_POLL_INTERVAL = 5


def _bot_base_url() -> str:
    return (get_secret("WA_BOT_BASE_URL", WHATSAPP_BOT_BASE_URL_DEFAULT) or "").strip().rstrip("/")


def _has_wa_config() -> bool:
    """True if WA bridge is configured (bot URL set)."""
    return bool(_bot_base_url())


async def _get_status(client: httpx.AsyncClient) -> Optional[dict]:
    try:
        r = await client.get(f"{_bot_base_url()}/status", timeout=5.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


async def _trigger_reconnect(client: httpx.AsyncClient) -> bool:
    try:
        r = await client.post(f"{_bot_base_url()}/reconnect", timeout=15.0)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.warning("wa_keepalive: reconnect failed: %s", e)
        return False


async def _fetch_qr_from_bot(client: httpx.AsyncClient) -> Optional[str]:
    try:
        r = await client.get(f"{_bot_base_url()}/qr", timeout=5.0)
        r.raise_for_status()
        data = r.json()
        return data.get("qr") if isinstance(data, dict) else None
    except Exception:
        return None


async def _run_keepalive_cycle() -> None:
    """One cycle: check status, reconnect if needed, poll for QR, cache."""
    if not _has_wa_config():
        return
    async with httpx.AsyncClient() as client:
        status = await _get_status(client)
        if not status:
            return
        status_name = str(status.get("status") or "").strip().lower()
        connected = bool(status.get("connected")) or status_name == "connected"
        # Do not reconnect while bot is already connecting, has a QR, or is in cooldown.
        if connected or status_name in ("qr_ready", "not_ready"):
            return
        if bool(status.get("in_cooldown")):
            return
        # Disconnected: trigger reconnect
        ok = await _trigger_reconnect(client)
        if not ok:
            return
        # Poll for QR up to WA_QR_POLL_TIMEOUT
        deadline = time.monotonic() + WA_QR_POLL_TIMEOUT
        while time.monotonic() < deadline:
            await asyncio.sleep(WA_QR_POLL_INTERVAL)
            qr = await _fetch_qr_from_bot(client)
            if qr:
                set_cached_qr(qr)
                logger.info("wa_keepalive: QR cached after reconnect")
                return


async def run_keepalive_loop() -> None:
    """
    Main loop: run keepalive cycle every WA_KEEPALIVE_INTERVAL with backoff on errors.
    Never exits normally; on exception log and continue after backoff.
    """
    logger.info("wa_keepalive: started (interval=%ss)", WA_KEEPALIVE_INTERVAL)
    while True:
        try:
            if _has_wa_config():
                await _run_keepalive_cycle()
        except asyncio.CancelledError:
            logger.info("wa_keepalive: cancelled")
            raise
        except Exception as e:
            jitter = random.uniform(0, 5)
            backoff = WA_RECONNECT_BACKOFF + jitter
            logger.warning("wa_keepalive: error %s, backing off %.1fs", e, backoff)
            await asyncio.sleep(backoff)
            continue
        await asyncio.sleep(WA_KEEPALIVE_INTERVAL)
