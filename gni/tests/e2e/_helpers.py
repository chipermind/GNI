"""Pure helpers shared across the E2E test module.

Lives outside ``conftest.py`` so it can be imported normally without
relying on pytest's special conftest handling.
"""
from __future__ import annotations


def make_seeded_draft(headline: dict, *, priority: str) -> dict:
    """Hand-build a validated draft for a given priority.

    The V1 auto-build templates produce titles that fail the headline
    validator (no '—' separators), so naturally-validated drafts are not
    currently reachable through the build pipeline. For the queue→publish
    contract we seed clean drafts directly. Everything downstream (queue
    mapping, formatter, publisher) treats them identically to build_draft()
    output that happened to validate.
    """
    hk = headline["hash_key"]
    emoji_map = {
        "critical": "🔴", "high": "🟠",
        "medium": "🟡", "low": "🔵", "info": "🟢",
    }
    emoji = emoji_map.get(priority, "🟡")
    text_by_priority = {
        "critical": f"{emoji} Breaking incident — operator review required",
        "high":     f"{emoji} High-impact event — operator review required",
        "medium":   f"{emoji} Markets snapshot: rally extends, volatility flat",
        "low":      f"{emoji} Routine update from monitoring desk",
        "info":     f"{emoji} Informational note from monitoring desk",
    }
    return {
        "draft_id": f"draft_{hk}_{priority}",
        "headline_hash_key": hk,
        "template": "FLASH",
        "route_confidence": 0.9,
        "draft_status": "validated",
        "priority": priority,
        "payload": {
            "template": "FLASH",
            "text": text_by_priority[priority],
        },
        "guard_errors": [],
        "source_item": headline,
        "created_at": "2026-05-05T11:10:00Z",
    }


def make_validated_medium_draft(headline: dict) -> dict:
    """Convenience: seed a validated+medium draft."""
    return make_seeded_draft(headline, priority="medium")
