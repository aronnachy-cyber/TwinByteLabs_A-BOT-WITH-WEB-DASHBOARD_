import discord
import json
import os
import random
import time

_LOCAL_FILE = "xp.json"
XP_COOLDOWN = {}  # in-memory cooldown: key = "guild_id:user_id" -> timestamp

def _get_supabase():
    try:
        from dashboard import supabase
        return supabase
    except Exception:
        return None

# ── XP math ─────────────────────────────────────────────────────────────────

def xp_for_level(level):
    """XP required to complete level `level` and advance to the next."""
    return 100 * (level + 1)

def get_xp_progress(total_xp):
    """
    Returns (level, xp_in_current_level, xp_needed_for_next_level).
    xp_in / xp_needed shows progress toward the next level.
    """
    level = 0
    remaining = total_xp
    while remaining >= xp_for_level(level):
        remaining -= xp_for_level(level)
        level += 1
    return level, remaining, xp_for_level(level)

def get_level_from_xp(total_xp):
    level, _, _ = get_xp_progress(total_xp)
    return level

def make_progress_bar(current, total, length=12):
    filled = int(length * current / max(total, 1))
    bar = "█" * filled + "░" * (length - filled)
    pct = int(100 * current / max(total, 1))
    return f"`{bar}` {pct}%"

# ── Local JSON fallback ──────────────────────────────────────────────────────

def _local_load():
    if os.path.exists(_LOCAL_FILE):
        try:
            return json.load(open(_LOCAL_FILE))
        except Exception:
            pass
    return {}

def _local_save(data):
    with open(_LOCAL_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ── Supabase helpers ─────────────────────────────────────────────────────────

def _sb_get(guild_id, user_id):
    sb = _get_supabase()
    if not sb:
        return None
    try:
        res = (
            sb.table("sentinel_xp")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("user_id", str(user_id))
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[XP] sb_get error: {e}")
        return None

def _sb_upsert(guild_id, user_id, username, xp, level, messages):
    sb = _get_supabase()
    if not sb:
        return
    try:
        sb.table("sentinel_xp").upsert({
            "guild_id": str(guild_id),
            "user_id":  str(user_id),
            "username": username,
            "xp":       xp,
            "level":    level,
            "messages": messages,
        }, on_conflict="guild_id,user_id").execute()
    except Exception as e:
        print(f"[XP] sb_upsert error: {e}")

# ── Public API ───────────────────────────────────────────────────────────────

def get_member_xp(guild_id, user_id):
    """Return xp record dict with keys: xp, level, messages, username."""
    sb = _get_supabase()
    if sb:
        row = _sb_get(guild_id, user_id)
        if row:
            return row
    # fallback
    data = _local_load()
    return data.get(str(guild_id), {}).get(str(user_id),
           {"xp": 0, "level": 0, "messages": 0, "username": "Unknown"})


def get_leaderboard(guild_id, limit=10):
    """Return list of (user_id, record_dict) tuples sorted by XP descending."""
    sb = _get_supabase()
    if sb:
        try:
            res = (
                sb.table("sentinel_xp")
                .select("*")
                .eq("guild_id", str(guild_id))
                .order("xp", desc=True)
                .limit(limit)
                .execute()
            )
            return [(r["user_id"], r) for r in (res.data or [])]
        except Exception as e:
            print(f"[XP] leaderboard error: {e}")
    # fallback
    data = _local_load()
    guild_data = data.get(str(guild_id), {})
    sorted_members = sorted(guild_data.items(), key=lambda x: x[1].get("xp", 0), reverse=True)
    return sorted_members[:limit]


async def process_message_xp(bot, message, config):
    """Award XP for a message. Returns True on level-up."""
    if config.get("leveling", "OFF") != "ON":
        return False

    guild_id = str(message.guild.id)
    user_id  = str(message.author.id)
    key      = f"{guild_id}:{user_id}"
    now      = time.time()

    if now - XP_COOLDOWN.get(key, 0) < 60:
        return False
    XP_COOLDOWN[key] = now

    xp_gain = random.randint(15, 25)

    # Load existing record
    sb = _get_supabase()
    if sb:
        row = _sb_get(guild_id, user_id)
        if row:
            new_xp       = row["xp"] + xp_gain
            new_messages = row.get("messages", 0) + 1
            old_level    = row.get("level", 0)
        else:
            new_xp       = xp_gain
            new_messages = 1
            old_level    = 0
        new_level = get_level_from_xp(new_xp)
        _sb_upsert(guild_id, user_id, str(message.author), new_xp, new_level, new_messages)
    else:
        data = _local_load()
        if guild_id not in data:
            data[guild_id] = {}
        entry = data[guild_id].get(user_id, {"xp": 0, "level": 0, "messages": 0, "username": str(message.author)})
        entry["username"] = str(message.author)
        entry["messages"] = entry.get("messages", 0) + 1
        old_level         = entry.get("level", 0)
        new_xp            = entry.get("xp", 0) + xp_gain
        entry["xp"]       = new_xp
        new_level         = get_level_from_xp(new_xp)
        entry["level"]    = new_level
        data[guild_id][user_id] = entry
        _local_save(data)

    if new_level > old_level:
        await _announce_level_up(bot, message, new_level, config)
        return True
    return False


async def _announce_level_up(bot, message, new_level, config):
    embed = discord.Embed(
        title="⭐ Level Up!",
        description=f"{message.author.mention} just reached **Level {new_level}**! Keep it up 🔥",
        color=0xf1c40f
    )
    embed.set_thumbnail(url=message.author.display_avatar.url)

    # Post in the channel where the message was sent
    try:
        await message.channel.send(embed=embed)
    except Exception:
        pass

    # Also post in log channel if configured and different
    log_channel_id = config.get("log_channel", "").strip()
    if log_channel_id:
        try:
            ch = bot.get_channel(int(log_channel_id))
            if ch and ch.id != message.channel.id:
                await ch.send(embed=embed)
        except Exception:
            pass
