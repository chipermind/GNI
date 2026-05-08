"""Dedup of normalized headlines.

V1 dedup combines two layers:

1. Intra-day: against the existing ``headlines_YYYYMMDD_UTC.json`` day-file.
2. Cross-day: against ``gni/data/state/seen_hashes.json``, which retains
   ``hash_key`` and ``url`` for the last 7 days. Suppresses items that
   reappear across the UTC midnight boundary.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


def load_existing(path: Path) -> list[dict]:
    """Load existing day-file. Returns [] if missing or unreadable."""
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("dedup: failed to read existing day-file %s: %r", path, exc)
        return []
    if isinstance(data, dict) and "items" in data:
        return list(data["items"])
    if isinstance(data, list):
        return data
    return []


def existing_keys(items: Iterable[dict]) -> tuple[set[str], set[str]]:
    """Return (hash_keys, urls) seen so far."""
    hashes: set[str] = set()
    urls: set[str] = set()
    for it in items:
        hk = it.get("hash_key") or it.get("id")
        if hk:
            hashes.add(hk)
        url = it.get("url")
        if url:
            urls.add(url)
    return hashes, urls


def filter_new(
    candidates: list[dict],
    seen_hashes: set[str],
    seen_urls: set[str],
) -> tuple[list[dict], int]:
    """Return (new_items, dup_count). Mutates the seen sets in-place."""
    new_items: list[dict] = []
    dups = 0
    for item in candidates:
        hk = item.get("hash_key") or item.get("id")
        url = item.get("url")
        if hk and hk in seen_hashes:
            dups += 1
            continue
        if url and url in seen_urls:
            dups += 1
            continue
        new_items.append(item)
        if hk:
            seen_hashes.add(hk)
        if url:
            seen_urls.add(url)
    return new_items, dups


# ---------------------------------------------------------------------------
# Cross-day state (seen_hashes.json)
# ---------------------------------------------------------------------------

STATE_RETENTION_DAYS = 7
STATE_SCHEMA_VERSION = 1


def empty_state() -> dict:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at": "",
        "seen_items": [],
        "source_zero_streaks": {},
    }


def load_state(path: Path) -> dict:
    """Load cross-day state. Returns empty state on missing/corrupt file."""
    if not path.exists():
        return empty_state()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("state file unreadable %s: %r", path, exc)
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    data.setdefault("schema_version", STATE_SCHEMA_VERSION)
    data.setdefault("seen_items", [])
    data.setdefault("source_zero_streaks", {})
    if not isinstance(data["seen_items"], list):
        data["seen_items"] = []
    if not isinstance(data["source_zero_streaks"], dict):
        data["source_zero_streaks"] = {}
    return data


def prune_seen(state: dict, days: int = STATE_RETENTION_DAYS, now: datetime | None = None) -> dict:
    """Drop ``seen_items`` older than ``days``. Mutates and returns state."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    state["seen_items"] = [
        s for s in state.get("seen_items", [])
        if isinstance(s, dict) and s.get("first_seen", "") >= cutoff_iso
    ]
    return state


def state_keys(state: dict) -> tuple[set[str], set[str]]:
    """Return (hash_keys, urls) currently retained in state."""
    hashes: set[str] = set()
    urls: set[str] = set()
    for s in state.get("seen_items", []):
        if not isinstance(s, dict):
            continue
        hk = s.get("hash_key")
        u = s.get("url")
        if hk:
            hashes.add(hk)
        if u:
            urls.add(u)
    return hashes, urls


def append_to_state(state: dict, items: Iterable[dict], now_iso: str) -> dict:
    """Append new items to state['seen_items'] (dedup by hash_key)."""
    seen_items = state.setdefault("seen_items", [])
    existing_hashes = {
        s.get("hash_key") for s in seen_items
        if isinstance(s, dict) and s.get("hash_key")
    }
    for it in items:
        hk = it.get("hash_key") or it.get("id")
        if not hk or hk in existing_hashes:
            continue
        seen_items.append(
            {
                "hash_key": hk,
                "url": it.get("url", ""),
                "first_seen": now_iso,
            }
        )
        existing_hashes.add(hk)
    return state
