import discord
from discord.ext import commands, tasks
from discord import app_commands
import threading
import asyncio
import os
import json
import re
import requests
import aiohttp
from datetime import datetime, timedelta

from dashboard import app, config as dash_config, run_dashboard, add_dashboard_log, set_bot_restart_callback
import moderation, automod, roles, logs, social, security

# ---------------- CONFIG LOADER ----------------
def load_config():
    if not os.path.exists('settings.json'):
        return {}
    with open('settings.json', 'r') as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def refresh_config():
    global config
    config = {**DEFAULT_CONFIG, **load_config()}

DEFAULT_CONFIG = {
    "prefix": "!",
    "bot_status": "online",
    "theme_color": "#3498db",
    "welcome_msg": "",
    "bot_avatar": "",
    "log_channel": "",
    "mod_block_links": "OFF",
    "mod_block_mentions": "OFF",
    "profanity_level": "low",
    "mute_role": "",
    "auto_role": "",
    "auto_block_links": "OFF",
    "auto_block_invites": "OFF",
    "auto_block_mentions": "OFF",
    "auto_custom_blacklist": "",
    "auto_dm_warning": "OFF",
    "webhook_log": "",
    "warning_limit": 30,
    "owner_approval": "OFF",
    "anti_raid": "OFF",
    "banned_words": "",
    "security_2fa": "OFF",
    "role_monitor": "OFF",
    "mass_ban_prevent": "OFF",
    "temp_lockdown": "OFF",
    "discord_alerts": "OFF",
    "twitter_alerts": "OFF",
    "youtube_alerts": "OFF",
    "youtube_channel_link": "",
    "youtube_notify_msg": "📺 A new YouTube video was shared! Check it out:",
    "multi_channel": "",
    "owner_dm_id": "",
    "daily_report": "OFF"
}

config = {**DEFAULT_CONFIG, **load_config()}

TOKEN         = os.environ.get("DISCORD_TOKEN", "")
OPENAI_KEY    = os.environ.get("OPENAI_KEY", "")
_PORT         = int(os.environ.get("PORT", 5000))
DASHBOARD_URL = f"http://127.0.0.1:{_PORT}"

intents = discord.Intents.all()
bot = commands.Bot(
    command_prefix=lambda b, m: config.get('prefix', '!') or '!',
    intents=intents
)

def dlog(event_type, description):
    try:
        add_dashboard_log(event_type, description)
    except Exception:
        pass

# ---------------- WARNING STORAGE ----------------
# Structure: {guild_id: {member_id: {"count": int, "history": [{"reason": str, "time": str}]}}}
warnings_file = "warnings.json"

def load_warnings():
    if os.path.exists(warnings_file):
        with open(warnings_file, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_warnings():
    with open(warnings_file, "w") as f:
        json.dump(warnings_db, f, indent=4)

def _get_member_entry(guild_id: str, member_id: str) -> dict:
    if guild_id not in warnings_db:
        warnings_db[guild_id] = {}
    entry = warnings_db[guild_id].get(member_id)
    if isinstance(entry, int):
        # Migrate old format
        warnings_db[guild_id][member_id] = {"count": entry, "history": []}
    elif not isinstance(entry, dict):
        warnings_db[guild_id][member_id] = {"count": 0, "history": []}
    return warnings_db[guild_id][member_id]

warnings_db = load_warnings()

# ---------------- DURATION PARSER ----------------
def parse_duration(raw: str) -> int | None:
    """Parse duration string like 30m, 2h, 1d into seconds. Returns None if invalid."""
    match = re.fullmatch(r"(\d+)(s|m|h|d|w)", raw.lower().strip())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return value * multipliers[unit]

def duration_label(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"

# ---------------- MULTI-OWNER HELPERS ----------------
pending_approvals = {}

def get_owner_ids():
    raw = config.get("owner_dm_id", "").strip()
    if not raw:
        return []
    return [oid.strip() for oid in raw.split(",") if oid.strip().isdigit()]

async def dm_all_owners(message: str):
    for oid in get_owner_ids():
        try:
            owner = await bot.fetch_user(int(oid))
            if owner:
                await owner.send(message)
        except Exception as e:
            print(f"Owner DM error (ID {oid}): {e}")

async def dm_owner(action, target, reason, moderator):
    if not get_owner_ids():
        return
    msg = (
        f"🔔 **Sentinel OS — Action Performed**\n"
        f"**Action:** {action.upper()}\n"
        f"**Target:** {target}\n"
        f"**Reason:** {reason}\n"
        f"**Moderator:** {moderator}"
    )
    await dm_all_owners(msg)

async def notify_owners_warning(member, count):
    if not get_owner_ids():
        admins = [m for m in member.guild.members if m.guild_permissions.administrator and not m.bot]
        for admin in admins:
            try:
                await admin.send(
                    f"⚠️ **Warning Limit Reached!**\n"
                    f"**Server:** {member.guild.name}\n"
                    f"**Member:** {member} ({member.id})\n"
                    f"**Warnings:** {count}\n"
                    f"Reply `!approve ban {member.id}` or `!approve kick {member.id}`"
                )
            except Exception:
                pass
        return

    key = f"{member.id}:{member.guild.id}"
    pending_approvals[key] = {
        "member_id": member.id,
        "guild_id":  member.guild.id,
        "username":  str(member),
        "warnings":  count
    }

    msg = (
        f"⚠️ **Sentinel OS — Approval Required**\n"
        f"**Server:** {member.guild.name}\n"
        f"**Member:** {member} (`{member.id}`)\n"
        f"**Warnings:** {count} (limit reached!)\n\n"
        f"Reply with:\n"
        f"• `!approve ban {member.id}` — ban this user\n"
        f"• `!approve kick {member.id}` — kick this user\n"
        f"• `!approve ignore {member.id}` — dismiss alert\n\n"
        f"Any owner can confirm — first reply wins."
    )
    await dm_all_owners(msg)
    dlog("⚠️ APPROVAL", f"Approval request sent for {member} ({count} warnings)")

async def check_warning_limit(member, count):
    refresh_config()
    limit           = int(config.get("warning_limit", 30))
    owner_approval  = config.get("owner_approval", "OFF") == "ON"
    if count >= limit and owner_approval:
        await notify_owners_warning(member, count)
    try:
        requests.post(
            f"{DASHBOARD_URL}/api/add_warning",
            json={"member_id": member.id, "username": str(member), "warnings": count},
            timeout=3
        )
    except Exception:
        pass

async def add_warning(member, reason="Violation"):
    guild_id  = str(member.guild.id)
    member_id = str(member.id)
    entry = _get_member_entry(guild_id, member_id)
    entry["count"] += 1
    entry["history"].append({
        "reason": reason,
        "time":   datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    count = entry["count"]
    save_warnings()
    dlog("⚠️ WARNING", f"{member} now has {count} warning(s) — {reason}")
    print(f"⚠️ Warning added to {member}: {count} total | Reason: {reason}")
    await check_warning_limit(member, count)

# ---------------- AI MODERATION ----------------
async def handle_ai_moderation(message):
    if not OPENAI_KEY:
        return
    try:
        import openai
        client = openai.OpenAI(api_key=OPENAI_KEY)
        response = client.moderations.create(input=message.content)
        result   = response.results[0]
        if result.flagged:
            categories = [k for k, v in result.categories.__dict__.items() if v]
            await message.delete()
            await add_warning(message.author, reason=f"AI Moderation: {', '.join(categories)}")
            await message.channel.send(
                f"⚠️ {message.author.mention}, your message was removed by AI moderation.",
                delete_after=7
            )
            dlog("🤖 AI-MOD", f"{message.author} flagged — {', '.join(categories)}")
    except Exception as e:
        print(f"AI moderation error (ignored): {e}")

# ---------------- BACKGROUND TASKS ----------------
_last_avatar_url = ""
_last_status     = ""

@tasks.loop(seconds=60)
async def sync_bot_settings():
    global _last_avatar_url, _last_status
    refresh_config()
    current_status = config.get("bot_status", "online")
    if current_status != _last_status:
        s_map = {"online": discord.Status.online, "idle": discord.Status.idle, "dnd": discord.Status.dnd}
        try:
            await bot.change_presence(status=s_map.get(current_status, discord.Status.online))
            _last_status = current_status
        except Exception:
            pass
    avatar_url = config.get("bot_avatar", "").strip()
    if avatar_url and avatar_url != _last_avatar_url:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(avatar_url) as resp:
                    if resp.status == 200:
                        await bot.user.edit(avatar=await resp.read())
                        _last_avatar_url = avatar_url
                        dlog("🖼️ AVATAR", "Bot avatar updated")
        except Exception as e:
            print(f"Avatar update error: {e}")

@tasks.loop(hours=24)
async def daily_report_task():
    refresh_config()
    if config.get("daily_report") != "ON":
        return
    log_channel_id = config.get("log_channel", "").strip()
    if not log_channel_id:
        return
    try:
        channel = bot.get_channel(int(log_channel_id))
        if not channel:
            return
        total_warnings = sum(
            (v.get("count", 0) if isinstance(v, dict) else v)
            for guild in warnings_db.values()
            for v in (guild.values() if isinstance(guild, dict) else [])
        )
        embed = discord.Embed(
            title="📊 Daily Moderation Report",
            description=(
                f"**Date:** {datetime.now().strftime('%Y-%m-%d')}\n"
                f"**Total Warnings (all-time):** {total_warnings}\n"
                f"**Bot Status:** Online ✅"
            ),
            color=0x4f8ef7
        )
        embed.set_footer(text="Sentinel OS — Automated Daily Report")
        await channel.send(embed=embed)
        dlog("📊 DAILY-REPORT", "Daily report posted to log channel")
    except Exception as e:
        print(f"Daily report error: {e}")

# ---------------- SLASH COMMANDS ----------------
@bot.tree.command(name="commands", description="List all Sentinel OS commands")
async def slash_commands(interaction: discord.Interaction):
    p = config.get('prefix', '!')
    embed = discord.Embed(title="🛡️ Sentinel OS — Command List", description=f"Prefix: `{p}`", color=0x4f8ef7)
    embed.add_field(name="⚖️ Moderation", inline=False, value=(
        f"`{p}warn @user [reason]` — Warn a member\n"
        f"`{p}warnings @user` — Show warning count + full history\n"
        f"`{p}clearwarnings @user` — Clear all warnings\n"
        f"`{p}kick @user [reason]` — Kick a member\n"
        f"`{p}ban @user [reason]` — Permanent ban\n"
        f"`{p}tempban @user <dur> [reason]` — Temp ban (e.g. `1h`, `2d`, `7d`)\n"
        f"`{p}mute @user` — Mute a member\n"
        f"`{p}purge [1-200]` — Bulk delete messages\n"
        f"`{p}slowmode [0-21600]` — Set channel slowmode (0 = off)"
    ))
    embed.add_field(name="🔍 Info", inline=False, value=(
        f"`{p}userinfo @user` — Detailed member info\n"
        f"`{p}warnings @user` — Warning history with reasons"
    ))
    embed.add_field(name="🔒 Security", inline=False, value=(
        f"`{p}lockdown` — Lock all text channels\n"
        f"`{p}unlockdown` — Unlock all channels\n"
        f"`{p}refresh` — Reload config from dashboard"
    ))
    embed.add_field(name="💫 Slash Commands", inline=False, value=(
        "`/commands` — This list\n`/serverinfo` — Server stats"
    ))
    embed.add_field(name="📬 Owner DM (via DM only)", inline=False, value=(
        f"`!approve ban <id>` / `!approve kick <id>` / `!approve ignore <id>`"
    ))
    embed.set_footer(text="Sentinel OS V2.0.3 • AI-Powered Moderation")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="serverinfo", description="Show server information")
async def slash_serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=f"📋 {guild.name}", color=0x4f8ef7)
    embed.add_field(name="Members",  value=guild.member_count)
    embed.add_field(name="Channels", value=len(guild.channels))
    embed.add_field(name="Roles",    value=len(guild.roles))
    embed.add_field(name="Created",  value=guild.created_at.strftime("%Y-%m-%d"))
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="Sentinel OS V2.0.3")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- EVENTS ----------------
@bot.event
async def on_ready():
    refresh_config()
    s_map = {"online": discord.Status.online, "idle": discord.Status.idle, "dnd": discord.Status.dnd}
    await bot.change_presence(status=s_map.get(config.get("bot_status", "online"), discord.Status.online))
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Slash command sync error: {e}")
    sync_bot_settings.start()
    daily_report_task.start()
    dlog("✅ BOT-START", f"Sentinel OS online as {bot.user}")
    print(f"✅ Sentinel OS Online! Logged in as {bot.user}")

@bot.event
async def on_member_join(member):
    refresh_config()
    await roles.assign_auto_role(member, config)
    await moderation.handle_auto_role(member, config)
    await security.anti_raid_check(member, config, dm_callback=dm_all_owners, log_callback=dlog)
    await social.send_welcome_message(bot, member, config)
    await logs.log_event(bot, config, "👋 New Member", f"{member.mention} joined **{member.guild.name}**")
    dlog("👋 JOIN", f"{member} joined {member.guild.name}")

@bot.event
async def on_member_update(before, after):
    refresh_config()
    await security.role_monitor_log(bot, config, after, before, after, log_callback=dlog)

@bot.event
async def on_member_ban(guild, user):
    refresh_config()
    await logs.log_event(bot, config, "⛔ Member Banned", f"{user} was banned from **{guild.name}**", color=0xe74c3c)
    dlog("⛔ BAN", f"{user} banned from {guild.name}")

@bot.event
async def on_member_remove(member):
    refresh_config()
    await logs.log_event(bot, config, "🚪 Member Left", f"{member.mention} left **{member.guild.name}**", color=0xe67e22)
    dlog("🚪 LEAVE", f"{member} left {member.guild.name}")

@bot.event
async def on_message_edit(before, after):
    if before.author.bot:
        return
    if before.content == after.content:
        return
    refresh_config()
    log_ch = config.get("log_channel", "").strip()
    if not log_ch:
        return
    try:
        channel = bot.get_channel(int(log_ch))
        if channel:
            embed = discord.Embed(
                title="✏️ Message Edited",
                color=0x3498db,
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Author",  value=f"{before.author.mention} (`{before.author}`)", inline=False)
            embed.add_field(name="Channel", value=before.channel.mention, inline=False)
            embed.add_field(name="Before",  value=before.content[:1000] or "*empty*", inline=False)
            embed.add_field(name="After",   value=after.content[:1000]  or "*empty*", inline=False)
            embed.add_field(name="Jump",    value=f"[Go to message]({after.jump_url})", inline=False)
            embed.set_footer(text=f"User ID: {before.author.id}")
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Edit log error: {e}")
    dlog("✏️ MSG-EDIT", f"{before.author} edited a message in #{before.channel.name}")

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    refresh_config()
    log_ch = config.get("log_channel", "").strip()
    if not log_ch:
        return
    try:
        channel = bot.get_channel(int(log_ch))
        if channel:
            embed = discord.Embed(
                title="🗑️ Message Deleted",
                color=0xe74c3c,
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Author",  value=f"{message.author.mention} (`{message.author}`)", inline=False)
            embed.add_field(name="Channel", value=message.channel.mention, inline=False)
            embed.add_field(name="Content", value=message.content[:1000] or "*empty / attachment*", inline=False)
            embed.set_footer(text=f"User ID: {message.author.id}")
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Delete log error: {e}")
    dlog("🗑️ MSG-DELETE", f"{message.author} deleted a message in #{message.channel.name}")

@bot.event
async def on_voice_state_update(member, before, after):
    refresh_config()
    log_ch = config.get("log_channel", "").strip()
    if not log_ch:
        return

    if before.channel == after.channel:
        return

    try:
        channel = bot.get_channel(int(log_ch))
        if not channel:
            return
        if before.channel is None:
            # Joined VC
            embed = discord.Embed(
                title="🔊 Joined Voice",
                description=f"{member.mention} joined **{after.channel.name}**",
                color=0x2ecc71,
                timestamp=datetime.utcnow()
            )
            dlog("🔊 VC-JOIN", f"{member} joined VC: {after.channel.name}")
        elif after.channel is None:
            # Left VC
            embed = discord.Embed(
                title="🔇 Left Voice",
                description=f"{member.mention} left **{before.channel.name}**",
                color=0xe74c3c,
                timestamp=datetime.utcnow()
            )
            dlog("🔇 VC-LEAVE", f"{member} left VC: {before.channel.name}")
        else:
            # Moved VC
            embed = discord.Embed(
                title="🔀 Moved Voice Channel",
                description=f"{member.mention} moved from **{before.channel.name}** → **{after.channel.name}**",
                color=0xf39c12,
                timestamp=datetime.utcnow()
            )
            dlog("🔀 VC-MOVE", f"{member} moved: {before.channel.name} → {after.channel.name}")
        embed.set_footer(text=f"User ID: {member.id}")
        await channel.send(embed=embed)
    except Exception as e:
        print(f"Voice log error: {e}")

_processed_msg_ids = set()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Deduplicate — prevent double-firing if two connections briefly overlap
    if message.id in _processed_msg_ids:
        return
    _processed_msg_ids.add(message.id)
    if len(_processed_msg_ids) > 2000:
        _processed_msg_ids.clear()

    # ---- Owner DM approval handler ----
    if isinstance(message.channel, discord.DMChannel):
        owner_ids = get_owner_ids()
        if str(message.author.id) in owner_ids:
            parts = message.content.strip().split()
            if len(parts) == 3 and parts[0].lower() == "!approve":
                action    = parts[1].lower()
                target_id = parts[2]

                matched_key = matched_data = None
                for key, data in pending_approvals.items():
                    if str(data["member_id"]) == target_id:
                        matched_key, matched_data = key, data
                        break

                if not matched_data:
                    await message.channel.send(f"❌ No pending approval for member ID `{target_id}`.")
                    return

                if action == "ignore":
                    del pending_approvals[matched_key]
                    await message.channel.send(f"✅ Alert dismissed for `{matched_data['username']}`.")
                    for oid in owner_ids:
                        if oid != str(message.author.id):
                            try:
                                o = await bot.fetch_user(int(oid))
                                if o:
                                    await o.send(f"ℹ️ Alert dismissed by {message.author} for `{matched_data['username']}`.")
                            except Exception:
                                pass
                    dlog("🚫 DISMISS", f"{message.author} dismissed alert for {matched_data['username']}")
                    return

                if action in ("ban", "kick"):
                    guild = bot.get_guild(matched_data["guild_id"])
                    if not guild:
                        await message.channel.send("❌ Server not found.")
                        return
                    try:
                        target_member = await guild.fetch_member(matched_data["member_id"])
                    except discord.NotFound:
                        await message.channel.send(f"❌ `{matched_data['username']}` is no longer in the server.")
                        del pending_approvals[matched_key]
                        return

                    reason = f"Owner-approved {action} — {matched_data['warnings']} warnings"
                    try:
                        if action == "ban":
                            await target_member.ban(reason=reason)
                        else:
                            await target_member.kick(reason=reason)
                        del pending_approvals[matched_key]
                        await message.channel.send(
                            f"✅ **{action.upper()} executed** on `{matched_data['username']}`.\nReason: {reason}"
                        )
                        await logs.log_event(
                            bot, config,
                            f"{'⛔' if action=='ban' else '🚪'} Owner-Approved {action.capitalize()}",
                            f"`{matched_data['username']}` was {action}ned — approved by {message.author}.",
                            color=0xe74c3c if action == "ban" else 0xe67e22
                        )
                        dlog(f"{'⛔' if action=='ban' else '🚪'} {action.upper()}", f"{matched_data['username']} {action}ned — approved by {message.author}")
                        notify_msg = (
                            f"{'⛔' if action=='ban' else '🚪'} **{action.upper()} confirmed** by {message.author}\n"
                            f"Target: `{matched_data['username']}` | Reason: {reason}"
                        )
                        for oid in owner_ids:
                            if oid != str(message.author.id):
                                try:
                                    o = await bot.fetch_user(int(oid))
                                    if o:
                                        await o.send(notify_msg)
                                except Exception:
                                    pass
                    except discord.Forbidden:
                        await message.channel.send(f"❌ Missing permissions to {action} `{matched_data['username']}`.")
                    except Exception as e:
                        await message.channel.send(f"❌ Error: {e}")
                else:
                    await message.channel.send("❌ Usage: `!approve ban/kick/ignore <user_id>`")
            return

    refresh_config()
    handled = await automod.handle_message(message, config)
    if not handled:
        await handle_ai_moderation(message)
    await social.check_social_alerts(message, config, bot)
    await bot.process_commands(message)

# ---------------- COMMAND ERROR HANDLER ----------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        perms = ", ".join(error.missing_permissions)
        await ctx.send(f"❌ {ctx.author.mention}, you need the **{perms}** permission to use this command.", delete_after=8)
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send(f"❌ Member not found. Make sure you @mention or use their ID.", delete_after=8)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: `{error.param.name}`. Check `!commands` for usage.", delete_after=8)
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Invalid argument. Check `!commands` for usage.", delete_after=8)
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"Unhandled command error: {error}")

# ---------------- MODERATION COMMANDS ----------------
@bot.command()
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member):
    await moderation.mute_member(ctx, member, config)
    dlog("🔇 MUTE", f"{member} muted by {ctx.author}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason="Violation"):
    await add_warning(member, reason)
    entry = _get_member_entry(str(ctx.guild.id), str(member.id))
    count = entry["count"]
    await ctx.send(f"⚠️ {member.mention} has been warned! Now at **{count}** warning(s).\nReason: {reason}")
    await logs.log_event(bot, config, "⚠️ Warning Issued", f"{member} warned by {ctx.author} | Reason: {reason}", color=0xf39c12)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def warnings(ctx, member: discord.Member):
    guild_id  = str(ctx.guild.id)
    member_id = str(member.id)
    entry = _get_member_entry(guild_id, member_id)
    count   = entry["count"]
    history = entry["history"]

    embed = discord.Embed(
        title=f"⚠️ Warning History — {member}",
        color=0xf39c12 if count > 0 else 0x2ecc71
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Total Warnings", value=f"**{count}**", inline=True)
    embed.add_field(name="Warning Limit",  value=f"**{config.get('warning_limit', 30)}**", inline=True)
    embed.add_field(name="Status",
        value="🔴 Limit Reached" if count >= int(config.get("warning_limit", 30)) else
              "🟠 Watch"         if count >= int(config.get("warning_limit", 30)) // 2 else
              "🟢 OK",
        inline=True
    )

    if history:
        last5 = history[-5:][::-1]
        history_text = "\n".join(
            f"`{i+1}.` {h['reason']} — *{h['time']}*"
            for i, h in enumerate(last5)
        )
        embed.add_field(name=f"Recent History (last {len(last5)})", value=history_text, inline=False)
    else:
        embed.add_field(name="History", value="No warnings on record.", inline=False)

    embed.set_footer(text=f"User ID: {member.id}")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def clearwarnings(ctx, member: discord.Member):
    guild_id  = str(ctx.guild.id)
    member_id = str(member.id)
    if guild_id in warnings_db and member_id in warnings_db[guild_id]:
        warnings_db[guild_id][member_id] = {"count": 0, "history": []}
        save_warnings()
    dlog("🧹 CLEAR-WARN", f"Warnings cleared for {member} by {ctx.author}")
    await ctx.send(f"✅ All warnings cleared for {member.mention}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    allowed = await security.mass_action_prevent(ctx, "kick", config, log_callback=dlog)
    if not allowed:
        return
    try:
        await member.kick(reason=reason)
        await ctx.send(f"🚪 {member.mention} has been kicked! Reason: {reason}")
        await logs.log_event(bot, config, "🚪 Kick", f"{member} kicked by {ctx.author} | Reason: {reason}", color=0xe67e22)
        await dm_owner("kick", str(member), reason, str(ctx.author))
        dlog("🚪 KICK", f"{member} kicked by {ctx.author} — {reason}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to kick {member.mention}.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    allowed = await security.mass_action_prevent(ctx, "ban", config, log_callback=dlog)
    if not allowed:
        return
    try:
        await member.ban(reason=reason)
        await ctx.send(f"⛔ {member.mention} has been permanently banned! Reason: {reason}")
        await logs.log_event(bot, config, "⛔ Ban", f"{member} banned by {ctx.author} | Reason: {reason}", color=0xe74c3c)
        await dm_owner("ban", str(member), reason, str(ctx.author))
        dlog("⛔ BAN", f"{member} banned by {ctx.author} — {reason}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to ban {member.mention}.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def tempban(ctx, member: discord.Member, duration: str, *, reason="No reason provided"):
    """Temp-ban a member. Duration: 30m, 2h, 1d, 7d, etc."""
    seconds = parse_duration(duration)
    if not seconds:
        await ctx.send("❌ Invalid duration! Use formats like `30m`, `2h`, `1d`, `7d`.")
        return

    allowed = await security.mass_action_prevent(ctx, "ban", config, log_callback=dlog)
    if not allowed:
        return

    label = duration_label(seconds)
    try:
        await member.ban(reason=f"[TEMP-BAN {label}] {reason}")
        await ctx.send(
            f"⏱️ **{member}** has been temp-banned for **{label}**!\n"
            f"Reason: {reason}\n"
            f"They will be automatically unbanned <t:{int((datetime.utcnow() + timedelta(seconds=seconds)).timestamp())}:R>."
        )
        await logs.log_event(
            bot, config, "⏱️ Temp Ban",
            f"{member} temp-banned by {ctx.author} for {label} | Reason: {reason}",
            color=0xe74c3c
        )
        await dm_owner("temp-ban", str(member), f"{label} — {reason}", str(ctx.author))
        dlog("⏱️ TEMP-BAN", f"{member} temp-banned for {label} by {ctx.author}")

        # Schedule auto-unban
        async def auto_unban():
            await asyncio.sleep(seconds)
            try:
                await ctx.guild.unban(member, reason=f"Temp-ban expired ({label})")
                await logs.log_event(
                    bot, config, "✅ Temp Ban Expired",
                    f"{member} was automatically unbanned after {label}.",
                    color=0x2ecc71
                )
                dlog("✅ UNBAN", f"Temp-ban expired for {member} after {label}")
            except Exception as e:
                print(f"Auto-unban error for {member}: {e}")

        asyncio.create_task(auto_unban())

    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to ban {member.mention}.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int = 10):
    """Bulk delete messages. Usage: !purge 50"""
    if amount < 1 or amount > 200:
        await ctx.send("❌ Amount must be between 1 and 200.", delete_after=5)
        return
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
        actual  = len(deleted) - 1
        msg = await ctx.send(f"🧹 Deleted **{actual}** message(s).")
        await asyncio.sleep(5)
        await msg.delete()
        await logs.log_event(
            bot, config, "🧹 Purge",
            f"{ctx.author} purged {actual} messages in #{ctx.channel.name}",
            color=0x9b59b6
        )
        dlog("🧹 PURGE", f"{ctx.author} deleted {actual} messages in #{ctx.channel.name}")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to delete messages here.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def slowmode(ctx, seconds: int = 0):
    """Set slowmode for the current channel. 0 = disabled. Max 21600 (6h)."""
    if seconds < 0 or seconds > 21600:
        await ctx.send("❌ Slowmode must be between 0 and 21600 seconds (6 hours).", delete_after=7)
        return
    try:
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await ctx.send("✅ Slowmode disabled in this channel.")
            dlog("⏱️ SLOWMODE", f"Slowmode disabled in #{ctx.channel.name} by {ctx.author}")
        else:
            label = duration_label(seconds)
            await ctx.send(f"⏱️ Slowmode set to **{label}** in {ctx.channel.mention}.")
            dlog("⏱️ SLOWMODE", f"Slowmode set to {label} in #{ctx.channel.name} by {ctx.author}")
        await logs.log_event(
            bot, config, "⏱️ Slowmode",
            f"#{ctx.channel.name} slowmode set to {seconds}s by {ctx.author}",
            color=0x3498db
        )
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to edit this channel.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def userinfo(ctx, member: discord.Member = None):
    """Show detailed info about a member."""
    member = member or ctx.author
    guild_id  = str(ctx.guild.id)
    member_id = str(member.id)
    entry = _get_member_entry(guild_id, member_id)
    count = entry["count"]

    roles_list = [r.mention for r in member.roles if r.name != "@everyone"]
    roles_str  = " ".join(roles_list[-10:]) if roles_list else "None"

    embed = discord.Embed(
        title=f"👤 {member}",
        color=member.color if str(member.color) != "#000000" else 0x4f8ef7,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🆔 User ID",      value=f"`{member.id}`",                                 inline=True)
    embed.add_field(name="🏷️ Nickname",     value=member.nick or "None",                            inline=True)
    embed.add_field(name="🤖 Bot",          value="Yes" if member.bot else "No",                    inline=True)
    embed.add_field(name="📅 Account Created",
        value=f"<t:{int(member.created_at.timestamp())}:D> (<t:{int(member.created_at.timestamp())}:R>)",
        inline=False
    )
    embed.add_field(name="📥 Joined Server",
        value=f"<t:{int(member.joined_at.timestamp())}:D> (<t:{int(member.joined_at.timestamp())}:R>)" if member.joined_at else "Unknown",
        inline=False
    )
    embed.add_field(name=f"🎭 Roles ({len(roles_list)})", value=roles_str[:1000], inline=False)
    embed.add_field(name="⚠️ Warnings",
        value=f"**{count}** / {config.get('warning_limit', 30)}",
        inline=True
    )

    perms = []
    if member.guild_permissions.administrator:
        perms.append("Administrator")
    if member.guild_permissions.manage_guild:
        perms.append("Manage Server")
    if member.guild_permissions.manage_messages:
        perms.append("Manage Messages")
    if member.guild_permissions.ban_members:
        perms.append("Ban Members")
    if member.guild_permissions.kick_members:
        perms.append("Kick Members")
    embed.add_field(name="🔑 Key Permissions", value=", ".join(perms) or "None", inline=False)
    embed.set_footer(text=f"Requested by {ctx.author}")
    await ctx.send(embed=embed)

# ---------------- SECURITY COMMANDS ----------------
@bot.command()
async def lockdown(ctx):
    if ctx.author.guild_permissions.administrator:
        config["temp_lockdown"] = "ON"
        await security.toggle_lockdown(ctx.guild, config, enable=True, log_callback=dlog)
        await ctx.send("🚨 Server is now under lockdown! Use `!unlockdown` to lift it.")

@bot.command()
async def unlockdown(ctx):
    if ctx.author.guild_permissions.administrator:
        config["temp_lockdown"] = "OFF"
        await security.toggle_lockdown(ctx.guild, config, enable=False, log_callback=dlog)
        await ctx.send("✅ Lockdown lifted! Server is back to normal.")

@bot.command()
@commands.has_permissions(administrator=True)
async def refresh(ctx):
    refresh_config()
    await ctx.send(f"🔄 Config refreshed! Prefix: `{config.get('prefix', '!')}`")

# ---------------- RUN ----------------
_bot_thread = None

def run_bot():
    if not TOKEN:
        print("⚠️  No DISCORD_TOKEN set. Bot will not start.")
        return
    import time
    retry_delay = 5
    while True:
        print("🤖 Starting Sentinel OS Bot V2.0.3...")
        try:
            bot.run(TOKEN, reconnect=True)
        except discord.errors.LoginFailure:
            print("❌ Invalid DISCORD_TOKEN. Check your DISCORD_TOKEN env var.")
            break
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print(f"🚫 Rate limited. Retrying in 60s...")
                time.sleep(60)
            else:
                print(f"❌ Bot HTTP error: {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
        except Exception as e:
            print(f"❌ Bot crashed: {e}. Retrying in {retry_delay}s...")
            time.sleep(retry_delay)

def start_bot_thread():
    global _bot_thread
    if _bot_thread and _bot_thread.is_alive():
        return False
    _bot_thread = threading.Thread(target=run_bot, daemon=True)
    _bot_thread.start()
    return True

if __name__ == "__main__":
    # Register the restart function so the dashboard can trigger it
    set_bot_restart_callback(start_bot_thread)

    # Run bot in background thread so Flask can bind to PORT immediately
    start_bot_thread()

    # Flask runs in the main thread — binds to PORT (from env) right away
    print(f"📡 Starting Dashboard on port {_PORT}...")
    run_dashboard(_PORT)
