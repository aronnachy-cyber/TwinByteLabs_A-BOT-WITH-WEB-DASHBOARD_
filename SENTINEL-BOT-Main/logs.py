import discord


async def _resolve_log_channel_id(config, guild_id=None):
    """Return the effective log channel ID for a specific guild.

    If guild_id is provided we ONLY use that guild's own log_channel setting.
    We never fall back to another guild's config — that would cause cross-server
    log leaking when a guild hasn't configured a log channel yet.
    """
    if guild_id:
        try:
            from dashboard import get_guild_config
            gcfg = get_guild_config(str(guild_id))
            ch = gcfg.get("log_channel", "").strip()
            return ch  # empty string if not configured — caller will skip logging
        except Exception:
            return ""  # on any error, do not fall through to another guild's channel
    # No guild context at all (e.g. a DM-based event) — use the global config only
    return config.get("log_channel", "").strip()


async def log_event(bot, config, title, description, color=0x3498db, guild_id=None):
    """Log a simple embed to the configured log channel for the given guild."""
    log_channel_id = await _resolve_log_channel_id(config, guild_id)
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
    and posts it to the configured log channel.

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

    log_channel_id = await _resolve_log_channel_id(config, guild_id)
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
