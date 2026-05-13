import discord
import json
import os

_LOCAL_FILE = "reaction_roles.json"

def _get_supabase():
    try:
        from dashboard import supabase
        return supabase
    except Exception:
        return None

# ── Postgres + Local JSON fallback ──────────────────────────────────────────

def _local_load():
    try:
        import db_spark as _ds
        if _ds.is_available():
            val = _ds.get_blob("reaction_roles")
            if val is not None:
                return val
    except Exception:
        pass
    if os.path.exists(_LOCAL_FILE):
        try:
            return json.load(open(_LOCAL_FILE))
        except Exception:
            pass
    return {"rules": []}

def _local_save(data):
    try:
        import db_spark as _ds
        if _ds.is_available():
            _ds.set_blob("reaction_roles", data)
    except Exception:
        pass
    tmp = _LOCAL_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, _LOCAL_FILE)
    except Exception as e:
        print(f"Reaction roles save error: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

# ── Public API ───────────────────────────────────────────────────────────────

def add_rule(guild_id, channel_id, message_id, emoji, role_id, role_name):
    """Add or update a reaction-role mapping. Returns True if new, False if updated."""
    sb = _get_supabase()
    guild_id   = str(guild_id)
    channel_id = str(channel_id)
    message_id = str(message_id)
    emoji      = str(emoji)
    role_id    = str(role_id)

    if sb:
        try:
            existing = (
                sb.table("sentinel_reaction_roles")
                .select("id")
                .eq("message_id", message_id)
                .eq("emoji", emoji)
                .execute()
            )
            if existing.data:
                sb.table("sentinel_reaction_roles").update({
                    "role_id": role_id, "role_name": role_name
                }).eq("message_id", message_id).eq("emoji", emoji).execute()
                return False
            sb.table("sentinel_reaction_roles").insert({
                "guild_id":   guild_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "emoji":      emoji,
                "role_id":    role_id,
                "role_name":  role_name,
            }).execute()
            return True
        except Exception as e:
            print(f"[RR] Supabase add_rule error: {e}")

    # fallback
    data = _local_load()
    for r in data["rules"]:
        if r["message_id"] == message_id and r["emoji"] == emoji:
            r.update({"role_id": role_id, "role_name": role_name})
            _local_save(data)
            return False
    data["rules"].append({
        "guild_id": guild_id, "channel_id": channel_id,
        "message_id": message_id, "emoji": emoji,
        "role_id": role_id, "role_name": role_name,
    })
    _local_save(data)
    return True


def remove_rule(guild_id, message_id, emoji):
    """Remove a reaction-role mapping. Returns True if something was deleted."""
    sb = _get_supabase()
    message_id = str(message_id)
    emoji      = str(emoji)
    guild_id   = str(guild_id)

    if sb:
        try:
            res = (
                sb.table("sentinel_reaction_roles")
                .delete()
                .eq("guild_id", guild_id)
                .eq("message_id", message_id)
                .eq("emoji", emoji)
                .execute()
            )
            return bool(res.data)
        except Exception as e:
            print(f"[RR] Supabase remove_rule error: {e}")

    # fallback
    data = _local_load()
    before = len(data["rules"])
    data["rules"] = [
        r for r in data["rules"]
        if not (r["message_id"] == message_id and r["emoji"] == emoji)
    ]
    _local_save(data)
    return len(data["rules"]) < before


def get_guild_rules(guild_id):
    """Return all reaction-role rules for a specific guild."""
    sb = _get_supabase()
    guild_id = str(guild_id)

    if sb:
        try:
            res = (
                sb.table("sentinel_reaction_roles")
                .select("*")
                .eq("guild_id", guild_id)
                .execute()
            )
            return res.data or []
        except Exception as e:
            print(f"[RR] Supabase get_guild_rules error: {e}")

    # fallback
    return [r for r in _local_load().get("rules", []) if str(r.get("guild_id")) == guild_id]


def get_all_rules():
    """Return every reaction-role rule across all guilds."""
    sb = _get_supabase()
    if sb:
        try:
            res = sb.table("sentinel_reaction_roles").select("*").execute()
            return res.data or []
        except Exception as e:
            print(f"[RR] Supabase get_all_rules error: {e}")
    return _local_load().get("rules", [])


# ── Discord event handlers ───────────────────────────────────────────────────

async def handle_reaction_add(bot, payload):
    if payload.user_id == bot.user.id:
        return
    emoji_str  = str(payload.emoji)
    message_id = str(payload.message_id)
    guild_id   = str(payload.guild_id)

    rules = get_all_rules()
    for rule in rules:
        if str(rule.get("message_id")) == message_id and rule.get("emoji") == emoji_str:
            guild = bot.get_guild(payload.guild_id)
            if not guild:
                return
            member = guild.get_member(payload.user_id)
            if not member:
                return
            role = guild.get_role(int(rule["role_id"]))
            if role:
                try:
                    await member.add_roles(role, reason="Reaction Role")
                except Exception as e:
                    print(f"[RR] add_roles error: {e}")
            return


async def handle_reaction_remove(bot, payload):
    if payload.user_id == bot.user.id:
        return
    emoji_str  = str(payload.emoji)
    message_id = str(payload.message_id)

    rules = get_all_rules()
    for rule in rules:
        if str(rule.get("message_id")) == message_id and rule.get("emoji") == emoji_str:
            guild = bot.get_guild(payload.guild_id)
            if not guild:
                return
            member = guild.get_member(payload.user_id)
            if not member:
                return
            role = guild.get_role(int(rule["role_id"]))
            if role:
                try:
                    await member.remove_roles(role, reason="Reaction Role removed")
                except Exception as e:
                    print(f"[RR] remove_roles error: {e}")
            return
