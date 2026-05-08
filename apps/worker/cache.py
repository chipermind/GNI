"""
Intelligent caching: Redis with in-memory fallback.
TTL configurable; cache never blocks fresh items (miss = compute).
"""
import hashlib
import json
import os
import threading
import time
from typing import Any, Optional

from apps.api.settings_utils import env_int


def _normalize_prompt(text: str) -> str:
    """Normalize prompt for stable hash: strip, collapse whitespace."""
    if not text:
        return ""
    return " ".join(text.strip().split())


def prompt_hash(*parts: str) -> str:
    """SHA256 hash of normalized prompt parts. Deterministic."""
    normalized = "|".join(_normalize_prompt(p) for p in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


CACHE_TTL_SECONDS = env_int("CACHE_TTL_SECONDS", default=86400)  # 24h default
CACHE_PREFIX = "gni:"


def _get_redis():
    """Lazy Redis client. Returns None if Redis unavailable. VM-first default: redis:6379."""
    try:
        import redis
        url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        return redis.Redis.from_url(url)
    except Exception:
        return None


class _InMemoryCache:
    """Thread-safe in-memory cache with TTL. Fallback when Redis unavailable."""

    def __init__(self, ttl: int):
        self._ttl = ttl
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            val, expires = entry
            if time.monotonic() > expires:
                del self._data[key]
                return None
            return val

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self._ttl
        with self._lock:
            self._data[key] = (value, time.monotonic() + ttl)


_redis = None
_memory_cache: Optional[_InMemoryCache] = None
_cache_lock = threading.Lock()


def _cache_backend() -> tuple[Any, bool]:
    """Return (backend, use_redis). use_redis=False => in-memory fallback."""
    global _redis, _memory_cache
    with _cache_lock:
        if _redis is None:
            _redis = _get_redis()
        if _memory_cache is None:
            _memory_cache = _InMemoryCache(CACHE_TTL_SECONDS)
    if _redis is not None:
        try:
            _redis.ping()
            return _redis, True
        except Exception:
            pass
    return _memory_cache, False


def cache_get(key: str) -> Optional[str]:
    """Get value by key. Returns None on miss or error."""
    backend, use_redis = _cache_backend()
    try:
        if use_redis:
            val = backend.get(CACHE_PREFIX + key)
            return val.decode("utf-8") if isinstance(val, bytes) else val
        return backend.get(CACHE_PREFIX + key)
    except Exception:
        return None


def cache_set(key: str, value: str, ttl: Optional[int] = None) -> None:
    """Set value with TTL. Silently no-op on error."""
    backend, use_redis = _cache_backend()
    ttl = ttl if ttl is not None else CACHE_TTL_SECONDS
    try:
        if use_redis:
            backend.setex(CACHE_PREFIX + key, ttl, value)
        else:
            backend.set(CACHE_PREFIX + key, value, ttl=ttl)
    except Exception:
        pass


# --- Scoring cache (by fingerprint) ---

SCORE_KEY_PREFIX = "score:"


def get_score_cached(fingerprint: str) -> Optional[dict[str, Any]]:
    """Get cached score for fingerprint. Returns None on miss."""
    raw = cache_get(SCORE_KEY_PREFIX + fingerprint)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def set_score_cached(fingerprint: str, score: dict[str, Any], ttl: Optional[int] = None) -> None:
    """Cache score for fingerprint."""
    cache_set(SCORE_KEY_PREFIX + fingerprint, json.dumps(score), ttl=ttl)


# --- LLM cache (by prompt hash) ---

LLM_CLASSIFY_PREFIX = "llm:classify:"
LLM_GENERATE_PREFIX = "llm:generate:"


def get_llm_classify_cached(hash_key: str) -> Optional[str]:
    """Get cached classify JSON. Returns None on miss."""
    return cache_get(LLM_CLASSIFY_PREFIX + hash_key)


def set_llm_classify_cached(hash_key: str, json_str: str, ttl: Optional[int] = None) -> None:
    """Cache classify result JSON."""
    cache_set(LLM_CLASSIFY_PREFIX + hash_key, json_str, ttl=ttl)


def get_llm_generate_cached(hash_key: str) -> Optional[str]:
    """Get cached generate JSON. Returns None on miss."""
    return cache_get(LLM_GENERATE_PREFIX + hash_key)


def set_llm_generate_cached(hash_key: str, json_str: str, ttl: Optional[int] = None) -> None:
    """Cache generate result JSON."""
    cache_set(LLM_GENERATE_PREFIX + hash_key, json_str, ttl=ttl)
