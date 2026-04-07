import discord
import json
import os
import random
from datetime import datetime

XP_FILE = "xp.json"
XP_COOLDOWN = {}

def load_xp():
    if os.path.exists(XP_FILE):
        with open(XP_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_xp(data):
    with open(XP_FILE, "w") as f:
        json.dump(data, f, indent=4)

def xp_for_level(level):
    return 100 * (level + 1)

def get_level_from_xp(xp):
    level = 0
    while xp >= xp_for_level(level):
        xp -= xp_for_level(level)
        level += 1
    return level

def get_member_xp(guild_id, user_id):
    data = load_xp()
    gid, uid = str(guild_id), str(user_id)
    return data.get(gid, {}).get(uid, {"xp": 0, "level": 0, "messages": 0, "username": "Unknown"})

def get_leaderboard(guild_id, limit=10):
    data = load_xp()
    gid = str(guild_id)
    guild_data = data.get(gid, {})
    sorted_members = sorted(guild_data.items(), key=lambda x: x[1].get("xp", 0), reverse=True)
    return sorted_members[:limit]

async def process_message_xp(bot, message, config):
    if config.get("leveling", "OFF") != "ON":
        return False
    guild_id = str(message.guild.id)
    user_id = str(message.author.id)
    key = f"{guild_id}:{user_id}"
    import time
    now = time.time()
    if now - XP_COOLDOWN.get(key, 0) < 60:
        return False
    XP_COOLDOWN[key] = now

    xp_gain = random.randint(15, 25)
    data = load_xp()
    if guild_id not in data:
        data[guild_id] = {}
    if user_id not in data[guild_id]:
        data[guild_id][user_id] = {"xp": 0, "level": 0, "messages": 0, "username": str(message.author)}

    entry = data[guild_id][user_id]
    entry["username"] = str(message.author)
    entry["messages"] = entry.get("messages", 0) + 1
    old_level = entry.get("level", 0)
    entry["xp"] = entry.get("xp", 0) + xp_gain
    new_level = get_level_from_xp(entry["xp"])
    entry["level"] = new_level
    save_xp(data)

    if new_level > old_level:
        log_channel_id = config.get("log_channel", "").strip()
        if log_channel_id:
            try:
                channel = bot.get_channel(int(log_channel_id))
                if channel:
                    embed = discord.Embed(
                        title="⭐ Level Up!",
                        description=f"{message.author.mention} reached **Level {new_level}**!",
                        color=0xf1c40f
                    )
                    await channel.send(embed=embed)
            except Exception:
                pass
        return True
    return False
