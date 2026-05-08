"""Shared E2E pytest fixtures.

Provides:
  - sandbox dirs (per-test) so the run never touches real ``gni/data/``.
  - safe-mode env (TELEGRAM_TEST_CHAT_ID required, prod chat var blocked).
  - Telegram HTTP mock (no egress possible — all _post_json calls captured).

ALL pipeline modules' module-level path constants are redirected via
monkeypatch so each orchestrator (ingestion, drafting, queue, publisher,
approve CLI) reads/writes inside the sandbox only.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Topic IDs used during the test (route_topic env vars).
_TEST_TOPIC_IDS = {
    "TELEGRAM_TOPIC_ALERTS": "9001",
    "TELEGRAM_TOPIC_GEOPOLITICS": "9002",
    "TELEGRAM_TOPIC_CYBER": "9003",
    "TELEGRAM_TOPIC_AI": "9004",
    "TELEGRAM_TOPIC_MARKETS": "9005",
    "TELEGRAM_TOPIC_COMMUNITY": "9006",
}

# Production env vars we explicitly refuse to use during E2E.
_PROD_FORBIDDEN_VALUE_ENVS = ("TELEGRAM_CHAT_ID_PROD", "TELEGRAM_CHAT_ID_PRODUCTION")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


@pytest.fixture(scope="session", autouse=True)
def _safety_preflight():
    """Hard-fail if the test channel is not configured.

    Per spec: ``TELEGRAM_TEST_CHAT_ID`` MUST be set; production chat IDs
    must NOT leak into this test process.
    """
    test_chat = os.environ.get("TELEGRAM_TEST_CHAT_ID", "").strip()
    if not test_chat:
        pytest.exit(
            "E2E ABORT: TELEGRAM_TEST_CHAT_ID is not set. "
            "Set it to a TEST-ONLY Telegram chat id before running E2E.",
            returncode=2,
        )
    for forbidden in _PROD_FORBIDDEN_VALUE_ENVS:
        v = os.environ.get(forbidden)
        if v and v == test_chat:
            pytest.exit(
                f"E2E ABORT: {forbidden} == TELEGRAM_TEST_CHAT_ID. "
                "Refusing to run E2E with a production chat id.",
                returncode=2,
            )
    yield


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Per-test sandbox: redirects every pipeline path under tmp_path.

    Returns an object exposing the redirected dirs so tests can assert on
    artefacts directly without re-deriving paths.
    """
    # Lay out the sandbox to mirror the prod tree.
    raw_dir = tmp_path / "gni" / "data" / "raw"
    state_dir = tmp_path / "gni" / "data" / "state"
    drafts_dir = tmp_path / "gni" / "data" / "drafts"
    queue_dir = tmp_path / "gni" / "data" / "queue"
    published_dir = tmp_path / "gni" / "data" / "published"
    log_dir = tmp_path / "gni" / "logs"
    for d in (raw_dir, state_dir, drafts_dir, queue_dir, published_dir, log_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Locks: each module has its own .lock; redirect all to the sandbox.
    locks = {
        "ingestion": tmp_path / "gni" / "ingestion" / ".lock",
        "drafting": tmp_path / "gni" / "drafting" / ".lock",
        "queue": tmp_path / "gni" / "editorial_queue" / ".lock",
        "publish": tmp_path / "gni" / "publisher" / ".lock",
    }
    for p in locks.values():
        p.parent.mkdir(parents=True, exist_ok=True)

    # Imports happen lazily so the monkeypatch is applied to live modules.
    from gni.ingestion import run_ingestion as ing
    from gni.drafting import run_drafting as drf
    from gni.editorial_queue import run_queue as rq
    from gni.editorial_queue import approve as appr
    from gni.publisher import run_publish as pub

    # Redirect path constants on each orchestrator.
    monkeypatch.setattr(ing, "DATA_DIR", raw_dir, raising=True)
    monkeypatch.setattr(ing, "STATE_DIR", state_dir, raising=True)
    monkeypatch.setattr(ing, "STATE_PATH", state_dir / "seen_hashes.json", raising=True)
    monkeypatch.setattr(ing, "LOG_DIR", log_dir, raising=True)
    monkeypatch.setattr(ing, "LOCK_PATH", locks["ingestion"], raising=True)

    monkeypatch.setattr(drf, "RAW_DIR", raw_dir, raising=True)
    monkeypatch.setattr(drf, "DRAFTS_DIR", drafts_dir, raising=True)
    monkeypatch.setattr(drf, "LOG_DIR", log_dir, raising=True)
    monkeypatch.setattr(drf, "LOCK_PATH", locks["drafting"], raising=True)

    monkeypatch.setattr(rq, "DRAFTS_DIR", drafts_dir, raising=True)
    monkeypatch.setattr(rq, "QUEUE_DIR", queue_dir, raising=True)
    monkeypatch.setattr(rq, "LOG_DIR", log_dir, raising=True)
    monkeypatch.setattr(rq, "LOCK_PATH", locks["queue"], raising=True)

    monkeypatch.setattr(appr, "QUEUE_DIR", queue_dir, raising=True)
    monkeypatch.setattr(appr, "LOCK_PATH", locks["queue"], raising=True)  # shared lock

    monkeypatch.setattr(pub, "QUEUE_DIR", queue_dir, raising=True)
    monkeypatch.setattr(pub, "PUBLISHED_DIR", published_dir, raising=True)
    monkeypatch.setattr(pub, "LOG_DIR", log_dir, raising=True)
    monkeypatch.setattr(pub, "LOCK_PATH", locks["publish"], raising=True)

    class _Sandbox:
        day = _today()
        root = tmp_path
        raw = raw_dir
        state = state_dir
        drafts = drafts_dir
        queue = queue_dir
        published = published_dir
        logs = log_dir

        @property
        def headlines_path(self):
            return raw_dir / f"headlines_{self.day}_UTC.json"

        @property
        def drafts_path(self):
            return drafts_dir / f"drafts_{self.day}.json"

        @property
        def queue_path(self):
            return queue_dir / f"queue_{self.day}.json"

        @property
        def published_path(self):
            return published_dir / f"published_{self.day}.json"

        @property
        def approvals_path(self):
            return queue_dir / f"approvals_{self.day}.json"

    return _Sandbox()


@pytest.fixture
def telegram_safe_env(monkeypatch):
    """Set test-channel env, populate topic IDs, and sanitize prod vars.

    Combined with ``telegram_http_mock`` below this guarantees no real
    Telegram API call is made even if credentials happen to be valid.
    """
    test_chat = os.environ["TELEGRAM_TEST_CHAT_ID"].strip()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "E2E_TEST_TOKEN_NOT_REAL")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", test_chat)
    for k, v in _TEST_TOPIC_IDS.items():
        monkeypatch.setenv(k, v)
    # Belt-and-suspenders: clear any prod chat id env that might be in scope.
    for forbidden in _PROD_FORBIDDEN_VALUE_ENVS:
        monkeypatch.delenv(forbidden, raising=False)
    monkeypatch.delenv("TELEGRAM_DRY_RUN", raising=False)
    return test_chat


class FakeTelegram:
    """Records every send, returns canned success. Never opens a socket."""

    def __init__(self):
        self.calls: list[dict] = []
        self._next_message_id = 10_000

    def __call__(self, url, payload, timeout):  # signature of _post_json
        self._next_message_id += 1
        self.calls.append({"url": url, "payload": payload, "timeout": timeout})
        # Telegram-shaped success envelope.
        return 200, {
            "ok": True,
            "result": {
                "message_id": self._next_message_id,
                "chat": {"id": payload.get("chat_id")},
                "text": payload.get("text", ""),
            },
        }


@pytest.fixture
def telegram_http_mock(monkeypatch):
    """Replace _post_json so no HTTP goes out under any circumstance."""
    from gni.publisher import telegram_publisher

    fake = FakeTelegram()
    monkeypatch.setattr(telegram_publisher, "_post_json", fake)
    return fake


@pytest.fixture
def seed_headlines(sandbox):
    """Copy the fixture headlines file into the sandbox's raw dir.

    Re-stamps ``day_utc`` so the day-name resolution in run_drafting picks it up.
    """
    src = FIXTURES / "sample_headlines.json"
    payload = json.loads(src.read_text(encoding="utf-8"))
    payload["day_utc"] = sandbox.day
    sandbox.headlines_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


