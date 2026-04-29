import discord

async def log_event(bot, config, title, description, color=0x3498db):
    """Legacy log helper — kept for callers that haven't been migrated to embed templates yet."""
    log_channel_id = config.get("log_channel", "").strip()
    if not log_channel_id:
        return
    try:
        channel = bot.get_channel(int(log_channel_id))
        if channel:
            embed = discord.Embed(title=title, description=description, color=color)
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Log event error: {e}")


async def log_template(bot, config, key, **vars):
    """
    Modern log helper — renders an admin-customizable embed from `embeds.py`
    and posts it to the configured log channel. Falls back silently if the
    channel isn't set or the embed key is unknown.
    """
    log_channel_id = (config.get("log_channel") or "").strip()
    if not log_channel_id:
        return
    try:
        channel = bot.get_channel(int(log_channel_id))
        if not channel:
            return
        import embeds
        guild_id = getattr(getattr(channel, "guild", None), "id", None)
        e = embeds.render(key, guild_id=guild_id, **vars)
        await channel.send(embed=e)
    except Exception as ex:
        print(f"Log template ({key}) error: {ex}")
