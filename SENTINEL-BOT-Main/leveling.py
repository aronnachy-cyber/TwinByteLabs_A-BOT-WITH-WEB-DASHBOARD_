import json
import os
import math
import time

_XP_FILE  = "leveling_xp.json"
_CFG_FILE = "leveling_config.json"
_COOLDOWNS: dict = {}

DEFAULT_CFG = {
    "xp_min": 15,
    "xp_max": 25,
    "cooldown": 60,
    "level_up_channel": "",
    "level_up_message": "🎉 {user} levelled up to **Level {level}**!",
    "role_rewards": {},
    "leveling_enabled": True,
}

# ── helpers ──────────────────────────────────────────────────────────────────
def _load_file(path, default):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    return default.copy() if isinstance(default, dict) else {}

def _save_file(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[LVL] save error ({path}): {e}")
        try:
            os.remove(tmp)
        except Exception:
            pass

def _db():
    try:
        import db_spark as _ds
        return _ds if _ds.is_available() else None
    except Exception:
        return None

# ── math ─────────────────────────────────────────────────────────────────────
def xp_to_level(xp: int) -> int:
    return int(math.floor(math.sqrt(max(xp, 0) / 100)))

def level_to_xp(level: int) -> int:
    return level * level * 100

# ── config ───────────────────────────────────────────────────────────────────
def get_config(guild_id: str) -> dict:
    guild_id = str(guild_id)
    ds = _db()
    if ds:
        try:
            cfg = ds.get_leveling_config(guild_id)
            if cfg:
                merged = DEFAULT_CFG.copy()
                merged.update(cfg)
                return merged
        except Exception as e:
            print(f"[LVL] get_config DB error: {e}")
    data = _load_file(_CFG_FILE, {})
    cfg = data.get(guild_id, {})
    merged = DEFAULT_CFG.copy()
    merged.update(cfg)
    return merged

def set_config(guild_id: str, **kwargs):
    guild_id = str(guild_id)
    valid = {k: v for k, v in kwargs.items() if k in DEFAULT_CFG or k == "role_rewards"}
    if not valid:
        return
    ds = _db()
    if ds:
        try:
            ds.upsert_leveling_config(guild_id, valid)
        except Exception as e:
            print(f"[LVL] set_config DB error: {e}")
    data = _load_file(_CFG_FILE, {})
    if guild_id not in data:
        data[guild_id] = {}
    data[guild_id].update(valid)
    _save_file(_CFG_FILE, data)

# ── user XP ──────────────────────────────────────────────────────────────────
def get_user(guild_id: str, user_id: str) -> dict:
    guild_id = str(guild_id)
    user_id  = str(user_id)
    ds = _db()
    if ds:
        try:
            row = ds.get_xp(guild_id, user_id)
            if row:
                return row
        except Exception as e:
            print(f"[LVL] get_user DB error: {e}")
    data = _load_file(_XP_FILE, {})
    return data.get(f"{guild_id}:{user_id}", {"xp": 0, "level": 0, "messages": 0})

def _save_user(guild_id: str, user_id: str, entry: dict, username: str = ""):
    guild_id = str(guild_id)
    user_id  = str(user_id)
    ds = _db()
    if ds:
        try:
            ds.upsert_xp(guild_id, user_id,
                         int(entry.get("xp", 0)),
                         int(entry.get("level", 0)),
                         int(entry.get("messages", 0)),
                         username)
        except Exception as e:
            print(f"[LVL] _save_user DB error: {e}")
    data = _load_file(_XP_FILE, {})
    data[f"{guild_id}:{user_id}"] = entry
    _save_file(_XP_FILE, data)

# ── XP awarding ──────────────────────────────────────────────────────────────
def is_leveling_enabled(guild_id: str) -> bool:
    """Return True if leveling/XP is enabled for this guild (default: True).

    Checks the leveling-specific config first, then falls back to the main
    guild config (where the dashboard toggle saves to) so both the
    ``!leveling on/off`` command and the dashboard toggle stay in sync.
    """
    guild_id = str(guild_id)

    # 1) Check leveling-specific config store (set by !leveling command)
    cfg = get_config(guild_id)
    # Only trust the leveling store if it actually has the key set
    if "leveling_enabled" in cfg and cfg["leveling_enabled"] != DEFAULT_CFG["leveling_enabled"]:
        val = cfg["leveling_enabled"]
        if isinstance(val, bool):
            return val
        return str(val).lower() not in ("false", "0", "off", "no")

    # 2) Fall back to main guild config (set by dashboard toggle)
    try:
        import db_spark as _ds
        if _ds.is_available():
            main_cfg = _ds.get_one_guild_config(guild_id)
            if main_cfg and "leveling_enabled" in main_cfg:
                val = main_cfg["leveling_enabled"]
                if isinstance(val, bool):
                    return val
                return str(val).lower() not in ("false", "0", "off", "no")
    except Exception:
        pass

    # 3) Default: enabled
    return True


def set_leveling_enabled(guild_id: str, enabled: bool) -> None:
    """Enable or disable leveling/XP for a guild and persist the setting."""
    set_config(str(guild_id), leveling_enabled=enabled)


def try_add_xp(guild_id: str, user_id: str, username: str = "") -> dict | None:
    """
    Award XP if the cooldown has passed and leveling is enabled for the guild.
    Returns {level_up, new_level, old_level, xp} or None if on cooldown/disabled.
    """
    import random
    guild_id = str(guild_id)
    user_id  = str(user_id)

    # Bail out immediately if leveling is disabled for this server
    if not is_leveling_enabled(guild_id):
        return None

    cfg      = get_config(guild_id)
    cooldown = int(cfg.get("cooldown", 60))
    now      = time.time()
    cd_key   = f"{guild_id}:{user_id}"

    if now - _COOLDOWNS.get(cd_key, 0) < cooldown:
        return None

    _COOLDOWNS[cd_key] = now
    entry     = get_user(guild_id, user_id)
    old_level = entry.get("level", 0)
    gain      = random.randint(int(cfg.get("xp_min", 15)), int(cfg.get("xp_max", 25)))
    entry["xp"]       = entry.get("xp", 0) + gain
    entry["messages"] = entry.get("messages", 0) + 1
    new_level         = xp_to_level(entry["xp"])
    entry["level"]    = new_level
    _save_user(guild_id, user_id, entry, username)
    return {"level_up": new_level > old_level, "new_level": new_level,
            "old_level": old_level, "xp": entry["xp"]}

# ── leaderboard ──────────────────────────────────────────────────────────────
def get_leaderboard(guild_id: str, limit: int = 20) -> list:
    guild_id = str(guild_id)
    ds = _db()
    if ds:
        try:
            rows = ds.get_xp_leaderboard(guild_id, limit=limit)
            if rows:
                return rows
        except Exception as e:
            print(f"[LVL] get_leaderboard DB error: {e}")
    data    = _load_file(_XP_FILE, {})
    prefix  = f"{guild_id}:"
    entries = []
    for key, val in data.items():
        if key.startswith(prefix):
            uid = key[len(prefix):]
            entries.append({
                "user_id":  uid,
                "xp":       val.get("xp", 0),
                "level":    val.get("level", 0),
                "messages": val.get("messages", 0),
            })
    entries.sort(key=lambda x: x["xp"], reverse=True)
    for i, e in enumerate(entries):
        e["rank"] = i + 1
    return entries[:limit]

def get_rank(guild_id: str, user_id: str) -> int:
    lb  = get_leaderboard(guild_id, limit=9999)
    uid = str(user_id)
    for e in lb:
        if e["user_id"] == uid:
            return e["rank"]
    return -1

# ── startup migration ────────────────────────────────────────────────────────
def migrate_xp_from_json() -> int:
    """
    On startup: read leveling_xp.json and upsert every entry into Postgres.
    This prevents data loss when the JSON file is wiped on a fresh deploy.
    Safe to call repeatedly — uses ON CONFLICT DO UPDATE so it never
    overwrites a HIGHER xp value that already exists in the DB.
    Returns the number of rows migrated (0 if nothing to do or no DB).
    """
    ds = _db()
    if not ds:
        print("[LVL] ⚠️  Postgres not available — XP will only be stored in leveling_xp.json "
              "(data will be lost on redeploy). Set DATABASE_URL to fix this.")
        return 0

    if not os.path.exists(_XP_FILE):
        return 0

    data = _load_file(_XP_FILE, {})
    if not data:
        return 0

    migrated = 0
    errors   = 0
    for key, entry in data.items():
        try:
            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            guild_id, user_id = parts
            xp       = int(entry.get("xp", 0))
            level    = int(entry.get("level", 0))
            messages = int(entry.get("messages", 0))
            username = entry.get("username", "")
            # Only migrate if xp > 0 so we don't flood the DB with empty rows
            if xp <= 0:
                continue
            ds.upsert_xp(guild_id, user_id, xp, level, messages, username, safe_migrate=True)
            migrated += 1
        except Exception as e:
            errors += 1
            print(f"[LVL] migrate row {key} error: {e}")

    if migrated:
        print(f"[LVL] ✅ Migrated {migrated} XP row(s) from leveling_xp.json → Postgres"
              + (f" ({errors} errors)" if errors else ""))
    return migrated
