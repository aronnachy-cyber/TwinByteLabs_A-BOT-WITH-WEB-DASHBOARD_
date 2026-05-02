import json
import os

_CFG_FILE   = "starboard_config.json"
_TRACK_FILE = "starboard_tracked.json"

DEFAULT_CFG = {
    "channel": "",
    "threshold": 3,
    "emoji": "⭐",
    "enabled": True,
    "self_star": False
}

def _load(path, default=None):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    return (default or {}).copy()

def _save(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[SB] save error: {e}")
        try:
            os.remove(tmp)
        except Exception:
            pass

def get_config(guild_id: str) -> dict:
    data = _load(_CFG_FILE)
    cfg  = data.get(str(guild_id), {})
    merged = DEFAULT_CFG.copy()
    merged.update(cfg)
    return merged

def set_config(guild_id: str, **kwargs):
    data = _load(_CFG_FILE)
    gid  = str(guild_id)
    if gid not in data:
        data[gid] = {}
    for k, v in kwargs.items():
        data[gid][k] = v
    _save(_CFG_FILE, data)

def get_star_msg_id(guild_id: str, msg_id: str) -> str | None:
    data = _load(_TRACK_FILE)
    return data.get(f"{guild_id}:{msg_id}")

def set_star_msg_id(guild_id: str, msg_id: str, star_msg_id: str):
    data = _load(_TRACK_FILE)
    data[f"{guild_id}:{msg_id}"] = str(star_msg_id)
    _save(_TRACK_FILE, data)

def remove_star_msg(guild_id: str, msg_id: str):
    data = _load(_TRACK_FILE)
    data.pop(f"{guild_id}:{msg_id}", None)
    _save(_TRACK_FILE, data)

async def handle_reaction(bot, payload, added: bool):
    """Handle a raw reaction add/remove event for starboard."""
    try:
        if payload.guild_id is None:
            return
        guild_id  = str(payload.guild_id)
        cfg       = get_config(guild_id)
        if not cfg.get("enabled", True):
            return
        sb_emoji  = cfg.get("emoji", "⭐")
        reacted   = str(payload.emoji)
        if reacted != sb_emoji:
            return
        sb_channel_id = cfg.get("channel", "").strip()
        if not sb_channel_id:
            return
        threshold = int(cfg.get("threshold", 3))

        channel   = bot.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        if message.author.bot:
            return
        if not cfg.get("self_star", False) and payload.user_id == message.author.id:
            return

        star_count = 0
        for r in message.reactions:
            if str(r.emoji) == sb_emoji:
                users = [u async for u in r.users()]
                if not cfg.get("self_star", False):
                    users = [u for u in users if u.id != message.author.id]
                star_count = len(users)
                break

        sb_channel = bot.get_channel(int(sb_channel_id))
        if sb_channel is None:
            try:
                sb_channel = await bot.fetch_channel(int(sb_channel_id))
            except Exception:
                return

        existing_id = get_star_msg_id(guild_id, str(payload.message_id))

        if star_count >= threshold:
            content   = message.content or ""
            img_url   = None
            if message.attachments:
                img_url = message.attachments[0].url
            elif message.embeds and message.embeds[0].image:
                img_url = message.embeds[0].image.url

            import discord
            embed = discord.Embed(
                description=content[:2048] if content else None,
                color=0xFFD700,
                timestamp=message.created_at
            )
            embed.set_author(name=str(message.author.display_name), icon_url=str(message.author.display_avatar.url))
            embed.add_field(name="Source", value=f"[Jump to message]({message.jump_url})", inline=False)
            if img_url:
                embed.set_image(url=img_url)
            embed.set_footer(text=f"#{channel.name}")

            star_text = f"{sb_emoji} **{star_count}** | <#{channel.id}>"

            if existing_id:
                try:
                    sb_msg = await sb_channel.fetch_message(int(existing_id))
                    await sb_msg.edit(content=star_text, embed=embed)
                except Exception:
                    sb_msg = await sb_channel.send(content=star_text, embed=embed)
                    set_star_msg_id(guild_id, str(payload.message_id), str(sb_msg.id))
            else:
                sb_msg = await sb_channel.send(content=star_text, embed=embed)
                set_star_msg_id(guild_id, str(payload.message_id), str(sb_msg.id))
        else:
            if existing_id and star_count == 0:
                try:
                    sb_msg = await sb_channel.fetch_message(int(existing_id))
                    await sb_msg.delete()
                except Exception:
                    pass
                remove_star_msg(guild_id, str(payload.message_id))
    except Exception as e:
        print(f"[SB] handle_reaction error: {e}")
