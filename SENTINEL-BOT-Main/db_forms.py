"""
Postgres-backed form-submission store.

Currently holds the public "Ask for a Bot" requests, but the schema is
intentionally generic (`form_type` + `payload` JSONB) so additional form
types can be added later without a new table.

Falls back to None / no-op when DATABASE_URL is not configured, so
callers can use the JSON file fallback.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

_LOCK = threading.Lock()
_POOL: Optional[SimpleConnectionPool] = None
_INITIALIZED = False
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# Type tag stored in the DB for the "Ask for a custom bot" submissions.
FORM_TYPE_BOT_REQUEST = "bot_request"


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
            _POOL = SimpleConnectionPool(minconn=1, maxconn=2, dsn=_DATABASE_URL)
        except Exception as e:
            print(f"[db_forms] could not open Postgres pool: {e}")
            _POOL = None
            return None
    _init_schema()
    return _POOL


def _init_schema() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    sql = """
    CREATE TABLE IF NOT EXISTS sentinel_form_submissions (
        id          TEXT PRIMARY KEY,
        form_type   TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'new',
        payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS sentinel_form_submissions_type_idx
      ON sentinel_form_submissions (form_type);
    CREATE INDEX IF NOT EXISTS sentinel_form_submissions_created_idx
      ON sentinel_form_submissions (created_at DESC);
    """
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        _INITIALIZED = True
        print("✅ Postgres form store ready (sentinel_form_submissions table)")
    except Exception as e:
        print(f"[db_forms] schema init failed: {e}")


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


def _row_to_entry(r: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a DB row back to the in-memory entry shape the dashboard expects."""
    payload = r.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    out: Dict[str, Any] = dict(payload) if isinstance(payload, dict) else {}
    out["id"] = r.get("id")
    out["status"] = r.get("status") or out.get("status") or "new"
    created = r.get("created_at")
    if isinstance(created, datetime):
        out["created_at"] = created.astimezone(timezone.utc).isoformat()
    elif created:
        out["created_at"] = str(created)
    return out


def _entry_to_payload(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Strip top-level columns from the entry; rest goes into the JSONB blob."""
    payload = {k: v for k, v in (entry or {}).items()
               if k not in ("id", "status", "created_at")}
    return payload


# ─── Public API ──────────────────────────────────────────────────────────
def insert(entry: Dict[str, Any], form_type: str = FORM_TYPE_BOT_REQUEST) -> bool:
    if not is_available() or not entry or not entry.get("id"):
        return False
    payload = _entry_to_payload(entry)
    sql = """
        INSERT INTO sentinel_form_submissions (id, form_type, status, payload, created_at, updated_at)
        VALUES (%(id)s, %(form_type)s, %(status)s, %(payload)s::jsonb, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET
            form_type  = EXCLUDED.form_type,
            status     = EXCLUDED.status,
            payload    = EXCLUDED.payload,
            updated_at = NOW();
    """
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {
                    "id": str(entry["id"]),
                    "form_type": form_type,
                    "status": entry.get("status") or "new",
                    "payload": json.dumps(payload),
                })
            conn.commit()
        return True
    except Exception as e:
        print(f"[db_forms] insert failed: {e}")
        return False


def list_all(form_type: Optional[str] = FORM_TYPE_BOT_REQUEST,
             limit: int = 500) -> List[Dict[str, Any]]:
    if not is_available():
        return []
    sql = "SELECT * FROM sentinel_form_submissions"
    params: List[Any] = []
    if form_type:
        sql += " WHERE form_type = %s"
        params.append(form_type)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(int(limit))
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [_row_to_entry(dict(r)) for r in rows]
    except Exception as e:
        print(f"[db_forms] list_all failed: {e}")
        return []


def delete_by_id(rid: str) -> bool:
    if not is_available() or not rid:
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sentinel_form_submissions WHERE id = %s", (str(rid),))
                deleted = cur.rowcount
            conn.commit()
        return deleted > 0
    except Exception as e:
        print(f"[db_forms] delete failed: {e}")
        return False


def update_status(rid: str, status: str) -> bool:
    if not is_available() or not rid or not status:
        return False
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sentinel_form_submissions SET status=%s, updated_at=NOW() WHERE id=%s",
                    (str(status), str(rid)),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as e:
        print(f"[db_forms] update_status failed: {e}")
        return False


def migrate_from_json(json_path: str,
                      form_type: str = FORM_TYPE_BOT_REQUEST) -> int:
    """Copy every entry from a legacy bot_requests.json into PG. Idempotent."""
    if not is_available() or not os.path.exists(json_path):
        return 0
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f) or []
        items = data if isinstance(data, list) else (data.get("items") or [])
        n = 0
        for entry in items:
            if isinstance(entry, dict) and entry.get("id"):
                if insert(entry, form_type=form_type):
                    n += 1
        if n:
            print(f"📦 Migrated {n} form submission(s) from {json_path} → Postgres")
        return n
    except Exception as e:
        print(f"[db_forms] migration failed: {e}")
        return 0
