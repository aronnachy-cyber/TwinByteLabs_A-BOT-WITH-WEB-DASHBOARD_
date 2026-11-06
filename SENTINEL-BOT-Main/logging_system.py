"""NVD Official configurable Discord logging system."""
from __future__ import annotations
import re
import time
import discord

CATEGORIES = {
    "messages": "message-logs", "members": "member-logs",
    "join_leave": "join-leave-logs", "server": "server-logs",
    "voice": "voice-logs", "moderation": "moderation-logs",
    "roles": "role-logs", "channels": "channel-logs",
    "emoji": "emoji-logs", "invites": "invite-logs",
}
LEGACY = {
    "messages": "message_log_channel", "members": "join_log_channel",
    "join_leave": "join_log_channel", "voice": "voice_log_channel",
}
EVENT_CATEGORY = {
    "message_delete":"messages","message_edit":"messages","message_bulk_delete":"messages",
    "member_join":"join_leave","member_leave":"join_leave","member_kick":"moderation",
    "member_ban":"moderation","member_unban":"moderation","username_change":"members",
    "nickname_change":"members","avatar_change":"members","profile_update":"members",
    "role_create":"roles","role_delete":"roles",
    "role_update":"roles","role_add":"roles","role_remove":"roles",
    "role_rename":"roles","role_color_change":"roles","role_permissions_change":"roles",
    "role_position_change":"roles","role_hoist_change":"roles","role_mentionable_change":"roles",
    "channel_create":"channels","channel_delete":"channels","channel_update":"channels",
    
    "channel_rename":"channels","channel_topic_change":"channels","channel_permissions_change":"channels",
    "channel_category_change":"channels","channel_position_change":"channels","channel_type_change":"channels",
    "channel_slowmode_change":"channels","channel_nsfw_change":"channels",
    "voice_join":"voice","voice_leave":"voice","voice_move":"voice",
    "voice_self_mute":"voice","voice_self_unmute":"voice","voice_self_deafen":"voice",
    "voice_self_undeafen":"voice","voice_server_mute":"voice","voice_server_unmute":"voice",
    "voice_server_deafen":"voice","voice_server_undeafen":"voice",
    "voice_stream_start":"voice","voice_stream_stop":"voice","voice_video_start":"voice","voice_video_stop":"voice",
    "server_update":"server","server_name_change":"server","server_icon_change":"server",
    "server_banner_change":"server","server_description_change":"server","server_settings_change":"server",
    "emoji_create":"emoji","emoji_delete":"emoji","emoji_update":"emoji",
    "sticker_create":"emoji","sticker_delete":"emoji","sticker_update":"emoji",
    "invite_detected":"invites","invite_create":"invites","invite_delete":"invites","invite_used":"invites",
    "member_timeout":"moderation","timeout_removed":"moderation",
    "warning_issued":"moderation","warning_removed":"moderation","automod_action":"moderation",
}
_seen = {}
_invite_cache = {}
_registered = False

def _cfg(guild_id):
    try:
        import main
        return main.get_config_for_guild(str(guild_id))
    except Exception:
        try:
            from dashboard import get_guild_config
            return get_guild_config(str(guild_id))
        except Exception:
            return {}

def infer_event(title, description=""):
    text = (str(title) + " " + str(description[:120])).lower()
    checks = [
        (("message edited","msg-edit"),"message_edit"),
        (("message deleted","msg-delete"),"message_delete"),
        (("bulk messages deleted","bulk-delete"),"message_bulk_delete"),
        (("nickname changed","nick-change"),"nickname_change"),
        (("role added","role-add"),"role_add"),(("role removed","role-remove"),"role_remove"),
        (("member unbanned","unban"),"member_unban"),(("member banned","ban"),"member_ban"),
        (("member timed out","timeout"),"member_timeout"),(("timeout removed",),"timeout_removed"),
        (("role renamed",),"role_rename"),(("role color changed",),"role_color_change"),
        (("role permissions changed",),"role_permissions_change"),(("role position changed",),"role_position_change"),
        (("role hoist changed",),"role_hoist_change"),(("role mentionable changed",),"role_mentionable_change"),
        (("role created","role-create"),"role_create"),(("role deleted","role-delete"),"role_delete"),
        (("role updated","role-update"),"role_update"),
        (("channel renamed",),"channel_rename"),(("channel topic changed",),"channel_topic_change"),
        (("channel permissions changed",),"channel_permissions_change"),(("channel category changed",),"channel_category_change"),
        (("channel position changed",),"channel_position_change"),(("channel type changed",),"channel_type_change"),
        (("channel slowmode changed",),"channel_slowmode_change"),(("channel nsfw changed",),"channel_nsfw_change"),
        (("channel created","ch-create"),"channel_create"),(("channel deleted","ch-delete"),"channel_delete"),
        (("channel updated","ch-update"),"channel_update"),
        (("server name changed",),"server_name_change"),(("server icon changed",),"server_icon_change"),
        (("server banner changed",),"server_banner_change"),(("server description changed",),"server_description_change"),
        (("server settings changed",),"server_settings_change"),(("server updated","server-update"),"server_update"),
        (("invite created","invite-create"),"invite_create"),(("invite deleted","invite-delete"),"invite_delete"),
        (("server muted",),"voice_server_mute"),(("server unmuted",),"voice_server_unmute"),
        (("server deafened",),"voice_server_deafen"),(("server undeafened",),"voice_server_undeafen"),
        (("voice muted",),"voice_self_mute"),(("voice unmuted",),"voice_self_unmute"),
        (("voice deafened",),"voice_self_deafen"),(("voice undeafened",),"voice_self_undeafen"),
        (("emoji added",),"emoji_create"),(("emoji removed",),"emoji_delete"),
        (("sticker added",),"sticker_create"),(("sticker removed",),"sticker_delete"),
        (("warning issued","warning"),"warning_issued"),(("warnings cleared","clear-warn"),"warning_removed"),
    ]
    for needles, event in checks:
        if any(n in text for n in needles):
            return event
    return "server_update"

def _ignored(cfg, actor_id=None, channel_id=None, is_bot=False, subject_id=None, subject_is_bot=False):
    if cfg.get("logging_ignore_bots") in (True,"ON","on","true") and (is_bot or subject_is_bot):
        return True
    users = {str(x) for x in (cfg.get("logging_ignore_users") or [])}
    channels = {str(x) for x in (cfg.get("logging_ignore_channels") or [])}
    return (actor_id is not None and str(actor_id) in users) or (subject_id is not None and str(subject_id) in users) or (channel_id is not None and str(channel_id) in channels)

def _target(bot, guild, cfg, category):
    mapping = cfg.get("logging_channels") or {}
    raw = mapping.get(category) if isinstance(mapping,dict) else None
    if not raw:
        raw = cfg.get(LEGACY.get(category,"log_channel")) or cfg.get("log_channel")
    try:
        ident = int(raw)
    except (TypeError,ValueError):
        return None
    # Never route one guild's logs into a cached channel belonging to another guild.
    target = guild.get_channel(ident)
    if target is None:
        target = bot.get_channel(ident)
    target_guild = getattr(target, "guild", None)
    if target is not None and (target_guild is None or target_guild.id != guild.id):
        return None
    return target

async def emit(bot, guild, event, title, description, *, color=None, actor=None, subject=None, channel=None, dedupe_id=None, fields=None):
    if guild is None:
        return False
    category = EVENT_CATEGORY.get(event,"server")
    cfg = _cfg(guild.id)
    if "logging_enabled" in cfg and str(cfg.get("logging_enabled")).upper() in ("OFF","FALSE","0"):
        return False
    if (cfg.get("logging_categories") or {}).get(category, True) is False:
        return False
    if (cfg.get("logging_events") or {}).get(event, True) is False:
        return False
    if _ignored(cfg,getattr(actor,"id",None),getattr(channel,"id",None),getattr(actor,"bot",False),getattr(subject,"id",None),getattr(subject,"bot",False)):
        return False
    now = time.monotonic()
    if dedupe_id is not None:
        key=(guild.id,event,str(dedupe_id))
        if key in _seen and now-_seen[key] < 8:
            return False
        _seen[key]=now
    if len(_seen)>4000:
        for k,v in list(_seen.items()):
            if now-v>30: _seen.pop(k,None)
    target=_target(bot,guild,cfg,category)
    if target is None or not hasattr(target,"send"):
        return False
    try:
        me=guild.me
        if me:
            p=target.permissions_for(me)
            if not (p.view_channel and p.send_messages and p.embed_links):
                print("[LOGS] Missing View Channel, Send Messages, or Embed Links for "+category)
                return False
        raw=color if color is not None else cfg.get("logging_embed_color",0x3498DB)
        if isinstance(raw,str): raw=int(raw.strip().lstrip("#"),16)
        embed=discord.Embed(title=str(title)[:256],description=str(description)[:4000],color=int(raw))
        if cfg.get("logging_timestamps",True) not in (False,"OFF","off","false"):
            embed.timestamp=discord.utils.utcnow()
        if actor and cfg.get("logging_format","detailed") != "compact":
            embed.set_author(name=str(actor)+" | "+str(actor.id),icon_url=getattr(getattr(actor,"display_avatar",None),"url",None))
        for name,value,inline in (fields or [])[:20]:
            embed.add_field(name=str(name)[:256],value=str(value)[:1024] or "—",inline=bool(inline))
        await target.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
        return True
    except discord.NotFound:
        print("[LOGS] Destination for "+category+" was deleted; run /logs setup.")
    except discord.Forbidden:
        print("[LOGS] Discord denied sending "+category+" log.")
    except Exception as exc:
        print("[LOGS] "+event+" failed: "+str(exc))
    return False

async def send_existing(bot,guild_id,category,event,embed,actor_id=None,channel_id=None,is_bot=False):
    guild=bot.get_guild(int(guild_id)) if guild_id else None
    if not guild: return False
    cfg=_cfg(guild.id)
    if "logging_enabled" in cfg and str(cfg.get("logging_enabled")).upper() in ("OFF","FALSE","0"): return False
    if (cfg.get("logging_categories") or {}).get(category,True) is False: return False
    if (cfg.get("logging_events") or {}).get(event,True) is False: return False
    if _ignored(cfg,actor_id,channel_id,is_bot): return False
    target=_target(bot,guild,cfg,category)
    if target is None: return False
    try:
        if cfg.get("logging_timestamps",True) in (False,"OFF","off","false"): embed.timestamp=None
        await target.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
        return True
    except (discord.NotFound,discord.Forbidden) as exc:
        print("[LOGS] Could not send "+category+" embed: "+str(exc))
    except Exception as exc: print("[LOGS] "+str(exc))
    return False

async def audit_actor(guild,action,target_id=None):
    try:
        from datetime import timedelta
        cutoff=discord.utils.utcnow()-timedelta(seconds=10)
        async for entry in guild.audit_logs(limit=10,action=action,after=cutoff):
            if target_id is None or getattr(entry.target,"id",None)==target_id:
                return entry.user,entry.reason
    except Exception:
        pass
    return None,None

async def setup(bot):
    global _registered
    if _registered: return
    _registered=True

    @bot.listen("on_raw_message_delete")
    async def _uncached_message_delete(payload):
        if not payload.guild_id or payload.cached_message is not None:
            return
        guild = bot.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(payload.channel_id)
        await emit(bot, guild, "message_delete", "🗑️ Message Deleted (uncached)",
                   "Discord did not provide cached message content for this delete event.\n"
                   "Message ID: " + str(payload.message_id) + "\n"
                   "Channel ID: " + str(payload.channel_id) + "\n"
                   "Author/content/attachments: Unavailable from this gateway event.",
                   channel=channel, dedupe_id=payload.message_id)

    @bot.listen("on_message")
    async def _invite_message(message):
        if not message.guild: return
        links=re.findall(r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+",message.content or "",re.I)
        if links:
            await emit(bot,message.guild,"invite_detected","🔗 Discord Invite Link Detected",
                "Message: "+message.jump_url+"\nChannel: "+message.channel.mention+"\nLinks:\n"+"\n".join(links[:5]),
                actor=message.author,channel=message.channel,dedupe_id=message.id)

    @bot.listen("on_member_join")
    async def _invite_usage(member):
        guild=member.guild
        previous=_invite_cache.get(guild.id,{})
        try:
            invites=await guild.invites()
            current={i.code:(i.uses or 0) for i in invites}
            used=[i for i in invites if (i.uses or 0)>previous.get(i.code,0)]
            _invite_cache[guild.id]=current
            if used:
                invite=max(used,key=lambda i:i.uses or 0)
                await emit(bot,guild,"invite_used","📨 Invite Used",
                    "Member: "+str(member)+" ("+str(member.id)+")\nInvite: "+invite.code+
                    "\nInviter: "+str(invite.inviter or "Unavailable")+"\nUses: "+str(invite.uses),
                    actor=member,dedupe_id=str(member.id)+":"+invite.code)
        except Exception:
            return

    @bot.listen("on_user_update")
    async def _profile_update(before,after):
        events=[]
        if before.name!=after.name:
            events.append(("username_change","📝 Username Changed","Username: "+before.name+" → "+after.name))
        if before.global_name!=after.global_name:
            events.append(("profile_update","👤 Display Name Changed","Display name: "+str(before.global_name)+" → "+str(after.global_name)))
        if before.avatar!=after.avatar:
            events.append(("avatar_change","🖼️ Avatar Changed","Avatar changed (Discord profile update event)."))
        if getattr(before,"banner",None)!=getattr(after,"banner",None):
            events.append(("profile_update","🖼️ Profile Banner Changed","Profile banner changed (where exposed by Discord)."))
        if not events: return
        for guild in bot.guilds:
            if guild.get_member(after.id):
                for event,title,detail in events:
                    await emit(bot,guild,event,title,"User: "+str(after)+" ("+str(after.id)+")\n"+detail,
                        actor=after,dedupe_id=str(after.id)+":"+event+":"+str(after.avatar)+":"+after.name+":"+str(after.global_name))

    @bot.listen("on_voice_state_update")
    async def _voice_extras(member,before,after):
        if before.channel!=after.channel: return
        for attr,on_event,off_event,on_text,off_text in [
            ("self_stream","voice_stream_start","voice_stream_stop","Started streaming","Stopped streaming"),
            ("self_video","voice_video_start","voice_video_stop","Started camera video","Stopped camera video")]:
            old,new=getattr(before,attr,False),getattr(after,attr,False)
            if old!=new:
                await emit(bot,member.guild,on_event if new else off_event,"🎥 Voice Activity",
                    "Member: "+str(member)+" ("+str(member.id)+")\nChannel: "+
                    (after.channel.mention if after.channel else "Unknown")+"\nAction: "+(on_text if new else off_text),
                    actor=member,channel=after.channel,dedupe_id=str(member.id)+":"+attr+":"+str(new)+":"+str(int(time.time()//3)))

    @bot.listen("on_guild_emojis_update")
    async def _emoji_renames(guild,before,after):
        old={x.id:x for x in before}; new={x.id:x for x in after}
        for ident in old.keys() & new.keys():
            if old[ident].name!=new[ident].name:
                await emit(bot,guild,"emoji_update","😀 Emoji Updated",
                    ":"+old[ident].name+": → :"+new[ident].name+":\nEmoji ID: "+str(ident),
                    dedupe_id="emoji:"+str(ident)+":"+new[ident].name)

    @bot.listen("on_guild_stickers_update")
    async def _sticker_edits(guild,before,after):
        old={x.id:x for x in before}; new={x.id:x for x in after}
        for ident in old.keys() & new.keys():
            if old[ident].name!=new[ident].name or old[ident].description!=new[ident].description:
                await emit(bot,guild,"sticker_update","🏷️ Sticker Updated",
                    "Sticker ID: "+str(ident)+"\nName: "+old[ident].name+" → "+new[ident].name+
                    "\nDescription: "+str(old[ident].description)+" → "+str(new[ident].description),
                    dedupe_id="sticker:"+str(ident)+":"+new[ident].name)

    @bot.listen("on_automod_action")
    async def _automod_log(execution):
        guild=getattr(execution,"guild",None)
        if not guild: return
        member=getattr(execution,"member",None); channel=getattr(execution,"channel",None)
        rule=getattr(getattr(execution,"rule",None),"name","Unknown")
        await emit(bot,guild,"automod_action","🤖 AutoMod Action",
            "Member: "+str(member or "Unavailable")+"\nRule: "+str(rule)+
            "\nAction: "+str(getattr(execution,"action","Unknown"))+
            "\nChannel: "+str(channel or "Unavailable"),actor=member,channel=channel,
            dedupe_id=getattr(execution,"id",None))

    for guild in bot.guilds:
        try:
            invites=await guild.invites()
            _invite_cache[guild.id]={i.code:(i.uses or 0) for i in invites}
        except Exception:
            _invite_cache[guild.id]={}

async def create_log_channels(guild,save_cfg,setup_member=None):
    me=guild.me
    if not me or not me.guild_permissions.manage_channels:
        raise PermissionError("Manage Channels permission is required.")
    category=discord.utils.get(guild.categories,name="SERVER LOGS")
    if category is None:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True),
        }
        if setup_member is not None:
            overwrites[setup_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        category=await guild.create_category("SERVER LOGS",overwrites=overwrites,reason="NVD Official logging setup")
    else:
        try:
            await category.set_permissions(guild.default_role, view_channel=False, reason="Keep NVD log channels private")
            await category.set_permissions(me, view_channel=True, send_messages=True, embed_links=True, read_message_history=True, reason="NVD logging permissions")
            if setup_member is not None:
                await category.set_permissions(setup_member, view_channel=True, send_messages=True, read_message_history=True, reason="NVD logging setup operator")
        except discord.Forbidden:
            raise PermissionError("Manage Channels/Manage Roles is required to secure the SERVER LOGS category.")
    mapping={}
    for key,name in CATEGORIES.items():
        channel=discord.utils.get(guild.text_channels,name=name)
        if channel is None or channel.category_id!=category.id:
            channel=await guild.create_text_channel(name,category=category,reason="NVD Official logging setup")
        mapping[key]=str(channel.id)
    current = _cfg(guild.id)
    events = dict(current.get("logging_events") or {})
    categories = dict(current.get("logging_categories") or {})
    for key in EVENT_CATEGORY:
        events.setdefault(key, True)
    for key in CATEGORIES:
        categories.setdefault(key, True)
    updates = {
        "logging_enabled": True,
        "logging_channels": mapping,
        "logging_events": events,
        "logging_categories": categories,
    }
    updates.setdefault("logging_ignore_users", current.get("logging_ignore_users") or [])
    updates.setdefault("logging_ignore_channels", current.get("logging_ignore_channels") or [])
    updates.setdefault("logging_ignore_bots", current.get("logging_ignore_bots", False))
    updates.setdefault("logging_embed_color", current.get("logging_embed_color", 0x3498DB))
    updates.setdefault("logging_timestamps", current.get("logging_timestamps", True))
    updates.setdefault("logging_format", current.get("logging_format", "detailed"))
    save_cfg(str(guild.id), updates)
    return category,mapping

def register_commands(bot,guard,get_cfg,save_cfg):
    group=discord.app_commands.Group(name="logs",description="Configure NVD's complete logging system.")
    cats=list(CATEGORIES)
    evts=list(EVENT_CATEGORY)

    @group.command(name="setup",description="Create SERVER LOGS and all ten log channels.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setup_cmd(interaction: discord.Interaction):
        if not await guard(interaction): return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            _,mapping=await create_log_channels(interaction.guild,save_cfg,interaction.user)
            rows=[key.replace("_"," ").title()+" → <#"+value+">" for key,value in mapping.items()]
            await interaction.followup.send("✅ Logging enabled.\n"+"\n".join(rows),ephemeral=True)
        except Exception as exc:
            await interaction.followup.send("❌ Setup failed: "+str(exc)[:250],ephemeral=True)

    @group.command(name="enable",description="Enable or disable all logging.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def enable_cmd(interaction: discord.Interaction,enabled:bool):
        if not await guard(interaction): return
        save_cfg(interaction.guild.id,{"logging_enabled":enabled})
        await interaction.response.send_message("Logging is now "+("ON" if enabled else "OFF")+".",ephemeral=True)

    @group.command(name="category",description="Toggle a complete log category.")
    @discord.app_commands.choices(category=[discord.app_commands.Choice(name=x.replace("_"," ").title(),value=x) for x in cats])
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def category_cmd(interaction: discord.Interaction,category:str,enabled:bool):
        if not await guard(interaction): return
        states=dict(get_cfg(interaction.guild.id).get("logging_categories") or {})
        states[category]=enabled
        save_cfg(interaction.guild.id,{"logging_categories":states})
        await interaction.response.send_message(category+" category: "+("ON" if enabled else "OFF"),ephemeral=True)

    @group.command(name="event",description="Toggle one individual event. Use an event key from /logs status or the event list.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def event_cmd(interaction: discord.Interaction,event:str,enabled:bool):
        if not await guard(interaction): return
        if event not in EVENT_CATEGORY:
            return await interaction.response.send_message("Unknown event key. Valid keys: "+", ".join(evts),ephemeral=True)
        states=dict(get_cfg(interaction.guild.id).get("logging_events") or {})
        states[event]=enabled
        save_cfg(interaction.guild.id,{"logging_events":states})
        await interaction.response.send_message(event+": "+("ON" if enabled else "OFF"),ephemeral=True)

    @group.command(name="ignore-user",description="Ignore a user in logs.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_user(interaction: discord.Interaction,user:discord.User):
        if not await guard(interaction): return
        ids=list(get_cfg(interaction.guild.id).get("logging_ignore_users") or [])
        if str(user.id) not in [str(x) for x in ids]: ids.append(str(user.id))
        save_cfg(interaction.guild.id,{"logging_ignore_users":ids})
        await interaction.response.send_message("Now ignoring "+str(user)+".",ephemeral=True)

    @group.command(name="channel",description="Set the destination channel for one logging category.")
    @discord.app_commands.choices(category=[discord.app_commands.Choice(name=x.replace("_"," ").title(),value=x) for x in cats])
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def channel_cmd(interaction: discord.Interaction,category:str,channel:discord.TextChannel):
        if not await guard(interaction): return
        mapping=dict(get_cfg(interaction.guild.id).get("logging_channels") or {})
        mapping[category]=str(channel.id)
        save_cfg(interaction.guild.id,{"logging_channels":mapping})
        await interaction.response.send_message(category+" logs will go to "+channel.mention,ephemeral=True)

    @group.command(name="ignore-channel",description="Ignore events from a channel.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_channel(interaction: discord.Interaction,channel:discord.TextChannel):
        if not await guard(interaction): return
        ids=list(get_cfg(interaction.guild.id).get("logging_ignore_channels") or [])
        if str(channel.id) not in [str(x) for x in ids]: ids.append(str(channel.id))
        save_cfg(interaction.guild.id,{"logging_ignore_channels":ids})
        await interaction.response.send_message("Now ignoring "+channel.mention+".",ephemeral=True)

    @group.command(name="unignore-user",description="Remove a user from the ignore list.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def unignore_user(interaction: discord.Interaction,user:discord.User):
        if not await guard(interaction): return
        ids=[str(x) for x in (get_cfg(interaction.guild.id).get("logging_ignore_users") or []) if str(x)!=str(user.id)]
        save_cfg(interaction.guild.id,{"logging_ignore_users":ids})
        await interaction.response.send_message("Restored logs involving "+str(user)+".",ephemeral=True)

    @group.command(name="unignore-channel",description="Restore logs for a channel.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def unignore_channel(interaction: discord.Interaction,channel:discord.TextChannel):
        if not await guard(interaction): return
        ids=[str(x) for x in (get_cfg(interaction.guild.id).get("logging_ignore_channels") or []) if str(x)!=str(channel.id)]
        save_cfg(interaction.guild.id,{"logging_ignore_channels":ids})
        await interaction.response.send_message("Restored logs for "+channel.mention+".",ephemeral=True)

    @group.command(name="ignore-bots",description="Choose whether bot accounts are ignored.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_bots(interaction: discord.Interaction,enabled:bool):
        if not await guard(interaction): return
        save_cfg(interaction.guild.id,{"logging_ignore_bots":enabled})
        await interaction.response.send_message("Ignore bots: "+("ON" if enabled else "OFF"),ephemeral=True)

    @group.command(name="color",description="Set embed color using six-digit hex.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def color_cmd(interaction: discord.Interaction,hex_color:str):
        if not await guard(interaction): return
        raw=hex_color.strip().lstrip("#")
        try:
            if len(raw)!=6: raise ValueError()
            value=int(raw,16)
        except ValueError:
            return await interaction.response.send_message("Use a color such as #3498DB.",ephemeral=True)
        save_cfg(interaction.guild.id,{"logging_embed_color":value})
        await interaction.response.send_message("Embed color saved: #"+raw.upper(),ephemeral=True)

    @group.command(name="format",description="Choose compact or detailed log embeds.")
    @discord.app_commands.choices(format=[discord.app_commands.Choice(name="Detailed",value="detailed"),discord.app_commands.Choice(name="Compact",value="compact")])
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def format_cmd(interaction: discord.Interaction,format:str):
        if not await guard(interaction): return
        save_cfg(interaction.guild.id,{"logging_format":format})
        await interaction.response.send_message("Log format set to "+format+".",ephemeral=True)

    @group.command(name="timestamps",description="Enable or disable timestamps.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def timestamps(interaction: discord.Interaction,enabled:bool):
        if not await guard(interaction): return
        save_cfg(interaction.guild.id,{"logging_timestamps":enabled})
        await interaction.response.send_message("Timestamps: "+("ON" if enabled else "OFF"),ephemeral=True)

    @group.command(name="status",description="Show logging status and destinations.")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def status(interaction: discord.Interaction):
        if not await guard(interaction): return
        cfg=get_cfg(interaction.guild.id); mapping=cfg.get("logging_channels") or {}
        states=cfg.get("logging_categories") or {}
        rows=["Master: "+("ON" if cfg.get("logging_enabled") else "OFF")]
        for key in cats:
            raw=mapping.get(key,"")
            ch=interaction.guild.get_channel(int(raw)) if str(raw).isdigit() else None
            rows.append(key.replace("_"," ").title()+": "+("ON" if states.get(key,True) else "OFF")+" — "+(ch.mention if ch else "Not configured"))
        await interaction.response.send_message(embed=discord.Embed(title="NVD Logging Status",description="\n".join(rows)[:4000],color=0x3498DB),ephemeral=True)

    try:
        bot.tree.add_command(group)
    except Exception as exc:
        if "already" not in str(exc).lower(): print("[LOGS] /logs registration failed: "+str(exc))
