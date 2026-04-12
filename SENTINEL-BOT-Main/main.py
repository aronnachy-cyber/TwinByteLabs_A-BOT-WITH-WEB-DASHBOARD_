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

from dashboard import (
    app, config as dash_config, run_dashboard, add_dashboard_log,
    set_bot_restart_callback, set_bot_config_refresh_callback,
    supabase_save_warning, supabase_clear_warnings,
    supabase_save_mod_log, supabase_save_note, supabase_get_notes,
)
import moderation, automod, roles, logs, social, security
import giveaway as giveaway_mod
import tickets as tickets_mod
import autoresponder as autoresponder_mod
import reaction_roles as rr_mod
import leveling as leveling_mod
import server_template as template_mod

BOT_START_TIME = datetime.utcnow()

# ---------------- CONFIG LOADER ----------------
def load_config():
    """Legacy fallback: read settings.json. Only used if Supabase is unavailable."""
    if not os.path.exists('settings.json'):
        return {}
    with open('settings.json', 'r') as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def refresh_config():
    """Reload config from Supabase (via dashboard module) so bot always has the latest settings."""
    global config
    try:
        import dashboard as _dash
        _dash.load_config_from_supabase()   # pulls fresh data from Supabase into dashboard.config
        config = {**DEFAULT_CONFIG, **_dash.config}
    except Exception as e:
        print(f"refresh_config Supabase error, falling back to settings.json: {e}")
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
    "daily_report": "OFF",
    "leveling": "OFF",
    "ticket_category": "",
    # Advanced automod
    "spam_filter": "OFF",
    "spam_threshold": 5,
    "spam_window": 5,
    "caps_filter": "OFF",
    "caps_percent": 70,
    "emoji_filter": "OFF",
    "emoji_limit": 8,
    "duplicate_filter": "OFF",
    "wall_filter": "OFF",
    "wall_newlines": 10,
    "wall_length": 1000,
    "anti_zalgo": "OFF",
    "grad_punish": "OFF",
}

# Bootstrap from the dashboard's Supabase-backed config (already loaded at import time)
config = {**DEFAULT_CONFIG, **dash_config}

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
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry["history"].append({"reason": reason, "time": ts})
    count = entry["count"]
    save_warnings()
    supabase_save_warning(guild_id, member_id, str(member), count, reason, ts)
    dlog("⚠️ WARNING", f"{member} now has {count} warning(s) — {reason}")
    print(f"⚠️ Warning added to {member}: {count} total | Reason: {reason}")
    await check_warning_limit(member, count)
    return count

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
        f"`{p}modlogs @user` — Full mod log (warnings + notes)\n"
        f"`{p}note @user <note>` — Add a private mod note\n"
        f"`{p}notes @user` — View all mod notes\n"
        f"`{p}timeout @user <dur> [reason]` — Timeout (e.g. `10m`, `2h`, `7d`)\n"
        f"`{p}untimeout @user` — Remove timeout\n"
        f"`{p}kick @user [reason]` — Kick a member\n"
        f"`{p}ban @user [reason]` — Permanent ban\n"
        f"`{p}unban <user_id> [reason]` — Unban by ID\n"
        f"`{p}tempban @user <dur> [reason]` — Temp ban (e.g. `1h`, `2d`, `7d`)\n"
        f"`{p}mute @user [duration]` — Mute a member (e.g. `30m`, `2h`)\n"
        f"`{p}unmute @user` — Unmute a member\n"
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
        "`/bot` — Bot stats & uptime\n"
        "`/dashboard` — Mini admin dashboard (admin only)\n"
        "`/commands` — This list\n"
        "`/serverinfo` — Server statistics\n"
        "`/giveaway <duration> <prize>` — Start a giveaway\n"
        "`/giveaway-end <msg_id>` — End giveaway early\n"
        "`/giveaway-reroll <msg_id>` — Reroll giveaway winner\n"
        "`/poll <question> <opt1> <opt2>` — Create a vote poll\n"
        "`/ticket [reason]` — Open a support ticket\n"
        "`/ticket-close` — Close current ticket\n"
        "`/setup-reaction-role <msg> <emoji> <role>` — Link emoji to role\n"
        "`/remove-reaction-role <msg> <emoji>` — Remove a reaction-role link\n"
        "`/list-reaction-roles` — Show all active reaction roles\n"
        "`/autorespond add <trigger> <response>` — Add auto-reply\n"
        "`/autorespond remove <trigger>` — Remove auto-reply\n"
        "`/autorespond list` — Show all auto-replies\n"
        "`/rank [@user]` — Check XP and level\n"
        "`/leaderboard` — Server XP leaderboard"
    ))
    embed.add_field(name="🗂️ Server Templates", inline=False, value=(
        "`/copy` — Snapshot this server's channels, roles & permissions\n"
        "`/paste` — Recreate a copied template into this server\n"
        "`/delete-template` — Remove everything /paste created (with confirmation)"
    ))
    embed.add_field(name="📬 Owner DM (via DM only)", inline=False, value=(
        f"`!approve ban <id>` / `!approve kick <id>` / `!approve ignore <id>`"
    ))
    embed.set_footer(text="Sentinel OS V2.5.0 • AI-Powered Moderation")
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
    embed.set_footer(text="Sentinel OS V2.0.4")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="bot", description="Show Sentinel OS bot details and status")
async def slash_bot(interaction: discord.Interaction):
    uptime = datetime.utcnow() - BOT_START_TIME
    hours, rem = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    latency_ms = round(bot.latency * 1000)
    guild_count = len(bot.guilds)
    total_members = sum(g.member_count for g in bot.guilds)
    giveaways_active = len([g for g in giveaway_mod.get_all_giveaways() if not g.get("ended")])
    tickets_open = len([t for t in tickets_mod.get_all_tickets() if t.get("status") == "open"])
    ar_count = len(autoresponder_mod.get_all_responses())
    rr_count = len(rr_mod.get_all_rules())
    embed = discord.Embed(
        title="🛡️ Sentinel OS — Bot Details",
        description="AI-Powered Discord Moderation & Management",
        color=0x3a8fff
    )
    embed.add_field(name="⚡ Latency",       value=f"{latency_ms}ms",       inline=True)
    embed.add_field(name="⏱️ Uptime",         value=uptime_str,              inline=True)
    embed.add_field(name="🌐 Servers",        value=str(guild_count),        inline=True)
    embed.add_field(name="👥 Total Members",  value=str(total_members),      inline=True)
    embed.add_field(name="🎉 Active Giveaways", value=str(giveaways_active), inline=True)
    embed.add_field(name="🎫 Open Tickets",   value=str(tickets_open),       inline=True)
    embed.add_field(name="🤖 Auto-Responders", value=str(ar_count),          inline=True)
    embed.add_field(name="🎭 Reaction Roles", value=str(rr_count),           inline=True)
    embed.add_field(name="🔢 Version",        value="V2.5.0",                inline=True)
    embed.set_footer(text="Sentinel OS — Your Server's Guardian")
    embed.timestamp = datetime.utcnow()
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="dashboard", description="Mini admin dashboard (admin only)")
async def slash_dashboard(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ This command is for admins only.", ephemeral=True)
        return
    refresh_config()
    guild = interaction.guild
    total_warnings = sum(
        (v.get("count", 0) if isinstance(v, dict) else v)
        for g in warnings_db.values()
        for v in (g.values() if isinstance(g, dict) else [])
    )
    giveaways_active = len([g for g in giveaway_mod.get_all_giveaways() if not g.get("ended")])
    tickets_open = len([t for t in tickets_mod.get_all_tickets() if t.get("status") == "open"])
    uptime = datetime.utcnow() - BOT_START_TIME
    hours, rem = divmod(int(uptime.total_seconds()), 3600)
    minutes = rem // 60
    embed = discord.Embed(
        title="🖥️ Sentinel OS — Admin Dashboard",
        description=f"Quick overview for **{guild.name}**",
        color=0x3a8fff
    )
    embed.add_field(name="👥 Members",        value=str(guild.member_count),                   inline=True)
    embed.add_field(name="⚠️ Total Warnings", value=str(total_warnings),                       inline=True)
    embed.add_field(name="⏱️ Uptime",          value=f"{hours}h {minutes}m",                    inline=True)
    embed.add_field(name="🎉 Giveaways Active", value=str(giveaways_active),                   inline=True)
    embed.add_field(name="🎫 Open Tickets",    value=str(tickets_open),                         inline=True)
    embed.add_field(name="🔰 Prefix",          value=f"`{config.get('prefix','!')}`",           inline=True)
    embed.add_field(name="🔴 Profanity Filter", value=config.get("profanity_level","low").upper(), inline=True)
    embed.add_field(name="🛡️ Anti-Raid",       value=config.get("anti_raid","OFF"),             inline=True)
    embed.add_field(name="🤖 AI Moderation",   value="ON" if OPENAI_KEY else "OFF",             inline=True)
    embed.set_footer(text="Full dashboard available at your web dashboard URL")
    embed.timestamp = datetime.utcnow()
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="giveaway", description="Start a giveaway (use duration like 30m, 2h, 1d)")
@app_commands.describe(duration="Duration e.g. 30m, 2h, 1d", prize="What are you giving away?", winners="Number of winners (default 1)")
async def slash_giveaway(interaction: discord.Interaction, duration: str, prize: str, winners: int = 1):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    seconds = parse_duration(duration)
    if not seconds:
        await interaction.response.send_message("❌ Invalid duration! Use `30m`, `2h`, `1d`, etc.", ephemeral=True)
        return
    if winners < 1 or winners > 20:
        await interaction.response.send_message("❌ Winners must be between 1 and 20.", ephemeral=True)
        return
    await interaction.response.send_message(f"🎉 Starting giveaway for **{prize}**!", ephemeral=True)
    await giveaway_mod.start_giveaway(bot, interaction.channel, seconds, prize, interaction.user, winners)
    dlog("🎉 GIVEAWAY", f"{interaction.user} started giveaway: {prize} ({duration})")

@bot.tree.command(name="giveaway-end", description="Manually end a giveaway by message ID")
@app_commands.describe(message_id="The giveaway message ID")
async def slash_giveaway_end(interaction: discord.Interaction, message_id: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    try:
        mid = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    result = await giveaway_mod.end_giveaway(bot, mid, interaction.channel_id, force=True)
    if result:
        await interaction.followup.send("✅ Giveaway ended!", ephemeral=True)
    else:
        await interaction.followup.send("❌ Giveaway not found or already ended.", ephemeral=True)

@bot.tree.command(name="giveaway-reroll", description="Reroll a giveaway winner")
@app_commands.describe(message_id="The giveaway message ID")
async def slash_giveaway_reroll(interaction: discord.Interaction, message_id: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    try:
        mid = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    winner = await giveaway_mod.reroll_giveaway(bot, mid, interaction.channel_id)
    if winner:
        await interaction.followup.send(f"🔄 Rerolled! New winner: {winner.mention}", ephemeral=True)
    else:
        await interaction.followup.send("❌ Could not reroll. No eligible entries.", ephemeral=True)

@bot.tree.command(name="poll", description="Create a reaction poll")
@app_commands.describe(question="The poll question", option1="First option", option2="Second option", option3="Third option (optional)", option4="Fourth option (optional)")
async def slash_poll(interaction: discord.Interaction, question: str, option1: str, option2: str, option3: str = None, option4: str = None):
    options = [o for o in [option1, option2, option3, option4] if o]
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    desc = "\n".join(f"{emojis[i]} **{opt}**" for i, opt in enumerate(options))
    embed = discord.Embed(
        title=f"📊 {question}",
        description=desc,
        color=0x3a8fff,
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=f"Poll by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    for i in range(len(options)):
        await msg.add_reaction(emojis[i])
    dlog("📊 POLL", f"{interaction.user} created poll: {question}")

@bot.tree.command(name="ticket", description="Open a support ticket")
@app_commands.describe(reason="Why are you opening this ticket?")
async def slash_ticket(interaction: discord.Interaction, reason: str = "General Support"):
    await interaction.response.defer(ephemeral=True)
    try:
        result = await tickets_mod.create_ticket(interaction.guild, interaction.user, config, reason)
        if result:
            channel, ticket = result
            await interaction.followup.send(f"✅ Your ticket has been created: {channel.mention}", ephemeral=True)
            dlog("🎫 TICKET", f"{interaction.user} opened ticket #{ticket['id']}: {reason}")
        else:
            await interaction.followup.send("❌ Could not create ticket. Make sure I have **Manage Channels** permission and a valid ticket category is set in the dashboard.", ephemeral=True)
    except Exception as e:
        print(f"Ticket slash error: {e}")
        await interaction.followup.send(f"❌ An error occurred while creating your ticket: `{e}`", ephemeral=True)

@bot.tree.command(name="ticket-close", description="Close the current ticket channel")
async def slash_ticket_close(interaction: discord.Interaction):
    ticket_data = next((t for t in tickets_mod.get_all_tickets() if t["channel_id"] == interaction.channel.id), None)
    if not ticket_data:
        await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.manage_messages and interaction.user.id != ticket_data.get("user_id"):
        await interaction.response.send_message("❌ Only staff or the ticket opener can close this.", ephemeral=True)
        return
    await interaction.response.send_message("🔒 Closing ticket...")
    await tickets_mod.close_ticket(interaction.channel, interaction.user)
    dlog("🔒 TICKET-CLOSE", f"{interaction.user} closed ticket #{ticket_data.get('id', '?')}")

@bot.tree.command(name="setup-reaction-role", description="Link an emoji on a message to a role — users get the role when they react")
@app_commands.describe(message_id="ID of the message to watch", emoji="The emoji to react with", role="Role to assign on reaction")
async def slash_setup_rr(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ You need Manage Roles permission.", ephemeral=True)
        return
    try:
        mid = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ That doesn't look like a valid message ID.", ephemeral=True)
        return
    # Verify the message actually exists in this channel
    try:
        await interaction.channel.fetch_message(mid)
    except discord.NotFound:
        await interaction.response.send_message(
            "❌ Message not found in this channel. Make sure you copy the message ID from **this** channel.",
            ephemeral=True
        )
        return
    except Exception:
        pass

    is_new = rr_mod.add_rule(interaction.guild.id, interaction.channel.id, mid, emoji, role.id, role.name)
    action = "linked" if is_new else "updated"
    embed = discord.Embed(
        title="✅ Reaction Role Set",
        description=(
            f"**Emoji:** {emoji}\n"
            f"**Role:** {role.mention}\n"
            f"**Message ID:** `{message_id}`\n\n"
            f"Users who react with {emoji} on that message will automatically receive **{role.name}**. "
            f"Removing the reaction removes the role."
        ),
        color=0x2ecc71
    )
    embed.set_footer(text=f"Sentinel OS • Reaction Roles — {action}")
    await interaction.response.send_message(embed=embed, ephemeral=True)
    dlog("🎭 REACTION-ROLE", f"{interaction.user} {action} {emoji} → {role.name} on msg {message_id}")

@bot.tree.command(name="remove-reaction-role", description="Remove a reaction-role link from a message")
@app_commands.describe(message_id="Message ID the rule is on", emoji="The emoji to unlink")
async def slash_remove_rr(interaction: discord.Interaction, message_id: str, emoji: str):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ You need Manage Roles permission.", ephemeral=True)
        return
    removed = rr_mod.remove_rule(interaction.guild.id, message_id, emoji)
    if removed:
        await interaction.response.send_message(
            f"✅ Removed reaction role: {emoji} on message `{message_id}`.", ephemeral=True
        )
        dlog("🎭 REACTION-ROLE REMOVE", f"{interaction.user} removed {emoji} on msg {message_id}")
    else:
        await interaction.response.send_message(
            f"❌ No rule found for {emoji} on message `{message_id}`.", ephemeral=True
        )

@bot.tree.command(name="list-reaction-roles", description="Show all active reaction roles in this server")
async def slash_list_rr(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ You need Manage Roles permission.", ephemeral=True)
        return
    rules = rr_mod.get_guild_rules(interaction.guild.id)
    if not rules:
        await interaction.response.send_message(
            "No reaction roles set up yet. Use `/setup-reaction-role` to add one.", ephemeral=True
        )
        return
    embed = discord.Embed(
        title=f"🎭 Reaction Roles — {interaction.guild.name}",
        color=0x3a8fff
    )
    lines = []
    for r in rules[:25]:
        mid  = r.get("message_id", "?")
        emo  = r.get("emoji", "?")
        rname = r.get("role_name", "Unknown Role")
        lines.append(f"{emo} → **{rname}** (msg `{mid}`)")
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"{len(rules)} rule(s) active • Use /remove-reaction-role to delete one")
    await interaction.response.send_message(embed=embed, ephemeral=True)

autorespond_group = app_commands.Group(name="autorespond", description="Manage auto-responder rules")

@autorespond_group.command(name="add", description="Add a keyword auto-responder rule")
@app_commands.describe(trigger="Keyword or phrase to trigger on", response="Bot's response (use {user} for mention)", match="Match type: contains, exact, startswith")
async def ar_add(interaction: discord.Interaction, trigger: str, response: str, match: str = "contains"):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ You need Manage Messages permission.", ephemeral=True)
        return
    if match not in ("contains", "exact", "startswith"):
        await interaction.response.send_message("❌ Match type must be: `contains`, `exact`, or `startswith`.", ephemeral=True)
        return
    is_new = autoresponder_mod.add_response(trigger, response, match)
    action = "Added" if is_new else "Updated"
    await interaction.response.send_message(f"✅ {action} auto-responder: `{trigger}` → `{response}` (match: {match})", ephemeral=True)
    dlog("🤖 AUTO-RESPOND", f"{interaction.user} {action.lower()} trigger: {trigger}")

@autorespond_group.command(name="remove", description="Remove an auto-responder rule")
@app_commands.describe(trigger="The trigger keyword to remove")
async def ar_remove(interaction: discord.Interaction, trigger: str):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ You need Manage Messages permission.", ephemeral=True)
        return
    removed = autoresponder_mod.remove_response(trigger)
    if removed:
        await interaction.response.send_message(f"✅ Removed auto-responder for `{trigger}`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ No rule found for `{trigger}`.", ephemeral=True)

@autorespond_group.command(name="list", description="List all auto-responder rules")
async def ar_list(interaction: discord.Interaction):
    rules = autoresponder_mod.get_all_responses()
    if not rules:
        await interaction.response.send_message("No auto-responder rules set.", ephemeral=True)
        return
    desc = "\n".join(f"• `{r['trigger']}` → {r['response'][:40]} ({r.get('match','contains')})" for r in rules[:20])
    embed = discord.Embed(title="🤖 Auto-Responder Rules", description=desc, color=0x3a8fff)
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(autorespond_group)

@bot.tree.command(name="rank", description="Check your XP and level")
@app_commands.describe(member="Member to check (default: yourself)")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    data   = leveling_mod.get_member_xp(interaction.guild.id, target.id)
    total_xp = data.get("xp", 0)
    level, xp_in, xp_needed = leveling_mod.get_xp_progress(total_xp)
    bar = leveling_mod.make_progress_bar(xp_in, xp_needed)

    embed = discord.Embed(
        title=f"⭐ {target.display_name}",
        color=0xf1c40f
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="🏅 Level",    value=f"**{level}**",            inline=True)
    embed.add_field(name="✨ Total XP", value=f"**{total_xp:,}**",       inline=True)
    embed.add_field(name="💬 Messages", value=f"**{data.get('messages', 0):,}**", inline=True)
    embed.add_field(
        name=f"Progress to Level {level + 1}",
        value=f"{bar}\n`{xp_in:,} / {xp_needed:,} XP`",
        inline=False
    )
    embed.set_footer(text=f"Sentinel OS • Leveling System")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Show the XP leaderboard for this server")
async def slash_leaderboard(interaction: discord.Interaction):
    board = leveling_mod.get_leaderboard(interaction.guild.id, 10)
    if not board:
        await interaction.response.send_message("No XP data yet. Start chatting!", ephemeral=True)
        return
    desc = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, data) in enumerate(board):
        medal = medals[i] if i < 3 else f"`#{i+1}`"
        desc += f"{medal} <@{uid}> — **Level {leveling_mod.get_level_from_xp(data.get('xp',0))}** ({data.get('xp',0)} XP)\n"
    embed = discord.Embed(title=f"🏆 {interaction.guild.name} XP Leaderboard", description=desc, color=0xf1c40f)
    await interaction.response.send_message(embed=embed)

# ---------------- EVENTS ----------------
@bot.event
async def on_ready():
    refresh_config()
    # Register persistent views so buttons survive bot restarts
    bot.add_view(tickets_mod.TicketCloseView())
    # Wire up instant config refresh: dashboard save → refresh_config() immediately
    set_bot_config_refresh_callback(refresh_config)
    s_map = {"online": discord.Status.online, "idle": discord.Status.idle, "dnd": discord.Status.dnd}
    await bot.change_presence(status=s_map.get(config.get("bot_status", "online"), discord.Status.online))
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} global slash command(s)")
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
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None:
        return
    await rr_mod.handle_reaction_add(bot, payload)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None:
        return
    await rr_mod.handle_reaction_remove(bot, payload)

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
    handled = await automod.handle_message(message, config, add_warning_fn=add_warning)
    if not handled:
        await handle_ai_moderation(message)
        await autoresponder_mod.check_message(message)
    await leveling_mod.process_message_xp(bot, message, config)
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
async def mute(ctx, member: discord.Member, duration: str = None):
    """Mute a member. Optional duration: 30m, 2h, 1d. Example: !mute @user 30m"""
    await moderation.mute_member(ctx, member, config, duration)
    dlog("🔇 MUTE", f"{member} muted by {ctx.author}" + (f" for {duration}" if duration else ""))

@bot.command()
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    """Unmute a member. Example: !unmute @user"""
    await moderation.unmute_member(ctx, member, config)
    dlog("🔊 UNMUTE", f"{member} unmuted by {ctx.author}")

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
    supabase_clear_warnings(guild_id, member_id)
    dlog("🧹 CLEAR-WARN", f"Warnings cleared for {member} by {ctx.author}")
    await logs.log_event(bot, config, "🧹 Warnings Cleared", f"{member}'s warnings cleared by {ctx.author}", color=0x2ecc71)
    await ctx.send(f"✅ All warnings cleared for {member.mention}.")

@bot.command(name="timeout")
@commands.has_permissions(moderate_members=True)
async def timeout_member(ctx, member: discord.Member, duration: str, *, reason="No reason provided"):
    """Timeout a member. Duration: 30s, 10m, 2h, 7d (max 28d)."""
    seconds = parse_duration(duration)
    if not seconds:
        await ctx.send("❌ Invalid duration! Use e.g. `30m`, `2h`, `1d`. Max 28 days.")
        return
    if seconds > 2419200:
        await ctx.send("❌ Discord timeout limit is 28 days.")
        return
    label = duration_label(seconds)
    try:
        until = discord.utils.utcnow() + timedelta(seconds=seconds)
        await member.timeout(until, reason=reason)
        await ctx.send(f"⏱️ {member.mention} has been timed out for **{label}**. Reason: {reason}")
        await logs.log_event(bot, config, "⏱️ Timeout", f"{member} timed out by {ctx.author} for {label} | Reason: {reason}", color=0xf39c12)
        supabase_save_mod_log(ctx.guild.id, "timeout", str(member), str(ctx.author), f"{label} — {reason}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dlog("⏱️ TIMEOUT", f"{member} timed out for {label} by {ctx.author}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to timeout {member.mention}.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command(name="untimeout")
@commands.has_permissions(moderate_members=True)
async def untimeout_member(ctx, member: discord.Member):
    """Remove a timeout from a member."""
    try:
        await member.timeout(None, reason=f"Timeout removed by {ctx.author}")
        await ctx.send(f"✅ Timeout removed for {member.mention}.")
        await logs.log_event(bot, config, "✅ Timeout Removed", f"{member}'s timeout removed by {ctx.author}", color=0x2ecc71)
        dlog("✅ UNTIMEOUT", f"Timeout removed for {member} by {ctx.author}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to remove {member.mention}'s timeout.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command(name="unban")
@commands.has_permissions(ban_members=True)
async def unban_member(ctx, user_id: int, *, reason="No reason provided"):
    """Unban a user by their ID. Usage: !unban <user_id> [reason]"""
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f"✅ {user} has been unbanned. Reason: {reason}")
        await logs.log_event(bot, config, "✅ Unban", f"{user} unbanned by {ctx.author} | Reason: {reason}", color=0x2ecc71)
        supabase_save_mod_log(ctx.guild.id, "unban", str(user), str(ctx.author), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dlog("✅ UNBAN", f"{user} unbanned by {ctx.author}")
    except discord.NotFound:
        await ctx.send("❌ User not found or not banned.")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to unban users.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command(name="note")
@commands.has_permissions(manage_messages=True)
async def add_note(ctx, member: discord.Member, *, note: str):
    """Add a moderator note to a user. Usage: !note @user <note>"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    supabase_save_note(ctx.guild.id, member.id, str(member), note, str(ctx.author), ts)
    await ctx.send(f"📝 Note added for {member.mention}: *{note}*", delete_after=10)
    dlog("📝 NOTE", f"Note added for {member} by {ctx.author}: {note}")

@bot.command(name="notes")
@commands.has_permissions(manage_messages=True)
async def get_member_notes(ctx, member: discord.Member):
    """View all moderator notes for a user. Usage: !notes @user"""
    notes = supabase_get_notes(ctx.guild.id, member.id)
    embed = discord.Embed(title=f"📝 Mod Notes — {member}", color=0x3a8fff)
    embed.set_thumbnail(url=member.display_avatar.url)
    if not notes:
        embed.description = "No notes on record."
    else:
        for n in notes[-10:]:
            embed.add_field(
                name=f"🕐 {n.get('timestamp', 'Unknown')} by {n.get('moderator', '?')}",
                value=n.get('note', '—'),
                inline=False
            )
    embed.set_footer(text=f"User ID: {member.id} | {len(notes)} total note(s)")
    await ctx.send(embed=embed)

@bot.command(name="modlogs")
@commands.has_permissions(manage_messages=True)
async def modlogs(ctx, member: discord.Member):
    """View warning history + mod notes for a user. Usage: !modlogs @user"""
    guild_id  = str(ctx.guild.id)
    member_id = str(member.id)
    entry   = _get_member_entry(guild_id, member_id)
    count   = entry["count"]
    history = entry["history"]
    notes   = supabase_get_notes(ctx.guild.id, member.id)

    embed = discord.Embed(
        title=f"📋 Mod Logs — {member}",
        color=0xe74c3c if count >= 5 else (0xf39c12 if count >= 2 else 0x2ecc71)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="⚠️ Warnings", value=f"**{count}** / {config.get('warning_limit', 30)}", inline=True)
    embed.add_field(name="📝 Notes",    value=str(len(notes)), inline=True)
    embed.add_field(name="🆔 User ID",  value=f"`{member.id}`", inline=True)

    if history:
        last5 = history[-5:][::-1]
        warn_text = "\n".join(f"`{i+1}.` {h['reason']} — *{h['time']}*" for i, h in enumerate(last5))
        embed.add_field(name="Recent Warnings", value=warn_text, inline=False)

    if notes:
        last3 = notes[-3:]
        note_text = "\n".join(f"*{n.get('note')}* — {n.get('moderator')} @ {n.get('timestamp')}" for n in last3)
        embed.add_field(name="Recent Notes", value=note_text, inline=False)

    embed.set_footer(text=f"Requested by {ctx.author}")
    await ctx.send(embed=embed)

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
        supabase_save_mod_log(ctx.guild.id, "kick", str(member), str(ctx.author), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
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
        supabase_save_mod_log(ctx.guild.id, "ban", str(member), str(ctx.author), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
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

# ---------------- SERVER TEMPLATE COMMANDS ----------------
@bot.tree.command(name="copy", description="Copy this server's full structure (channels, roles, permissions) into a template")
async def slash_copy(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.edit_original_response(content="📸 Scanning server structure…")
        template = await template_mod.copy_server(interaction.guild)
        info = template_mod.get_template_info()
        embed = discord.Embed(
            title="✅ Server Template Saved",
            description=(
                f"**Source:** {interaction.guild.name}\n"
                f"**Copied at:** {info['copied_at'][:19].replace('T', ' ')}\n\n"
                f"Use `/paste` in any server to recreate this structure."
            ),
            color=0x2ecc71
        )
        embed.add_field(name="🎭 Roles",          value=str(info["roles"]),          inline=True)
        embed.add_field(name="📁 Categories",      value=str(info["categories"]),     inline=True)
        embed.add_field(name="💬 Text Channels",   value=str(info["text_channels"]),  inline=True)
        embed.add_field(name="🔊 Voice Channels",  value=str(info["voice_channels"]), inline=True)
        embed.set_footer(text="Sentinel OS — Server Template")
        await interaction.edit_original_response(content=None, embed=embed)
        dlog("📸 COPY", f"{interaction.user} copied server template from {interaction.guild.name}")
    except Exception as e:
        print(f"/copy error: {e}")
        await interaction.edit_original_response(content=f"❌ Failed to copy server: `{e}`")

class DeleteTemplateConfirmView(discord.ui.View):
    """Two-button confirm/cancel for /delete-template."""
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=60)
        self.guild = guild

    @discord.ui.button(label="Yes, delete everything", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Administrators only.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="⏳ Deleting pasted channels, categories, and roles… this may take a minute.",
            view=self
        )
        try:
            async def progress(msg):
                try:
                    await interaction.edit_original_response(content=f"⏳ {msg}")
                except Exception:
                    pass

            summary = await template_mod.delete_pasted(self.guild, progress_callback=progress)

            log = template_mod.load_paste_log(self.guild.id)  # already cleared, use summary
            embed = discord.Embed(
                title="🗑️ Template Deleted",
                description=f"Everything pasted by Sentinel has been removed from **{self.guild.name}**.",
                color=0xe74c3c
            )
            embed.add_field(name="🎭 Roles Deleted",          value=str(summary["roles"]),         inline=True)
            embed.add_field(name="📁 Categories Deleted",     value=str(summary["categories"]),    inline=True)
            embed.add_field(name="💬 Text Channels Deleted",  value=str(summary["text_channels"]), inline=True)
            embed.add_field(name="🔊 Voice Channels Deleted", value=str(summary["voice_channels"]),inline=True)
            if summary["failed"]:
                embed.add_field(name="⚠️ Failed (already gone or no permission)",
                                value=str(summary["failed"]), inline=True)
            embed.set_footer(text="Sentinel OS — Server Template • Paste log cleared")
            await interaction.edit_original_response(content=None, embed=embed, view=None)
            dlog("🗑️ DELETE-TEMPLATE", f"{interaction.user} deleted pasted template from {self.guild.name}")
        except FileNotFoundError:
            await interaction.edit_original_response(
                content="❌ No paste log found — either `/paste` was never run here, or it was already deleted.",
                view=None
            )
        except Exception as e:
            print(f"/delete-template error: {e}")
            await interaction.edit_original_response(content=f"❌ An error occurred: `{e}`", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="✅ Cancelled. Nothing was deleted.", view=self)

@bot.tree.command(name="paste", description="Recreate a copied server template in this server (adds channels & roles)")
async def slash_paste(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
        return
    info = template_mod.get_template_info()
    if not info:
        await interaction.response.send_message(
            "❌ No template found. Run `/copy` in a server first.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(
        content=(
            f"⏳ Pasting template from **{info['source']}**…\n"
            f"Creating {info['roles']} role(s), {info['categories']} categor(ies), "
            f"{info['text_channels']} text and {info['voice_channels']} voice channel(s).\n"
            f"This may take a minute — please wait."
        )
    )
    try:
        async def progress(msg):
            try:
                await interaction.edit_original_response(content=f"⏳ {msg}")
            except Exception:
                pass

        summary = await template_mod.paste_server(interaction.guild, progress_callback=progress)

        embed = discord.Embed(
            title="✅ Server Template Applied",
            description=f"Template from **{info['source']}** has been pasted into **{interaction.guild.name}**.",
            color=0x2ecc71
        )
        embed.add_field(name="🎭 Roles Created",         value=str(summary["roles"]),         inline=True)
        embed.add_field(name="📁 Categories Created",    value=str(summary["categories"]),    inline=True)
        embed.add_field(name="💬 Text Channels Created", value=str(summary["text_channels"]), inline=True)
        embed.add_field(name="🔊 Voice Channels Created",value=str(summary["voice_channels"]),inline=True)
        embed.add_field(name="⏭️ Skipped (already exist)", value=str(summary["skipped"]),    inline=True)
        embed.set_footer(text="Sentinel OS — Server Template • Existing channels/roles were not duplicated")
        await interaction.edit_original_response(content=None, embed=embed)
        dlog("📋 PASTE", f"{interaction.user} pasted server template into {interaction.guild.name}")
    except FileNotFoundError:
        await interaction.edit_original_response(content="❌ No template found. Run `/copy` first.")
    except Exception as e:
        print(f"/paste error: {e}")
        await interaction.edit_original_response(content=f"❌ An error occurred: `{e}`")

@bot.tree.command(name="delete-template", description="Delete all channels, categories and roles that were added by /paste")
async def slash_delete_template(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
        return
    log = template_mod.load_paste_log(interaction.guild.id)
    if not log:
        await interaction.response.send_message(
            "❌ No paste log found for this server. Either `/paste` was never run here, "
            "or the log was already cleared.",
            ephemeral=True
        )
        return
    # Show what will be deleted and ask for confirmation
    pasted_at = log.get("pasted_at", "Unknown")[:19].replace("T", " ")
    embed = discord.Embed(
        title="⚠️ Confirm Deletion",
        description=(
            f"This will **permanently delete** everything Sentinel created with `/paste` "
            f"on **{pasted_at} UTC** from the **{log.get('source','Unknown')}** template.\n\n"
            f"**🎭 Roles:** {len(log.get('role_ids', []))}\n"
            f"**📁 Categories:** {len(log.get('category_ids', []))}\n"
            f"**💬 Text Channels:** {len(log.get('text_channel_ids', []))}\n"
            f"**🔊 Voice Channels:** {len(log.get('voice_channel_ids', []))}\n\n"
            f"⚡ Items that were already manually deleted will be skipped.\n"
            f"This action **cannot be undone**."
        ),
        color=0xe74c3c
    )
    embed.set_footer(text="Sentinel OS — Server Template • You have 60 seconds to decide")
    view = DeleteTemplateConfirmView(interaction.guild)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

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

@bot.command(name="sync")
async def force_sync(ctx):
    """Clear duplicate guild commands and re-sync globally. Admin only."""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ You need Administrator permission to run this.")
        return
    msg = await ctx.send("⏳ Removing duplicates and syncing…")
    try:
        # Wipe any guild-specific commands (these caused the duplicates)
        bot.tree.clear_commands(guild=ctx.guild)
        await bot.tree.sync(guild=ctx.guild)
        # Push the real global command list
        synced = await bot.tree.sync()
        await msg.edit(content=(
            f"✅ Done! Removed guild-specific duplicates and synced **{len(synced)}** global "
            f"command(s). Discord may take up to a minute to refresh — try closing and reopening the slash menu."
        ))
    except Exception as e:
        await msg.edit(content=f"❌ Sync failed: `{e}`")

# ---------------- RUN ----------------
_bot_thread = None

def run_bot():
    if not TOKEN:
        print("⚠️  No DISCORD_TOKEN set. Bot will not start.")
        return
    import time
    retry_delay = 5
    while True:
        print("🤖 Starting Sentinel OS Bot V2.0.4...")
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
