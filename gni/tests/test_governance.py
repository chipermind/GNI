"""Governance tests for the draft → queue → publisher pipeline.

These tests cover the new safety contract:
  - "ignored" and "failed_processing" are terminal queue statuses.
  - classifier↔router mismatch never auto-validates.
  - per-headline exceptions in build_draft are isolated to one record.
  - publisher's status allowlist refuses anything outside ready_to_publish.

Pure unit-level: no Telegram, no filesystem (except temp dirs through pytest
fixtures), no network. Determinism is required.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from gni.drafting import draft_builder, run_drafting
from gni.editorial_queue import queue_manager
from gni.publisher import run_publish


# ---------------------------------------------------------------------------
# 1. ignored → queue
# ---------------------------------------------------------------------------


def test_ignored_draft_maps_to_ignored_queue_status():
    assert queue_manager.map_draft_status_to_queue_status(
        "ignored", "medium"
    ) == "ignored"


def test_ignored_queue_item_built_from_ignored_draft():
    drafts = [{
        "draft_id": "draft_abc",
        "headline_hash_key": "abc",
        "draft_status": "ignored",
        "priority": "medium",
        "payload": {},
        "template": "",
        "source_item": {"title": "low value"},
    }]
    items, dups = queue_manager.build_queue_items(drafts)
    assert dups == 0
    assert len(items) == 1
    assert items[0]["status"] == "ignored"


# ---------------------------------------------------------------------------
# 2. ignored cannot be approved / cannot transition out
# ---------------------------------------------------------------------------


def test_ignored_cannot_be_approved():
    item = {"queue_id": "q1", "status": "ignored"}
    with pytest.raises(ValueError):
        queue_manager.update_status(item, "ready_to_publish")


def test_ignored_cannot_be_set_to_published():
    item = {"queue_id": "q1", "status": "ignored"}
    with pytest.raises(ValueError):
        queue_manager.update_status(item, "published")


def test_failed_processing_cannot_be_approved():
    item = {"queue_id": "q1", "status": "failed_processing"}
    with pytest.raises(ValueError):
        queue_manager.update_status(item, "ready_to_publish")


# ---------------------------------------------------------------------------
# 3. publisher refuses non-ready_to_publish (forbidden statuses)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        "ignored",
        "needs_review",
        "failed_guard",
        "needs_editorial_build",
        "failed_processing",
        "rejected",
        "published",
    ],
)
def test_publisher_refuses_forbidden_statuses(status):
    item = {
        "queue_id": "q1",
        "status": status,
        "priority": "medium",
        "template": "RADAR",
        "payload": {"text": "x"},
    }
    ok, reason = run_publish._is_publishable(item)
    assert ok is False
    assert reason == f"forbidden_status_{status}"


def test_publisher_accepts_only_ready_to_publish():
    item = {
        "queue_id": "q1",
        "status": "ready_to_publish",
        "priority": "medium",
        "template": "RADAR",
        "payload": {"text": "x"},
    }
    ok, reason = run_publish._is_publishable(item)
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------------
# 4. classifier=alerta + router=BRIEFING → needs_review
# ---------------------------------------------------------------------------


def test_alerta_with_briefing_router_becomes_needs_review():
    headline = {
        "title": "Anything",
        "raw_text": "",
        "category": "markets",
        "source_name": "tier1news",
        "tier": "tier1",
        "hash_key": "abc",
    }
    fake_classify = lambda _item: {
        "decision": "alerta",
        "confidence": 0.95,
        "reasons": ["urgency_hit:war"],
        "risk_flags": [],
    }
    fake_route = lambda _text: {"template": "BRIEFING", "confidence": 0.90}
    with patch.object(draft_builder, "classify_relevance", fake_classify), \
         patch.object(draft_builder, "route_content", fake_route):
        draft = draft_builder.build_draft(headline)
    assert draft["draft_status"] == "needs_review"
    assert draft["classifier_router_match"] is False
    assert draft["router_template"] == "BRIEFING"
    assert "classifier_router_mismatch" in draft["classifier_risk_flags"]
    assert draft["mismatch_reason"]


# ---------------------------------------------------------------------------
# 5. classifier=briefing + router=FLASH → needs_review
# ---------------------------------------------------------------------------


def test_briefing_with_flash_router_becomes_needs_review():
    headline = {
        "title": "Anything",
        "raw_text": "",
        "category": "markets",
        "source_name": "tier2news",
        "tier": "tier2",
        "hash_key": "def",
    }
    fake_classify = lambda _item: {
        "decision": "briefing",
        "confidence": 0.85,
        "reasons": ["scheduled_summary:morning brief"],
        "risk_flags": [],
    }
    fake_route = lambda _text: {"template": "FLASH", "confidence": 0.90}
    with patch.object(draft_builder, "classify_relevance", fake_classify), \
         patch.object(draft_builder, "route_content", fake_route):
        draft = draft_builder.build_draft(headline)
    assert draft["draft_status"] == "needs_review"
    assert draft["classifier_router_match"] is False
    assert draft["router_template"] == "FLASH"
    assert "classifier_router_mismatch" in draft["classifier_risk_flags"]


def test_briefing_with_radar_router_is_match():
    headline = {
        "title": "rally",
        "raw_text": "",
        "category": "markets",
        "source_name": "x",
        "tier": "tier2",
        "hash_key": "ghi",
    }
    fake_classify = lambda _item: {
        "decision": "briefing",
        "confidence": 0.80,
        "reasons": ["impact_hit:fed"],
        "risk_flags": [],
    }
    fake_route = lambda _text: {"template": "RADAR", "confidence": 0.70}
    with patch.object(draft_builder, "classify_relevance", fake_classify), \
         patch.object(draft_builder, "route_content", fake_route):
        draft = draft_builder.build_draft(headline)
    assert draft["classifier_router_match"] is True
    assert draft["router_template"] == "RADAR"
    assert draft["template"] == "RADAR"
    assert "classifier_router_mismatch" not in draft["classifier_risk_flags"]


# ---------------------------------------------------------------------------
# 6+7. headline exception → failed_processing record; batch continues
# ---------------------------------------------------------------------------


def test_failed_processing_record_on_build_exception(tmp_path, monkeypatch):
    """One bad headline becomes a failed_processing record; the rest of the
    batch must still be processed."""
    raw_dir = tmp_path / "raw"
    drafts_dir = tmp_path / "drafts"
    log_dir = tmp_path / "logs"
    lock_path = tmp_path / "lock"
    for d in (raw_dir, drafts_dir, log_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(run_drafting, "RAW_DIR", raw_dir, raising=True)
    monkeypatch.setattr(run_drafting, "DRAFTS_DIR", drafts_dir, raising=True)
    monkeypatch.setattr(run_drafting, "LOG_DIR", log_dir, raising=True)
    monkeypatch.setattr(run_drafting, "LOCK_PATH", lock_path, raising=True)

    day = run_drafting._today_utc_str()
    headlines = [
        {"title": "fed cuts", "hash_key": "good1", "tier": "tier1",
         "category": "markets", "source_name": "Reuters"},
        {"title": "bad item", "hash_key": "bad1", "tier": "tier1",
         "category": "markets", "source_name": "Reuters"},
        {"title": "another fine one", "hash_key": "good2", "tier": "tier2",
         "category": "tech", "source_name": "feed"},
    ]
    import json
    (raw_dir / f"headlines_{day}_UTC.json").write_text(
        json.dumps({"items": headlines}, ensure_ascii=False), encoding="utf-8"
    )

    real_build = draft_builder.build_draft

    def flaky_build(h):
        if h.get("hash_key") == "bad1":
            raise RuntimeError("synthetic failure")
        return real_build(h)

    monkeypatch.setattr(
        run_drafting.draft_builder, "build_draft", flaky_build, raising=True
    )

    rc = run_drafting.run()
    assert rc == 0

    out = json.loads((drafts_dir / f"drafts_{day}.json").read_text("utf-8"))
    drafts = out["drafts"]
    assert len(drafts) == 3, "all three headlines must produce a record"
    statuses = {d["headline_hash_key"]: d["draft_status"] for d in drafts}
    assert statuses["bad1"] == "failed_processing"
    bad = [d for d in drafts if d["headline_hash_key"] == "bad1"][0]
    assert "synthetic failure" in bad["processing_error"]
    assert bad["source_item"]["title"] == "bad item"
    # The other two must NOT be failed_processing.
    assert statuses["good1"] != "failed_processing"
    assert statuses["good2"] != "failed_processing"


# ---------------------------------------------------------------------------
# 8. status contract surface (allowlist + terminal coverage)
# ---------------------------------------------------------------------------


def test_status_contract_surface():
    expected_allowed = {
        "validated", "needs_review", "failed_guard", "needs_editorial_build",
        "approved", "rejected", "ready_to_publish", "published",
        "ignored", "failed_processing",
    }
    assert queue_manager.ALLOWED_STATUSES == frozenset(expected_allowed)

    expected_terminal = {"published", "rejected", "ignored", "failed_processing"}
    assert queue_manager.TERMINAL_STATUSES == frozenset(expected_terminal)

    assert queue_manager.PUBLISHABLE_STATUSES == frozenset({"ready_to_publish"})

    expected_forbidden = {
        "ignored", "needs_review", "failed_guard", "needs_editorial_build",
        "failed_processing", "rejected", "published",
    }
    assert queue_manager.PUBLISH_FORBIDDEN_STATUSES == frozenset(expected_forbidden)
    assert run_publish.PUBLISH_FORBIDDEN_STATUSES == frozenset(expected_forbidden)
