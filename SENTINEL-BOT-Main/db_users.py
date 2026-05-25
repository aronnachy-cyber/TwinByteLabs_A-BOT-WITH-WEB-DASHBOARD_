"""
Postgres-backed user store.

Replaces the JSON file (`users.json`) with a real database table when
DATABASE_URL is configured. Falls back to None when no DB is available so
callers can use the JSON fallback.

The schema mirrors the in-memory user dict shape used everywhere else in
the dashboard so no migration of the call sites is needed.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

_LOCK = threading.Lock()
_POOL: Optional[SimpleConnectionPool] = None
_INITIALIZED = False
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def is_available() -> bool:
    """Return True when DATABASE_URL is set AND the pool initialised cleanly."""
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
            _POOL = SimpleConnectionPool(
                minconn=1, maxconn=2, dsn=_DATABASE_URL,
                # sslmode is set by the URL on managed PG; don't force it here.
            )
        except Exception as e:
            print(f"[db_users] could not open Postgres pool: {e}")
            _POOL = None
            return None
    _init_schema()
    return _POOL


def _init_schema() -> None:
    """Create the users table on first use, and run any column migrations. Idempotent."""
    global _INITIALIZED
    if _INITIALIZED:
        return
    create_sql = """
    CREATE TABLE IF NOT EXISTS sentinel_users (
        uid              TEXT PRIMARY KEY,
        name             TEXT NOT NULL,
        email            TEXT,
        status           TEXT NOT NULL DEFAULT 'pending',
        is_admin         BOOLEAN NOT NULL DEFAULT FALSE,
        created_at       TEXT,
        session_token    TEXT,
        discord_id       TEXT,
        discord_username TEXT,
        avatar_url       TEXT,
        extras           JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS sentinel_users_discord_id_idx
      ON sentinel_users (discord_id);
    """
    # Column migrations — safe to run repeatedly on any pre-existing table.
    migrations = [
        "ALTER TABLE sentinel_users ADD COLUMN IF NOT EXISTS avatar_url       TEXT;",
        "ALTER TABLE sentinel_users ADD COLUMN IF NOT EXISTS discord_id       TEXT;",
        "ALTER TABLE sentinel_users ADD COLUMN IF NOT EXISTS discord_username TEXT;",
        "ALTER TABLE sentinel_users ADD COLUMN IF NOT EXISTS session_token    TEXT;",
        "ALTER TABLE sentinel_users ADD COLUMN IF NOT EXISTS extras           JSONB NOT NULL DEFAULT '{}'::jsonb;",
        "ALTER TABLE sentinel_users ADD COLUMN IF NOT EXISTS updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW();",
    ]
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(create_sql)
                for m in migrations:
                    try:
                        cur.execute(m)
                    except Exception:
                        pass  # column already exists with different constraints — skip
            conn.commit()
        _INITIALIZED = True
        print("✅ Postgres user store ready (sentinel_users table)")
    except Exception as e:
        print(f"[db_users] schema init failed: {e}")


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


# ─── known columns we map between dict ↔ row ──────────────────────────────
_COLUMNS = (
    "uid", "name", "email", "status", "is_admin",
    "created_at", "session_token",
    "discord_id", "discord_username", "avatar_url",
)


def _to_row(u: Dict[str, Any]) -> Dict[str, Any]:
    """Pull known fields out, stash the rest in `extras`."""
    row = {c: u.get(c) for c in _COLUMNS}
    row["uid"] = str(row["uid"] or "")
    row["name"] = str(row["name"] or "")
    row["status"] = str(row["status"] or "pending")
    row["is_admin"] = bool(row["is_admin"])
    extras = {k: v for k, v in (u or {}).items() if k not in _COLUMNS}
    row["extras"] = json.dumps(extras)
    return row


def _from_row(r: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten DB row back into the in-memory user dict shape."""
    out: Dict[str, Any] = {}
    for c in _COLUMNS:
        v = r.get(c)
        if v is not None:
            out[c] = v
    extras = r.get("extras") or {}
    if isinstance(extras, str):
        try:
            extras = json.loads(extras)
        except Exception:
            extras = {}
    if isinstance(extras, dict):
        for k, v in extras.items():
            out.setdefault(k, v)
    return out


# ─── Public API ───────────────────────────────────────────────────────────
def load_all() -> List[Dict[str, Any]]:
    """Return every user in insertion order."""
    if not is_available():
        return []
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM sentinel_users ORDER BY updated_at ASC")
                rows = cur.fetchall()
        return [_from_row(dict(r)) for r in rows]
    except Exception as e:
        print(f"[db_users] load_all failed: {e}")
        return []


def upsert(u: Dict[str, Any]) -> bool:
    """Insert or update a single user. Returns True on success."""
    if not is_available():
        return False
    if not u or not u.get("uid"):
        return False
    row = _to_row(u)
    sql = """
        INSERT INTO sentinel_users
            (uid, name, email, status, is_admin, created_at, session_token,
             discord_id, discord_username, avatar_url, extras, updated_at)
        VALUES
            (%(uid)s, %(name)s, %(email)s, %(status)s, %(is_admin)s,
             %(created_at)s, %(session_token)s,
             %(discord_id)s, %(discord_username)s, %(avatar_url)s,
             %(extras)s::jsonb, NOW())
        ON CONFLICT (uid) DO UPDATE SET
            name             = EXCLUDED.name,
            email            = EXCLUDED.email,
            status           = EXCLUDED.status,
            is_admin         = EXCLUDED.is_admin,
            created_at       = EXCLUDED.created_at,
            session_token    = EXCLUDED.session_token,
            discord_id       = EXCLUDED.discord_id,
            discord_username = EXCLUDED.discord_username,
            avatar_url       = EXCLUDED.avatar_url,
            extras           = EXCLUDED.extras,
            updated_at       = NOW();
    """
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, row)
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_users] upsert failed: {e}")
        return False


def find_by_discord_id(discord_id: str) -> Optional[Dict[str, Any]]:
    if not is_available() or not discord_id:
        return None
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM sentinel_users WHERE discord_id = %s LIMIT 1",
                    (str(discord_id),),
                )
                row = cur.fetchone()
        return _from_row(dict(row)) if row else None
    except Exception as e:
        print(f"[db_users] find_by_discord_id failed: {e}")
        return None


def delete(uid: str) -> bool:
    if not is_available() or not uid:
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sentinel_users WHERE uid = %s", (str(uid),))
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_users] delete failed: {e}")
        return False


def migrate_from_json(json_path: str) -> int:
    """One-shot migration: copy every user from a legacy users.json into PG.
    Returns the number of users migrated. Safe to call repeatedly (uses upsert).
    """
    if not is_available() or not os.path.exists(json_path):
        return 0
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        users = data.get("users") or []
        n = 0
        for u in users:
            if isinstance(u, dict) and u.get("uid"):
                if upsert(u):
                    n += 1
        if n:
            print(f"📦 Migrated {n} user(s) from {json_path} → Postgres")
        return n
    except Exception as e:
        print(f"[db_users] migration failed: {e}")
        return 0
