"""RSS source collector for GNI V1 ingestion pipeline.

Loads sources from ``sources.json``, fetches each enabled RSS feed with a
per-source timeout and one retry on transient failures. One failing source
never aborts the run.
"""
from __future__ import annotations

import json
import logging
import socket
from pathlib import Path
from typing import Iterable
from urllib import error as urllib_error
from urllib import request as urllib_request

import feedparser

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10
DEFAULT_RETRIES = 1
USER_AGENT = "GNI-Collector/1.0 (+ops contact)"


def load_sources(path: Path) -> list[dict]:
    """Load source definitions from ``sources.json``."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "sources" in data:
        return list(data["sources"])
    if isinstance(data, list):
        return data
    raise ValueError(
        f"sources.json must be a list or {{'sources': [...]}}; got {type(data).__name__}"
    )


def _fetch_bytes(url: str, timeout_s: int) -> bytes:
    req = urllib_request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    with urllib_request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read()


def fetch_rss(
    url: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    retries: int = DEFAULT_RETRIES,
) -> list[dict]:
    """Fetch and parse an RSS feed, returning a list of raw entry dicts.

    Retries once on transient network errors. Raises the last exception if
    all attempts fail.
    """
    last_err: Exception | None = None
    for _attempt in range(retries + 1):
        try:
            payload = _fetch_bytes(url, timeout_s=timeout_s)
            parsed = feedparser.parse(payload)
            entries: list[dict] = []
            for e in parsed.entries:
                entries.append(
                    {
                        "title": getattr(e, "title", "") or "",
                        "link": getattr(e, "link", "") or "",
                        "summary": getattr(e, "summary", "") or "",
                        "published": getattr(e, "published", "") or "",
                        "published_parsed": getattr(e, "published_parsed", None),
                        "id": getattr(e, "id", "") or getattr(e, "guid", "") or "",
                    }
                )
            return entries
        except (
            urllib_error.URLError,
            urllib_error.HTTPError,
            socket.timeout,
            TimeoutError,
            ConnectionError,
        ) as exc:
            last_err = exc
            continue
        except Exception as exc:  # parse error or unknown
            last_err = exc
            break
    if last_err is not None:
        raise last_err
    return []


def collect_all(sources: Iterable[dict]) -> list[dict]:
    """Fetch every enabled RSS source. One failure never aborts the run.

    Returns a list of result dicts:
        {"source": <source_meta>, "entries": [...], "ok": bool, "error": str|None}
    """
    out: list[dict] = []
    for src in sources:
        name = src.get("source_name", "<unnamed>")
        if not src.get("enabled", True):
            logger.info("source skipped (disabled) name=%s", name)
            continue
        if src.get("source_type", "rss") != "rss":
            logger.info(
                "source skipped (non-rss in V1) name=%s type=%s",
                name,
                src.get("source_type"),
            )
            continue
        url = src.get("url", "")
        if not url or url.startswith("${"):
            logger.warning(
                "source skipped (no url or unresolved placeholder) name=%s url=%r",
                name,
                url,
            )
            continue
        try:
            entries = fetch_rss(url)
            logger.info("source ok name=%s entries=%d", name, len(entries))
            out.append(
                {"source": src, "entries": entries, "ok": True, "error": None}
            )
        except Exception as exc:
            logger.error(
                "source failed name=%s url=%s error=%r", name, url, exc
            )
            out.append(
                {"source": src, "entries": [], "ok": False, "error": repr(exc)}
            )
    return out
