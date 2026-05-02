import json
import os
import math
import time

_XP_FILE      = "leveling_xp.json"
_CFG_FILE     = "leveling_config.json"
_COOLDOWNS    = {}

DEFAULT_CFG = {
    "xp_min": 15,
    "xp_max": 25,
    "cooldown": 60,
    "level_up_channel": "",
    "level_up_message": "🎉 {user} levelled up to **Level {level}**!",
    "role_rewards": {}
}

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

def xp_to_level(xp: int) -> int:
    return int(math.floor(math.sqrt(max(xp, 0) / 100)))

def level_to_xp(level: int) -> int:
    return level * level * 100

def get_config(guild_id: str) -> dict:
    data = _load_file(_CFG_FILE, {})
    cfg = data.get(str(guild_id), {})
    merged = DEFAULT_CFG.copy()
    merged.update(cfg)
    return merged

def set_config(guild_id: str, **kwargs):
    data = _load_file(_CFG_FILE, {})
    guild_id = str(guild_id)
    if guild_id not in data:
        data[guild_id] = {}
    for k, v in kwargs.items():
        if k in DEFAULT_CFG or k == "role_rewards":
            data[guild_id][k] = v
    _save_file(_CFG_FILE, data)

def get_user(guild_id: str, user_id: str) -> dict:
    data = _load_file(_XP_FILE, {})
    key = f"{guild_id}:{user_id}"
    return data.get(key, {"xp": 0, "level": 0, "messages": 0})

def _save_user(guild_id: str, user_id: str, entry: dict):
    data = _load_file(_XP_FILE, {})
    key = f"{guild_id}:{user_id}"
    data[key] = entry
    _save_file(_XP_FILE, data)

def try_add_xp(guild_id: str, user_id: str) -> dict | None:
    """
    Awards XP if the cooldown has passed.
    Returns dict with {level_up, new_level, old_level} if XP was added, None if on cooldown.
    """
    import random
    guild_id  = str(guild_id)
    user_id   = str(user_id)
    cfg       = get_config(guild_id)
    cooldown  = int(cfg.get("cooldown", 60))
    now       = time.time()
    cd_key    = f"{guild_id}:{user_id}"

    if now - _COOLDOWNS.get(cd_key, 0) < cooldown:
        return None

    _COOLDOWNS[cd_key] = now
    entry     = get_user(guild_id, user_id)
    old_level = entry["level"]
    gain      = random.randint(int(cfg.get("xp_min", 15)), int(cfg.get("xp_max", 25)))
    entry["xp"]       = entry.get("xp", 0) + gain
    entry["messages"] = entry.get("messages", 0) + 1
    new_level         = xp_to_level(entry["xp"])
    entry["level"]    = new_level
    _save_user(guild_id, user_id, entry)
    return {"level_up": new_level > old_level, "new_level": new_level, "old_level": old_level, "xp": entry["xp"]}

def get_leaderboard(guild_id: str, limit: int = 20) -> list:
    data     = _load_file(_XP_FILE, {})
    guild_id = str(guild_id)
    prefix   = f"{guild_id}:"
    entries  = []
    for key, val in data.items():
        if key.startswith(prefix):
            uid = key[len(prefix):]
            entries.append({"user_id": uid, "xp": val.get("xp", 0), "level": val.get("level", 0), "messages": val.get("messages", 0)})
    entries.sort(key=lambda x: x["xp"], reverse=True)
    for i, e in enumerate(entries):
        e["rank"] = i + 1
    return entries[:limit]

def get_rank(guild_id: str, user_id: str) -> int:
    lb = get_leaderboard(guild_id, limit=9999)
    uid = str(user_id)
    for e in lb:
        if e["user_id"] == uid:
            return e["rank"]
    return -1
