import json
import os
from datetime import datetime, timezone

_FILE = "afk_data.json"

def _load():
    try:
        import db_spark as _ds
        if _ds.is_available():
            val = _ds.get_blob("afk")
            if val is not None:
                return val
    except Exception:
        pass
    if os.path.exists(_FILE):
        try:
            return json.load(open(_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save(data):
    try:
        import db_spark as _ds
        if _ds.is_available():
            _ds.set_blob("afk", data)
    except Exception:
        pass
    tmp = _FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _FILE)
    except Exception as e:
        print(f"[AFK] save error: {e}")
        try:
            os.remove(tmp)
        except Exception:
            pass

def set_afk(guild_id: str, user_id: str, reason: str = "AFK"):
    data = _load()
    key  = f"{guild_id}:{user_id}"
    data[key] = {
        "reason": reason[:200].strip() or "AFK",
        "since": datetime.now(timezone.utc).isoformat()
    }
    _save(data)

def remove_afk(guild_id: str, user_id: str) -> bool:
    data = _load()
    key  = f"{guild_id}:{user_id}"
    if key in data:
        del data[key]
        _save(data)
        return True
    return False

def get_afk(guild_id: str, user_id: str) -> dict | None:
    data = _load()
    return data.get(f"{guild_id}:{user_id}")

def get_all_afk(guild_id: str) -> list:
    data     = _load()
    guild_id = str(guild_id)
    prefix   = f"{guild_id}:"
    result   = []
    for key, val in data.items():
        if key.startswith(prefix):
            uid = key[len(prefix):]
            result.append({"user_id": uid, **val})
    return result

def time_since(iso_str: str) -> str:
    try:
        then  = datetime.fromisoformat(iso_str)
        delta = datetime.now(timezone.utc) - then
        s     = int(delta.total_seconds())
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m"
        if s < 86400:
            return f"{s // 3600}h {(s % 3600) // 60}m"
        return f"{s // 86400}d"
    except Exception:
        return "?"
