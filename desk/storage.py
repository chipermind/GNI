"""
Desk storage: SQLite DB for desk-related data.
Uses standard library only. Standalone — no project imports.
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT: Final[Path] = _THIS_DIR.parent


def _resolve_db_path() -> Path:
    """Single source of truth for DB path. DESK24H_DB_PATH env, else data/gni.db."""
    env_path = os.environ.get("DESK24H_DB_PATH")
    if env_path and env_path.strip():
        p = Path(env_path.strip())
        if not p.is_absolute():
            p = _REPO_ROOT / p
        p = p.resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    db_path = _REPO_ROOT / "data" / "gni.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


DB_PATH: Final[Path] = _resolve_db_path()

SCHEMA_VERSION: Final[int] = 1


def get_conn() -> sqlite3.Connection:
    """Open SQLite connection with row_factory, foreign_keys, WAL mode."""
    conn = sqlite3.connect(str(DB_PATH), uri=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass  # e.g. read-only fs; continue without WAL
    return conn


def init_db() -> None:
    """Create tables if they do not exist."""
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            desk_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            desk_type TEXT NOT NULL,
            text TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            snapshot_id INTEGER NULL REFERENCES snapshots(id),
            created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS day_state (
            day TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cooldowns (
            key TEXT PRIMARY KEY,
            last_ok_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_desk_created ON snapshots(desk_type, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_desk_created ON posts(desk_type, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_snapshot ON posts(snapshot_id)"
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


def _utc_now_iso() -> str:
    """Return current UTC time as ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_utc(s: str) -> datetime | None:
    """Parse ISO8601 string to UTC datetime. Return None on parse error."""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError, AttributeError):
        return None


def save_snapshot(desk_type: str, payload: dict, payload_hash: str) -> int:
    """Insert snapshot, return snapshot_id."""
    init_db()
    payload_json = json.dumps(payload, ensure_ascii=False)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO snapshots (desk_type, payload_json, payload_hash, created_at) VALUES (?, ?, ?, ?)",
            (desk_type, payload_json, payload_hash, _utc_now_iso()),
        )
        conn.commit()
        return cur.lastrowid


def save_post(
    desk_type: str,
    text: str,
    meta: dict | None = None,
    snapshot_id: int | None = None,
) -> int:
    """Insert post, return post_id."""
    init_db()
    meta_json = json.dumps(meta if meta is not None else {}, ensure_ascii=False)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO posts (desk_type, text, meta_json, snapshot_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (desk_type, text, meta_json, snapshot_id, _utc_now_iso()),
        )
        conn.commit()
        return cur.lastrowid


def get_last_posts(hours: int = 24) -> list[dict]:
    """Return last posts within N hours (UTC), newest first."""
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, desk_type, text, meta_json, snapshot_id, created_at FROM posts WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r["id"],
            "desk_type": r["desk_type"],
            "text": r["text"],
            "meta": json.loads(r["meta_json"]) if r["meta_json"] else {},
            "snapshot_id": r["snapshot_id"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _validate_day(day: str) -> None:
    """Raise ValueError if day is not YYYY-MM-DD."""
    if not day or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError(f"day must be YYYY-MM-DD, got {day!r}")


def get_day_state(day: str) -> dict | None:
    """Return parsed state dict for day (YYYY-MM-DD) or None if not found."""
    _validate_day(day)
    init_db()
    with get_conn() as conn:
        cur = conn.execute("SELECT state_json FROM day_state WHERE day = ?", (day,))
        row = cur.fetchone()
    if row is None:
        return None
    return json.loads(row["state_json"]) if row["state_json"] else {}


def set_day_state(day: str, state: dict) -> None:
    """Upsert day_state for day (YYYY-MM-DD)."""
    _validate_day(day)
    init_db()
    state_json = json.dumps(state, ensure_ascii=False)
    updated_at = _utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO day_state (day, state_json, updated_at) VALUES (?, ?, ?)",
            (day, state_json, updated_at),
        )
        conn.commit()


def cooldown_ok(key: str, minutes: int) -> bool:
    """Return True if cooldown elapsed (or key new). Updates last_ok_at on True."""
    init_db()
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    with get_conn() as conn:
        cur = conn.execute("SELECT last_ok_at FROM cooldowns WHERE key = ?", (key,))
        row = cur.fetchone()
        if row is None:
            conn.execute("INSERT INTO cooldowns (key, last_ok_at) VALUES (?, ?)", (key, now_str))
            conn.commit()
            return True
        last_ok = _parse_iso_utc(row["last_ok_at"])
        if last_ok is None or (now - last_ok) >= timedelta(minutes=minutes):
            conn.execute("UPDATE cooldowns SET last_ok_at = ? WHERE key = ?", (now_str, key))
            conn.commit()
            return True
        return False


def cleanup(days: int = 7, day_state_days: int = 30) -> dict[str, int]:
    """Delete old rows. Returns counts removed per table.
    - snapshots, posts: older than `days`.
    - day_state: older than `day_state_days` (by day YYYY-MM-DD).
    - cooldowns: never pruned (kept forever; keys are small and fast to check).
    """
    init_db()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()
    cutoff_day = (now - timedelta(days=day_state_days)).strftime("%Y-%m-%d")
    result: dict[str, int] = {"snapshots": 0, "posts": 0, "day_state": 0, "cooldowns": 0}
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM posts WHERE created_at < ?", (cutoff,))
        result["posts"] = cur.rowcount
        cur = conn.execute("DELETE FROM snapshots WHERE created_at < ?", (cutoff,))
        result["snapshots"] = cur.rowcount
        cur = conn.execute("DELETE FROM day_state WHERE day < ?", (cutoff_day,))
        result["day_state"] = cur.rowcount
        conn.commit()
    return result
