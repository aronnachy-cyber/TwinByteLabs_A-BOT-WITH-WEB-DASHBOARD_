import json
import os

_LOCAL_FILE = "mention_reactions.json"

def _get_supabase():
    try:
        from dashboard import supabase
        return supabase
    except Exception:
        return None

def _local_load():
    try:
        import db_spark as _ds
        if _ds.is_available():
            val = _ds.get_blob("mention_reactions")
            if val is not None:
                return val
    except Exception:
        pass
    if os.path.exists(_LOCAL_FILE):
        try:
            return json.load(open(_LOCAL_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {"rules": []}

def _local_save(data):
    try:
        import db_spark as _ds
        if _ds.is_available():
            _ds.set_blob("mention_reactions", data)
    except Exception:
        pass
    tmp = _LOCAL_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, _LOCAL_FILE)
    except Exception as e:
        print(f"[MR] save error: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

_TABLE = "sentinel_mention_reactions"

def get_rules(guild_id=None):
    """Return all mention-reaction rules, optionally filtered by guild."""
    sb = _get_supabase()
    if sb:
        try:
            q = sb.table(_TABLE).select("*")
            if guild_id:
                q = q.eq("guild_id", str(guild_id))
            res = q.execute()
            return res.data or []
        except Exception as e:
            print(f"[MR] Supabase get_rules error: {e}")
    data = _local_load()
    rules = data.get("rules", [])
    if guild_id:
        rules = [r for r in rules if str(r.get("guild_id")) == str(guild_id)]
    return rules

def add_rule(guild_id, user_id, emoji, label=""):
    """Add or update a mention-reaction rule. Returns True if new, False if updated."""
    sb = _get_supabase()
    guild_id = str(guild_id)
    user_id  = str(user_id)
    emoji    = str(emoji).strip()
    label    = str(label).strip()

    if sb:
        try:
            existing = (
                sb.table(_TABLE).select("id")
                .eq("guild_id", guild_id)
                .eq("user_id", user_id)
                .execute()
            )
            if existing.data:
                sb.table(_TABLE).update({"emoji": emoji, "label": label}).eq("guild_id", guild_id).eq("user_id", user_id).execute()
                return False
            sb.table(_TABLE).insert({"guild_id": guild_id, "user_id": user_id, "emoji": emoji, "label": label}).execute()
            return True
        except Exception as e:
            print(f"[MR] Supabase add_rule error: {e}")

    data = _local_load()
    for r in data["rules"]:
        if r["guild_id"] == guild_id and r["user_id"] == user_id:
            r.update({"emoji": emoji, "label": label})
            _local_save(data)
            return False
    data["rules"].append({"guild_id": guild_id, "user_id": user_id, "emoji": emoji, "label": label})
    _local_save(data)
    return True

def remove_rule(guild_id, user_id):
    """Remove a mention-reaction rule. Returns True if something was deleted."""
    sb = _get_supabase()
    guild_id = str(guild_id)
    user_id  = str(user_id)

    if sb:
        try:
            res = (
                sb.table(_TABLE).delete()
                .eq("guild_id", guild_id)
                .eq("user_id", user_id)
                .execute()
            )
            return bool(res.data)
        except Exception as e:
            print(f"[MR] Supabase remove_rule error: {e}")

    data = _local_load()
    before = len(data["rules"])
    data["rules"] = [r for r in data["rules"] if not (r["guild_id"] == guild_id and r["user_id"] == user_id)]
    _local_save(data)
    return len(data["rules"]) < before
