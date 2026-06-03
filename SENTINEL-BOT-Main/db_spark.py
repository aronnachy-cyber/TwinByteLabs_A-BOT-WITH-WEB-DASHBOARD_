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
    guild_id    TEXT DEFAULT '',
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

CREATE TABLE IF NOT EXISTS spark_warning_history (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    member_id   TEXT NOT NULL,
    username    TEXT,
    reason      TEXT DEFAULT '',
    ts          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS spark_wh_guild_member ON spark_warning_history (guild_id, member_id, ts ASC);

CREATE TABLE IF NOT EXISTS spark_xp (
    guild_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    username    TEXT,
    xp          INT DEFAULT 0,
    level       INT DEFAULT 0,
    messages    INT DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS spark_xp_lb ON spark_xp (guild_id, xp DESC);

CREATE TABLE IF NOT EXISTS spark_leveling_config (
    guild_id    TEXT PRIMARY KEY,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spark_json_store (
    scope       TEXT NOT NULL,
    key         TEXT NOT NULL DEFAULT 'global',
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (scope, key)
);

CREATE TABLE IF NOT EXISTS spark_analytics_daily (
    guild_id    TEXT NOT NULL,
    date        DATE NOT NULL,
    messages    INT DEFAULT 0,
    joins       INT DEFAULT 0,
    leaves      INT DEFAULT 0,
    PRIMARY KEY (guild_id, date)
);
CREATE INDEX IF NOT EXISTS spark_ad_guild_date ON spark_analytics_daily (guild_id, date DESC);

CREATE TABLE IF NOT EXISTS spark_analytics_voice (
    guild_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    username    TEXT,
    minutes     INT DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS spark_av_guild_mins ON spark_analytics_voice (guild_id, minutes DESC);

CREATE TABLE IF NOT EXISTS spark_mood_signals (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    ts          TIMESTAMPTZ DEFAULT NOW(),
    score       INT NOT NULL,
    reason      TEXT,
    user_id     TEXT
);
CREATE INDEX IF NOT EXISTS spark_msig_guild_ts ON spark_mood_signals (guild_id, ts DESC);

CREATE TABLE IF NOT EXISTS spark_mood_snapshots (
    guild_id    TEXT NOT NULL,
    ts          TIMESTAMPTZ DEFAULT NOW(),
    mood_score  NUMERIC(6,2) NOT NULL DEFAULT 0,
    mood_label  TEXT NOT NULL,
    mood_emoji  TEXT NOT NULL,
    pos_count   INT DEFAULT 0,
    neg_count   INT DEFAULT 0,
    neu_count   INT DEFAULT 0,
    highlights  JSONB DEFAULT '[]'::jsonb,
    PRIMARY KEY (guild_id, ts)
);
CREATE INDEX IF NOT EXISTS spark_msnap_guild_ts ON spark_mood_snapshots (guild_id, ts DESC);
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
            _POOL = SimpleConnectionPool(minconn=1, maxconn=8, dsn=_DATABASE_URL)
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
            if exc_type is not None:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
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
def add_action_log(event_type: str, description: str, guild_id: str = "") -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                # Add guild_id column if it doesn't exist (one-time migration)
                cur.execute("""
                    ALTER TABLE spark_action_logs
                    ADD COLUMN IF NOT EXISTS guild_id TEXT DEFAULT ''
                """)
                cur.execute("""
                    INSERT INTO spark_action_logs (id, event_type, description, guild_id, ts)
                    VALUES (%s, %s, %s, %s, NOW())
                """, (_gen_id(), str(event_type), str(description), str(guild_id or "")))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] add_action_log: {e}")
        return False


def list_action_logs(limit: int = 300, guild_id: str = "") -> List[Dict[str, Any]]:
    if not is_available():
        return []
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Ensure the guild_id column exists (one-time migration for older installs)
                cur.execute("""
                    ALTER TABLE spark_action_logs
                    ADD COLUMN IF NOT EXISTS guild_id TEXT DEFAULT ''
                """)
                conn.commit()
                if guild_id:
                    cur.execute(
                        """SELECT event_type, description, guild_id, ts
                           FROM spark_action_logs
                           WHERE guild_id = %s OR guild_id = ''
                           ORDER BY ts DESC LIMIT %s""",
                        (str(guild_id), int(limit))
                    )
                else:
                    cur.execute(
                        "SELECT event_type, description, guild_id, ts FROM spark_action_logs ORDER BY ts DESC LIMIT %s",
                        (int(limit),)
                    )
                rows = cur.fetchall()
        return [{"type": r["event_type"], "description": r["description"],
                 "guild_id": r.get("guild_id", ""),
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
# Warning history (individual events with reason + timestamp)
# ---------------------------------------------------------------------------
def add_warning_event(guild_id: str, member_id: str, username: str, reason: str) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_warning_history (id, guild_id, member_id, username, reason, ts)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (_gen_id(), str(guild_id), str(member_id), str(username), str(reason)))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] add_warning_event: {e}")
        return False


def clear_warning_history(guild_id: str, member_id: str) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM spark_warning_history WHERE guild_id=%s AND member_id=%s",
                    (str(guild_id), str(member_id))
                )
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] clear_warning_history: {e}")
        return False


def get_all_warning_history() -> Dict[str, Any]:
    """
    Load all warning history rows and return them grouped as:
        { guild_id: { member_id: { "count": int, "history": [ {reason, time} ] } } }
    Returns {} if unavailable.
    """
    if not is_available():
        return {}
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT guild_id, member_id, reason, ts FROM spark_warning_history ORDER BY ts ASC"
                )
                rows = cur.fetchall()
    except Exception as e:
        print(f"[db_spark] get_all_warning_history: {e}")
        return {}
    out: Dict[str, Any] = {}
    for r in rows:
        gid = str(r["guild_id"] or "")
        mid = str(r["member_id"] or "")
        if not gid or not mid:
            continue
        guild = out.setdefault(gid, {})
        entry = guild.setdefault(mid, {"count": 0, "history": []})
        ts_val = r["ts"]
        ts_str = ts_val.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts_val, datetime) else str(ts_val)
        entry["history"].append({"reason": r.get("reason") or "", "time": ts_str})
    for guild in out.values():
        for entry in guild.values():
            entry["count"] = len(entry["history"])
    return out


# ---------------------------------------------------------------------------
# XP / Leveling
# ---------------------------------------------------------------------------
def upsert_xp(guild_id: str, user_id: str, xp: int, level: int, messages: int,
              username: str = "", safe_migrate: bool = False) -> bool:
    """
    Upsert XP for a user.
    When safe_migrate=True (startup migration from JSON), never overwrite a
    HIGHER xp value already stored in Postgres — protects existing data.
    """
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                if safe_migrate:
                    # During migration: only update if the stored xp is lower
                    cur.execute("""
                        INSERT INTO spark_xp (guild_id, user_id, username, xp, level, messages, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (guild_id, user_id) DO UPDATE
                            SET xp       = GREATEST(spark_xp.xp, EXCLUDED.xp),
                                level    = GREATEST(spark_xp.level, EXCLUDED.level),
                                messages = GREATEST(spark_xp.messages, EXCLUDED.messages),
                                username = COALESCE(NULLIF(EXCLUDED.username,''), spark_xp.username),
                                updated_at = NOW()
                    """, (str(guild_id), str(user_id), str(username), int(xp), int(level), int(messages)))
                else:
                    cur.execute("""
                        INSERT INTO spark_xp (guild_id, user_id, username, xp, level, messages, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (guild_id, user_id) DO UPDATE
                            SET xp         = EXCLUDED.xp,
                                level      = EXCLUDED.level,
                                messages   = EXCLUDED.messages,
                                username   = COALESCE(NULLIF(EXCLUDED.username,''), spark_xp.username),
                                updated_at = NOW()
                    """, (str(guild_id), str(user_id), str(username), int(xp), int(level), int(messages)))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] upsert_xp: {e}")
        return False


def get_xp(guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    if not is_available():
        return None
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT xp, level, messages FROM spark_xp WHERE guild_id=%s AND user_id=%s",
                    (str(guild_id), str(user_id))
                )
                row = cur.fetchone()
        if row:
            return {"xp": row["xp"], "level": row["level"], "messages": row["messages"]}
        return None
    except Exception as e:
        print(f"[db_spark] get_xp: {e}")
        return None


def get_xp_leaderboard(guild_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    if not is_available():
        return []
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT user_id, username, xp, level, messages FROM spark_xp "
                    "WHERE guild_id=%s ORDER BY xp DESC LIMIT %s",
                    (str(guild_id), int(limit))
                )
                rows = cur.fetchall()
        result = []
        for i, r in enumerate(rows):
            result.append({
                "user_id": r["user_id"],
                "username": r.get("username") or "",
                "xp": r["xp"],
                "level": r["level"],
                "messages": r["messages"],
                "rank": i + 1,
            })
        return result
    except Exception as e:
        print(f"[db_spark] get_xp_leaderboard: {e}")
        return []


# ---------------------------------------------------------------------------
# Leveling config
# ---------------------------------------------------------------------------
def upsert_leveling_config(guild_id: str, cfg: Dict[str, Any]) -> bool:
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_leveling_config (guild_id, data, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (guild_id) DO UPDATE
                        SET data = spark_leveling_config.data || EXCLUDED.data,
                            updated_at = NOW()
                """, (str(guild_id), json.dumps(cfg, default=str)))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] upsert_leveling_config: {e}")
        return False


def get_leveling_config(guild_id: str) -> Optional[Dict[str, Any]]:
    if not is_available():
        return None
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT data FROM spark_leveling_config WHERE guild_id=%s",
                    (str(guild_id),)
                )
                row = cur.fetchone()
        return _parse_jsonb(row["data"]) if row else None
    except Exception as e:
        print(f"[db_spark] get_leveling_config: {e}")
        return None


# ---------------------------------------------------------------------------
# Generic JSON blob store  (spark_json_store)
# ---------------------------------------------------------------------------
def get_blob(scope: str, key: str = "global") -> Optional[Any]:
    """Return the stored value for (scope, key), or None if not found."""
    if not is_available():
        return None
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT data FROM spark_json_store WHERE scope=%s AND key=%s",
                    (scope, key),
                )
                row = cur.fetchone()
        return _parse_jsonb(row["data"]) if row else None
    except Exception as e:
        print(f"[db_spark] get_blob({scope},{key}): {e}")
        return None


def set_blob(scope: str, data: Any, key: str = "global") -> bool:
    """Upsert a value into spark_json_store."""
    if not is_available():
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO spark_json_store (scope, key, data, updated_at)
                    VALUES (%s, %s, %s::jsonb, NOW())
                    ON CONFLICT (scope, key) DO UPDATE
                        SET data = EXCLUDED.data, updated_at = NOW()
                    """,
                    (scope, key, json.dumps(data, default=str)),
                )
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_spark] set_blob({scope},{key}): {e}")
        return False


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def analytics_record_message(guild_id: str) -> None:
    if not is_available():
        return
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO spark_analytics_daily (guild_id, date, messages)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (guild_id, date) DO UPDATE
                        SET messages = spark_analytics_daily.messages + 1
                    """,
                    (guild_id, today),
                )
            conn.commit()
    except Exception as e:
        print(f"[db_spark] analytics_record_message: {e}")


def analytics_record_join(guild_id: str) -> None:
    if not is_available():
        return
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO spark_analytics_daily (guild_id, date, joins)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (guild_id, date) DO UPDATE
                        SET joins = spark_analytics_daily.joins + 1
                    """,
                    (guild_id, today),
                )
            conn.commit()
    except Exception as e:
        print(f"[db_spark] analytics_record_join: {e}")


def analytics_record_leave(guild_id: str) -> None:
    if not is_available():
        return
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO spark_analytics_daily (guild_id, date, leaves)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (guild_id, date) DO UPDATE
                        SET leaves = spark_analytics_daily.leaves + 1
                    """,
                    (guild_id, today),
                )
            conn.commit()
    except Exception as e:
        print(f"[db_spark] analytics_record_leave: {e}")


def analytics_record_voice(guild_id: str, user_id: str, username: str, minutes: int) -> None:
    if not is_available() or minutes < 1:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO spark_analytics_voice (guild_id, user_id, username, minutes)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (guild_id, user_id) DO UPDATE
                        SET minutes    = spark_analytics_voice.minutes + EXCLUDED.minutes,
                            username   = EXCLUDED.username,
                            updated_at = NOW()
                    """,
                    (guild_id, user_id, username, minutes),
                )
            conn.commit()
    except Exception as e:
        print(f"[db_spark] analytics_record_voice: {e}")


# ---------------------------------------------------------------------------
# Server Mood System
# ---------------------------------------------------------------------------

_MOOD_POSITIVE_WORDS = {
    "thanks","thank","gg","nice","great","awesome","love","good","cool","yes","perfect",
    "beautiful","amazing","helpful","welcome","happy","lol","lmao","haha","agree","agreed",
    "wholesome","kind","friendly","blessed","poggers","pog","based","w","goat","legend",
    "❤️","🔥","✅","🎉","👍","😂","💯","🙏","✨","😊",
}
_MOOD_NEGATIVE_WORDS = {
    "hate","stupid","idiot","noob","wtf","stfu","trash","loser","ugly","dumb","toxic",
    "shut up","kys","die","banned","worst","terrible","awful","garbage","pathetic","cringe",
    "waste","useless","cancer","scam","fraud","liar","cheat","disgrace","disgusting","vile",
}
_MOOD_TENSION_WORDS = {
    "fight","mad","angry","argument","drama","beef","heated","arguing","drama","conflict",
    "disagree","wrong","leave","ban","kick","report","abuse","harass",
}
_MOOD_SPARK_FLAVOUR = {
    "Golden Server":     "The server is operating at peak emotional capacity. Rare. Cherish this.",
    "Chill & Friendly":  "Vibes are smooth. The server is at peace with itself.",
    "Normal Activity":   "Everything seems… normal. Suspiciously normal.",
    "Tense Atmosphere":  "Something is brewing. The mood has shifted. Tread carefully.",
    "Chaotic / Dead":    "The server is in emotional freefall. Reality is negotiable here. 💀",
}

# In-memory fallback (deque of dicts per guild)
from collections import deque as _deque
_mood_sig_cache: dict = {}   # guild_id -> deque(maxlen=150) of {score, reason, user_id, ts}
_mood_snap_cache: dict = {}  # guild_id -> latest snapshot dict
_mood_hist_cache: dict = {}  # guild_id -> deque(maxlen=48) of snapshots


def _mood_cache_signal(guild_id: str, score: int, reason: str, user_id: str) -> None:
    if guild_id not in _mood_sig_cache:
        _mood_sig_cache[guild_id] = _deque(maxlen=150)
    _mood_sig_cache[guild_id].append({
        "score": score, "reason": reason, "user_id": user_id,
        "ts": datetime.utcnow().isoformat(),
    })


def _mood_label_from_score(score: float) -> tuple:
    """Returns (label, emoji, css_class)."""
    if score >= 40:
        return "Golden Server",    "😇", "mood-golden"
    elif score >= 20:
        return "Chill & Friendly", "😊", "mood-chill"
    elif score >= -10:
        return "Normal Activity",  "😐", "mood-normal"
    elif score >= -30:
        return "Tense Atmosphere", "😬", "mood-tense"
    else:
        return "Chaotic / Dead",   "💀", "mood-chaotic"


def classify_mood_signal(content: str) -> tuple:
    """Score a message: returns (score: int, reason: str, category: str)."""
    if not content:
        return 0, "Empty message", "neutral"
    lower = content.lower()
    words = set(lower.split())

    neg_hits = words & _MOOD_NEGATIVE_WORDS
    if neg_hits:
        sc = max(-4, -2 * len(neg_hits))
        return sc, f"Negative language: {', '.join(list(neg_hits)[:2])}", "negative"

    tension_hits = words & _MOOD_TENSION_WORDS
    if tension_hits:
        return -2, f"Tension keywords: {', '.join(list(tension_hits)[:2])}", "negative"

    pos_hits = words & _MOOD_POSITIVE_WORDS
    if pos_hits:
        sc = min(4, 1 + len(pos_hits))
        return sc, f"Positive interaction: {', '.join(list(pos_hits)[:2])}", "positive"

    # Spam-like (very short)
    stripped = content.strip()
    if len(stripped) < 4:
        return -1, "Low-quality message (very short)", "negative"

    # Heavy caps
    alpha = [c for c in stripped if c.isalpha()]
    if len(alpha) > 5 and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.7:
        return -1, "Heavy caps usage", "negative"

    return 1, "Active chat contribution", "positive"


def mood_record_signal(guild_id: str, score: int, reason: str,
                       user_id: str, category: str = "neutral") -> None:
    """Record a mood signal — Postgres with in-memory fallback."""
    _mood_cache_signal(guild_id, score, reason, user_id)
    if not is_available():
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO spark_mood_signals (guild_id, score, reason, user_id) VALUES (%s,%s,%s,%s)",
                    (guild_id, score, reason, user_id),
                )
                conn.commit()
    except Exception as e:
        print(f"[db_spark] mood_record_signal: {e}")


def mood_compute_snapshot(guild_id: str) -> dict:
    """Compute and persist current mood from recent signals. Returns snapshot dict."""
    # Pull signals from DB or cache
    signals = []
    if is_available():
        try:
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        """SELECT score, reason FROM spark_mood_signals
                           WHERE guild_id = %s
                           ORDER BY ts DESC LIMIT 100""",
                        (guild_id,),
                    )
                    signals = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[db_spark] mood_compute_snapshot fetch: {e}")

    if not signals and guild_id in _mood_sig_cache:
        signals = list(_mood_sig_cache[guild_id])

    if not signals:
        label, emoji, _ = _mood_label_from_score(0)
        return {
            "mood_score": 0, "mood_label": label, "mood_emoji": emoji,
            "pos_count": 0, "neg_count": 0, "neu_count": 0,
            "highlights": [], "ts": datetime.utcnow().isoformat(),
            "flavour": _MOOD_SPARK_FLAVOUR.get(label, ""),
        }

    scores = [s["score"] for s in signals]
    total = sum(scores)
    # Normalize: 100 signals * max +4 = 400 → map to -50..+50
    max_magnitude = max(len(signals) * 4, 1)
    normalized = round(max(-50, min(50, (total / max_magnitude) * 50)), 1)

    pos_count = sum(1 for s in scores if s > 0)
    neg_count = sum(1 for s in scores if s < 0)
    neu_count = len(scores) - pos_count - neg_count

    # Collect top reason highlights (deduplicated)
    seen_reasons = set()
    highlights = []
    for s in signals:
        r = s.get("reason", "")
        if r and r not in seen_reasons:
            seen_reasons.add(r)
            highlights.append(r)
        if len(highlights) >= 5:
            break

    label, emoji, _ = _mood_label_from_score(normalized)
    snap = {
        "mood_score":  normalized,
        "mood_label":  label,
        "mood_emoji":  emoji,
        "pos_count":   pos_count,
        "neg_count":   neg_count,
        "neu_count":   neu_count,
        "highlights":  highlights,
        "ts":          datetime.utcnow().isoformat(),
        "flavour":     _MOOD_SPARK_FLAVOUR.get(label, ""),
    }

    # Persist snapshot
    _mood_snap_cache[guild_id] = snap
    if guild_id not in _mood_hist_cache:
        _mood_hist_cache[guild_id] = _deque(maxlen=48)
    _mood_hist_cache[guild_id].append(snap)

    if is_available():
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO spark_mood_snapshots
                           (guild_id, mood_score, mood_label, mood_emoji,
                            pos_count, neg_count, neu_count, highlights)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (guild_id, normalized, label, emoji,
                         pos_count, neg_count, neu_count,
                         json.dumps(highlights)),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[db_spark] mood_compute_snapshot persist: {e}")

    return snap


def mood_get_current(guild_id: str) -> dict:
    """Return the latest mood snapshot, computing if needed."""
    if guild_id in _mood_snap_cache:
        return _mood_snap_cache[guild_id]
    if is_available():
        try:
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        """SELECT mood_score, mood_label, mood_emoji,
                                  pos_count, neg_count, neu_count, highlights,
                                  ts::text AS ts
                           FROM spark_mood_snapshots
                           WHERE guild_id = %s
                           ORDER BY ts DESC LIMIT 1""",
                        (guild_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        snap = dict(row)
                        if isinstance(snap.get("highlights"), str):
                            try:
                                snap["highlights"] = json.loads(snap["highlights"])
                            except Exception:
                                snap["highlights"] = []
                        snap["flavour"] = _MOOD_SPARK_FLAVOUR.get(snap.get("mood_label", ""), "")
                        _mood_snap_cache[guild_id] = snap
                        return snap
        except Exception as e:
            print(f"[db_spark] mood_get_current: {e}")
    return mood_compute_snapshot(guild_id)


def mood_get_history(guild_id: str, limit: int = 24) -> list:
    """Return recent mood snapshots for history chart."""
    if guild_id in _mood_hist_cache:
        hist = list(_mood_hist_cache[guild_id])[-limit:]
        if hist:
            return hist
    if is_available():
        try:
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        """SELECT mood_score, mood_label, mood_emoji,
                                  pos_count, neg_count, neu_count,
                                  ts::text AS ts
                           FROM spark_mood_snapshots
                           WHERE guild_id = %s
                           ORDER BY ts DESC LIMIT %s""",
                        (guild_id, limit),
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                    return list(reversed(rows))
        except Exception as e:
            print(f"[db_spark] mood_get_history: {e}")
    return []


def analytics_get_data(guild_id: str, days: int = 30) -> dict:
    """Return analytics data for the dashboard."""
    empty = {"daily": [], "prev_daily": [], "top_chatters": [], "top_voice": []}
    if not is_available():
        return empty
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT date::text, messages, joins, leaves
                    FROM spark_analytics_daily
                    WHERE guild_id = %s
                      AND date >= CURRENT_DATE - (%s * INTERVAL '1 day')
                    ORDER BY date ASC
                    """,
                    (guild_id, days),
                )
                daily = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    """
                    SELECT date::text, messages, joins, leaves
                    FROM spark_analytics_daily
                    WHERE guild_id = %s
                      AND date >= CURRENT_DATE - (%s * INTERVAL '1 day')
                      AND date <  CURRENT_DATE - (%s * INTERVAL '1 day')
                    ORDER BY date ASC
                    """,
                    (guild_id, days * 2, days),
                )
                prev_daily = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    """
                    SELECT COALESCE(username, user_id) AS username, messages
                    FROM spark_xp
                    WHERE guild_id = %s AND messages > 0
                    ORDER BY messages DESC
                    LIMIT 10
                    """,
                    (guild_id,),
                )
                top_chatters = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    """
                    SELECT COALESCE(username, user_id) AS username, minutes
                    FROM spark_analytics_voice
                    WHERE guild_id = %s
                    ORDER BY minutes DESC
                    LIMIT 10
                    """,
                    (guild_id,),
                )
                top_voice = [dict(r) for r in cur.fetchall()]

        return {
            "daily": daily,
            "prev_daily": prev_daily,
            "top_chatters": top_chatters,
            "top_voice": top_voice,
        }
    except Exception as e:
        print(f"[db_spark] analytics_get_data: {e}")
        return empty


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
