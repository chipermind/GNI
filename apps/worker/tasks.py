"""
Pipeline: scoring → LLM classify+generate → render → publish (Telegram + Make).
Ingest is handled by the collector service. Idempotency via items.status.
Internal scheduler: run every RUN_EVERY_MINUTES.
Uses batch operations and bounded parallelism to reduce DB round trips.
Graceful shutdown: SIGTERM stops new work, finishes current task or exits.
"""
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ensure repo root on path when run as __main__ or from worker container
_repo = Path(__file__).resolve().parent.parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from sqlalchemy import func

from apps.api.db import SessionLocal, init_db
from apps.shared.config import ConfigError, validate_config
from apps.shared.env_helpers import get_int_env, parse_int
from apps.shared.env_validation import EnvValidationError, validate_env
from apps.api.db.models import DeadLetterQueue, Draft, EventsLog, Item, Publication
from apps.api.settings import get_settings
from apps.worker.cache import get_score_cached, set_score_cached
from apps.worker.scoring import score_item
from apps.worker.llm import run_classify_then_generate
from apps.worker.llm.ollama_ensure import ensure_ollama_model
from apps.worker.render import render
from apps.worker.safety import PublishPausedError, assert_publish_allowed
from apps.publisher.delivery import deliver_message, DeliveryResult
from apps.publisher.make_webhook import send_make_webhook
from apps.publisher.rate_limit import (
    RateLimitExceededError,
    check_rate_limit,
    log_rate_limit_event,
)

try:
    from apps.observability.logging import get_logger
    from apps.observability.metrics import record_pipeline_step
    from apps.observability.tracing import get_tracer
    _log = get_logger("apps.worker.tasks")
    _tracer = get_tracer("gni-worker", "1.0")
    _has_obs = True
except ImportError:
    _log = None
    _tracer = None
    _has_obs = False


def _log_info(msg: str, **kw: Any) -> None:
    if _log:
        _log.info(msg, **kw)
    else:
        print(f"{msg} {kw}" if kw else msg)


RUN_EVERY_MINUTES = get_int_env("RUN_EVERY_MINUTES", default=15)
TELEGRAM_SINCE_MINUTES = get_int_env("TELEGRAM_SINCE_MINUTES", default=60)
PUBLISH_MAX_WORKERS = get_int_env("PUBLISH_MAX_WORKERS", default=4)
MAX_PIPELINE_ATTEMPTS = get_int_env("MAX_PIPELINE_ATTEMPTS", default=3)
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

# Serialize rate limit check to avoid Redis race; each worker acquires before check+increment
_rate_limit_lock = threading.Lock()


def _dry_run() -> bool:
    return os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")


def _null_ctx():
    from contextlib import nullcontext
    return nullcontext()


def step_scoring(limit: int = 100, item_ids: Optional[list[int]] = None) -> int:
    """Score items with status=new; set priority, risk, template, needs_review; set status=scored."""
    init_db()
    session = SessionLocal()
    try:
        q = session.query(Item).filter(Item.status == "new")
        if item_ids:
            q = q.filter(Item.id.in_(item_ids))
        items = q.limit(limit).all()
        if not items:
            return 0
        now = datetime.now(timezone.utc)
        update_mappings = []
        for item in items:
            score = get_score_cached(item.fingerprint or "")
            if score is None:
                score = score_item(
                    title=item.title,
                    summary=item.summary,
                    source_name=item.source_name,
                )
                if item.fingerprint:
                    set_score_cached(item.fingerprint, score)
            update_mappings.append({
                "id": item.id,
                "priority": score.get("priority", 2),
                "risk": score.get("risk"),
                "template": score.get("template"),
                "needs_review": score.get("needs_review", False),
                "status": "scored",
                "updated_at": now,
            })
        session.bulk_update_mappings(Item, update_mappings)
        session.commit()
        n = len(items)
        if _has_obs and n > 0:
            record_pipeline_step("scoring", n)
        return n
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def step_llm_draft(limit: int = 20, item_ids: Optional[list[int]] = None) -> int:
    """For items status=scored: classify+generate, create Draft, set status=drafted. On error set status=failed."""
    span = _tracer.start_as_current_span("step_llm_draft") if _tracer else _null_ctx()
    with span:
        return _step_llm_draft_impl(limit=limit, item_ids=item_ids)


def _step_llm_draft_impl(limit: int = 20, item_ids: Optional[list[int]] = None) -> int:
    init_db()
    session = SessionLocal()
    dry_run = _dry_run()
    from apps.worker.llm.ollama_client import OLLAMA_BASE_URL_DEFAULT

    base_url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL_DEFAULT)
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
    try:
        q = session.query(Item).filter(Item.status == "scored")
        if item_ids:
            q = q.filter(Item.id.in_(item_ids))
        items = q.limit(limit).all()
        if not items:
            return 0
        drafts_to_add = []
        item_updates = []
        now = datetime.now(timezone.utc)
        for item in items:
            try:
                title = item.title or ""
                summary = item.summary or ""
                source_name = item.source_name or ""
                c, g = run_classify_then_generate(
                    title=title,
                    summary=summary,
                    source_name=source_name,
                    model=model,
                    base_url=base_url,
                )
                payload = g.payload or {}
                drafts_to_add.append(
                    Draft(item_id=item.id, data=payload, rendered_text=None)
                )
                item_updates.append({
                    "id": item.id,
                    "template": c.template,
                    "status": "drafted",
                    "last_error": None,
                    "updated_at": now,
                })
            except Exception as e:
                err = str(e)[:500]
                retry_count = (item.retry_count or 0) + 1
                if retry_count >= MAX_PIPELINE_ATTEMPTS:
                    session.add(
                        DeadLetterQueue(
                            item_id=item.id,
                            stage="llm_draft",
                            error=err,
                            attempts=retry_count,
                            last_seen=now,
                        )
                    )
                    item_updates.append({
                        "id": item.id,
                        "status": "dlq",
                        "last_error": err,
                        "retry_count": retry_count,
                        "updated_at": now,
                    })
                else:
                    item_updates.append({
                        "id": item.id,
                        "status": "scored",
                        "last_error": err,
                        "retry_count": retry_count,
                        "updated_at": now,
                    })
        session.add_all(drafts_to_add)
        if item_updates:
            session.bulk_update_mappings(Item, item_updates)
        session.commit()
        n = len(drafts_to_add)
        if _has_obs and n > 0:
            try:
                from apps.observability.metrics import record_drafts_generated
                record_drafts_generated(n)
            except ImportError:
                pass
        return n
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _fetch_latest_drafts_per_items(session, item_ids: list[int]) -> dict[int, Draft]:
    """Fetch latest draft per item_id in one query. Returns {item_id: Draft}."""
    if not item_ids:
        return {}
    subq = (
        session.query(Draft.item_id, func.max(Draft.id).label("max_id"))
        .filter(Draft.item_id.in_(item_ids))
        .group_by(Draft.item_id)
    ).subquery()
    drafts = (
        session.query(Draft)
        .join(subq, (Draft.item_id == subq.c.item_id) & (Draft.id == subq.c.max_id))
        .all()
    )
    return {d.item_id: d for d in drafts if d.item_id is not None}


@dataclass
class _PublishTask:
    """Immutable task data for parallel processing."""

    item_id: int
    template: Optional[str]
    priority: Optional[int]
    source_name: Optional[str]
    url: Optional[str]
    draft_id: int
    draft_data: dict[str, Any]


def _process_single_item(
    task: _PublishTask,
    settings: dict[str, Any],
    dry_run: bool,
) -> tuple[int, bool, Optional[str], Optional[str]]:
    """
    Process one item: rate limit (with lock), render, publish, update DB.
    Uses own session; commits atomically. Returns (item_id, success, rendered_text, error).
    """
    session = SessionLocal()
    try:
        _log_info(
            "publish_start",
            item_id=task.item_id,
            template=task.template or "DEFAULT",
            channel="telegram,whatsapp_web,make",
        )
        if not dry_run:
            with _rate_limit_lock:
                try:
                    check_rate_limit("telegram", settings=settings)
                    check_rate_limit("whatsapp_web", settings=settings)
                    check_rate_limit("make", settings=settings)
                except RateLimitExceededError as rle:
                    log_rate_limit_event(
                        session, rle.channel, rle.limit_type, rle.current, rle.limit
                    )
                    session.commit()
                    return (task.item_id, False, None, f"rate limited: {rle.channel}")
        payload = task.draft_data if isinstance(task.draft_data, dict) else {}
        sector = (task.source_name or "").strip() or "Sector"
        flag = ""
        messages = render(
            template=task.template or "DEFAULT",
            payload=payload,
            sector=sector,
            flag=flag,
            priority=task.priority,
        )
        if not messages:
            messages = [str(payload)[:1000]]
        rendered_text = "\n---\n".join(messages) if messages else ""
        priority = f"P{task.priority}" if task.priority is not None else "P2"

        # Primary delivery: WhatsApp if connected, else Telegram (webhook or Bot API). Idempotent by message_id.
        item = session.query(Item).filter(Item.id == task.item_id).first()
        primary_ok = False
        if item:
            primary_result = deliver_message(
                session,
                message_id=str(task.item_id),
                messages=messages,
                item=item,
                template=task.template or "DEFAULT",
                dry_run=dry_run,
            )
            primary_ok = primary_result.ok
            if primary_result.used_fallback:
                try:
                    _log_info(
                        "DELIVERY_TELEGRAM_FALLBACK",
                        item_id=task.item_id,
                        channel=primary_result.channel or "telegram",
                    )
                except Exception:
                    pass
            # Optional fallback: when primary delivery failed, try make_webhook (never blocks)
            if not primary_ok:
                try:
                    mw_result = send_make_webhook(
                        session, item,
                        rendered_text=rendered_text,
                        dry_run=dry_run,
                    )
                    if mw_result.status == "sent":
                        try:
                            _log_info("MAKE_WEBHOOK_FALLBACK_SUCCESS", item_id=task.item_id)
                        except Exception:
                            pass
                except Exception:
                    pass

            from apps.publisher.whatsapp_make import send_whatsapp_via_make

            make_result = send_whatsapp_via_make(
                session,
                item,
                rendered_text=rendered_text,
                template=task.template or "ANALISE_INTEL",
                priority=priority,
                dry_run=dry_run,
                messages=messages,
            )
            make_ok = make_result.status == "sent" or (make_result.dry_run and dry_run)
            any_channel_ok = primary_ok or make_ok
            if make_result.status == "dead_letter" and not any_channel_ok:
                raise RuntimeError(make_result.last_error or "Make webhook exhausted retries")
        else:
            any_channel_ok = False

        if not any_channel_ok:
            raise RuntimeError("No channel delivered (wa/telegram fallback, make)")

        now = datetime.now(timezone.utc)
        session.bulk_update_mappings(
            Draft,
            [{"id": task.draft_id, "rendered_text": rendered_text, "updated_at": now}],
        )
        session.bulk_update_mappings(
            Item,
            [{"id": task.item_id, "status": "published", "updated_at": now}],
        )
        session.commit()
        if _has_obs:
            try:
                from apps.observability.metrics import record_publication_success
                record_publication_success()
            except ImportError:
                pass
        return (task.item_id, True, rendered_text, None)
    except Exception as e:
        session.rollback()
        err = str(e)[:500]
        try:
            now = datetime.now(timezone.utc)
            item = session.query(Item).filter(Item.id == task.item_id).first()
            if item:
                retry_count = (item.retry_count or 0) + 1
                if retry_count >= MAX_PIPELINE_ATTEMPTS:
                    session.add(
                        DeadLetterQueue(
                            item_id=task.item_id,
                            stage="publish",
                            error=err,
                            attempts=retry_count,
                            last_seen=now,
                        )
                    )
                    session.bulk_update_mappings(
                        Item,
                        [{
                            "id": task.item_id,
                            "status": "dlq",
                            "last_error": err,
                            "retry_count": retry_count,
                            "updated_at": now,
                        }],
                    )
                else:
                    session.bulk_update_mappings(
                        Item,
                        [{
                            "id": task.item_id,
                            "status": "drafted",
                            "last_error": err,
                            "retry_count": retry_count,
                            "updated_at": now,
                        }],
                    )
            session.commit()
        except Exception:
            session.rollback()
        if _has_obs:
            try:
                from apps.observability.metrics import record_publication_failure
                record_publication_failure()
            except ImportError:
                pass
        return (task.item_id, False, None, err)
    finally:
        session.close()


def step_render_and_publish(limit: int = 20, dry_run: bool = True, item_ids_filter: Optional[list[int]] = None) -> int:
    span = _tracer.start_as_current_span("step_render_and_publish") if _tracer else _null_ctx()
    with span:
        return _step_render_and_publish_impl(limit, dry_run, item_ids_filter)


def _step_render_and_publish_impl(limit: int = 20, dry_run: bool = True, item_ids_filter: Optional[list[int]] = None) -> int:
    """For items status=drafted with draft: render, publish Telegram + Make, set status=published (or failed)."""
    init_db()
    session = SessionLocal()
    try:
        try:
            assert_publish_allowed(session)
        except PublishPausedError:
            session.add(
                EventsLog(
                    event_type="publish_blocked",
                    payload={"reason": "pause_all_publish", "message": "publish blocked by pause"},
                )
            )
            session.add(
                Publication(
                    channel="whatsapp_web",
                    status="blocked",
                    attempts=0,
                )
            )
            session.commit()
            return 0
        q = session.query(Item).filter(Item.status == "drafted")
        if item_ids_filter:
            q = q.filter(Item.id.in_(item_ids_filter))
        items = q.limit(limit).all()
        if not items:
            return 0
        item_ids = [i.id for i in items]
        draft_by_item = _fetch_latest_drafts_per_items(session, item_ids)
        settings = get_settings(session)
        now = datetime.now(timezone.utc)

        # Items without draft: fail upfront (single-threaded)
        item_updates = []
        tasks: list[_PublishTask] = []
        for item in items:
            draft = draft_by_item.get(item.id)
            if not draft or not draft.data:
                item_updates.append({
                    "id": item.id,
                    "status": "failed",
                    "last_error": "no draft data",
                    "updated_at": now,
                })
                continue
            tasks.append(
                _PublishTask(
                    item_id=item.id,
                    template=item.template,
                    priority=item.priority,
                    source_name=item.source_name,
                    url=item.url,
                    draft_id=draft.id,
                    draft_data=draft.data if isinstance(draft.data, dict) else {},
                )
            )

        if item_updates:
            session.bulk_update_mappings(Item, item_updates)
            session.commit()

        if not tasks:
            return 0

        # Process publish tasks in parallel; each worker uses own session and commits atomically
        max_workers = min(PUBLISH_MAX_WORKERS, len(tasks))
        count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_process_single_item, task, settings, dry_run): task
                for task in tasks
            }
            for future in as_completed(futures):
                try:
                    item_id, success, _, _ = future.result()
                    if success:
                        count += 1
                except Exception:
                    pass  # _process_single_item catches and updates DB

        if _has_obs and count > 0:
            record_pipeline_step("publish", count)
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_pipeline(
    dry_run: Optional[bool] = None,
    ingest_limit: Optional[int] = None,
    item_ids: Optional[list[int]] = None,
) -> dict:
    """
    Run full pipeline once: scoring → LLM draft → render & publish.
    Ingest is handled by the collector service. Idempotency: each step processes items by status.
    When item_ids is provided, only those items are processed.
    """
    if dry_run is None:
        dry_run = _dry_run()
    # Defensive: step_* expect None or list[int]; avoid crashes from wrong type (e.g. from API)
    if item_ids is not None and not isinstance(item_ids, list):
        item_ids = None
    out = {
        "scoring": 0,
        "llm_draft": 0,
        "publish": 0,
        "dry_run": dry_run,
    }
    t0 = time.perf_counter()
    out["scoring"] = step_scoring(item_ids=item_ids)
    out["llm_draft"] = step_llm_draft(item_ids=item_ids)
    out["publish"] = step_render_and_publish(dry_run=dry_run, item_ids_filter=item_ids)
    elapsed = time.perf_counter() - t0
    if _has_obs:
        try:
            from apps.observability.metrics import record_pipeline_cycle_duration
            record_pipeline_cycle_duration(elapsed)
        except ImportError:
            pass
    return out


_worker_shutdown = False

# RADAR: BRT 07=UTC10, 12=UTC15, 18=UTC21, 23=UTC02
_RADAR_UTC_HOURS = frozenset([10, 15, 21, 2])
# Fechamento 24H: BRT 23:00 = UTC 02:00
_FECHAMENTO_UTC_HOUR = 2
_last_radar_utc_hour: int = -1
_last_fechamento_utc_day: int = -1


def step_radar(dry_run: bool = False) -> bool:
    """Post GNI RADAR bulletin if current UTC hour matches a scheduled time. Returns True if posted."""
    import datetime as _dt
    global _last_radar_utc_hour
    now = _dt.datetime.now(_dt.timezone.utc)
    h = now.hour
    if h not in _RADAR_UTC_HOURS:
        return False
    if h == _last_radar_utc_hour:
        return False  # already sent this hour
    init_db()
    session = SessionLocal()
    try:
        since = now - _dt.timedelta(hours=6)
        items = (
            session.query(Item)
            .filter(Item.status == "published", Item.updated_at >= since)
            .order_by(Item.priority.asc(), Item.updated_at.desc())
            .limit(5)
            .all()
        )
        if not items:
            return False
        brt_labels = {10: "07:00", 15: "12:00", 21: "18:00", 2: "23:00"}
        hour_label = brt_labels.get(h, f"{(h - 3) % 24:02d}:00")
        from apps.worker.render import render_radar
        from apps.publisher.telegram import publish_telegram
        text = render_radar(
            [{"title": i.title, "source_name": i.source_name, "priority": i.priority} for i in items],
            hour_label=hour_label,
        )
        result = publish_telegram([text], channel="telegram_radar", dry_run=dry_run)
        _last_radar_utc_hour = h
        _log_info("RADAR posted", hour=hour_label, items=len(items), status=result.status)
        return True
    except Exception as e:
        _log_info("RADAR error", error=str(e))
        return False
    finally:
        session.close()


def step_fechamento(dry_run: bool = False) -> bool:
    """Post Fechamento 24H at BRT 23:00 (UTC 02:00). Returns True if posted."""
    import datetime as _dt
    global _last_fechamento_utc_day
    now = _dt.datetime.now(_dt.timezone.utc)
    if now.hour != _FECHAMENTO_UTC_HOUR:
        return False
    today = now.day
    if today == _last_fechamento_utc_day:
        return False
    init_db()
    session = SessionLocal()
    try:
        since = now - _dt.timedelta(hours=24)
        items = (
            session.query(Item)
            .filter(Item.status == "published", Item.updated_at >= since)
            .order_by(Item.priority.asc(), Item.updated_at.desc())
            .limit(5)
            .all()
        )
        if not items:
            return False
        from apps.worker.render import render_fechamento
        from apps.publisher.telegram import publish_telegram
        text = render_fechamento(
            [{"title": i.title, "source_name": i.source_name} for i in items],
        )
        result = publish_telegram([text], channel="telegram_fechamento", dry_run=dry_run)
        _last_fechamento_utc_day = today
        _log_info("Fechamento 24H posted", items=len(items), status=result.status)
        return True
    except Exception as e:
        _log_info("Fechamento error", error=str(e))
        return False
    finally:
        session.close()


def _worker_sigterm(signum, frame):
    global _worker_shutdown
    _worker_shutdown = True


DEGRADED_RETRY_SECONDS = 300  # 5 min between retries when Ollama model not available


def run_scheduler() -> None:
    """Loop: run_pipeline() every RUN_EVERY_MINUTES. Handles SIGTERM: stops new work, finishes current task."""
    global _worker_shutdown
    try:
        validate_env(role="worker")
    except (ConfigError, EnvValidationError) as e:
        _log_info("Startup env validation failed", error=str(e))
        raise
    signal.signal(signal.SIGTERM, _worker_sigterm)
    signal.signal(signal.SIGINT, _worker_sigterm)

    # Ensure Ollama model (pull if missing). If not available, degraded mode: retry until present (no crash-loop).
    while not _worker_shutdown:
        if ensure_ollama_model():
            break
        _log_info(
            "Ollama model not available; degraded mode. Will retry pull in %s min.",
            DEGRADED_RETRY_SECONDS // 60,
        )
        for _ in range(DEGRADED_RETRY_SECONDS):
            if _worker_shutdown:
                return
            time.sleep(1)

    interval_sec = max(1, RUN_EVERY_MINUTES * 60)
    dry_run = _dry_run()
    _log_info("Pipeline scheduler started", interval_min=RUN_EVERY_MINUTES, dry_run=dry_run)
    try:
        _log_info("step_llm_draft signature check", step_llm_draft_sig=str(inspect.signature(step_llm_draft)))
    except Exception:
        pass
    while not _worker_shutdown:
        try:
            result = run_pipeline(dry_run=dry_run)
            _log_info(
                "Pipeline run",
                scoring=result["scoring"],
                llm_draft=result["llm_draft"],
                publish=result["publish"],
            )
        except Exception as e:
            _log_info("Pipeline error", error=str(e))
        # Scheduled bulletins: RADAR (07/12/18/23 BRT) and Fechamento 24H (23 BRT)
        try:
            step_radar(dry_run=dry_run)
        except Exception as e:
            _log_info("RADAR scheduler error", error=str(e))
        try:
            step_fechamento(dry_run=dry_run)
        except Exception as e:
            _log_info("Fechamento scheduler error", error=str(e))
        for _ in range(interval_sec):
            if _worker_shutdown:
                break
            time.sleep(1)
    _log_info("Worker shutdown")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run pipeline once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Publish in dry_run mode")
    parser.add_argument("--no-dry-run", action="store_true", help="Publish for real (stubs still used)")
    args = parser.parse_args()
    if args.once:
        try:
            validate_env(role="worker")
        except (ConfigError, EnvValidationError) as e:
            _log_info("Env validation failed", error=str(e))
            raise
        dry_run = args.dry_run if args.dry_run else (not args.no_dry_run and _dry_run())
        result = run_pipeline(dry_run=dry_run)
        _log_info("Pipeline run once", **result)
    else:
        if args.dry_run:
            os.environ["DRY_RUN"] = "1"
        elif args.no_dry_run:
            os.environ["DRY_RUN"] = "0"
        run_scheduler()
