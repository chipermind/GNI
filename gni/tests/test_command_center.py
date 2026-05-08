"""Unit tests for :mod:`gni.api.command_center`.

Tests target the pure-stdlib builder ``build_command_center`` directly so
they run without FastAPI. Each test gets an isolated ``data_dir`` via
``tmp_path`` so no real ``gni/data/`` write ever occurs.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gni.api import command_center as cc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_day(
    data_dir: Path,
    *,
    day: str = "20260506",
    queue: list[dict] | None = None,
    drafts: list[dict] | None = None,
    published: list[dict] | None = None,
    heartbeat: dict | None = None,
) -> None:
    if queue is not None:
        _write_json(data_dir / "queue" / f"queue_{day}.json", {"items": queue})
    if drafts is not None:
        _write_json(data_dir / "drafts" / f"drafts_{day}.json", {"drafts": drafts})
    if published is not None:
        _write_json(data_dir / "published" / f"published_{day}.json", {"items": published})
    if heartbeat is not None:
        _write_json(data_dir / "state" / "runner_heartbeat.json", heartbeat)


def _now() -> datetime:
    return datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. Full response (happy path)
# ---------------------------------------------------------------------------


def test_full_response_shape_and_values(tmp_path: Path) -> None:
    hb_iso = (_now() - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_day(
        tmp_path,
        day="20260506",
        queue=[
            {"id": "1", "status": "ready_to_publish", "template": "FLASH",
             "priority": "high", "headline": "US sanctions Iran",
             "updated_at": "2026-05-06T11:55:00Z"},
            {"id": "2", "status": "published", "template": "FLASH",
             "priority": "high", "updated_at": "2026-05-06T11:50:00Z"},
            {"id": "3", "status": "failed_guard", "template": "ALERTA",
             "priority": "medium",
             "review_notes": ["headline_length_invalid:title"],
             "updated_at": "2026-05-06T11:30:00Z"},
            {"id": "4", "status": "needs_review", "template": "ALERTA",
             "priority": "critical", "updated_at": "2026-05-06T11:20:00Z"},
        ],
        drafts=[
            {"id": "1", "draft_status": "validated"},
            {"id": "3", "draft_status": "failed_guard"},
        ],
        published=[
            {"id": "2", "template": "FLASH",
             "published_at": "2026-05-06T11:50:00Z"},
        ],
        heartbeat={"last_heartbeat": hb_iso, "status": "running",
                   "active_workers": 2},
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )

    assert set(resp.keys()) == {
        "system_status", "queue", "runner", "tasks", "cost", "audit_mode"
    }
    assert resp["system_status"] in {"ok", "degraded", "down"}

    assert set(resp["queue"].keys()) == {
        "pending", "in_progress", "completed", "failed"
    }
    assert resp["queue"]["completed"] == 1
    assert resp["queue"]["failed"] == 1
    assert resp["queue"]["pending"] >= 2  # ready_to_publish + needs_review
    assert resp["queue"]["in_progress"] == 1  # ready_to_publish + runner running

    assert resp["runner"]["status"] == "running"
    assert resp["runner"]["active_workers"] == 2
    assert resp["runner"]["last_heartbeat"]

    assert isinstance(resp["tasks"]["last_task"], dict)
    assert resp["tasks"]["last_task"]
    assert isinstance(resp["tasks"]["running_tasks"], list)
    assert isinstance(resp["tasks"]["recent_failures"], list)

    assert isinstance(resp["cost"]["current_usage"], (int, float))
    assert isinstance(resp["cost"]["daily_estimate"], (int, float))
    assert resp["cost"]["current_usage"] >= 0
    assert resp["cost"]["daily_estimate"] >= 0

    assert isinstance(resp["audit_mode"], bool)


# ---------------------------------------------------------------------------
# 2. Empty queue
# ---------------------------------------------------------------------------


def test_empty_queue_returns_zero_counts(tmp_path: Path) -> None:
    hb_iso = (_now() - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_day(
        tmp_path, day="20260506",
        queue=[], drafts=[], published=[],
        heartbeat={"last_heartbeat": hb_iso, "status": "running",
                   "active_workers": 1},
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    assert resp["queue"] == {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0}
    assert resp["tasks"]["last_task"] == {}
    assert resp["tasks"]["running_tasks"] == []
    assert resp["tasks"]["recent_failures"] == []


# ---------------------------------------------------------------------------
# 3. Runner missing
# ---------------------------------------------------------------------------


def test_runner_missing_yields_down_status(tmp_path: Path) -> None:
    # No heartbeat file, no data files at all.
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    assert resp["runner"]["status"] == "down"
    assert resp["runner"]["last_heartbeat"] == ""
    assert resp["runner"]["active_workers"] == 0
    assert resp["system_status"] == "down"


def test_runner_stale_heartbeat_idle(tmp_path: Path) -> None:
    stale = (_now() - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_day(
        tmp_path, day="20260506", queue=[], drafts=[], published=[],
        heartbeat={"last_heartbeat": stale, "status": "running",
                   "active_workers": 1},
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    assert resp["runner"]["status"] == "idle"
    # Idle = degraded (not "ok").
    assert resp["system_status"] in {"degraded", "down"}


def test_runner_very_stale_heartbeat_down(tmp_path: Path) -> None:
    very_stale = (_now() - timedelta(seconds=1000)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_day(
        tmp_path, day="20260506", queue=[], drafts=[], published=[],
        heartbeat={"last_heartbeat": very_stale, "status": "running",
                   "active_workers": 1},
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    assert resp["runner"]["status"] == "down"


# ---------------------------------------------------------------------------
# 4. Partial data missing
# ---------------------------------------------------------------------------


def test_only_drafts_present(tmp_path: Path) -> None:
    hb_iso = (_now() - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_day(
        tmp_path, day="20260506",
        drafts=[{"id": "1", "draft_status": "validated"}],
        heartbeat={"last_heartbeat": hb_iso, "status": "running",
                   "active_workers": 1},
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    # No crash, queue is all zeros, last_task is {} (no queue/published rows).
    assert resp["queue"] == {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0}
    assert resp["tasks"]["last_task"] == {}
    assert resp["runner"]["status"] == "running"


def test_corrupt_queue_file_does_not_crash(tmp_path: Path) -> None:
    qpath = tmp_path / "queue" / "queue_20260506.json"
    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text("{ not json", encoding="utf-8")
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    assert resp["queue"] == {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0}


# ---------------------------------------------------------------------------
# 5. Cost unavailable / safe defaults
# ---------------------------------------------------------------------------


def test_cost_zero_when_no_items(tmp_path: Path) -> None:
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    assert resp["cost"]["current_usage"] == 0
    assert resp["cost"]["daily_estimate"] == 0


def test_cost_uses_static_per_item_env(monkeypatch: pytest.MonkeyPatch,
                                       tmp_path: Path) -> None:
    monkeypatch.setenv(cc.COST_PER_ITEM_ENV, "0.5")
    _seed_day(
        tmp_path, day="20260506",
        queue=[{"id": "1", "status": "validated"}],
        drafts=[{"id": "1", "draft_status": "validated"}],
        published=[{"id": "1"}],
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    # 3 items * 0.5 = 1.5
    assert resp["cost"]["current_usage"] == pytest.approx(1.5, rel=1e-6)


def test_cost_handles_invalid_env_var(monkeypatch: pytest.MonkeyPatch,
                                      tmp_path: Path) -> None:
    monkeypatch.setenv(cc.COST_PER_ITEM_ENV, "not-a-number")
    _seed_day(
        tmp_path, day="20260506",
        queue=[{"id": "1", "status": "validated"}],
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    # Falls back to default (0.001) — no crash.
    assert resp["cost"]["current_usage"] >= 0


# ---------------------------------------------------------------------------
# 6. Sanitization (no secrets)
# ---------------------------------------------------------------------------


def test_sanitization_removes_urls_paths_tokens(tmp_path: Path) -> None:
    hb_iso = (_now() - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    poison = (
        "ConnectionError: Bearer abc123secrettoken at "
        "https://internal.gni.local/api/v1/items "
        "in /Users/me/secret/path/file.py:42 "
        "with api_key=DEADBEEF1234567890ABCDEF "
        "and token f8a7d6e5c4b3a29180716253445566778899"
    )
    _seed_day(
        tmp_path, day="20260506",
        queue=[{
            "id": "1", "status": "failed_guard", "template": "FLASH",
            "priority": "high",
            "review_notes": [poison],
            "updated_at": "2026-05-06T11:55:00Z",
        }],
        heartbeat={"last_heartbeat": hb_iso, "status": "running",
                   "active_workers": 1},
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    failures = resp["tasks"]["recent_failures"]
    assert failures, "expected at least one failure"
    short_error = failures[0]["short_error"]
    # Forbidden tokens never appear.
    assert "https://internal.gni.local" not in short_error
    assert "/Users/me/secret/path/file.py" not in short_error
    assert "Bearer abc123secrettoken" not in short_error
    assert "DEADBEEF1234567890ABCDEF" not in short_error
    # Sanitized markers appear instead.
    assert "[url]" in short_error or "[redacted]" in short_error or "[path]" in short_error
    # Length cap honored.
    assert len(short_error) <= cc.MAX_TEXT_CHARS


def test_sanitization_collapses_newlines(tmp_path: Path) -> None:
    hb_iso = (_now() - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    multi_line = "first line\n  second line\n\tthird line"
    _seed_day(
        tmp_path, day="20260506",
        queue=[{
            "id": "x", "status": "failed_guard",
            "review_notes": [multi_line],
            "updated_at": "2026-05-06T11:55:00Z",
        }],
        heartbeat={"last_heartbeat": hb_iso, "status": "running",
                   "active_workers": 1},
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    short_error = resp["tasks"]["recent_failures"][0]["short_error"]
    assert "\n" not in short_error
    assert "\t" not in short_error


# ---------------------------------------------------------------------------
# 7. Degraded system case
# ---------------------------------------------------------------------------


def test_degraded_when_runner_running_but_failures_dominant(tmp_path: Path) -> None:
    hb_iso = (_now() - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_day(
        tmp_path, day="20260506",
        queue=[
            {"id": "1", "status": "failed_guard"},
            {"id": "2", "status": "failed_guard"},
            {"id": "3", "status": "failed_processing"},
            {"id": "4", "status": "validated"},
        ],
        heartbeat={"last_heartbeat": hb_iso, "status": "running",
                   "active_workers": 1},
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    assert resp["queue"]["failed"] == 3
    assert resp["queue"]["pending"] == 1
    assert resp["system_status"] == "degraded"


def test_ok_when_runner_running_and_failures_not_dominant(tmp_path: Path) -> None:
    hb_iso = (_now() - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_day(
        tmp_path, day="20260506",
        queue=[
            {"id": "1", "status": "published"},
            {"id": "2", "status": "published"},
            {"id": "3", "status": "validated"},
            {"id": "4", "status": "failed_guard"},
        ],
        heartbeat={"last_heartbeat": hb_iso, "status": "running",
                   "active_workers": 1},
    )
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    assert resp["system_status"] == "ok"


# ---------------------------------------------------------------------------
# 8. Audit mode
# ---------------------------------------------------------------------------


def test_audit_mode_off_by_default(monkeypatch: pytest.MonkeyPatch,
                                   tmp_path: Path) -> None:
    monkeypatch.delenv(cc.AUDIT_MODE_ENV, raising=False)
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    assert resp["audit_mode"] is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE"])
def test_audit_mode_truthy_values(monkeypatch: pytest.MonkeyPatch,
                                  tmp_path: Path, val: str) -> None:
    monkeypatch.setenv(cc.AUDIT_MODE_ENV, val)
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now()
    )
    assert resp["audit_mode"] is True


# ---------------------------------------------------------------------------
# 9. Heartbeat write API
# ---------------------------------------------------------------------------


def test_heartbeat_write_creates_state_dir(tmp_path: Path) -> None:
    payload = cc.heartbeat(data_dir=tmp_path, status="running",
                           active_workers=3)
    hb_path = tmp_path / "state" / "runner_heartbeat.json"
    assert hb_path.exists()
    on_disk = json.loads(hb_path.read_text(encoding="utf-8"))
    assert on_disk["status"] == "running"
    assert on_disk["active_workers"] == 3
    assert on_disk["last_heartbeat"]
    assert payload == on_disk


def test_heartbeat_write_then_read_roundtrip(tmp_path: Path) -> None:
    cc.heartbeat(data_dir=tmp_path, status="running", active_workers=1)
    resp = cc.build_command_center(
        data_dir=tmp_path, today="20260506", now=_now() + timedelta(seconds=5),
    )
    # Heartbeat fresh enough → running. (Use real "now"-ish so the heartbeat
    # we just wrote falls inside the running window.)
    resp_now = cc.build_command_center(data_dir=tmp_path)
    assert resp_now["runner"]["status"] in {"running", "idle"}


# ---------------------------------------------------------------------------
# 10. Performance smoke test
# ---------------------------------------------------------------------------


def test_response_is_fast(tmp_path: Path) -> None:
    import time

    hb_iso = (_now() - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    queue = [
        {"id": str(i), "status": "validated", "template": "ALERTA",
         "priority": "medium", "updated_at": "2026-05-06T11:00:00Z"}
        for i in range(200)
    ]
    _seed_day(
        tmp_path, day="20260506",
        queue=queue, drafts=[], published=[],
        heartbeat={"last_heartbeat": hb_iso, "status": "running",
                   "active_workers": 1},
    )
    t0 = time.perf_counter()
    cc.build_command_center(data_dir=tmp_path, today="20260506", now=_now())
    dt_ms = (time.perf_counter() - t0) * 1000
    # Generous ceiling for slow CI; production target is <100ms on local SSD.
    assert dt_ms < 500, f"build_command_center too slow: {dt_ms:.1f}ms"
