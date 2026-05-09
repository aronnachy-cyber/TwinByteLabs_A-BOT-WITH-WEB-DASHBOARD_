"""
db_spark.py — PostgreSQL backend for SP4RK dashboard data.

Replaces JSON-file storage for:
  • spark_guild_configs  — per-guild SP4RK settings
  • spark_settings       — global app config (single JSONB blob)
  • spark_feedback       — star-rating feedback submissions
  • spark_dev_state      — maintenance-mode / dev state (single JSONB blob)
  • spark_warnings       — member warning counts
  • spark_action_logs    — dashboard audit/action log
  • spark_wave_rooms     — wave-bot streaming rooms (single JSONB blob)
  • spark_mod_logs       — moderation action history
  • spark_notes          — per-member moderator notes

Falls back silently when DATABASE_URL is not set.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

_LOCK = threading.Lock()
_POOL: Optional[SimpleConnectionPool] = None
_INITIALIZED = False
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS spark_guild_configs (
    guild_id    TEXT PRIMARY KEY,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spark_settings (
    id          TEXT PRIMARY KEY DEFAULT 'main',
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spark_feedback (
    id          TEXT PRIMARY KEY,
    discord_id  TEXT,
    user_name   TEXT,
    rating      INT,
    comment     TEXT DEFAULT '',
    source      TEXT DEFAULT 'main',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS spark_fb_created ON spark_feedback (created_at DESC);

CREATE TABLE IF NOT EXISTS spark_dev_state (
    id          TEXT PRIMARY KEY DEFAULT 'main',
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spark_warnings (
    member_id   TEXT NOT NULL,
    guild_id    TEXT NOT NULL DEFAULT 'global',
    username    TEXT,
    count       INT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (guild_id, member_id)
);
CREATE INDEX IF NOT EXISTS spark_warn_member ON spark_warnings (member_id);

CREATE TABLE IF NOT EXISTS spark_action_logs (
    id          TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    description TEXT NOT NULL,
    ts          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS spark_alog_ts ON spark_action_logs (ts DESC);

CREATE TABLE IF NOT EXISTS spark_wave_rooms (
    id          TEXT PRIMARY KEY DEFAULT 'main',
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spark_mod_logs (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    moderator   TEXT,
    reason      TEXT DEFAULT '',
    ts          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS spark_modlog_guild_ts ON spark_mod_logs (guild_id, ts DESC);

CREATE TABLE IF NOT EXISTS spark_notes (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    member_id   TEXT NOT NULL,
    username    TEXT,
    note        TEXT NOT NULL,
    moderator   TEXT,
    ts          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS spark_notes_member ON spark_notes (guild_id, member_id, ts DESC);
"""


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------
def is_available() -> bool:
    return bool(_DATABASE_URL) and _ensure_pool() is not None


def _ensure_pool() -> Optional[SimpleConnectionPool]:
    global _POOL, _INITIALIZED
    if _POOL is not None:
        return _POOL
    if not _DATABASE_URL:
        return None
    with _LOCK:
        if _POOL is not None:
            return _POOL
        try:
            _POOL = SimpleConnectionPool(minconn=1, maxconn=6, dsn=_DATABASE_URL)
        except Exception as e:
            print(f"[db_spark] pool init failed: {e}")
            _POOL = None
            return None
    _init_schema()
    return _POOL


def _init_schema() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
            conn.commit()
        _INITIALIZED = True
        print("✅ Postgres SP4RK store ready (spark_* tables)")
    except Exception as e:
        print(f"[db_spark] schema init failed: {e}")


class _ConnCtx:
    def __init__(self):
        self.conn = None

    def __enter__(self):
        pool = _ensure_pool()
        if pool is None:
            raise RuntimeError("Postgres pool not available")
        self.conn = pool.getconn()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if self.conn is not None and _POOL is not None:
            try:
                _POOL.putconn(self.conn)
            except Exception:
                pass


def _conn() -> _ConnCtx:
    return _ConnCtx()


def _gen_id() -> str:
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Guild configs
# ---------------------------------------------------------------------------
def get_all_guild_configs() -> Dict[str, Any]:
    """Return {guild_id: config_dict} for all stored guilds."""
    if not is_available():
        return {}
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT guild_id, data FROM spark_guild_configs")
                rows = cur.fetchall()
        return {r["guild_id"]: _parse_jsonb(r["data"]) for r in rows}
    except Exception as e:
        print(f"[db_spark] get_all_guild_configs: {e}")
        return {}


def save_all_guild_configs(data: Dict[str, Any]) -> bool:
    """Upsert the full guild config map."""
    if not is_available() or not data:
        return False
    rows = [{"guild_id": gid, "cfg": json.dumps(v, default=str)}
            for gid, v in data.items() if isinstance(v, dict)]
    if not rows:
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO spark_guild_configs (guild_id, data, updated_at)
                    VALUES (%(guild_id)s, %(cfg)s::jsonb, NOW())
                    ON CONFLICT (guild_id) DO UPDATE
                        SET data = EXCLUDED.data, updated_at = NOW()
                """, rows)
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] save_all_guild_configs: {e}")
        return False


def get_one_guild_config(guild_id: str) -> Optional[Dict[str, Any]]:
    if not is_available():
        return None
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT data FROM spark_guild_configs WHERE guild_id = %s", (str(guild_id),))
                row = cur.fetchone()
        return _parse_jsonb(row["data"]) if row else None
    except Exception as e:
        print(f"[db_spark] get_one_guild_config: {e}")
        return None


def upsert_guild_config(guild_id: str, updates: Dict[str, Any]) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_guild_configs (guild_id, data, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (guild_id) DO UPDATE
                        SET data = spark_guild_configs.data || EXCLUDED.data,
                            updated_at = NOW()
                """, (str(guild_id), json.dumps(updates, default=str)))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] upsert_guild_config: {e}")
        return False


# ---------------------------------------------------------------------------
# Global settings
# ---------------------------------------------------------------------------
def get_settings() -> Optional[Dict[str, Any]]:
    if not is_available():
        return None
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT data FROM spark_settings WHERE id = 'main'")
                row = cur.fetchone()
        return _parse_jsonb(row["data"]) if row else None
    except Exception as e:
        print(f"[db_spark] get_settings: {e}")
        return None


def save_settings(data: Dict[str, Any]) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_settings (id, data, updated_at)
                    VALUES ('main', %s::jsonb, NOW())
                    ON CONFLICT (id) DO UPDATE
                        SET data = EXCLUDED.data, updated_at = NOW()
                """, (json.dumps(data, default=str),))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] save_settings: {e}")
        return False


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------
def insert_feedback(entry: Dict[str, Any]) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_feedback (id, discord_id, user_name, rating, comment, source, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (id) DO NOTHING
                """, (
                    entry.get("id") or _gen_id(),
                    entry.get("discord_id"),
                    entry.get("user_name"),
                    entry.get("rating"),
                    entry.get("comment", ""),
                    entry.get("source", "main"),
                ))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] insert_feedback: {e}")
        return False


def list_feedback(limit: int = 50) -> Optional[List[Dict[str, Any]]]:
    if not is_available():
        return None
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM spark_feedback ORDER BY created_at DESC LIMIT %s",
                    (int(limit),)
                )
                rows = cur.fetchall()
        return [_ser_row(dict(r)) for r in rows]
    except Exception as e:
        print(f"[db_spark] list_feedback: {e}")
        return None


def migrate_feedback_from_list(items: List[Dict]) -> int:
    """Bulk-import feedback items from a JSON list. Idempotent."""
    if not is_available() or not items:
        return 0
    n = 0
    for item in items:
        if isinstance(item, dict):
            if not item.get("id"):
                item["id"] = _gen_id()
            if insert_feedback(item):
                n += 1
    return n


# ---------------------------------------------------------------------------
# Dev / maintenance state
# ---------------------------------------------------------------------------
def get_dev_state() -> Optional[Dict[str, Any]]:
    if not is_available():
        return None
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT data FROM spark_dev_state WHERE id = 'main'")
                row = cur.fetchone()
        return _parse_jsonb(row["data"]) if row else None
    except Exception as e:
        print(f"[db_spark] get_dev_state: {e}")
        return None


def save_dev_state(state: Dict[str, Any]) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_dev_state (id, data, updated_at)
                    VALUES ('main', %s::jsonb, NOW())
                    ON CONFLICT (id) DO UPDATE
                        SET data = EXCLUDED.data, updated_at = NOW()
                """, (json.dumps(state, default=str),))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] save_dev_state: {e}")
        return False


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------
def get_all_warnings() -> List[Dict[str, Any]]:
    """Return list of {id, username, warnings} matching the in-memory shape."""
    if not is_available():
        return []
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT member_id, guild_id, username, count FROM spark_warnings ORDER BY count DESC")
                rows = cur.fetchall()
        return [{"id": r["member_id"], "username": r["username"] or "", "warnings": r["count"],
                 "guild_id": r["guild_id"]} for r in rows]
    except Exception as e:
        print(f"[db_spark] get_all_warnings: {e}")
        return []


def upsert_warning(member_id: str, username: str, count: int, guild_id: str = "global") -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_warnings (member_id, guild_id, username, count, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (guild_id, member_id) DO UPDATE
                        SET username = EXCLUDED.username,
                            count = EXCLUDED.count,
                            updated_at = NOW()
                """, (str(member_id), str(guild_id), username, int(count)))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] upsert_warning: {e}")
        return False


def delete_warning(member_id: str, guild_id: str = "global") -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM spark_warnings WHERE member_id = %s AND guild_id = %s",
                    (str(member_id), str(guild_id))
                )
                deleted = cur.rowcount
            conn.commit()
        return deleted > 0
    except Exception as e:
        print(f"[db_spark] delete_warning: {e}")
        return False


def migrate_warnings_from_list(items: List[Dict]) -> int:
    """Bulk-import the in-memory warnings_db list. Idempotent."""
    if not is_available() or not items:
        return 0
    n = 0
    for w in items:
        if isinstance(w, dict) and w.get("id"):
            if upsert_warning(w["id"], w.get("username", ""), w.get("warnings", 0)):
                n += 1
    return n


# ---------------------------------------------------------------------------
# Action logs
# ---------------------------------------------------------------------------
def add_action_log(event_type: str, description: str) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_action_logs (id, event_type, description, ts)
                    VALUES (%s, %s, %s, NOW())
                """, (_gen_id(), str(event_type), str(description)))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] add_action_log: {e}")
        return False


def list_action_logs(limit: int = 300) -> List[Dict[str, Any]]:
    if not is_available():
        return []
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT event_type, description, ts FROM spark_action_logs ORDER BY ts DESC LIMIT %s",
                    (int(limit),)
                )
                rows = cur.fetchall()
        return [{"type": r["event_type"], "description": r["description"],
                 "time": r["ts"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(r["ts"], datetime) else str(r["ts"])}
                for r in rows]
    except Exception as e:
        print(f"[db_spark] list_action_logs: {e}")
        return []


# ---------------------------------------------------------------------------
# Wave rooms
# ---------------------------------------------------------------------------
def get_wave_rooms() -> Optional[Dict[str, Any]]:
    if not is_available():
        return None
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT data FROM spark_wave_rooms WHERE id = 'main'")
                row = cur.fetchone()
        return _parse_jsonb(row["data"]) if row else {}
    except Exception as e:
        print(f"[db_spark] get_wave_rooms: {e}")
        return None


def save_wave_rooms(data: Dict[str, Any]) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_wave_rooms (id, data, updated_at)
                    VALUES ('main', %s::jsonb, NOW())
                    ON CONFLICT (id) DO UPDATE
                        SET data = EXCLUDED.data, updated_at = NOW()
                """, (json.dumps(data, default=str),))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] save_wave_rooms: {e}")
        return False


# ---------------------------------------------------------------------------
# Mod logs
# ---------------------------------------------------------------------------
def add_mod_log(guild_id: str, action: str, target: str, moderator: str, reason: str) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_mod_logs (id, guild_id, action, target, moderator, reason, ts)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                """, (_gen_id(), str(guild_id), str(action), str(target), str(moderator), str(reason)))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] add_mod_log: {e}")
        return False


def list_mod_logs(guild_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    if not is_available():
        return []
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM spark_mod_logs WHERE guild_id=%s ORDER BY ts DESC LIMIT %s",
                    (str(guild_id), int(limit))
                )
                rows = cur.fetchall()
        return [_ser_row(dict(r)) for r in rows]
    except Exception as e:
        print(f"[db_spark] list_mod_logs: {e}")
        return []


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
def add_note(guild_id: str, member_id: str, username: str, note: str, moderator: str) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_notes (id, guild_id, member_id, username, note, moderator, ts)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                """, (_gen_id(), str(guild_id), str(member_id), str(username), str(note), str(moderator)))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] add_note: {e}")
        return False


def get_notes(guild_id: str, member_id: str) -> List[Dict[str, Any]]:
    if not is_available():
        return []
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM spark_notes WHERE guild_id=%s AND member_id=%s ORDER BY ts ASC",
                    (str(guild_id), str(member_id))
                )
                rows = cur.fetchall()
        return [_ser_row(dict(r)) for r in rows]
    except Exception as e:
        print(f"[db_spark] get_notes: {e}")
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_jsonb(val: Any) -> Dict[str, Any]:
    if val is None:
        return {}
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    if isinstance(val, dict):
        return val
    return {}


def _ser_row(row: Dict) -> Dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out
