"""GNI V1 end-to-end pipeline tests.

Flow under test:
    headlines JSON → drafting → editorial queue → approval gate
                  → publisher (Telegram-mocked) → published log

SAFE MODE invariants enforced by conftest:
  - TELEGRAM_TEST_CHAT_ID required (else session aborts).
  - All path constants redirected to a per-test sandbox; no real
    ``gni/data/`` or ``gni/logs/`` write.
  - ``gni.publisher.telegram_publisher._post_json`` is replaced with a
    FakeTelegram so no HTTP egress is possible.

Run with::

    bash gni/tests/e2e/run_e2e.sh
or::

    TELEGRAM_TEST_CHAT_ID=-100TESTCHAT \\
        python -m pytest gni/tests/e2e -v
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from gni.drafting import run_drafting
from gni.editorial_queue import approve as approve_cli
from gni.editorial_queue import run_queue
from gni.publisher import run_publish

from gni.tests.e2e._helpers import make_seeded_draft, make_validated_medium_draft


def _seed_drafts_file(sandbox, drafts: list[dict]) -> None:
    payload = {
        "schema_version": 1,
        "day_utc": sandbox.day,
        "last_updated_at": "2026-05-05T11:11:00Z",
        "count": len(drafts),
        "drafts": drafts,
    }
    sandbox.drafts_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _drafts_list(sandbox):
    payload = _load_json(sandbox.drafts_path)
    return payload["drafts"] if isinstance(payload, dict) else payload


def _queue_list(sandbox):
    payload = _load_json(sandbox.queue_path)
    return payload["queue"] if isinstance(payload, dict) else payload


# ---------------------------------------------------------------------------
# Test 1: valid RSS headline becomes draft
# ---------------------------------------------------------------------------


def test_1_valid_rss_headline_becomes_draft(sandbox, seed_headlines):
    """Headline → drafting → drafts_<DAY>.json containing draft records."""
    rc = run_drafting.run()
    assert rc == 0, "drafting orchestrator must exit 0"

    assert sandbox.drafts_path.exists(), "drafts file must be created"
    drafts = _drafts_list(sandbox)
    # Fixture has 3 entries, but #3 is a duplicate of #1 → 2 unique drafts.
    assert len(drafts) == 2, f"expected 2 unique drafts, got {len(drafts)}"

    # Every draft must carry the V1 contract fields.
    required = {
        "draft_id", "headline_hash_key", "template", "route_confidence",
        "draft_status", "priority", "payload", "source_item", "created_at",
    }
    for d in drafts:
        missing = required - set(d.keys())
        assert not missing, f"draft missing fields: {missing}"


# ---------------------------------------------------------------------------
# Test 2: draft passes guard or becomes failed_guard
# ---------------------------------------------------------------------------


def test_2_draft_status_is_well_formed(sandbox, seed_headlines):
    """Every draft.draft_status is one of the 4 V1 statuses."""
    rc = run_drafting.run()
    assert rc == 0

    drafts = _drafts_list(sandbox)
    legal_statuses = {
        "validated", "failed_guard", "needs_review", "needs_editorial_build",
    }
    for d in drafts:
        assert d["draft_status"] in legal_statuses, (
            f"unexpected draft_status={d['draft_status']!r} on {d['draft_id']}"
        )
        # If failed_guard, the violations payload must be populated so the
        # operator has actionable feedback.
        if d["draft_status"] == "failed_guard":
            assert isinstance(d.get("guard_errors"), list)
            assert d["guard_errors"], "failed_guard requires guard_errors"


# ---------------------------------------------------------------------------
# Test 3: validated medium item becomes ready_to_publish
# ---------------------------------------------------------------------------


def test_3_validated_medium_becomes_ready_to_publish(sandbox, seed_headlines):
    """Seed a hand-built validated+medium draft, run queue, assert mapping."""
    headline = seed_headlines["items"][1]  # the markets/rally headline
    seeded = make_validated_medium_draft(headline)
    _seed_drafts_file(sandbox, [seeded])

    rc = run_queue.run()
    assert rc == 0

    items = _queue_list(sandbox)
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "ready_to_publish", (
        f"validated+medium must auto-flow to ready_to_publish, got {item['status']!r}"
    )
    assert item["priority"] == "medium"
    assert item["draft_id"] == seeded["draft_id"]


# ---------------------------------------------------------------------------
# Test 4: critical item becomes needs_review
# ---------------------------------------------------------------------------


def test_4_critical_item_becomes_needs_review(sandbox, seed_headlines):
    """Seed validated+critical draft, run queue, assert critical → needs_review."""
    headline = seed_headlines["items"][0]  # the breaking/attack headline
    seeded = make_seeded_draft(headline, priority="critical")
    _seed_drafts_file(sandbox, [seeded])

    rc = run_queue.run()
    assert rc == 0

    items = _queue_list(sandbox)
    assert len(items) == 1
    crit = items[0]
    assert crit["priority"] == "critical"
    assert crit["status"] == "needs_review", (
        f"critical priority must require review, got {crit['status']!r}"
    )
    # Sanity: critical must NOT have manual_approval set yet.
    assert "manual_approval" not in crit or crit.get("manual_approval") is not True


# ---------------------------------------------------------------------------
# Test 5: approval moves critical to ready_to_publish (+manual_approval flag)
# ---------------------------------------------------------------------------


def test_5_approval_flips_critical_to_ready_to_publish(sandbox, seed_headlines):
    """approve.cmd_approve sets ready_to_publish + manual_approval=True."""
    headline = seed_headlines["items"][0]
    seeded = make_seeded_draft(headline, priority="critical")
    _seed_drafts_file(sandbox, [seeded])

    rc = run_queue.run()
    assert rc == 0

    items = _queue_list(sandbox)
    assert len(items) == 1
    crit = items[0]
    assert crit["status"] == "needs_review"
    queue_id = crit["queue_id"]

    rc = approve_cli.main([
        "approve",
        "--queue-id", queue_id,
        "--operator", "e2e_tester",
        "--day", sandbox.day,
    ])
    assert rc == 0, "approve CLI must exit 0 on success"

    items_after = _queue_list(sandbox)
    crit_after = items_after[0]
    assert crit_after["status"] == "ready_to_publish"
    assert crit_after.get("approved_by") == "e2e_tester"
    assert crit_after.get("approved_at"), "approved_at must be stamped"
    # Critical items get the explicit safety flag the publisher requires.
    assert crit_after.get("manual_approval") is True

    # Audit trail must record one approve event.
    assert sandbox.approvals_path.exists(), "approvals_<DAY>.json must exist"
    audit = _load_json(sandbox.approvals_path)
    events = [e for e in audit if e.get("queue_id") == queue_id]
    assert len(events) == 1
    assert events[0]["event"] == "approve"
    assert events[0]["new_status"] == "ready_to_publish"


# ---------------------------------------------------------------------------
# Test 6: publisher sends only ready_to_publish
# ---------------------------------------------------------------------------


def test_6_publisher_only_sends_ready_to_publish(
    sandbox, seed_headlines, telegram_safe_env, telegram_http_mock
):
    """Mix needs_review + ready_to_publish; publisher must skip the former."""
    h_critical = seed_headlines["items"][0]
    h_medium = seed_headlines["items"][1]
    seeded = [
        make_seeded_draft(h_critical, priority="critical"),  # → needs_review
        make_seeded_draft(h_medium,   priority="medium"),    # → ready_to_publish
    ]
    _seed_drafts_file(sandbox, seeded)

    rc = run_queue.run()
    assert rc == 0

    pre = _queue_list(sandbox)
    ready_before = [q for q in pre if q["status"] == "ready_to_publish"]
    review_before = [q for q in pre if q["status"] == "needs_review"]
    assert len(ready_before) == 1, "must have exactly one ready_to_publish"
    assert len(review_before) == 1, "must have exactly one needs_review"

    rc = run_publish.run()
    assert rc == 0

    # Publisher must have called Telegram exactly once per ready_to_publish item.
    sent_chat_ids = [c["payload"]["chat_id"] for c in telegram_http_mock.calls]
    assert len(sent_chat_ids) == 1, (
        f"telegram should have been called once; got {len(sent_chat_ids)}"
    )
    # All sends must target the test chat — never anything else.
    for cid in sent_chat_ids:
        assert cid == telegram_safe_env, (
            f"refusing to accept send to chat_id={cid!r}; "
            f"expected TELEGRAM_TEST_CHAT_ID={telegram_safe_env!r}"
        )

    # No needs_review item became published.
    post = _queue_list(sandbox)
    for q in post:
        if q["status"] == "needs_review":
            assert "publication" not in q, "needs_review must never be published"


# ---------------------------------------------------------------------------
# Test 7: published item records message_id, topic_id, published_at
# ---------------------------------------------------------------------------


def test_7_published_item_records_metadata(
    sandbox, seed_headlines, telegram_safe_env, telegram_http_mock
):
    """Successful publish persists publication{} on the queue item AND
    appends a row to published_<DAY>.json."""
    headline = seed_headlines["items"][1]
    seeded = make_validated_medium_draft(headline)
    _seed_drafts_file(sandbox, [seeded])
    assert run_queue.run() == 0
    assert run_publish.run() == 0

    items = _queue_list(sandbox)
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "published"

    pub = item.get("publication")
    assert isinstance(pub, dict), "publication block must be persisted"
    assert isinstance(pub.get("message_id"), int) and pub["message_id"] > 0
    assert pub.get("topic_id"), "topic_id must be set"
    assert pub.get("topic_env"), "topic_env must be set"
    assert pub.get("chat_id") == telegram_safe_env
    assert pub.get("published_at"), "published_at must be set"
    # ISO 8601 UTC sanity.
    datetime.strptime(pub["published_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    assert pub.get("http_status") == 200
    assert pub.get("attempts") >= 1

    # published_<DAY>.json append-only audit log.
    assert sandbox.published_path.exists()
    log = _load_json(sandbox.published_path)
    assert isinstance(log, list) and len(log) == 1
    entry = log[0]
    assert entry["message_id"] == pub["message_id"]
    assert entry["queue_id"] == item["queue_id"]
    assert entry["topic_id"] == pub["topic_id"]
    assert entry["published_at"] == pub["published_at"]


# ---------------------------------------------------------------------------
# Test 8: duplicate headline does not create duplicate draft / queue item
# ---------------------------------------------------------------------------


def test_8_duplicate_headline_does_not_duplicate(sandbox, seed_headlines):
    """Same headlines file, two drafting + queue passes → no duplicate items."""
    # First pass.
    assert run_drafting.run() == 0
    assert run_queue.run() == 0
    drafts_first = _drafts_list(sandbox)
    queue_first = _queue_list(sandbox)
    n_drafts_first = len(drafts_first)
    n_queue_first = len(queue_first)
    assert n_drafts_first == 2  # fixture has 1 duplicate already → 2 unique
    assert n_queue_first == 2

    # Second pass — same headlines file untouched.
    assert run_drafting.run() == 0
    assert run_queue.run() == 0
    drafts_second = _drafts_list(sandbox)
    queue_second = _queue_list(sandbox)

    assert len(drafts_second) == n_drafts_first, (
        f"drafting must be idempotent: {n_drafts_first} → {len(drafts_second)}"
    )
    assert len(queue_second) == n_queue_first, (
        f"queue must be idempotent: {n_queue_first} → {len(queue_second)}"
    )

    # draft_ids stay unique.
    seen_draft_ids = [d["draft_id"] for d in drafts_second]
    assert len(seen_draft_ids) == len(set(seen_draft_ids))
    # queue_ids stay unique.
    seen_queue_ids = [q["queue_id"] for q in queue_second]
    assert len(seen_queue_ids) == len(set(seen_queue_ids))


# ---------------------------------------------------------------------------
# Stage-level log artefact verification
# ---------------------------------------------------------------------------


def test_stage_logs_are_produced(
    sandbox, seed_headlines, telegram_safe_env, telegram_http_mock
):
    """Each stage writes its own rotating log under sandbox.logs."""
    assert run_drafting.run() == 0  # produces drafting_<DAY>.log

    # Seed a ready_to_publish so the publish stage actually does work.
    headline = seed_headlines["items"][1]
    seeded = make_validated_medium_draft(headline)
    _seed_drafts_file(sandbox, [seeded])
    assert run_queue.run() == 0
    assert run_publish.run() == 0

    expected = [
        sandbox.logs / f"drafting_{sandbox.day}.log",
        sandbox.logs / f"queue_{sandbox.day}.log",
        sandbox.logs / f"publish_{sandbox.day}.log",
    ]
    for p in expected:
        assert p.exists(), f"missing stage log: {p}"
        assert p.stat().st_size > 0, f"stage log empty: {p}"
