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

# Throttle: track which guilds have already been DMed about missing channels
# so the owner is only notified once per bot session, not on every event.
_admin_notified_guilds: set = set()


async def _notify_admin_unconfigured(bot, guild_id: str, missing_keys: list):
    """DM the guild owner once per session when key log channels are not set."""
    key = str(guild_id)
    if key in _admin_notified_guilds:
        return
    _admin_notified_guilds.add(key)
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return
        owner = guild.owner
        if not owner:
            try:
                owner = await guild.fetch_member(guild.owner_id)
            except Exception:
                return
        if not owner:
            return
        missing_fmt = "\n".join(f"• **{k.replace('_', ' ').title()}**" for k in missing_keys)
        embed = discord.Embed(
            title="⚠️ SP4RK — Log Channels Not Configured",
            description=(
                f"Some log channels are not set up in **{guild.name}**.\n\n"
                f"{missing_fmt}\n\n"
                "Please configure them in the SP4RK dashboard so the bot can "
                "send logs to the correct channels."
            ),
            color=0xf59e0b,
        )
        embed.set_footer(text="SP4RK Bot — this message is only sent once per session.")
        await owner.send(embed=embed)
    except Exception as e:
        print(f"[logs] admin DM error for guild {guild_id}: {e}")


async def _resolve_log_channel_id(config, guild_id=None, channel_key="log_channel", bot=None):
    """Return the effective log channel ID for a specific guild.

    Resolution order:
      1. Guild-specific config (saved per-guild in Postgres / JSON)
      2. Global config passed in as `config` param (covers single-server setups
         where the user saved the channel without selecting a specific guild)
      Falls back to "log_channel" if the specific channel key isn't set.
    If the channel is not configured and bot is provided, DMs the guild owner.
    """
    try:
        if guild_id:
            from dashboard import get_guild_config
            gcfg = get_guild_config(str(guild_id))
            # 1) Guild-specific: try specific channel key, fall back to main log_channel
            ch = gcfg.get(channel_key, "").strip()
            if not ch and channel_key != "log_channel":
                ch = gcfg.get("log_channel", "").strip()
            if ch:
                return ch

        # 2) Global config fallback — covers single-server / unconfigured-guild cases
        #    where the admin saved the channel ID without selecting a guild in the dashboard.
        if isinstance(config, dict):
            ch = config.get(channel_key, "").strip()
            if not ch and channel_key != "log_channel":
                ch = config.get("log_channel", "").strip()
            if ch:
                return ch

        # Channel not found — notify admin once per session
        if guild_id and bot is not None:
            missing = []
            try:
                from dashboard import get_guild_config as _ggc
                _gcfg = _ggc(str(guild_id))
                if not _gcfg.get("log_channel", "").strip():
                    missing.append("log_channel")
                if channel_key not in ("log_channel",) and not _gcfg.get(channel_key, "").strip():
                    if channel_key not in missing:
                        missing.append(channel_key)
            except Exception:
                pass
            if missing:
                import asyncio
                asyncio.ensure_future(_notify_admin_unconfigured(bot, guild_id, missing))

        return ""
    except Exception:
        return ""


async def _get_channel(bot, channel_id: int):
    """Return a channel object, fetching from the API if not in cache."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            channel = None
    return channel


async def log_event(bot, config, title, description, color=0x3498db, guild_id=None):
    """Log a simple embed to the configured log channel for the given guild."""
    log_channel_id = await _resolve_log_channel_id(config, guild_id, "log_channel", bot=bot)
    if not log_channel_id:
        return
    try:
        channel = await _get_channel(bot, int(log_channel_id))
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
    log_channel_id = await _resolve_log_channel_id(config, guild_id, channel_key, bot=bot)
    if not log_channel_id:
        return
    try:
        channel = await _get_channel(bot, int(log_channel_id))
        if not channel:
            return
        import embeds
        eid = guild_id or getattr(getattr(channel, "guild", None), "id", None)
        e = embeds.render(key, guild_id=eid, **vars)
        await channel.send(embed=e)
    except Exception as ex:
        print(f"Log template ({key}) error: {ex}")
