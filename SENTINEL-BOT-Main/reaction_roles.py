import discord
import json
import os

RR_FILE = "reaction_roles.json"

def load_reaction_roles():
    if os.path.exists(RR_FILE):
        with open(RR_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {"rules": []}
    return {"rules": []}

def save_reaction_roles(data):
    with open(RR_FILE, "w") as f:
        json.dump(data, f, indent=4)

def add_rule(guild_id, channel_id, message_id, emoji, role_id, role_name):
    data = load_reaction_roles()
    for r in data["rules"]:
        if r["message_id"] == message_id and r["emoji"] == emoji:
            r["role_id"] = role_id
            r["role_name"] = role_name
            save_reaction_roles(data)
            return False
    data["rules"].append({
        "guild_id": guild_id,
        "channel_id": channel_id,
        "message_id": message_id,
        "emoji": str(emoji),
        "role_id": role_id,
        "role_name": role_name
    })
    save_reaction_roles(data)
    return True

def remove_rule(message_id, emoji):
    data = load_reaction_roles()
    before = len(data["rules"])
    data["rules"] = [r for r in data["rules"] if not (r["message_id"] == message_id and r["emoji"] == str(emoji))]
    save_reaction_roles(data)
    return len(data["rules"]) < before

def get_all_rules():
    return load_reaction_roles().get("rules", [])

async def handle_reaction_add(bot, payload):
    if payload.user_id == bot.user.id:
        return
    data = load_reaction_roles()
    emoji_str = str(payload.emoji)
    for rule in data.get("rules", []):
        if rule["message_id"] == payload.message_id and rule["emoji"] == emoji_str:
            guild = bot.get_guild(payload.guild_id)
            if not guild:
                return
            member = guild.get_member(payload.user_id)
            if not member:
                return
            role = guild.get_role(rule["role_id"])
            if role:
                try:
                    await member.add_roles(role, reason="Reaction Role")
                except Exception as e:
                    print(f"Reaction role add error: {e}")

async def handle_reaction_remove(bot, payload):
    if payload.user_id == bot.user.id:
        return
    data = load_reaction_roles()
    emoji_str = str(payload.emoji)
    for rule in data.get("rules", []):
        if rule["message_id"] == payload.message_id and rule["emoji"] == emoji_str:
            guild = bot.get_guild(payload.guild_id)
            if not guild:
                return
            member = guild.get_member(payload.user_id)
            if not member:
                return
            role = guild.get_role(rule["role_id"])
            if role:
                try:
                    await member.remove_roles(role, reason="Reaction Role removed")
                except Exception as e:
                    print(f"Reaction role remove error: {e}")
