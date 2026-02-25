"""
Ensure Ollama model is present on worker startup. Pull via API if missing (VM-friendly).
Uses OLLAMA_MODEL, OLLAMA_PULL_ON_START, timeout and retries. No crash-loop on pull failure.
"""
import json
import logging
import os
import time
from typing import Optional

import httpx

from apps.shared.env_helpers import get_int_env, parse_int
from apps.worker.llm.ollama_client import OLLAMA_BASE_URL_DEFAULT

logger = logging.getLogger(__name__)

OLLAMA_MODEL_DEFAULT = "qwen2.5:7b"
PULL_ON_START = os.environ.get("OLLAMA_PULL_ON_START", "true").strip().lower() in ("1", "true", "yes")
PULL_TIMEOUT = get_int_env("OLLAMA_PULL_TIMEOUT_SECONDS", default=1800)
PULL_MAX_RETRIES = get_int_env("OLLAMA_PULL_MAX_RETRIES", default=6)
PULL_BACKOFF = get_int_env("OLLAMA_PULL_BACKOFF_SECONDS", default=20)
PROGRESS_LOG_INTERVAL = 20


def _base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL_DEFAULT).rstrip("/")


def _model_name() -> str:
    return (os.environ.get("OLLAMA_MODEL", OLLAMA_MODEL_DEFAULT) or OLLAMA_MODEL_DEFAULT).strip()


def _model_present(tags: dict, want: str) -> bool:
    """True if want is in the models list (exact or prefix match for name)."""
    models = tags.get("models") or []
    want_lower = want.lower()
    for m in models:
        name = (m.get("name") or "").strip().lower()
        if name == want_lower or name.startswith(want_lower + ":") or name.startswith(want_lower + "-"):
            return True
    return False


def _fetch_tags() -> Optional[dict]:
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(f"{_base_url()}/api/tags")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("ollama_ensure: fetch tags failed: %s", e)
        return None


def _pull_model(model: str) -> bool:
    """Pull model via POST /api/pull. Stream progress; log at intervals. Returns True on success."""
    url = f"{_base_url()}/api/pull"
    last_log = 0.0
    try:
        with httpx.Client(timeout=float(PULL_TIMEOUT)) as client:
            with client.stream("POST", url, json={"model": model, "stream": True}) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        status = obj.get("status", "")
                        if status == "success":
                            logger.info("ollama_ensure: pull completed for %s", model)
                            return True
                        now = time.monotonic()
                        if now - last_log >= PROGRESS_LOG_INTERVAL:
                            completed = obj.get("completed", 0)
                            total = obj.get("total", 0)
                            if total:
                                pct = 100.0 * completed / total
                                logger.info("ollama_ensure: pulling %s %.0f%%", model, pct)
                            else:
                                logger.info("ollama_ensure: pulling %s %s", model, status or "…")
                            last_log = now
                    except json.JSONDecodeError:
                        pass
        return True
    except httpx.TimeoutException:
        logger.warning("ollama_ensure: pull timeout for %s", model)
        return False
    except Exception as e:
        logger.warning("ollama_ensure: pull failed for %s: %s", model, e)
        return False


def ensure_ollama_model() -> bool:
    """
    Ensure OLLAMA_MODEL is available. If missing and OLLAMA_PULL_ON_START=true, pull via API.
    Uses timeout and retries with backoff. Returns True if model is present (or pull succeeded), False otherwise.
    """
    base = _base_url()
    model = _model_name()
    if not model:
        logger.warning("ollama_ensure: OLLAMA_MODEL not set")
        return False

    tags = _fetch_tags()
    if tags is None:
        return False
    if _model_present(tags, model):
        logger.info("ollama_ensure: model %s already present", model)
        return True

    if not PULL_ON_START:
        logger.info("ollama_ensure: model %s not present; OLLAMA_PULL_ON_START disabled", model)
        return False

    for attempt in range(1, PULL_MAX_RETRIES + 1):
        logger.info("ollama_ensure: pulling %s (attempt %s/%s)", model, attempt, PULL_MAX_RETRIES)
        if _pull_model(model):
            tags2 = _fetch_tags()
            if tags2 and _model_present(tags2, model):
                return True
        if attempt < PULL_MAX_RETRIES:
            logger.info("ollama_ensure: backoff %ss before retry", PULL_BACKOFF)
            time.sleep(PULL_BACKOFF)

    logger.warning("ollama_ensure: model %s still not available after %s attempts", model, PULL_MAX_RETRIES)
    return False
