import discord

# Map template keys to the guild config key that holds their log channel.
# Falls back to "log_channel" for anything not listed here.
_TEMPLATE_CHANNEL_MAP = {
    "sp4rk.welcome":       "join_log_channel",
    "sp4rk.leave":         "join_log_channel",
    "sp4rk.member_banned": "join_log_channel",
    "sp4rk.message_edit":  "message_log_channel",
    "sp4rk.message_delete":"message_log_channel",
    "sp4rk.voice_join":    "voice_log_channel",
    "sp4rk.voice_leave":   "voice_log_channel",
    "sp4rk.voice_move":    "voice_log_channel",
}


async def _resolve_log_channel_id(config, guild_id=None, channel_key="log_channel"):
    """Return the effective log channel ID for a specific guild.

    Uses `channel_key` to pick the right channel config field.
    Falls back to "log_channel" if the specific channel isn't set.
    Never falls through to another guild's channel.
    """
    if not guild_id:
        return ""
    try:
        from dashboard import get_guild_config
        gcfg = get_guild_config(str(guild_id))
        # Try the specific channel key first, then fall back to main log_channel
        ch = gcfg.get(channel_key, "").strip()
        if not ch and channel_key != "log_channel":
            ch = gcfg.get("log_channel", "").strip()
        return ch
    except Exception:
        return ""


async def log_event(bot, config, title, description, color=0x3498db, guild_id=None):
    """Log a simple embed to the configured log channel for the given guild."""
    log_channel_id = await _resolve_log_channel_id(config, guild_id, "log_channel")
    if not log_channel_id:
        return
    try:
        channel = bot.get_channel(int(log_channel_id))
        if channel:
            embed = discord.Embed(title=title, description=description, color=color)
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Log event error: {e}")


async def log_template(bot, config, key, guild_id=None, **vars):
    """
    Modern log helper — renders an admin-customizable embed from `embeds.py`
    and posts it to the correct typed log channel.

    guild_id is auto-inferred from any Discord Guild / Member / TextChannel / VoiceChannel
    object passed in vars when not provided explicitly.
    """
    if not guild_id:
        for v in vars.values():
            if isinstance(v, discord.Guild):
                guild_id = str(v.id)
                break
            guild_obj = getattr(v, "guild", None)
            if isinstance(guild_obj, discord.Guild):
                guild_id = str(guild_obj.id)
                break

    channel_key = _TEMPLATE_CHANNEL_MAP.get(key, "log_channel")
    log_channel_id = await _resolve_log_channel_id(config, guild_id, channel_key)
    if not log_channel_id:
        return
    try:
        channel = bot.get_channel(int(log_channel_id))
        if not channel:
            return
        import embeds
        eid = guild_id or getattr(getattr(channel, "guild", None), "id", None)
        e = embeds.render(key, guild_id=eid, **vars)
        await channel.send(embed=e)
    except Exception as ex:
        print(f"Log template ({key}) error: {ex}")
