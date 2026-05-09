"""
db_so.py — PostgreSQL backend for the Server Official bot state.

Architecture:
  • so_state        — single-row JSONB master blob (fast load/save)
  • so_server_logs  — denormalized log rows (append-only, queryable)
  • so_bot_actions  — denormalized bot action rows (append-only)
  • so_guild_configs— per-guild config (queryable)
  • so_announcements— scheduled announcements (queryable)
  • so_goals        — server goals (queryable)
  • so_admin_notes  — admin notes (queryable)
  • so_reminders    — reminders (queryable)
  • so_custom_cmds  — custom commands (queryable)
  • so_server_rules — server rules (queryable)
  • so_templates    — announcement templates (queryable)
  • so_giveaways    — giveaways (queryable)
  • so_setup_history— setup history (queryable)
  • so_config_backups — config backups (queryable)

Falls back silently when DATABASE_URL is not set.
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

STATE_ROW_ID = "main"

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS so_state (
    id          TEXT PRIMARY KEY,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_guild_configs (
    guild_id    TEXT PRIMARY KEY,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_announcements (
    id              TEXT PRIMARY KEY,
    guild_id        TEXT,
    channel_id      TEXT,
    title           TEXT,
    message         TEXT,
    color           INT,
    scheduled_for   TIMESTAMPTZ,
    posted          BOOLEAN DEFAULT FALSE,
    posted_at       TIMESTAMPTZ,
    image_url       TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS so_ann_guild ON so_announcements (guild_id);
CREATE INDEX IF NOT EXISTS so_ann_sched ON so_announcements (scheduled_for);

CREATE TABLE IF NOT EXISTS so_templates (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT,
    name        TEXT NOT NULL,
    title       TEXT,
    message     TEXT,
    color       INT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_goals (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    goal_type   TEXT NOT NULL,
    target      INT NOT NULL,
    current_val INT DEFAULT 0,
    ends_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    done        BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS so_goals_guild ON so_goals (guild_id);

CREATE TABLE IF NOT EXISTS so_admin_notes (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    text        TEXT NOT NULL,
    author      TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS so_notes_guild ON so_admin_notes (guild_id, created_at DESC);

CREATE TABLE IF NOT EXISTS so_reminders (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    guild_id    TEXT,
    channel_id  TEXT NOT NULL,
    text        TEXT NOT NULL,
    due_at      TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    sent        BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS so_stickies (
    channel_id      TEXT PRIMARY KEY,
    guild_id        TEXT,
    message         TEXT NOT NULL,
    last_message_id TEXT,
    enabled         BOOLEAN DEFAULT TRUE,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_custom_cmds (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    trigger     TEXT NOT NULL,
    response    TEXT,
    description TEXT,
    embed       BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (guild_id, trigger)
);
CREATE INDEX IF NOT EXISTS so_cmds_guild ON so_custom_cmds (guild_id);

CREATE TABLE IF NOT EXISTS so_webhooks_in (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    secret      TEXT NOT NULL UNIQUE,
    channel_id  TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_external_hooks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    events      TEXT[] DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_server_logs (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT,
    log_type    TEXT NOT NULL,
    description TEXT NOT NULL,
    ts          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS so_slogs_guild_ts ON so_server_logs (guild_id, ts DESC);

CREATE TABLE IF NOT EXISTS so_bot_actions (
    id          TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    summary     TEXT NOT NULL,
    actor       TEXT DEFAULT 'system',
    ts          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS so_bact_ts ON so_bot_actions (ts DESC);

CREATE TABLE IF NOT EXISTS so_setup_history (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    action      TEXT NOT NULL,
    details     JSONB DEFAULT '{}',
    ts          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_config_backups (
    id          TEXT PRIMARY KEY,
    label       TEXT,
    snapshot    JSONB NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_activity_stats (
    guild_id    TEXT PRIMARY KEY,
    stats       JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_server_rules (
    guild_id        TEXT PRIMARY KEY,
    text            TEXT NOT NULL DEFAULT '',
    channel_id      TEXT,
    last_posted_at  TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_polls (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT,
    channel_id  TEXT,
    message_id  TEXT,
    question    TEXT NOT NULL,
    options     JSONB DEFAULT '[]',
    ends_at     TIMESTAMPTZ,
    ended       BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS so_giveaways (
    id              TEXT PRIMARY KEY,
    guild_id        TEXT,
    channel_id      TEXT,
    message_id      TEXT,
    prize           TEXT NOT NULL,
    winner_count    INT DEFAULT 1,
    ends_at         TIMESTAMPTZ,
    ended           BOOLEAN DEFAULT FALSE,
    winners         JSONB DEFAULT '[]',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS so_giveaways_guild ON so_giveaways (guild_id);

CREATE TABLE IF NOT EXISTS so_invite_counts (
    guild_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    invite_count INT DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (guild_id, user_id)
);
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
            print(f"[db_so] pool init failed: {e}")
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
        print("✅ Postgres SO store ready (so_* tables)")
    except Exception as e:
        print(f"[db_so] schema init failed: {e}")


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


def _ts(val) -> Optional[str]:
    """Parse an ISO timestamp string (or None) safely."""
    if val is None:
        return None
    if isinstance(val, str):
        return val.replace("Z", "+00:00") if val else None
    return str(val)


# ---------------------------------------------------------------------------
# Master state blob: load / save
# ---------------------------------------------------------------------------
def load_full_state() -> Optional[Dict[str, Any]]:
    """Load the full SO state dict from so_state. Returns None if not found."""
    if not is_available():
        return None
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT data FROM so_state WHERE id = %s", (STATE_ROW_ID,))
                row = cur.fetchone()
        if not row:
            return None
        data = row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        return data
    except Exception as e:
        print(f"[db_so] load_full_state failed: {e}")
        return None


def save_full_state(state: Dict[str, Any]) -> bool:
    """Persist the full SO state dict to so_state and sync denormalized tables."""
    if not is_available():
        return False
    try:
        blob = json.dumps(state, default=str)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO so_state (id, data, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (id) DO UPDATE
                        SET data = EXCLUDED.data, updated_at = NOW()
                """, (STATE_ROW_ID, blob))
            conn.commit()
        _sync_denormalized(state)
        return True
    except Exception as e:
        print(f"[db_so] save_full_state failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Denormalized table sync — makes SQL editor useful
# ---------------------------------------------------------------------------
def _sync_denormalized(state: Dict[str, Any]) -> None:
    """Best-effort sync of specific tables from the master state blob."""
    try:
        _sync_guild_configs(state.get("server_configs", {}))
        _sync_announcements(state.get("scheduled_announcements", []))
        _sync_templates(state.get("announcement_templates", []))
        _sync_goals(state.get("server_goals", []))
        _sync_notes(state.get("admin_notes", {}))
        _sync_reminders(state.get("reminders", []))
        _sync_stickies(state.get("sticky_messages", {}))
        _sync_custom_cmds(state.get("custom_commands", {}))
        _sync_webhooks_in(state.get("webhooks_in", []))
        _sync_external_hooks(state.get("external_hooks", []))
        _sync_server_logs_append(state.get("server_logs", []))
        _sync_bot_actions_append(state.get("bot_action_logs", []))
        _sync_setup_history(state.get("setup_history", []))
        _sync_backups(state.get("config_backups", []))
        _sync_activity_stats(state.get("activity_stats", {}))
        _sync_server_rules(state.get("server_rules", {}))
        _sync_polls(state.get("polls", []))
        _sync_giveaways(state.get("giveaways", []))
        _sync_invite_counts(state.get("invite_counts", {}))
    except Exception as e:
        print(f"[db_so] sync_denormalized error: {e}")


def _upsert_batch(conn, table: str, rows: List[Dict], pk: str = "id") -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    cols = ", ".join(keys)
    placeholders = ", ".join([f"%({k})s" for k in keys])
    updates = ", ".join([f"{k} = EXCLUDED.{k}" for k in keys if k != pk])
    sql = (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT ({pk}) DO UPDATE SET {updates}"
    )
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)
    conn.commit()


def _sync_guild_configs(configs: Dict) -> None:
    if not configs:
        return
    rows = [{"guild_id": gid, "data": json.dumps(cfg, default=str)}
            for gid, cfg in configs.items() if isinstance(cfg, dict)]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_guild_configs (guild_id, data, updated_at)
                    VALUES (%(guild_id)s, %(data)s::jsonb, NOW())
                    ON CONFLICT (guild_id) DO UPDATE
                        SET data = EXCLUDED.data, updated_at = NOW()
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync guild_configs: {e}")


def _sync_announcements(items: List) -> None:
    if not items:
        return
    rows = []
    for a in items:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        rows.append({
            "id": str(a["id"]),
            "guild_id": a.get("guild_id"),
            "channel_id": a.get("channel_id", ""),
            "title": a.get("title"),
            "message": a.get("message", ""),
            "color": a.get("color"),
            "scheduled_for": _ts(a.get("scheduled_for")),
            "posted": bool(a.get("posted", False)),
            "posted_at": _ts(a.get("posted_at")),
            "image_url": a.get("image_url"),
            "created_at": _ts(a.get("created_at")) or "NOW()",
        })
    if not rows:
        return
    try:
        with _conn() as conn:
            _upsert_batch(conn, "so_announcements", rows)
    except Exception as e:
        print(f"[db_so] sync announcements: {e}")


def _sync_templates(items: List) -> None:
    rows = [{"id": str(t["id"]), "guild_id": t.get("guild_id"), "name": t.get("name", ""),
             "title": t.get("title"), "message": t.get("message", ""), "color": t.get("color"),
             "created_at": _ts(t.get("created_at"))}
            for t in items if isinstance(t, dict) and t.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            _upsert_batch(conn, "so_templates", rows)
    except Exception as e:
        print(f"[db_so] sync templates: {e}")


def _sync_goals(items: List) -> None:
    rows = [{"id": str(g["id"]), "guild_id": g.get("guild_id", ""), "goal_type": g.get("type", ""),
             "target": int(g.get("target", 0)), "current_val": int(g.get("current", 0)),
             "ends_at": _ts(g.get("ends_at")), "created_at": _ts(g.get("created_at")),
             "done": bool(g.get("done", False))}
            for g in items if isinstance(g, dict) and g.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            _upsert_batch(conn, "so_goals", rows)
    except Exception as e:
        print(f"[db_so] sync goals: {e}")


def _sync_notes(notes_map: Dict) -> None:
    rows = []
    for guild_id, note_list in notes_map.items():
        if not isinstance(note_list, list):
            continue
        for n in note_list:
            if not isinstance(n, dict) or not n.get("id"):
                continue
            rows.append({"id": str(n["id"]), "guild_id": str(guild_id),
                         "text": n.get("text", ""), "author": n.get("author"),
                         "created_at": _ts(n.get("created_at"))})
    if not rows:
        return
    try:
        with _conn() as conn:
            _upsert_batch(conn, "so_admin_notes", rows)
    except Exception as e:
        print(f"[db_so] sync admin_notes: {e}")


def _sync_reminders(items: List) -> None:
    rows = [{"id": str(r["id"]), "user_id": r.get("user_id", ""),
             "guild_id": r.get("guild_id"), "channel_id": r.get("channel_id", ""),
             "text": r.get("text", ""), "due_at": _ts(r.get("due_at")),
             "created_at": _ts(r.get("created_at")), "sent": bool(r.get("sent", False))}
            for r in items if isinstance(r, dict) and r.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            _upsert_batch(conn, "so_reminders", rows)
    except Exception as e:
        print(f"[db_so] sync reminders: {e}")


def _sync_stickies(stickies_map: Dict) -> None:
    rows = [{"channel_id": str(cid), "guild_id": v.get("guild_id"),
             "message": v.get("message", ""), "last_message_id": v.get("last_message_id"),
             "enabled": bool(v.get("enabled", True))}
            for cid, v in stickies_map.items() if isinstance(v, dict)]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_stickies (channel_id, guild_id, message, last_message_id, enabled, updated_at)
                    VALUES (%(channel_id)s, %(guild_id)s, %(message)s, %(last_message_id)s, %(enabled)s, NOW())
                    ON CONFLICT (channel_id) DO UPDATE
                        SET guild_id=EXCLUDED.guild_id, message=EXCLUDED.message,
                            last_message_id=EXCLUDED.last_message_id, enabled=EXCLUDED.enabled,
                            updated_at=NOW()
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync stickies: {e}")


def _sync_custom_cmds(cmds_map: Dict) -> None:
    rows = []
    for guild_id, triggers in cmds_map.items():
        if not isinstance(triggers, dict):
            continue
        for trigger, v in triggers.items():
            if not isinstance(v, dict):
                continue
            rows.append({
                "id": f"{guild_id}:{trigger}",
                "guild_id": str(guild_id), "trigger": str(trigger),
                "response": v.get("response", ""), "description": v.get("description"),
                "embed": bool(v.get("embed", False)),
                "created_at": _ts(v.get("created_at")),
            })
    if not rows:
        return
    try:
        with _conn() as conn:
            _upsert_batch(conn, "so_custom_cmds", rows)
    except Exception as e:
        print(f"[db_so] sync custom_cmds: {e}")


def _sync_webhooks_in(items: List) -> None:
    rows = [{"id": str(w["id"]), "guild_id": w.get("guild_id", ""),
             "name": w.get("name", ""), "secret": w.get("secret", ""),
             "channel_id": w.get("channel_id", ""), "created_at": _ts(w.get("created_at"))}
            for w in items if isinstance(w, dict) and w.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            _upsert_batch(conn, "so_webhooks_in", rows)
    except Exception as e:
        print(f"[db_so] sync webhooks_in: {e}")


def _sync_external_hooks(items: List) -> None:
    rows = [{"id": str(h["id"]), "name": h.get("name", ""), "url": h.get("url", ""),
             "events": h.get("events", []), "created_at": _ts(h.get("created_at"))}
            for h in items if isinstance(h, dict) and h.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_external_hooks (id, name, url, events, created_at)
                    VALUES (%(id)s, %(name)s, %(url)s, %(events)s::text[], %(created_at)s)
                    ON CONFLICT (id) DO UPDATE
                        SET name=EXCLUDED.name, url=EXCLUDED.url, events=EXCLUDED.events
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync external_hooks: {e}")


def _sync_server_logs_append(items: List) -> None:
    """Append-only: only insert rows that don't already exist."""
    rows = [{"id": str(l["id"]), "guild_id": l.get("guild_id"),
             "log_type": l.get("type", ""), "description": l.get("description", ""),
             "ts": _ts(l.get("ts"))}
            for l in items[-2000:] if isinstance(l, dict) and l.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_server_logs (id, guild_id, log_type, description, ts)
                    VALUES (%(id)s, %(guild_id)s, %(log_type)s, %(description)s, %(ts)s)
                    ON CONFLICT (id) DO NOTHING
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync server_logs: {e}")


def _sync_bot_actions_append(items: List) -> None:
    rows = [{"id": str(a["id"]), "action_type": a.get("type", ""),
             "summary": a.get("summary", ""), "actor": a.get("actor", "system"),
             "ts": _ts(a.get("ts"))}
            for a in items[-2000:] if isinstance(a, dict) and a.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_bot_actions (id, action_type, summary, actor, ts)
                    VALUES (%(id)s, %(action_type)s, %(summary)s, %(actor)s, %(ts)s)
                    ON CONFLICT (id) DO NOTHING
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync bot_actions: {e}")


def _sync_setup_history(items: List) -> None:
    rows = [{"id": str(h["id"]), "guild_id": h.get("guild_id", ""),
             "action": h.get("action", ""), "details": json.dumps(h.get("details", {}), default=str),
             "ts": _ts(h.get("ts"))}
            for h in items if isinstance(h, dict) and h.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_setup_history (id, guild_id, action, details, ts)
                    VALUES (%(id)s, %(guild_id)s, %(action)s, %(details)s::jsonb, %(ts)s)
                    ON CONFLICT (id) DO NOTHING
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync setup_history: {e}")


def _sync_backups(items: List) -> None:
    rows = [{"id": str(b["id"]), "label": b.get("label"),
             "snapshot": json.dumps(b.get("snapshot", {}), default=str),
             "created_at": _ts(b.get("ts"))}
            for b in items if isinstance(b, dict) and b.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_config_backups (id, label, snapshot, created_at)
                    VALUES (%(id)s, %(label)s, %(snapshot)s::jsonb, %(created_at)s)
                    ON CONFLICT (id) DO NOTHING
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync config_backups: {e}")


def _sync_activity_stats(stats_map: Dict) -> None:
    rows = [{"guild_id": str(gid), "stats": json.dumps(v, default=str)}
            for gid, v in stats_map.items() if isinstance(v, dict)]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_activity_stats (guild_id, stats, updated_at)
                    VALUES (%(guild_id)s, %(stats)s::jsonb, NOW())
                    ON CONFLICT (guild_id) DO UPDATE
                        SET stats=EXCLUDED.stats, updated_at=NOW()
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync activity_stats: {e}")


def _sync_server_rules(rules_map: Dict) -> None:
    rows = [{"guild_id": str(gid), "text": v.get("text", ""),
             "channel_id": v.get("channel_id"),
             "last_posted_at": _ts(v.get("last_posted_at"))}
            for gid, v in rules_map.items() if isinstance(v, dict)]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_server_rules (guild_id, text, channel_id, last_posted_at, updated_at)
                    VALUES (%(guild_id)s, %(text)s, %(channel_id)s, %(last_posted_at)s, NOW())
                    ON CONFLICT (guild_id) DO UPDATE
                        SET text=EXCLUDED.text, channel_id=EXCLUDED.channel_id,
                            last_posted_at=EXCLUDED.last_posted_at, updated_at=NOW()
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync server_rules: {e}")


def _sync_polls(items: List) -> None:
    rows = [{"id": str(p["id"]), "guild_id": p.get("guild_id"),
             "channel_id": p.get("channel_id"), "message_id": p.get("message_id"),
             "question": p.get("question", ""), "options": json.dumps(p.get("options", []), default=str),
             "ends_at": _ts(p.get("ends_at")), "ended": bool(p.get("ended", False)),
             "created_at": _ts(p.get("created_at"))}
            for p in items if isinstance(p, dict) and p.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_polls (id, guild_id, channel_id, message_id, question, options, ends_at, ended, created_at)
                    VALUES (%(id)s, %(guild_id)s, %(channel_id)s, %(message_id)s, %(question)s, %(options)s::jsonb, %(ends_at)s, %(ended)s, %(created_at)s)
                    ON CONFLICT (id) DO UPDATE
                        SET ended=EXCLUDED.ended, options=EXCLUDED.options
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync polls: {e}")


def _sync_giveaways(items: List) -> None:
    rows = [{"id": str(g["id"]), "guild_id": g.get("guild_id"),
             "channel_id": g.get("channel_id"), "message_id": g.get("message_id"),
             "prize": g.get("prize", ""), "winner_count": int(g.get("winner_count", 1)),
             "ends_at": _ts(g.get("ends_at")), "ended": bool(g.get("ended", False)),
             "winners": json.dumps(g.get("winners", []), default=str),
             "created_at": _ts(g.get("created_at"))}
            for g in items if isinstance(g, dict) and g.get("id")]
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_giveaways (id, guild_id, channel_id, message_id, prize, winner_count, ends_at, ended, winners, created_at)
                    VALUES (%(id)s, %(guild_id)s, %(channel_id)s, %(message_id)s, %(prize)s, %(winner_count)s, %(ends_at)s, %(ended)s, %(winners)s::jsonb, %(created_at)s)
                    ON CONFLICT (id) DO UPDATE
                        SET ended=EXCLUDED.ended, winners=EXCLUDED.winners
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync giveaways: {e}")


def _sync_invite_counts(counts_map: Dict) -> None:
    rows = []
    for guild_id, users in counts_map.items():
        if not isinstance(users, dict):
            continue
        for user_id, count in users.items():
            try:
                rows.append({"guild_id": str(guild_id), "user_id": str(user_id),
                             "invite_count": int(count)})
            except Exception:
                pass
    if not rows:
        return
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO so_invite_counts (guild_id, user_id, invite_count, updated_at)
                    VALUES (%(guild_id)s, %(user_id)s, %(invite_count)s, NOW())
                    ON CONFLICT (guild_id, user_id) DO UPDATE
                        SET invite_count=EXCLUDED.invite_count, updated_at=NOW()
                """, rows)
            conn.commit()
    except Exception as e:
        print(f"[db_so] sync invite_counts: {e}")


# ---------------------------------------------------------------------------
# SQL Editor helpers
# ---------------------------------------------------------------------------
def list_tables() -> List[Dict[str, Any]]:
    """Return all so_* tables with their column names and types."""
    if not is_available():
        return []
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        t.table_name,
                        c.column_name,
                        c.data_type,
                        c.is_nullable,
                        c.column_default
                    FROM information_schema.tables t
                    JOIN information_schema.columns c
                        ON c.table_name = t.table_name
                        AND c.table_schema = t.table_schema
                    WHERE t.table_schema = 'public'
                        AND t.table_name LIKE 'so\_%'
                    ORDER BY t.table_name, c.ordinal_position
                """)
                rows = cur.fetchall()
        tables: Dict[str, List] = {}
        for r in rows:
            tn = r["table_name"]
            if tn not in tables:
                tables[tn] = []
            tables[tn].append({
                "column": r["column_name"],
                "type": r["data_type"],
                "nullable": r["is_nullable"] == "YES",
            })
        return [{"table": name, "columns": cols} for name, cols in tables.items()]
    except Exception as e:
        print(f"[db_so] list_tables: {e}")
        return []


def run_sql(query: str, max_rows: int = 500) -> Dict[str, Any]:
    """Execute a SQL query safely. Returns {ok, columns, rows, rowcount, message}."""
    if not is_available():
        return {"ok": False, "error": "Database not configured", "columns": [], "rows": [], "rowcount": 0}
    query = query.strip()
    if not query:
        return {"ok": False, "error": "Empty query", "columns": [], "rows": [], "rowcount": 0}

    upper = query.upper()
    forbidden_ddl = ["DROP ", "TRUNCATE ", "CREATE ", "ALTER ", "GRANT ", "REVOKE "]
    for kw in forbidden_ddl:
        if kw in upper:
            return {"ok": False, "error": f"DDL not allowed: {kw.strip()}. Use SELECT, INSERT, UPDATE, DELETE only.",
                    "columns": [], "rows": [], "rowcount": 0}

    is_read = upper.lstrip().startswith("SELECT") or upper.lstrip().startswith("WITH")
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query)
                if is_read:
                    raw_rows = cur.fetchmany(max_rows)
                    cols = [desc[0] for desc in (cur.description or [])]
                    rows = [_serialize_row(dict(r)) for r in raw_rows]
                    truncated = len(raw_rows) >= max_rows
                    conn.commit()
                    return {
                        "ok": True, "columns": cols, "rows": rows,
                        "rowcount": len(rows),
                        "message": f"{len(rows)} row(s)" + (" (limit reached)" if truncated else ""),
                    }
                else:
                    rc = cur.rowcount
                    conn.commit()
                    return {
                        "ok": True, "columns": [], "rows": [],
                        "rowcount": rc,
                        "message": f"{rc} row(s) affected",
                    }
    except Exception as e:
        return {"ok": False, "error": str(e), "columns": [], "rows": [], "rowcount": 0}


def _serialize_row(row: Dict) -> Dict:
    """Make a row JSON-serializable."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, (dict, list)):
            out[k] = v
        elif v is None:
            out[k] = None
        else:
            out[k] = str(v) if not isinstance(v, (int, float, bool, str)) else v
    return out
