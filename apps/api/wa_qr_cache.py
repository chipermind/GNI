"""
WhatsApp QR cache: Redis -> in-memory -> file fallback.
Bridge writes QR when received from bot; GET /wa/qr reads from cache first.
Ensures QR is retrievable even if UI refreshes, Redis is down, or API restarts.
File cache reads from same mount as bot: /app/data/wa-auth/last_qr.json (or WA_QR_FILE_PATH).
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from apps.api.settings_utils import env_int
from apps.shared.config import REDIS_URL_DEFAULT
from apps.shared.secrets import get_secret

logger = logging.getLogger(__name__)

WA_QR_KEY = "wa:last_qr"
WA_QR_TS_KEY = "wa:last_qr_ts"
WA_QR_TTL = env_int("WA_QR_TTL_SECONDS", default=120)

# File cache path: same mount as bot's /data/wa-auth/last_qr.json
# Default: /app/data/wa-auth/last_qr.json (mounted from host ./data/wa-auth)
WA_QR_FILE_PATH = os.getenv("WA_QR_FILE_PATH", "/app/data/wa-auth/last_qr.json")

# In-memory fallback: (qr_string, timestamp, expires_at)
_memory_cache: Optional[tuple[str, float, float]] = None
_memory_lock = threading.Lock()
_file_lock = threading.Lock()


def _get_redis():
    """Lazy Redis client. Returns None if unavailable."""
    try:
        import redis
        url = get_secret("REDIS_URL", REDIS_URL_DEFAULT)
        if not url:
            return None
        return redis.Redis.from_url(url)
    except Exception as e:
        logger.debug("wa_qr_cache: redis unavailable: %s", e)
        return None


def get_cached_qr() -> Optional[tuple[str, float]]:
    """
    Return (qr_string, unix_ts) if cached, else None.
    Cache read order: Redis -> in-memory -> file.
    Thread-safe.
    """
    # 1) Try Redis first
    r = _get_redis()
    if r:
        try:
            qr = r.get(WA_QR_KEY)
            ts_raw = r.get(WA_QR_TS_KEY)
            if qr is not None:
                qr_str = qr.decode("utf-8") if isinstance(qr, bytes) else str(qr)
                ts = float(ts_raw.decode("utf-8")) if ts_raw else time.time()
                # Update in-memory and file cache from Redis
                with _memory_lock:
                    global _memory_cache
                    _memory_cache = (qr_str, ts, time.time() + WA_QR_TTL)
                _save_qr_to_file(qr_str, ts)
                return (qr_str, ts)
        except Exception as e:
            logger.debug("wa_qr_cache: redis get error: %s", e)
    
    # 2) Fallback to in-memory cache
    with _memory_lock:
        if _memory_cache:
            qr_str, ts, expires_at = _memory_cache
            if time.time() < expires_at:
                return (qr_str, ts)
            # Expired, clear it
            _memory_cache = None
    
    # 3) Fallback to file cache
    return _load_qr_from_file()


def _load_qr_from_file() -> Optional[tuple[str, float]]:
    """Load QR from file cache. Returns (qr_string, timestamp) or None."""
    with _file_lock:
        try:
            if not os.path.exists(WA_QR_FILE_PATH):
                return None
            
            with open(WA_QR_FILE_PATH, 'r', encoding='utf-8') as f:
                state = json.load(f)
            
            # Check if QR is still valid
            if state.get("status") == "qr_ready" and state.get("qr"):
                expires_at = state.get("expires_at")
                updated_at = state.get("updated_at")
                
                if expires_at and updated_at:
                    now = time.time()
                    if now < expires_at:
                        qr_str = state["qr"]
                        ts = float(updated_at)
                        # Update in-memory cache from file
                        with _memory_lock:
                            global _memory_cache
                            _memory_cache = (qr_str, ts, expires_at)
                        logger.debug("wa_qr_cache: loaded QR from file (expires in %ds)", int(expires_at - now))
                        return (qr_str, ts)
                    else:
                        logger.debug("wa_qr_cache: QR in file expired")
            
            return None
        except Exception as e:
            logger.debug("wa_qr_cache: file load error: %s", e)
            return None


def _save_qr_to_file(qr: str, ts: float) -> None:
    """
    Save QR to file cache. ALWAYS writes (best-effort, never fails silently).
    This ensures QR persists across API/bot/Redis restarts.
    Thread-safe.
    """
    with _file_lock:
        try:
            file_path = Path(WA_QR_FILE_PATH)
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            state = {
                "qr": qr,
                "status": "qr_ready",
                "expires_at": int(ts) + WA_QR_TTL,
                "updated_at": int(ts),
                "lastDisconnectReason": None,
            }
            
            # Write atomically: write to temp file, then rename
            temp_path = f"{WA_QR_FILE_PATH}.tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
            
            # Atomic rename (POSIX-compliant)
            os.replace(temp_path, WA_QR_FILE_PATH)
            logger.debug("wa_qr_cache: saved QR to file: %s", WA_QR_FILE_PATH)
        except Exception as e:
            logger.error("wa_qr_cache: file save error: %s", e)
            # Don't raise - file cache is best-effort, but log as error since it's critical


def set_cached_qr(qr: str, ttl: int = WA_QR_TTL) -> None:
    """
    Cache QR string with TTL. Write order: in-memory -> Redis (best-effort) -> file (always).
    Ensures QR persists even if Redis is temporarily unavailable or API restarts.
    Thread-safe.
    """
    if not qr:
        return
    
    ts = time.time()
    expires_at = ts + ttl
    
    # 1) Update in-memory cache immediately
    with _memory_lock:
        global _memory_cache
        _memory_cache = (qr, ts, expires_at)
    
    # 2) Try Redis (non-blocking, failures are OK)
    r = _get_redis()
    if r:
        try:
            pipe = r.pipeline()
            pipe.setex(WA_QR_KEY, ttl, qr)
            pipe.setex(WA_QR_TS_KEY, ttl, str(ts))
            pipe.execute()
        except Exception as e:
            logger.debug("wa_qr_cache: redis set error (using in-memory+file): %s", e)
    
    # 3) Always write to file (ensures persistence across restarts)
    _save_qr_to_file(qr, ts)
