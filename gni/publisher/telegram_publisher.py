"""Telegram Bot API client (V1).

Stdlib-only HTTP (no ``requests`` dep). Bounded retries, no infinite loops.
Token / chat_id are NEVER logged.

Public surface:
  - ``resolve_topic(item)``      : (env_var_name, topic_id_str | None)
  - ``send_to_telegram(text, *, token, chat_id, message_thread_id, timeout)``
       returns dict ``{ok, message_id, error, http_status, retry_after}``
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT_S = 10
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0


# ---------------------------------------------------------------------------
# Env / topic resolution
# ---------------------------------------------------------------------------

ENV_TOKEN = "TELEGRAM_BOT_TOKEN"
ENV_CHAT = "TELEGRAM_CHAT_ID"
ENV_DRY_RUN = "TELEGRAM_DRY_RUN"

ENV_TOPIC_ALERTS = "TELEGRAM_TOPIC_ALERTS"
ENV_TOPIC_GEOPOLITICS = "TELEGRAM_TOPIC_GEOPOLITICS"
ENV_TOPIC_CYBER = "TELEGRAM_TOPIC_CYBER"
ENV_TOPIC_AI = "TELEGRAM_TOPIC_AI"
ENV_TOPIC_MARKETS = "TELEGRAM_TOPIC_MARKETS"
ENV_TOPIC_COMMUNITY = "TELEGRAM_TOPIC_COMMUNITY"


def _category_of(item: dict) -> str:
    src = item.get("source_item") or {}
    return (src.get("category") or item.get("category") or "").strip().lower()


def resolve_topic(item: dict) -> tuple[str, str | None]:
    """Pick the topic env var for this queue item, then read its value.

    Routing precedence:
      1. priority in {critical, high}            -> alerts
      2. category == "geopolitics"               -> geopolitics
      3. category == "cyber"                     -> cyber
      4. category == "ai"                        -> ai
      5. category in {markets, macro, crypto}    -> markets
      6. else                                    -> community
    Returns (env_var_name, env_value_or_None).
    """
    priority = (item.get("priority") or "").strip().lower()
    category = _category_of(item)

    if priority in {"critical", "high"}:
        env_var = ENV_TOPIC_ALERTS
    elif category == "geopolitics":
        env_var = ENV_TOPIC_GEOPOLITICS
    elif category == "cyber":
        env_var = ENV_TOPIC_CYBER
    elif category == "ai":
        env_var = ENV_TOPIC_AI
    elif category in {"markets", "macro", "crypto"}:
        env_var = ENV_TOPIC_MARKETS
    else:
        env_var = ENV_TOPIC_COMMUNITY

    return env_var, os.environ.get(env_var) or None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _post_json(url: str, payload: dict, timeout: float) -> tuple[int, dict | None]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(data)
            except json.JSONDecodeError:
                return resp.status, None
    except urllib.error.HTTPError as exc:
        try:
            data = exc.read().decode("utf-8", errors="replace")
            return exc.code, json.loads(data)
        except Exception:
            return exc.code, None


def send_to_telegram(
    text: str,
    *,
    token: str,
    chat_id: str,
    message_thread_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Send ``text`` to Telegram. Bounded retries (3 attempts max).

    Returns:
        {
          "ok":            bool,
          "message_id":    int | None,
          "error":         str | None,        # human-readable, no token/chat
          "http_status":   int | None,
          "retry_after":   int | None,
          "attempts":      int,
        }
    """
    if not token or not chat_id:
        return {
            "ok": False,
            "message_id": None,
            "error": "telegram_credentials_missing",
            "http_status": None,
            "retry_after": None,
            "attempts": 0,
        }
    if not text or not text.strip():
        return {
            "ok": False,
            "message_id": None,
            "error": "empty_text",
            "http_status": None,
            "retry_after": None,
            "attempts": 0,
        }

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if message_thread_id:
        try:
            payload["message_thread_id"] = int(message_thread_id)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "message_id": None,
                "error": "invalid_message_thread_id",
                "http_status": None,
                "retry_after": None,
                "attempts": 0,
            }

    last_err: str | None = None
    last_status: int | None = None
    last_retry_after: int | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        status, data = _post_json(url, payload, timeout=timeout)
        last_status = status

        if data and data.get("ok") is True:
            result = data.get("result") or {}
            message_id = result.get("message_id")
            return {
                "ok": True,
                "message_id": message_id,
                "error": None,
                "http_status": status,
                "retry_after": None,
                "attempts": attempt,
            }

        # 429 rate-limit: honor retry_after exactly once if it fits within budget.
        if status == 429 and data:
            params = data.get("parameters") or {}
            retry_after = int(params.get("retry_after") or 0)
            last_retry_after = retry_after
            last_err = (data.get("description") or "rate_limited")[:160]
            if attempt < MAX_ATTEMPTS and 0 < retry_after <= 30:
                logger.warning(
                    "telegram_rate_limited attempt=%d retry_after=%ds", attempt, retry_after
                )
                time.sleep(retry_after)
                continue
            break

        # 5xx: backoff and retry; 4xx: do not retry.
        last_err = (data.get("description") if data else None) or f"http_{status}"
        last_err = str(last_err)[:160]

        if 500 <= (status or 0) < 600 and attempt < MAX_ATTEMPTS:
            backoff = BACKOFF_BASE_S * attempt
            logger.warning(
                "telegram_server_error attempt=%d status=%s backoff=%.1fs",
                attempt,
                status,
                backoff,
            )
            time.sleep(backoff)
            continue

        # 4xx and final attempts fall through.
        break

    return {
        "ok": False,
        "message_id": None,
        "error": last_err or "unknown_error",
        "http_status": last_status,
        "retry_after": last_retry_after,
        "attempts": MAX_ATTEMPTS,
    }


def is_dry_run() -> bool:
    return (os.environ.get(ENV_DRY_RUN) or "").strip().lower() in {"1", "true", "yes"}
