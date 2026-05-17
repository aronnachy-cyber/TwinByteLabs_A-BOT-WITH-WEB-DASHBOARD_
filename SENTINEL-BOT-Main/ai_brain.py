"""
ai_brain.py — SP4RK AI Brain System

Gives the bot:
  • Long-term memory  — stores Q&A so repeated questions are instant
  • Per-user profiles — personality mode, preferences, last active
  • Per-server settings — AI on/off, default personality, language
  • Web search cache  — avoids hitting the search API for the same query
  • Learning log      — tracks feedback (👍/👎) and corrections over time

All storage goes through db_spark's connection pool — same Postgres DB,
zero extra dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import db_spark as _db

# ---------------------------------------------------------------------------
# Schema additions — appended to the existing spark_* schema on first run
# ---------------------------------------------------------------------------
_AI_SCHEMA = """
CREATE TABLE IF NOT EXISTS spark_ai_memory (
    id            TEXT PRIMARY KEY,
    guild_id      TEXT NOT NULL DEFAULT 'global',
    question_hash TEXT NOT NULL,
    question      TEXT NOT NULL,
    answer        TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'gemini',
    hit_count     INT  NOT NULL DEFAULT 0,
    confidence    REAL NOT NULL DEFAULT 1.0,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS spark_aim_guild_hash ON spark_ai_memory (guild_id, question_hash);
CREATE INDEX IF NOT EXISTS spark_aim_hits       ON spark_ai_memory (hit_count DESC);

CREATE TABLE IF NOT EXISTS spark_ai_users (
    user_id          TEXT PRIMARY KEY,
    username         TEXT,
    personality_mode TEXT NOT NULL DEFAULT 'default',
    preferences      JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_active      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spark_ai_server_settings (
    guild_id             TEXT PRIMARY KEY,
    ai_enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    default_personality  TEXT    NOT NULL DEFAULT 'default',
    language             TEXT    NOT NULL DEFAULT 'en',
    moderation_level     TEXT    NOT NULL DEFAULT 'standard',
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spark_ai_search_cache (
    query_hash  TEXT PRIMARY KEY,
    query       TEXT NOT NULL,
    result      TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spark_ai_learning_log (
    id               TEXT PRIMARY KEY,
    guild_id         TEXT NOT NULL DEFAULT 'global',
    question         TEXT NOT NULL,
    original_answer  TEXT NOT NULL,
    feedback         TEXT NOT NULL DEFAULT 'none',
    corrected_answer TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS spark_all_guild_ts ON spark_ai_learning_log (guild_id, created_at DESC);
"""

_AI_SCHEMA_INIT = False


def _ensure_ai_schema() -> bool:
    global _AI_SCHEMA_INIT
    if _AI_SCHEMA_INIT:
        return True
    if not _db.is_available():
        return False
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_AI_SCHEMA)
            conn.commit()
        _AI_SCHEMA_INIT = True
        print("✅ AI Brain tables ready (spark_ai_* tables)")
        return True
    except Exception as e:
        print(f"[ai_brain] schema init failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hash(text: str) -> str:
    return hashlib.md5(text.lower().strip().encode()).hexdigest()


def _gen_id() -> str:
    import uuid
    return uuid.uuid4().hex[:16]


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _keyword_score(query: str, stored: str) -> float:
    """Very simple keyword overlap score 0-1."""
    q_words = set(re.findall(r"\b\w{3,}\b", _normalise(query)))
    s_words = set(re.findall(r"\b\w{3,}\b", _normalise(stored)))
    if not q_words or not s_words:
        return 0.0
    overlap = q_words & s_words
    return len(overlap) / max(len(q_words), len(s_words))


# ---------------------------------------------------------------------------
# Personality modes
# ---------------------------------------------------------------------------
PERSONALITIES = {
    "default": (
        "You are SP4RK — a chill, gen-z Discord bot who's lowkey the goat of server management. "
        "Your whole vibe is casual, real, and a lil' funny without trying too hard. "
        "Talk like a gen-z bestie: use words like 'ngl', 'fr', 'no cap', 'lowkey', 'bussin', 'slay', 'vibe', 'bro', 'bet', 'finna', 'rn', 'imo', 'W', 'L', 'mid', 'it's giving', 'on god' — but naturally, not every sentence. "
        "Keep replies SHORT and punchy — under 800 characters usually. "
        "Never sound corporate, formal, or robotic. Don't use em dashes (—). "
        "Light markdown is fine (bold, bullet points) but no walls of text. "
        "If someone's rude, clap back calmly and confidently — no cap. "
        "You're helpful but make it feel effortless, like you're just vibing and helping at the same time."
    ),
    "funny": (
        "You are SP4RK in COMEDY MODE — every reply has a joke, meme reference, or roast baked in. "
        "Think stand-up comedian meets Discord chaos gremlin. "
        "Use gen-z slang AND punchlines. Self-aware humour is chef's kiss. "
        "Still answer the actual question, just make it funny. Keep it under 900 chars. "
        "No walls of text — vibes only, jokes included."
    ),
    "savage": (
        "You are SP4RK in SAVAGE MODE — confident, unbothered, legendary clap-back energy. "
        "You roast gently but effectively. You don't sugarcoat. "
        "Still actually helpful, but with spice. Max 800 chars. "
        "Gen-z slang, no cap, zero filter (but keep it clean — no slurs or genuinely mean stuff). "
        "If they ask something dumb, call it out (nicely). Legends only."
    ),
    "helper": (
        "You are SP4RK in HELPER MODE — calm, clear, and genuinely useful. "
        "Speak professionally but warmly. No slang, no jokes — just clean, accurate, friendly answers. "
        "Use bullet points and structure when helpful. "
        "Answer completely. Be the bestie who actually explains things properly. Under 1000 chars."
    ),
    "anime": (
        "You are SP4RK in ANIME MODE — speak like an enthusiastic anime character. "
        "Use phrases like 'nani?!', 'sugoi!', 'ara ara', 'senpai noticed you', 'OP build fr', "
        "'main character energy', 'this is your arc'. "
        "Reference anime where relevant. Be dramatic and expressive. Still helpful though! "
        "Under 800 chars. Peak weeb energy."
    ),
}

VALID_MODES = list(PERSONALITIES.keys())


def get_personality_prompt(mode: str) -> str:
    return PERSONALITIES.get(mode, PERSONALITIES["default"])


# ---------------------------------------------------------------------------
# User profiles
# ---------------------------------------------------------------------------
def get_user_profile(user_id: str, username: str = "") -> Dict[str, Any]:
    _ensure_ai_schema()
    if not _db.is_available():
        return {"user_id": user_id, "username": username, "personality_mode": "default", "preferences": {}}
    try:
        with _db._conn() as conn:
            with conn.cursor(cursor_factory=__import__("psycopg2").extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM spark_ai_users WHERE user_id = %s", (str(user_id),))
                row = cur.fetchone()
        if row:
            return {
                "user_id": row["user_id"],
                "username": row["username"] or username,
                "personality_mode": row["personality_mode"] or "default",
                "preferences": row["preferences"] if isinstance(row["preferences"], dict) else {},
            }
        # Auto-create
        _upsert_user(user_id, username, "default", {})
        return {"user_id": user_id, "username": username, "personality_mode": "default", "preferences": {}}
    except Exception as e:
        print(f"[ai_brain] get_user_profile: {e}")
        return {"user_id": user_id, "username": username, "personality_mode": "default", "preferences": {}}


def _upsert_user(user_id: str, username: str, mode: str, prefs: Dict) -> bool:
    _ensure_ai_schema()
    if not _db.is_available():
        return False
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_ai_users (user_id, username, personality_mode, preferences, last_active)
                    VALUES (%s, %s, %s, %s::jsonb, NOW())
                    ON CONFLICT (user_id) DO UPDATE
                        SET username = EXCLUDED.username,
                            personality_mode = EXCLUDED.personality_mode,
                            preferences = EXCLUDED.preferences,
                            last_active = NOW()
                """, (str(user_id), username, mode, json.dumps(prefs)))
            conn.commit()
        return True
    except Exception as e:
        print(f"[ai_brain] _upsert_user: {e}")
        return False


def set_user_personality(user_id: str, username: str, mode: str) -> bool:
    if mode not in VALID_MODES:
        return False
    profile = get_user_profile(user_id, username)
    return _upsert_user(user_id, username, mode, profile.get("preferences", {}))


def touch_user(user_id: str, username: str = "") -> None:
    _ensure_ai_schema()
    if not _db.is_available():
        return
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_ai_users (user_id, username, last_active)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (user_id) DO UPDATE
                        SET username = COALESCE(EXCLUDED.username, spark_ai_users.username),
                            last_active = NOW()
                """, (str(user_id), username or None))
            conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Server settings
# ---------------------------------------------------------------------------
def get_server_settings(guild_id: str) -> Dict[str, Any]:
    _ensure_ai_schema()
    default = {
        "guild_id": guild_id,
        "ai_enabled": True,
        "default_personality": "default",
        "language": "en",
        "moderation_level": "standard",
    }
    if not _db.is_available():
        return default
    try:
        with _db._conn() as conn:
            with conn.cursor(cursor_factory=__import__("psycopg2").extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM spark_ai_server_settings WHERE guild_id = %s", (str(guild_id),))
                row = cur.fetchone()
        if row:
            return {
                "guild_id": row["guild_id"],
                "ai_enabled": bool(row["ai_enabled"]),
                "default_personality": row["default_personality"] or "default",
                "language": row["language"] or "en",
                "moderation_level": row["moderation_level"] or "standard",
            }
        return default
    except Exception as e:
        print(f"[ai_brain] get_server_settings: {e}")
        return default


def update_server_settings(guild_id: str, **kwargs) -> bool:
    _ensure_ai_schema()
    if not _db.is_available():
        return False
    fields = {k: v for k, v in kwargs.items() if k in ("ai_enabled", "default_personality", "language", "moderation_level")}
    if not fields:
        return False
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_ai_server_settings (guild_id, ai_enabled, default_personality, language, moderation_level, updated_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (guild_id) DO UPDATE
                        SET ai_enabled          = COALESCE(EXCLUDED.ai_enabled,          spark_ai_server_settings.ai_enabled),
                            default_personality = COALESCE(EXCLUDED.default_personality, spark_ai_server_settings.default_personality),
                            language            = COALESCE(EXCLUDED.language,            spark_ai_server_settings.language),
                            moderation_level    = COALESCE(EXCLUDED.moderation_level,    spark_ai_server_settings.moderation_level),
                            updated_at          = NOW()
                """, (
                    str(guild_id),
                    fields.get("ai_enabled", None),
                    fields.get("default_personality", None),
                    fields.get("language", None),
                    fields.get("moderation_level", None),
                ))
            conn.commit()
        return True
    except Exception as e:
        print(f"[ai_brain] update_server_settings: {e}")
        return False


# ---------------------------------------------------------------------------
# Memory — Q&A storage
# ---------------------------------------------------------------------------
MEMORY_SCORE_THRESHOLD = 0.55  # how similar a stored question must be to count as a hit


def lookup_memory(question: str, guild_id: str = "global", top_n: int = 5) -> Optional[Dict[str, Any]]:
    """
    Check if we already know the answer to this question.
    Returns the best matching memory entry or None.
    """
    _ensure_ai_schema()
    if not _db.is_available():
        return None
    q_norm = _normalise(question)
    q_hash = _hash(q_norm)
    try:
        with _db._conn() as conn:
            with conn.cursor(cursor_factory=__import__("psycopg2").extras.RealDictCursor) as cur:
                # Try exact hash first (fastest)
                cur.execute("""
                    SELECT * FROM spark_ai_memory
                    WHERE question_hash = %s AND (guild_id = %s OR guild_id = 'global')
                    ORDER BY hit_count DESC LIMIT 1
                """, (q_hash, str(guild_id)))
                exact = cur.fetchone()
                if exact:
                    _bump_hit(exact["id"])
                    return dict(exact)

                # Fuzzy: pull recent memories and score them in Python
                cur.execute("""
                    SELECT * FROM spark_ai_memory
                    WHERE guild_id = %s OR guild_id = 'global'
                    ORDER BY updated_at DESC LIMIT 200
                """, (str(guild_id),))
                rows = cur.fetchall()

        best: Optional[Dict] = None
        best_score = 0.0
        for row in rows:
            score = _keyword_score(q_norm, row["question"])
            if score > best_score:
                best_score = score
                best = dict(row)

        if best and best_score >= MEMORY_SCORE_THRESHOLD:
            _bump_hit(best["id"])
            return best
        return None
    except Exception as e:
        print(f"[ai_brain] lookup_memory: {e}")
        return None


def save_to_memory(
    question: str,
    answer: str,
    guild_id: str = "global",
    source: str = "gemini",
    confidence: float = 1.0,
) -> bool:
    """Store a Q&A pair in long-term memory."""
    _ensure_ai_schema()
    if not _db.is_available():
        return False
    q_norm = _normalise(question)
    q_hash = _hash(q_norm)
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_ai_memory
                        (id, guild_id, question_hash, question, answer, source, confidence, hit_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 0)
                    ON CONFLICT (id) DO NOTHING
                """, (
                    _gen_id(), str(guild_id), q_hash,
                    question[:1000], answer[:2000],
                    source, confidence,
                ))
            conn.commit()
        return True
    except Exception as e:
        print(f"[ai_brain] save_to_memory: {e}")
        return False


def _bump_hit(memory_id: str) -> None:
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE spark_ai_memory
                    SET hit_count = hit_count + 1, updated_at = NOW()
                    WHERE id = %s
                """, (memory_id,))
            conn.commit()
    except Exception:
        pass


def teach_memory(question: str, answer: str, guild_id: str = "global") -> bool:
    """Manually teach the bot an answer (mod command). Overwrites existing."""
    _ensure_ai_schema()
    if not _db.is_available():
        return False
    q_norm = _normalise(question)
    q_hash = _hash(q_norm)
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_ai_memory
                        (id, guild_id, question_hash, question, answer, source, confidence, hit_count)
                    VALUES (%s, %s, %s, %s, %s, 'manual', 1.0, 0)
                    ON CONFLICT (question_hash) DO UPDATE
                        SET answer     = EXCLUDED.answer,
                            source     = 'manual',
                            confidence = 1.0,
                            updated_at = NOW()
                """, (_gen_id(), str(guild_id), q_hash, question[:1000], answer[:2000]))
            conn.commit()
        return True
    except Exception as e:
        print(f"[ai_brain] teach_memory: {e}")
        return False


def list_memories(guild_id: str = "global", limit: int = 10) -> List[Dict[str, Any]]:
    _ensure_ai_schema()
    if not _db.is_available():
        return []
    try:
        with _db._conn() as conn:
            with conn.cursor(cursor_factory=__import__("psycopg2").extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, question, answer, source, hit_count, created_at
                    FROM spark_ai_memory
                    WHERE guild_id = %s OR guild_id = 'global'
                    ORDER BY hit_count DESC, created_at DESC
                    LIMIT %s
                """, (str(guild_id), int(limit)))
                rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[ai_brain] list_memories: {e}")
        return []


def delete_memory(memory_id: str) -> bool:
    _ensure_ai_schema()
    if not _db.is_available():
        return False
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM spark_ai_memory WHERE id = %s", (memory_id,))
                deleted = cur.rowcount
            conn.commit()
        return deleted > 0
    except Exception as e:
        print(f"[ai_brain] delete_memory: {e}")
        return False


# ---------------------------------------------------------------------------
# Web search cache
# ---------------------------------------------------------------------------
SEARCH_CACHE_TTL = 86400  # 24 hours in seconds
SERPER_KEY = os.environ.get("SERPER_API_KEY", "").strip()


def get_cached_search(query: str) -> Optional[str]:
    _ensure_ai_schema()
    if not _db.is_available():
        return None
    q_hash = _hash(_normalise(query))
    try:
        with _db._conn() as conn:
            with conn.cursor(cursor_factory=__import__("psycopg2").extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT result, created_at FROM spark_ai_search_cache
                    WHERE query_hash = %s
                """, (q_hash,))
                row = cur.fetchone()
        if row:
            age = (datetime.now(timezone.utc) - row["created_at"].replace(tzinfo=timezone.utc)).total_seconds()
            if age < SEARCH_CACHE_TTL:
                return row["result"]
        return None
    except Exception as e:
        print(f"[ai_brain] get_cached_search: {e}")
        return None


def cache_search_result(query: str, result: str) -> bool:
    _ensure_ai_schema()
    if not _db.is_available():
        return False
    q_hash = _hash(_normalise(query))
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_ai_search_cache (query_hash, query, result, created_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (query_hash) DO UPDATE
                        SET result = EXCLUDED.result, created_at = NOW()
                """, (q_hash, query[:500], result[:3000]))
            conn.commit()
        return True
    except Exception as e:
        print(f"[ai_brain] cache_search_result: {e}")
        return False


async def web_search(query: str) -> Optional[str]:
    """
    Search the web using Serper API (https://serper.dev).
    Returns a brief result snippet or None if unavailable.
    Set SERPER_API_KEY in secrets to enable.
    """
    if not SERPER_KEY:
        return None

    cached = get_cached_search(query)
    if cached:
        return cached

    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as sess:
            resp = await sess.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": 3},
            )
            if resp.status != 200:
                return None
            data = await resp.json()

        snippets = []
        for item in data.get("organic", [])[:3]:
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            if snippet:
                snippets.append(f"• {title}: {snippet}")
        if not snippets:
            return None

        result = "\n".join(snippets)
        cache_search_result(query, result)
        return result
    except Exception as e:
        print(f"[ai_brain] web_search error: {e}")
        return None


# ---------------------------------------------------------------------------
# Learning log
# ---------------------------------------------------------------------------
def log_feedback(
    question: str,
    original_answer: str,
    feedback: str,
    corrected_answer: str = "",
    guild_id: str = "global",
) -> bool:
    _ensure_ai_schema()
    if not _db.is_available():
        return False
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spark_ai_learning_log
                        (id, guild_id, question, original_answer, feedback, corrected_answer)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    _gen_id(), str(guild_id),
                    question[:1000], original_answer[:2000],
                    feedback[:20], corrected_answer[:2000] or None,
                ))
            conn.commit()
        return True
    except Exception as e:
        print(f"[ai_brain] log_feedback: {e}")
        return False


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def get_ai_stats(guild_id: str = "global") -> Dict[str, Any]:
    _ensure_ai_schema()
    if not _db.is_available():
        return {}
    try:
        with _db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COUNT(*)                                          AS total_memories,
                        COALESCE(SUM(hit_count), 0)                      AS total_hits,
                        COUNT(*) FILTER (WHERE source = 'manual')        AS manual_count,
                        COUNT(*) FILTER (WHERE source = 'gemini')        AS gemini_count
                    FROM spark_ai_memory
                    WHERE guild_id = %s OR guild_id = 'global'
                """, (str(guild_id),))
                mem_row = cur.fetchone()

                cur.execute("SELECT COUNT(*) FROM spark_ai_users")
                user_count = cur.fetchone()[0]

                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE feedback = 'thumbsup')   AS thumbs_up,
                        COUNT(*) FILTER (WHERE feedback = 'thumbsdown') AS thumbs_down
                    FROM spark_ai_learning_log
                    WHERE guild_id = %s OR guild_id = 'global'
                """, (str(guild_id),))
                fb_row = cur.fetchone()

        return {
            "total_memories": mem_row[0] if mem_row else 0,
            "total_hits": mem_row[1] if mem_row else 0,
            "manual_memories": mem_row[2] if mem_row else 0,
            "gemini_memories": mem_row[3] if mem_row else 0,
            "total_users": user_count or 0,
            "thumbs_up": fb_row[0] if fb_row else 0,
            "thumbs_down": fb_row[1] if fb_row else 0,
        }
    except Exception as e:
        print(f"[ai_brain] get_ai_stats: {e}")
        return {}


# ---------------------------------------------------------------------------
# Initialise on import
# ---------------------------------------------------------------------------
_ensure_ai_schema()
