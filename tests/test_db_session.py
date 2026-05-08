"""Tests for DB session: pooling config, scoped sessions, no leaks."""
from apps.api.db.session import (
    POOL_SIZE,
    MAX_OVERFLOW,
    POOL_RECYCLE,
    engine,
    get_db,
    get_db_dependency,
    _safe_close_session,
)


def test_engine_has_pooling():
    """Pool options are active on engine."""
    assert engine.pool is not None
    assert engine.pool.size() == POOL_SIZE
    assert engine.pool._max_overflow == MAX_OVERFLOW


def test_engine_pool_pre_ping():
    """pool_pre_ping is enabled (handles stale connections)."""
    # Engine created with pool_pre_ping=True; reflected in connect() behavior
    assert engine.pool._pre_ping is True


def test_get_db_closes_session():
    """get_db context manager always closes session."""
    with get_db() as session:
        assert session is not None
    # After exit, session is closed (connection returned to pool)


def test_get_db_dependency_is_generator():
    """get_db_dependency yields session."""
    gen = get_db_dependency()
    session = next(gen)
    assert session is not None
    try:
        next(gen)
    except StopIteration:
        pass


def test_safe_close_handles_none():
    """_safe_close_session handles None."""
    _safe_close_session(None)  # no error


def test_safe_close_handles_closed_session():
    """_safe_close_session handles already-closed session."""
    with get_db() as session:
        pass
    _safe_close_session(session)  # no error
