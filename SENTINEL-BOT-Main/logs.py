"""Compatibility logging facade routed through NVD's configurable log system."""
import discord
import logging_system

_TEMPLATE_EVENTS = {
    "sp4rk.welcome": ("join_leave", "member_join"),
    "sp4rk.leave": ("join_leave", "member_leave"),
    "sp4rk.member_banned": ("moderation", "member_ban"),
    "sp4rk.message_edit": ("messages", "message_edit"),
    "sp4rk.message_delete": ("messages", "message_delete"),
    "sp4rk.voice_join": ("voice", "voice_join"),
    "sp4rk.voice_leave": ("voice", "voice_leave"),
    "sp4rk.voice_move": ("voice", "voice_move"),
}

async def _resolve_log_channel_id(config, guild_id=None, channel_key="log_channel", bot=None):
    """Legacy resolver retained for integrations; category-aware when possible."""
    if not bot or not guild_id:
        return (config or {}).get(channel_key) or (config or {}).get("log_channel") or ""
    cfg = logging_system._cfg(guild_id)
    category = {
        "message_log_channel":"messages", "join_log_channel":"join_leave",
        "voice_log_channel":"voice"
    }.get(channel_key, "moderation")
    target = logging_system._target(bot, bot.get_guild(int(guild_id)), cfg, category)
    return str(target.id) if target else ""

async def _get_channel(bot, channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel is None:
        try: channel = await bot.fetch_channel(channel_id)
        except Exception: channel = None
    return channel

async def log_event(bot, config, title, description, color=0x3498db, guild_id=None, actor=None, channel=None, dedupe_id=None):
    if guild_id is None:
        return False
    guild = bot.get_guild(int(guild_id))
    event = logging_system.infer_event(title, description)
    extra = ""
    try:
        import re
        target_id = None
        match = re.search(r"ID:\s*\x60?(\d+)", str(description))
        if match:
            target_id = int(match.group(1))
        if not target_id:
            match = re.search(r"\((\d+)\)", str(description))
            if match:
                target_id = int(match.group(1))
        action_names = {
            "member_unban":"unban", "member_timeout":"member_update", "timeout_removed":"member_update", "nickname_change":"member_update",
            "voice_server_mute":"member_update", "voice_server_unmute":"member_update", "voice_server_deafen":"member_update", "voice_server_undeafen":"member_update",
            "role_add":"member_role_update", "role_remove":"member_role_update",
            "role_rename":"role_update", "role_color_change":"role_update", "role_permissions_change":"role_update", "role_position_change":"role_update", "role_hoist_change":"role_update", "role_mentionable_change":"role_update",
            "role_create":"role_create", "role_delete":"role_delete", "role_update":"role_update",
            "channel_create":"channel_create", "channel_delete":"channel_delete", "channel_update":"channel_update",
            "channel_rename":"channel_update", "channel_topic_change":"channel_update", "channel_permissions_change":"channel_update", "channel_category_change":"channel_update", "channel_position_change":"channel_update", "channel_type_change":"channel_update", "channel_slowmode_change":"channel_update", "channel_nsfw_change":"channel_update",
            "server_update":"guild_update", "server_name_change":"guild_update", "server_icon_change":"guild_update", "server_banner_change":"guild_update", "server_description_change":"guild_update", "server_settings_change":"guild_update", "emoji_create":"emoji_create", "emoji_delete":"emoji_delete", "emoji_update":"emoji_update", "sticker_create":"sticker_create", "sticker_delete":"sticker_delete", "sticker_update":"sticker_update", "invite_create":"invite_create", "invite_delete":"invite_delete",
        }
        action_name = action_names.get(event)
        if action_name:
            action = getattr(discord.AuditLogAction, action_name, None)
            if action is not None:
                moderator, reason = await logging_system.audit_actor(guild, action, target_id)
                extra = f"\n**Moderator:** {moderator or 'Unavailable from audit log'}\n**Reason:** {reason or 'Unavailable'}"
                if moderator:
                    actor = moderator
    except Exception:
        pass
    subject = guild.get_member(target_id) if guild is not None and target_id else None
    return await logging_system.emit(bot, guild, event, title, str(description) + extra,
                                     color=color, actor=actor, subject=subject, channel=channel, dedupe_id=dedupe_id)

async def log_template(bot, config, key, guild_id=None, **vars):
    if not guild_id:
        for value in vars.values():
            if isinstance(value, discord.Guild):
                guild_id = value.id
                break
            candidate = getattr(value, "guild", None)
            if isinstance(candidate, discord.Guild):
                guild_id = candidate.id
                break
    if not guild_id:
        return False
    category, event = _TEMPLATE_EVENTS.get(key, ("server", "server_update"))
    try:
        import embeds
        embed = embeds.render(key, guild_id=guild_id, **vars)
        user = vars.get("user")
        channel = vars.get("channel")
        # Add account age context to join records without claiming invite attribution.
        if key == "sp4rk.welcome" and user is not None:
            try:
                age = user.created_at
                embed.add_field(name="Account created", value=f"<t:{int(age.timestamp())}:F> (<t:{int(age.timestamp())}:R>)", inline=False)
            except Exception:
                pass
        # For ban logs, enrich with audit-log actor/reason only when Discord exposes a match.
        if key == "sp4rk.member_banned" and user is not None:
            actor, reason = await logging_system.audit_actor(user.guild if hasattr(user, "guild") else bot.get_guild(int(guild_id)), discord.AuditLogAction.ban, user.id)
            embed.add_field(name="Moderator", value=str(actor or "Unavailable from audit log"), inline=True)
            embed.add_field(name="Reason", value=str(reason or "Unavailable"), inline=True)
        return await logging_system.send_existing(
            bot, guild_id, category, event, embed,
            actor_id=getattr(user, "id", None),
            channel_id=getattr(channel, "id", None),
            is_bot=getattr(user, "bot", False),
        )
    except Exception as exc:
        print(f"[logs] template {key} failed: {exc}")
        return False
