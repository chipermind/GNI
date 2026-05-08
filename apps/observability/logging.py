"""
Structured logging: JSON when LOG_JSON=1, human-readable otherwise.
Lightweight: minimal overhead, lazy init.
"""
import logging
import os
import sys
from typing import Any

LOG_JSON = os.environ.get("LOG_JSON", "").lower() in ("1", "true", "yes")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


def _json_processor(logger: Any, method_name: str, event_dict: dict) -> dict:
    """Add timestamp and level for JSON output."""
    import time
    event_dict["timestamp"] = time.time()
    event_dict["level"] = method_name.upper()
    return event_dict


def get_logger(name: str) -> Any:
    """
    Return a structured logger. When LOG_JSON=1, outputs JSON lines.
    Otherwise human-readable. Use: logger.info("msg", key=val, ...)
    Falls back to standard logging when structlog not installed.
    """
    try:
        import structlog
    except ImportError:
        log = logging.getLogger(name)
        log.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        return _FallbackLogger(log)

    if getattr(get_logger, "_configured", False):
        return structlog.get_logger(name)

    with _get_config_lock():
        if getattr(get_logger, "_configured", False):
            return structlog.get_logger(name)
        _configure_structlog()
        setattr(get_logger, "_configured", True)
    return structlog.get_logger(name)


_config_lock: Any = None


class _FallbackLogger:
    """Fallback when structlog not installed: info(msg, **kw) -> log.info(msg)."""

    def __init__(self, log: logging.Logger):
        self._log = log

    def info(self, msg: str, **kw: Any) -> None:
        extra = f" {kw}" if kw else ""
        self._log.info("%s%s", msg, extra)

    def warning(self, msg: str, **kw: Any) -> None:
        extra = f" {kw}" if kw else ""
        self._log.warning("%s%s", msg, extra)

    def error(self, msg: str, **kw: Any) -> None:
        extra = f" {kw}" if kw else ""
        self._log.error("%s%s", msg, extra)


def _get_config_lock():
    import threading
    global _config_lock
    if _config_lock is None:
        _config_lock = threading.Lock()
    return _config_lock


def _configure_structlog() -> None:
    import structlog

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
    ]
    if LOG_JSON:
        structlog.configure(
            processors=shared_processors
            + [
                _json_processor,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, LOG_LEVEL, logging.INFO)
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        )
    else:
        structlog.configure(
            processors=shared_processors
            + [
                structlog.dev.ConsoleRenderer(colors=False),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, LOG_LEVEL, logging.INFO)
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        )
