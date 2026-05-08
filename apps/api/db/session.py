"""
DB engine and session. Connection pooling with defensive handling.
Session logic lives here; db/__init__.py re-exports for compatibility.
Uses secrets provider for DATABASE_URL (no hardcoding).
"""
import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from apps.shared.config import DATABASE_URL_DEFAULT
from apps.shared.env_helpers import parse_int
from apps.shared.secrets import get_secret

logger = logging.getLogger(__name__)

# Pool config: env overrides with safe defaults
POOL_SIZE = parse_int(get_secret("DB_POOL_SIZE", ""), default=5, min_val=1, name="DB_POOL_SIZE")
MAX_OVERFLOW = parse_int(get_secret("DB_MAX_OVERFLOW", ""), default=10, min_val=0, name="DB_MAX_OVERFLOW")
POOL_RECYCLE = parse_int(get_secret("DB_POOL_RECYCLE", ""), default=1800, min_val=60, name="DB_POOL_RECYCLE")  # 30 min
POOL_TIMEOUT = parse_int(get_secret("DB_POOL_TIMEOUT", ""), default=30, min_val=1, name="DB_POOL_TIMEOUT")


def get_engine() -> Engine:
    url = get_secret("DATABASE_URL", DATABASE_URL_DEFAULT)
    engine = create_engine(
        url,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE,
        pool_timeout=POOL_TIMEOUT,
    )
    return engine


engine = get_engine()
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)


def init_db() -> None:
    """
    Bootstrap DB: use Alembic when present and applicable; otherwise fall back to Base.metadata.create_all().
    Logs which path was taken. Idempotent.
    """
    import subprocess
    import sys
    from pathlib import Path

    _here = Path(__file__).resolve().parent  # db/
    alembic_ini: Path | None = None
    if Path("/app/alembic.ini").exists():
        alembic_ini = Path("/app/alembic.ini")
        cwd = "/app"
    else:
        for candidate in (_here.parent.parent.parent.parent, _here.parent.parent.parent, _here.parent.parent):
            if (candidate / "alembic.ini").exists():
                alembic_ini = candidate / "alembic.ini"
                cwd = str(candidate)
                break
        else:
            cwd = str(_here.parent.parent)

    # Alembic present: try upgrade head first
    if alembic_ini is not None and alembic_ini.exists():
        alembic_dir = alembic_ini.parent / "alembic"
        if alembic_dir.is_dir():
            cmd = (
                [sys.executable, "-m", "alembic", "-c", str(alembic_ini), "upgrade", "head"]
                if str(alembic_ini).startswith("/app")
                else [sys.executable, "-m", "alembic", "upgrade", "head"]
            )
            result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            if result.returncode == 0:
                logger.info("DB bootstrap: alembic upgrade head succeeded")
                return
            logger.warning(
                "DB bootstrap: alembic upgrade head failed (exit %s), falling back to create_all: %s",
                result.returncode,
                (result.stderr or result.stdout or "").strip()[:500],
            )

    # Fallback: no Alembic or upgrade failed (e.g. no revisions yet) — create_all for fresh envs
    from apps.api.db.models import Base
    Base.metadata.create_all(engine)
    logger.info("DB bootstrap: Base.metadata.create_all (fallback; alembic not present or no revisions)")


def _safe_close_session(session: Session | None) -> None:
    """Defensive close: always releases connection; logs on error."""
    if session is None:
        return
    try:
        session.close()
    except DBAPIError as e:
        logger.warning("Session close after DB error: %s", e)
    except Exception as e:
        logger.warning("Session close error: %s", e)


def check_db() -> bool:
    """Return True if DB is reachable. Uses pool; recycles stale connections via pool_pre_ping."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except DBAPIError as e:
        logger.debug("DB unreachable: %s", e)
        return False
    except Exception:
        return False


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Context manager: yields session, commits on success, rollback on error, always closes."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        _safe_close_session(session)


def get_db_dependency() -> Generator[Session, None, None]:
    """FastAPI dependency: yields session; caller commits. Always closes on exit."""
    session = SessionLocal()
    try:
        yield session
    finally:
        _safe_close_session(session)
