"""
Redis-based rate limiting: per-channel per-minute and per-hour from Settings.rate_limits.
Block publish + log event if rate limited. Default safe limits when missing.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

DEFAULT_PER_MINUTE = 5
DEFAULT_PER_HOUR = 100
RATE_LIMIT_EXCEEDED_EVENT = "rate_limit_exceeded"


class RateLimitExceededError(Exception):
    """Raised when publish is blocked due to rate limit."""

    def __init__(self, channel: str, limit_type: str, current: int, limit: int):
        self.channel = channel
        self.limit_type = limit_type
        self.current = current
        self.limit = limit
        super().__init__(f"Rate limit exceeded for {channel}: {limit_type} {current}/{limit}")


def _get_redis():
    """Lazy Redis client from REDIS_URL. VM-first default: redis:6379."""
    import redis
    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    return redis.Redis.from_url(url)


def _get_limits_for_channel(settings: dict[str, Any], channel: str) -> tuple[int, int]:
    """Return (per_minute, per_hour) for channel. Uses defaults if missing."""
    limits = settings.get("rate_limits")
    if not limits or not isinstance(limits, dict):
        return DEFAULT_PER_MINUTE, DEFAULT_PER_HOUR
    ch = limits.get(channel)
    if ch and isinstance(ch, dict):
        pm = ch.get("per_minute")
        ph = ch.get("per_hour")
        return (
            int(pm) if pm is not None else DEFAULT_PER_MINUTE,
            int(ph) if ph is not None else DEFAULT_PER_HOUR,
        )
    pm = limits.get("per_minute")
    ph = limits.get("per_hour")
    return (
        int(pm) if pm is not None else DEFAULT_PER_MINUTE,
        int(ph) if ph is not None else DEFAULT_PER_HOUR,
    )


def _minute_key(channel: str) -> str:
    now = datetime.now(timezone.utc)
    return f"rate:{channel}:min:{now.strftime('%Y-%m-%d-%H-%M')}"


def _hour_key(channel: str) -> str:
    now = datetime.now(timezone.utc)
    return f"rate:{channel}:hr:{now.strftime('%Y-%m-%d-%H')}"


def check_rate_limit(
    channel: str,
    settings: Optional[dict[str, Any]] = None,
    redis_client=None,
) -> None:
    """
    Check if channel is within rate limit. Raises RateLimitExceededError if blocked.
    Increments counters only when allowed. settings: dict from get_settings(session); if None, uses defaults.
    """
    per_minute, per_hour = _get_limits_for_channel(settings or {}, channel)
    r = redis_client or _get_redis()
    mk = _minute_key(channel)
    hk = _hour_key(channel)
    min_count = int(r.get(mk) or 0)
    hour_count = int(r.get(hk) or 0)
    if min_count >= per_minute:
        raise RateLimitExceededError(channel, "per_minute", min_count, per_minute)
    if hour_count >= per_hour:
        raise RateLimitExceededError(channel, "per_hour", hour_count, per_hour)
    pipe = r.pipeline()
    pipe.incr(mk)
    pipe.expire(mk, 120)
    pipe.incr(hk)
    pipe.expire(hk, 7200)
    pipe.execute()


def log_rate_limit_event(
    session,
    channel: str,
    limit_type: str,
    current: int,
    limit: int,
) -> None:
    """Log rate_limit_exceeded to events_log. Caller should commit."""
    from apps.api.db.models import EventsLog
    session.add(
        EventsLog(
            event_type=RATE_LIMIT_EXCEEDED_EVENT,
            payload={
                "channel": channel,
                "limit_type": limit_type,
                "current": current,
                "limit": limit,
            },
        )
    )
