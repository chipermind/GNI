"""Smoke tests for the V1 Telegram publisher in --prod mode.

Covers the governance contract for the publish stage:
  - status allowlist: only ready_to_publish is sent
  - duplicate protection via headline_hash_key in published_YYYYMMDD.json
  - guard validation runs before send; on failure → failed_guard
  - missing topic env → main-channel fallback (not a failure)
  - on success: queue item carries publication block + status="published"
  - on failure: queue item stays ready_to_publish, error scrubbed of token

No real network: telegram_publisher.send_to_telegram is monkeypatched.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gni.editorial_queue import queue_manager
from gni.publisher import run_publish, telegram_publisher


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _radar_payload(title: str = "Tech update") -> dict:
    return {
        "template": "RADAR",
        "title": f"🟡 {title}",
        "signal": title,
        "context": title,
        "probability": "medium",
        "source": "https://example.com/x",
        "implication": "Implication requires operator review.",
    }


def _make_queue_item(
    *,
    queue_id: str,
    status: str = "ready_to_publish",
    template: str = "RADAR",
    priority: str = "medium",
    headline_hash_key: str = "h-1",
    payload: dict | None = None,
) -> dict:
    return {
        "queue_id": queue_id,
        "draft_id": f"draft_{queue_id}",
        "headline_hash_key": headline_hash_key,
        "template": template,
        "priority": priority,
        "status": status,
        "payload": payload if payload is not None else _radar_payload(),
        "source_item": {"category": "tech", "title": "x"},
        "created_at": "2026-05-06T00:00:00Z",
        "updated_at": "2026-05-06T00:00:00Z",
        "review_notes": "",
    }


def _bootstrap_dirs(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    queue_dir = tmp_path / "queue"
    published_dir = tmp_path / "published"
    log_dir = tmp_path / "logs"
    lock_path = tmp_path / "publish.lock"
    for d in (queue_dir, published_dir, log_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run_publish, "QUEUE_DIR", queue_dir, raising=True)
    monkeypatch.setattr(run_publish, "PUBLISHED_DIR", published_dir, raising=True)
    monkeypatch.setattr(run_publish, "LOG_DIR", log_dir, raising=True)
    monkeypatch.setattr(run_publish, "LOCK_PATH", lock_path, raising=True)
    return queue_dir, published_dir, log_dir


def _write_queue(queue_dir: Path, items: list[dict]) -> Path:
    day = run_publish._today_utc_str()
    qpath = queue_dir / f"queue_{day}.json"
    queue_manager.save_queue(qpath, items, day_utc=day)
    return qpath


def _read_summary(capsys) -> dict:
    captured = capsys.readouterr().out.strip().splitlines()
    # Last stdout line is the JSON summary.
    return json.loads(captured[-1])


def _set_creds(monkeypatch) -> None:
    monkeypatch.setenv(telegram_publisher.ENV_TOKEN, "FAKE-TOKEN-123")
    monkeypatch.setenv(telegram_publisher.ENV_CHAT, "-100123")
    monkeypatch.delenv(telegram_publisher.ENV_DRY_RUN, raising=False)
    # Clear all topic envs by default.
    for env in (
        telegram_publisher.ENV_TOPIC_ALERTS,
        telegram_publisher.ENV_TOPIC_GEOPOLITICS,
        telegram_publisher.ENV_TOPIC_CYBER,
        telegram_publisher.ENV_TOPIC_AI,
        telegram_publisher.ENV_TOPIC_MARKETS,
        telegram_publisher.ENV_TOPIC_COMMUNITY,
    ):
        monkeypatch.delenv(env, raising=False)


class _FakePassValidator:
    """Validator stub that always passes — keeps publish tests independent
    of the editorial lexicon evolving."""
    class _OkResult:
        ok = True
        violations: list = []
        first_reason = ""

    def validate(self, payload):
        return self._OkResult()


def _stub_validator_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        run_publish, "get_editorial_validator", lambda: _FakePassValidator()
    )


# ---------------------------------------------------------------------------
# 1. Forbidden statuses are never sent
# ---------------------------------------------------------------------------


def test_publisher_skips_all_forbidden_statuses(tmp_path, monkeypatch, capsys):
    queue_dir, _, _ = _bootstrap_dirs(tmp_path, monkeypatch)
    _set_creds(monkeypatch)

    forbidden = [
        "ignored", "needs_review", "failed_guard", "needs_editorial_build",
        "failed_processing", "rejected", "published",
    ]
    items = [
        _make_queue_item(queue_id=f"q{i}", status=s, headline_hash_key=f"h{i}")
        for i, s in enumerate(forbidden)
    ]
    _write_queue(queue_dir, items)

    sent: list[dict] = []

    def fake_send(*args, **kwargs):
        sent.append(kwargs)
        return {"ok": True, "message_id": 1, "error": None,
                "http_status": 200, "retry_after": 0, "attempts": 1}

    monkeypatch.setattr(telegram_publisher, "send_to_telegram", fake_send)

    rc = run_publish.run(prod_mode=True)
    assert rc == 0
    assert sent == [], "publisher must NEVER send a forbidden-status item"
    summary = _read_summary(capsys)
    assert summary["items_published"] == 0
    assert summary["items_blocked_status"] == len(forbidden)


# ---------------------------------------------------------------------------
# 2. Happy path: ready_to_publish + valid payload + creds → sent + logged
# ---------------------------------------------------------------------------


def test_publisher_publishes_ready_item_and_writes_log(tmp_path, monkeypatch, capsys):
    queue_dir, published_dir, _ = _bootstrap_dirs(tmp_path, monkeypatch)
    _set_creds(monkeypatch)
    _stub_validator_pass(monkeypatch)

    item = _make_queue_item(queue_id="q1", headline_hash_key="abc")
    qpath = _write_queue(queue_dir, [item])

    sent_calls: list[dict] = []

    def fake_send(text, *, token, chat_id, message_thread_id=None, timeout=None):
        sent_calls.append({
            "text": text, "chat_id": chat_id,
            "message_thread_id": message_thread_id,
        })
        return {"ok": True, "message_id": 4242, "error": None,
                "http_status": 200, "retry_after": 0, "attempts": 1}

    monkeypatch.setattr(telegram_publisher, "send_to_telegram", fake_send)

    rc = run_publish.run(prod_mode=True)
    assert rc == 0
    assert len(sent_calls) == 1
    summary = _read_summary(capsys)
    assert summary["items_published"] == 1
    assert summary["items_failed_telegram"] == 0
    assert summary["items_blocked_status"] == 0

    # Queue updated in place.
    queue_after = queue_manager.load_queue(qpath)
    assert queue_after[0]["status"] == "published"
    assert queue_after[0]["publication"]["message_id"] == 4242

    # Published log persisted with required fields.
    day = run_publish._today_utc_str()
    log_path = published_dir / f"published_{day}.json"
    assert log_path.exists()
    entries = json.loads(log_path.read_text("utf-8"))
    assert len(entries) == 1
    e = entries[0]
    for k in ("message_id", "topic_id", "published_at",
              "headline_hash_key", "template", "priority"):
        assert k in e, f"published log missing {k}"
    assert e["headline_hash_key"] == "abc"
    assert e["message_id"] == 4242


# ---------------------------------------------------------------------------
# 3. Duplicate protection: same hash_key in published log → skip
# ---------------------------------------------------------------------------


def test_publisher_skips_duplicate_hash_key(tmp_path, monkeypatch, capsys):
    queue_dir, published_dir, _ = _bootstrap_dirs(tmp_path, monkeypatch)
    _set_creds(monkeypatch)
    _stub_validator_pass(monkeypatch)

    # Pre-existing published log with hash_key=dup1.
    day = run_publish._today_utc_str()
    pre_log = published_dir / f"published_{day}.json"
    pre_log.write_text(json.dumps([
        {"headline_hash_key": "dup1", "message_id": 1, "topic_id": None,
         "published_at": "2026-05-06T00:00:00Z",
         "template": "RADAR", "priority": "medium"}
    ]), encoding="utf-8")

    item = _make_queue_item(queue_id="q1", headline_hash_key="dup1")
    qpath = _write_queue(queue_dir, [item])

    sent: list = []
    monkeypatch.setattr(
        telegram_publisher, "send_to_telegram",
        lambda *a, **kw: sent.append(kw) or {"ok": True, "message_id": 1,
                                             "error": None, "http_status": 200,
                                             "retry_after": 0, "attempts": 1},
    )

    rc = run_publish.run(prod_mode=True)
    assert rc == 0
    assert sent == [], "duplicate must NOT be re-sent"
    summary = _read_summary(capsys)
    assert summary["items_duplicate_blocked"] == 1
    assert summary["items_published"] == 0
    queue_after = queue_manager.load_queue(qpath)
    assert queue_after[0]["status"] == "published"
    assert "duplicate_blocked" in (queue_after[0]["review_notes"] or "")


# ---------------------------------------------------------------------------
# 4. Guard validation runs before send; failure → failed_guard, not sent
# ---------------------------------------------------------------------------


def test_publisher_marks_failed_guard_when_validator_fails(
    tmp_path, monkeypatch, capsys
):
    queue_dir, _, _ = _bootstrap_dirs(tmp_path, monkeypatch)
    _set_creds(monkeypatch)

    item = _make_queue_item(queue_id="q1", headline_hash_key="abc")
    qpath = _write_queue(queue_dir, [item])

    class _FakeViolation:
        code = "fake_violation"
        field = "title"
        match = "x"

    class _FakeResult:
        ok = False
        violations = [_FakeViolation()]
        first_reason = "fake_violation"

    class _FakeValidator:
        def validate(self, payload):
            return _FakeResult()

    monkeypatch.setattr(
        run_publish, "get_editorial_validator",
        lambda: _FakeValidator(),
    )

    sent: list = []
    monkeypatch.setattr(
        telegram_publisher, "send_to_telegram",
        lambda *a, **kw: sent.append(kw) or {"ok": True, "message_id": 1,
                                             "error": None, "http_status": 200,
                                             "retry_after": 0, "attempts": 1},
    )

    rc = run_publish.run(prod_mode=True)
    assert rc == 0
    assert sent == [], "guard failure must block send"
    summary = _read_summary(capsys)
    assert summary["items_failed_guard"] == 1
    assert summary["items_published"] == 0
    queue_after = queue_manager.load_queue(qpath)
    assert queue_after[0]["status"] == "failed_guard"
    assert "guard_failed" in (queue_after[0]["review_notes"] or "")


# ---------------------------------------------------------------------------
# 5. Topic env unset → main-channel fallback (still publishes)
# ---------------------------------------------------------------------------


def test_publisher_falls_back_to_main_channel_when_topic_unset(
    tmp_path, monkeypatch, capsys
):
    queue_dir, _, _ = _bootstrap_dirs(tmp_path, monkeypatch)
    _set_creds(monkeypatch)
    _stub_validator_pass(monkeypatch)
    # Force routing into ALERTS but leave the env unset.
    item = _make_queue_item(queue_id="q1", priority="high",
                            headline_hash_key="abc")
    _write_queue(queue_dir, [item])

    captured: list[dict] = []

    def fake_send(text, *, token, chat_id, message_thread_id=None, timeout=None):
        captured.append({"thread": message_thread_id})
        return {"ok": True, "message_id": 9, "error": None,
                "http_status": 200, "retry_after": 0, "attempts": 1}

    monkeypatch.setattr(telegram_publisher, "send_to_telegram", fake_send)

    rc = run_publish.run(prod_mode=True)
    assert rc == 0
    assert len(captured) == 1
    # Fallback to main channel = no message_thread_id.
    assert captured[0]["thread"] is None
    summary = _read_summary(capsys)
    assert summary["items_published"] == 1
    assert summary["items_topic_fallback"] == 1


# ---------------------------------------------------------------------------
# 6. Telegram failure → ready_to_publish kept; no token in note
# ---------------------------------------------------------------------------


def test_publisher_keeps_ready_on_telegram_failure_and_redacts_token(
    tmp_path, monkeypatch, capsys
):
    queue_dir, _, _ = _bootstrap_dirs(tmp_path, monkeypatch)
    _set_creds(monkeypatch)
    _stub_validator_pass(monkeypatch)

    item = _make_queue_item(queue_id="q1", headline_hash_key="abc")
    qpath = _write_queue(queue_dir, [item])

    def fake_send(*args, **kwargs):
        return {
            "ok": False,
            "message_id": None,
            "error": "POST https://api.telegram.org/botFAKE-TOKEN-123/sendMessage 500",
            "http_status": 500,
            "retry_after": 0,
            "attempts": 3,
        }

    monkeypatch.setattr(telegram_publisher, "send_to_telegram", fake_send)

    rc = run_publish.run(prod_mode=True)
    assert rc == 0
    summary = _read_summary(capsys)
    assert summary["items_failed_telegram"] == 1
    assert summary["items_published"] == 0

    queue_after = queue_manager.load_queue(qpath)
    after = queue_after[0]
    assert after["status"] == "ready_to_publish"
    note = after.get("review_notes") or ""
    # Token must be redacted.
    assert "FAKE-TOKEN-123" not in note
    assert "<redacted>" in note or "publish_failed" in note


# ---------------------------------------------------------------------------
# 7. --prod overrides TELEGRAM_DRY_RUN=1
# ---------------------------------------------------------------------------


def test_prod_mode_overrides_dry_run_env(tmp_path, monkeypatch, capsys):
    queue_dir, _, _ = _bootstrap_dirs(tmp_path, monkeypatch)
    _set_creds(monkeypatch)
    _stub_validator_pass(monkeypatch)
    monkeypatch.setenv(telegram_publisher.ENV_DRY_RUN, "1")

    item = _make_queue_item(queue_id="q1", headline_hash_key="abc")
    _write_queue(queue_dir, [item])

    sent: list = []
    monkeypatch.setattr(
        telegram_publisher, "send_to_telegram",
        lambda *a, **kw: sent.append(kw) or {"ok": True, "message_id": 1,
                                             "error": None, "http_status": 200,
                                             "retry_after": 0, "attempts": 1},
    )

    rc = run_publish.run(prod_mode=True)
    assert rc == 0
    assert len(sent) == 1, "--prod must override dry-run env"
    summary = _read_summary(capsys)
    assert summary["prod_mode"] is True
    assert summary["dry_run"] is False
    assert summary["items_published"] == 1


# ---------------------------------------------------------------------------
# 8. Spec counter shape
# ---------------------------------------------------------------------------


def test_summary_contains_all_spec_counters(tmp_path, monkeypatch, capsys):
    queue_dir, _, _ = _bootstrap_dirs(tmp_path, monkeypatch)
    _set_creds(monkeypatch)
    _stub_validator_pass(monkeypatch)
    _write_queue(queue_dir, [])  # empty

    # Empty queue exits early; reseed with an item to force the loop.
    item = _make_queue_item(queue_id="q1", headline_hash_key="abc")
    _write_queue(queue_dir, [item])

    monkeypatch.setattr(
        telegram_publisher, "send_to_telegram",
        lambda *a, **kw: {"ok": True, "message_id": 1, "error": None,
                          "http_status": 200, "retry_after": 0, "attempts": 1},
    )
    rc = run_publish.run(prod_mode=True)
    assert rc == 0
    s = _read_summary(capsys)
    for k in (
        "items_checked", "items_published", "items_blocked_status",
        "items_failed_guard", "items_duplicate_blocked", "items_failed_telegram",
    ):
        assert k in s, f"summary missing required counter {k}"
