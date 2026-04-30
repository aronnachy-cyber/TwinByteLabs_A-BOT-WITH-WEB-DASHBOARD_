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
    supabase_load_all_warnings,
)
import moderation, automod, roles, logs, social, security
import giveaway as giveaway_mod
import tickets as tickets_mod
import autoresponder as autoresponder_mod
import reaction_roles as rr_mod
import server_template as template_mod

BOT_START_TIME = datetime.utcnow()

# ---------------- CONFIG LOADER ----------------
def load_config():
    dashboard_url = os.environ.get("DASHBOARD_URL", "").strip().rstrip("/")
    if dashboard_url:
        try:
            res = requests.get(f"{dashboard_url}/api/bot_config", timeout=5)
            if res.ok:
                data = res.json()
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"Dashboard config fetch error: {e}")
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
        if _dash.supabase:
            _dash.load_config_from_supabase()
            config = {**DEFAULT_CONFIG, **_dash.config}
        else:
            config = {**DEFAULT_CONFIG, **load_config()}
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
    # Master Automod auto-action toggle (5/10/20/30 escalation chart).
    # OFF preserves the current behaviour exactly.
    "automod_on": "OFF",
}

# Bootstrap from the dashboard's Supabase-backed config (already loaded at import time)
config = {**DEFAULT_CONFIG, **dash_config}

TOKEN         = os.environ.get("DISCORD_TOKEN", "")
GEMINI_KEY    = os.environ.get("GEMINI_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
_PORT         = int(os.environ.get("PORT", 5000))
DASHBOARD_URL = f"http://127.0.0.1:{_PORT}"

intents = discord.Intents.all()
bot = commands.Bot(
    command_prefix=lambda b, m: config.get('prefix', '!') or '!',
    intents=intents,
    help_command=None
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
    """
    Load warnings from Supabase first (source of truth across restarts /
    Render deploys). Fall back to the local JSON cache only when Supabase
    is unavailable, so a wiped container disk never loses warning history.
    """
    try:
        sb_data = supabase_load_all_warnings()
    except Exception as e:
        print(f"[WARN] Supabase warning load failed: {e}")
        sb_data = {}
    if sb_data:
        # Refresh the JSON cache so the next quick read is fast.
        try:
            with open(warnings_file, "w") as f:
                json.dump(sb_data, f, indent=4)
        except Exception:
            pass
        return sb_data
    if os.path.exists(warnings_file):
        with open(warnings_file, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_warnings():
    # JSON acts as a fast in-memory mirror; the authoritative copy lives
    # in Supabase via supabase_save_warning() at every call site.
    # Atomic write: write to .tmp then os.replace so a crash mid-write can
    # never leave a 0-byte / corrupted warnings.json behind.
    tmp = warnings_file + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(warnings_db, f, indent=4)
        os.replace(tmp, warnings_file)
    except Exception as e:
        print(f"[WARN] save_warnings failed: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

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
        f"🔔 **SP4RK — Action Performed**\n"
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
        f"⚠️ **SP4RK — Approval Required**\n"
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
    # Async, non-blocking dashboard ping (was sync `requests.post` which froze
    # the event loop for up to 3s and could cause heartbeat / "Interaction
    # Failed" errors on busy servers).
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=3)
        ) as sess:
            await sess.post(
                f"{DASHBOARD_URL}/api/add_warning",
                json={"member_id": member.id, "username": str(member), "warnings": count},
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
    if not GEMINI_KEY:
        return
    try:
        prompt = (
            "You are a strict Discord moderation classifier for Bangla, Banglish, Hindi/Urdu slang, and English. "
            "Read the whole message and decide if it is abusive slang/profanity or normal conversation. "
            "Flag abusive words, sexual profanity, harassment, hate, threats, and obfuscated spellings such as f*uck, fu ck, s!x, madarchod, maderchud, shala, sala. "
            "Do not flag harmless normal sentences just because they contain friendly teasing or ordinary words. "
            "Return only valid JSON: {\"flagged\": true, \"reason\": \"short reason\"} or {\"flagged\": false, \"reason\": \"safe\"}.\n\n"
            f"Message: {message.content}"
        )
        # Async, non-blocking call (was sync `requests.post` which blocked the
        # event loop for up to 8s per message — caused gateway lag, dropped
        # heartbeats, and "Interaction Failed" replies on busy servers).
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8)
        ) as sess:
            async with sess.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
                },
            ) as res:
                if res.status != 200:
                    print(f"Gemini moderation error: HTTP {res.status}")
                    return
                data = await res.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )
        cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        if "{" in cleaned and "}" in cleaned:
            cleaned = cleaned[cleaned.find("{"):cleaned.rfind("}") + 1]
        try:
            result = json.loads(cleaned)
        except Exception:
            result = {"flagged": '"flagged": true' in text.lower() or "flagged: true" in text.lower(), "reason": text[:120]}
        if result.get("flagged"):
            reason = result.get("reason", "Gemini moderation violation")
            from permissions import safe_delete
            await safe_delete(message)
            await add_warning(message.author, reason=f"Gemini Moderation: {reason}")
            await message.channel.send(
                f"⚠️ {message.author.mention}, your message was removed by AI moderation.",
                delete_after=7
            )
            dlog("🤖 GEMINI-MOD", f"{message.author} flagged — {reason}")
    except Exception as e:
        print(f"Gemini moderation error (ignored): {e}")

# ---------------- BACKGROUND TASKS ----------------
_last_avatar_url = ""
_last_status     = ""

@tasks.loop(seconds=60)
async def sync_bot_settings():
    """Pull dashboard config every minute and update presence/avatar.

    Wrapped in a top-level try so a single bad iteration (network blip,
    Discord API hiccup) can never kill the loop and leave the bot frozen
    on an old presence forever.
    """
    global _last_avatar_url, _last_status
    try:
        refresh_config()
        current_status = config.get("bot_status", "online")
        if current_status != _last_status:
            s_map = {"online": discord.Status.online, "idle": discord.Status.idle, "dnd": discord.Status.dnd}
            try:
                await bot.change_presence(status=s_map.get(current_status, discord.Status.online))
                _last_status = current_status
            except Exception as e:
                print(f"[sync_bot_settings] presence update failed: {e}")
        avatar_url = config.get("bot_avatar", "").strip()
        if avatar_url and avatar_url != _last_avatar_url:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as sess:
                    async with sess.get(avatar_url) as resp:
                        if resp.status == 200:
                            await bot.user.edit(avatar=await resp.read())
                            _last_avatar_url = avatar_url
                            dlog("🖼️ AVATAR", "Bot avatar updated")
            except Exception as e:
                print(f"[sync_bot_settings] avatar update error: {e}")
    except Exception as e:
        # Guard the whole loop — a discord.py tasks.loop dies silently if the
        # body raises, leaving the bot stuck on stale settings forever.
        print(f"[sync_bot_settings] iteration crashed: {e}")

@tasks.loop(hours=24)
async def daily_report_task():
    try:
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
            embed.set_footer(text="SP4RK — Automated Daily Report")
            await channel.send(embed=embed)
            dlog("📊 DAILY-REPORT", "Daily report posted to log channel")
        except Exception as e:
            print(f"Daily report error: {e}")
    except Exception as e:
        print(f"[daily_report_task] iteration crashed: {e}")

# ---------------- SLASH COMMANDS ----------------
def build_help_embed(prefix):
    embed = discord.Embed(
        title="🛡️ SP4RK — Full Command Reference",
        description=(
            f"Current prefix: `{prefix}`  ·  All slash commands also work via Discord's `/` menu.\n"
            "Sections below cover moderation, utilities, custom commands, tickets, giveaways and setup."
        ),
        color=0x4f8ef7
    )
    embed.add_field(name="⚖️ Moderation (prefix)", inline=False, value=(
        f"`{prefix}warn @user [reason]` — warn a member\n"
        f"`{prefix}warnings @user` — show warning history\n"
        f"`{prefix}clearwarnings @user` — clear warnings\n"
        f"`{prefix}kick @user [reason]` — kick a member\n"
        f"`{prefix}ban @user [reason]` — ban a member\n"
        f"`{prefix}unban <user_id>` — unban by ID\n"
        f"`{prefix}tempban @user <duration> [reason]` — temporary ban\n"
        f"`{prefix}timeout @user <duration> [reason]` — Discord timeout\n"
        f"`{prefix}untimeout @user` — remove timeout"
    ))
    embed.add_field(name="🧹 Channel Control (prefix)", inline=False, value=(
        f"`{prefix}purge <1-200>` — bulk delete messages\n"
        f"`{prefix}purgeuser @user <n>` — delete a user's recent messages\n"
        f"`{prefix}slowmode <seconds>` — set slowmode\n"
        f"`{prefix}lock [#channel]` — lock a channel\n"
        f"`{prefix}unlock [#channel]` — unlock a channel\n"
        f"`{prefix}lockdown` — lock all text channels\n"
        f"`{prefix}unlockdown` — unlock all text channels"
    ))
    embed.add_field(name="📌 Logs, Notes & Info (prefix)", inline=False, value=(
        f"`{prefix}note @user <note>` — add private mod note\n"
        f"`{prefix}notes @user` — view mod notes\n"
        f"`{prefix}modlogs @user` — moderation history\n"
        f"`{prefix}userinfo @user` — member info  (alias `!whois`, `!ui`)\n"
        f"`{prefix}serverinfo` — full server snapshot  (alias `!si`)\n"
        f"`{prefix}snipe` — show last deleted message"
    ))
    embed.add_field(name="🛡️ Advanced Mod (prefix)", inline=False, value=(
        f"`{prefix}role add @user @role` — give a role\n"
        f"`{prefix}role remove @user @role` — remove a role\n"
        f"`{prefix}nick @user [name]` — change nickname\n"
        f"`{prefix}voicekick @user` — disconnect from voice  (alias `!vkick`, `!vdc`)\n"
        f"`{prefix}massban id1 id2 ...` — ban many IDs (raid response)"
    ))
    embed.add_field(name="⚡ Slash — Moderation", inline=False, value=(
        "`/warn` `/warnings` `/clearwarnings` `/kick` `/ban` `/unban`\n"
        "`/softban` — ban+unban to wipe recent messages\n"
        "`/timeout` `/untimeout` `/purge`"
    ))
    embed.add_field(name="🔍 Slash — Info", inline=False, value=(
        "`/userinfo @user` — detailed profile card\n"
        "`/serverinfo` — full server snapshot\n"
        "`/avatar @user` — full-size avatar\n"
        "`/banner @user` — profile banner\n"
        "`/roleinfo @role` — role details"
    ))
    embed.add_field(name="🎉 Giveaways & Tickets", inline=False, value=(
        "`/giveaway <duration> <prize>` — start giveaway\n"
        "`/giveaway-end <message_id>` — end giveaway\n"
        "`/giveaway-reroll <message_id>` — reroll winner\n"
        "`/poll <question> <options>` — reaction poll\n"
        "`/ticket [reason]` — open support ticket\n"
        "`/ticket-close` — close ticket"
    ))
    embed.add_field(name="🎭 Reaction Roles", inline=False, value=(
        "`/setup-reaction-role <message_id> <emoji> <role>` — add\n"
        "`/remove-reaction-role <message_id> <emoji>` — remove\n"
        "`/list-reaction-roles` — list all in this server"
    ))
    embed.add_field(name="🗣️ Auto-Responder", inline=False, value=(
        "`/autorespond add <trigger> <response> [match]` — new rule\n"
        "`/autorespond remove <trigger>` — remove rule\n"
        "`/autorespond list` — list all rules"
    ))
    embed.add_field(name="🤖 Custom Commands (AI)", inline=False, value=(
        f"Define `!trigger` commands from the dashboard. The bot reads your "
        f"reference text, runs it through Gemini and posts a polished reply.\n"
        f"Trigger any one with `!<trigger>` in chat."
    ))
    embed.add_field(name="🏷️ Tags (Dyno/Carl-bot style)", inline=False, value=(
        "`/tag show <name>` — show a tag  (or `!tag <name>`)\n"
        "`/tag create <name> <content>` — create a snippet\n"
        "`/tag edit | delete | list | info`"
    ))
    embed.add_field(name="🔔 Quality of Life", inline=False, value=(
        "`/afk [reason]` — bot replies when you're mentioned\n"
        "`/highlight add <keyword>` — DM you when keyword appears\n"
        "`/highlight remove | list | clear`"
    ))
    embed.add_field(name="🧬 Server Templates", inline=False, value=(
        "`/copy` — snapshot this server's structure into a template\n"
        "`/paste <template>` — recreate a copied template here\n"
        "`/delete-template` — remove channels/roles added by `/paste`"
    ))
    embed.add_field(name="🤖 Bot / Setup", inline=False, value=(
        f"`{prefix}refresh` — reload dashboard config\n"
        f"`{prefix}help` — show this menu  (alias `!commands`, `!cmds`)\n"
        "`/commands` — slash version of this menu\n"
        "`/bot` — bot stats\n"
        "`/dashboard` — admin dashboard link"
    ))
    embed.set_footer(text="Tip: change the prefix or manage custom commands in the dashboard at /dashboard.")
    return embed

@bot.tree.command(name="commands", description="List all SP4RK commands")
async def slash_commands(interaction: discord.Interaction):
    refresh_config()
    p = config.get('prefix', '!')
    embed = build_help_embed(p)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# 📋 RICH /serverinfo  (big, informative embed)
# ============================================================
def _build_serverinfo_embed(guild: discord.Guild, requester: discord.abc.User) -> discord.Embed:
    """Build a large, detailed server info embed for SP4RK."""
    # --- Member breakdown -----------------------------------------------
    humans = sum(1 for m in guild.members if not m.bot) if guild.members else 0
    bots   = sum(1 for m in guild.members if m.bot) if guild.members else 0
    online = sum(1 for m in guild.members if m.status == discord.Status.online) if guild.members else 0
    idle   = sum(1 for m in guild.members if m.status == discord.Status.idle) if guild.members else 0
    dnd    = sum(1 for m in guild.members if m.status == discord.Status.dnd) if guild.members else 0
    offline= sum(1 for m in guild.members if m.status == discord.Status.offline) if guild.members else 0

    # --- Channel breakdown ----------------------------------------------
    text_count   = len(guild.text_channels)
    voice_count  = len(guild.voice_channels)
    stage_count  = len(getattr(guild, "stage_channels", []) or [])
    forum_count  = len(getattr(guild, "forums", []) or [])
    category_cnt = len(guild.categories)
    threads_cnt  = len(getattr(guild, "threads", []) or [])

    # --- Boost / verification / features --------------------------------
    boost_tier  = getattr(guild, "premium_tier", 0) or 0
    boost_count = getattr(guild, "premium_subscription_count", 0) or 0
    verif_map = {
        discord.VerificationLevel.none:    "🟢 None",
        discord.VerificationLevel.low:     "🟡 Low",
        discord.VerificationLevel.medium:  "🟠 Medium",
        discord.VerificationLevel.high:    "🔴 High",
        discord.VerificationLevel.highest: "🛑 Highest",
    }
    verification = verif_map.get(guild.verification_level, str(guild.verification_level))

    feature_pretty = {
        "COMMUNITY":         "🌐 Community",
        "PARTNERED":         "🤝 Partnered",
        "VERIFIED":          "✅ Verified",
        "DISCOVERABLE":      "🔭 Discoverable",
        "ANIMATED_ICON":     "🎞️ Animated Icon",
        "BANNER":            "🖼️ Banner",
        "VANITY_URL":        "🔗 Vanity URL",
        "INVITE_SPLASH":     "💦 Invite Splash",
        "VIP_REGIONS":       "🌟 VIP Voice",
        "WELCOME_SCREEN_ENABLED": "👋 Welcome Screen",
        "MEMBER_VERIFICATION_GATE_ENABLED": "🛡️ Membership Gate",
        "NEWS":              "📰 News Channels",
        "THREADS_ENABLED":   "🧵 Threads",
    }
    features = [feature_pretty.get(f, f"✨ {f.replace('_',' ').title()}") for f in (guild.features or [])]
    features_str = " · ".join(features[:8]) if features else "—"

    # --- Build embed ----------------------------------------------------
    embed = discord.Embed(
        title=f"📋 {guild.name}",
        description=(
            f"_{guild.description}_\n\n" if getattr(guild, "description", None) else ""
        ) + f"🆔 Server ID: `{guild.id}`",
        color=0x4f8ef7,
        timestamp=datetime.utcnow(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    if getattr(guild, "banner", None):
        try:
            embed.set_image(url=guild.banner.url)
        except Exception:
            pass

    # 👑 Owner
    owner_str = f"{guild.owner.mention}\n`{guild.owner}`" if guild.owner else "Unknown"
    embed.add_field(name="👑 Owner", value=owner_str, inline=True)

    # 📅 Created
    embed.add_field(
        name="📅 Created",
        value=f"<t:{int(guild.created_at.timestamp())}:D>\n<t:{int(guild.created_at.timestamp())}:R>",
        inline=True,
    )

    # 🌍 Region info — Discord removed per-server region but show preferred locale
    embed.add_field(
        name="🌍 Locale",
        value=f"`{getattr(guild, 'preferred_locale', 'en-US')}`",
        inline=True,
    )

    # 👥 Members (humans / bots)
    embed.add_field(
        name=f"👥 Members — {guild.member_count:,}",
        value=f"👤 Humans: **{humans:,}**\n🤖 Bots: **{bots:,}**",
        inline=True,
    )

    # 📶 Presence
    embed.add_field(
        name="📶 Presence",
        value=(
            f"🟢 Online: **{online:,}**\n"
            f"🟡 Idle: **{idle:,}**\n"
            f"🔴 DND: **{dnd:,}**\n"
            f"⚫ Offline: **{offline:,}**"
        ),
        inline=True,
    )

    # 🚀 Boosts
    embed.add_field(
        name="🚀 Boosts",
        value=f"Tier **{boost_tier}** · {boost_count} boost{'s' if boost_count != 1 else ''}",
        inline=True,
    )

    # 💬 Channels
    embed.add_field(
        name=f"💬 Channels — {text_count + voice_count + stage_count + forum_count}",
        value=(
            f"📁 Categories: **{category_cnt}**\n"
            f"💬 Text: **{text_count}**  ·  🔊 Voice: **{voice_count}**\n"
            f"🎙️ Stage: **{stage_count}**  ·  📋 Forum: **{forum_count}**\n"
            f"🧵 Active Threads: **{threads_cnt}**"
        ),
        inline=False,
    )

    # 🎭 Roles
    role_count = len(guild.roles) - 1   # exclude @everyone
    top_roles = [r.mention for r in sorted(guild.roles, key=lambda r: r.position, reverse=True)
                 if r.name != "@everyone"][:6]
    embed.add_field(
        name=f"🎭 Roles — {role_count}",
        value=" ".join(top_roles) if top_roles else "—",
        inline=False,
    )

    # 🛡️ Security
    embed.add_field(
        name="🛡️ Verification",
        value=verification,
        inline=True,
    )
    embed.add_field(
        name="🔞 Content Filter",
        value=str(guild.explicit_content_filter).replace("_", " ").title(),
        inline=True,
    )
    embed.add_field(
        name="📤 Default Notify",
        value=str(guild.default_notifications).replace("_", " ").title(),
        inline=True,
    )

    # ✨ Features
    if features:
        embed.add_field(name=f"✨ Features ({len(features)})", value=features_str, inline=False)

    try:
        icon_url = requester.display_avatar.url
    except Exception:
        icon_url = None
    if icon_url:
        embed.set_footer(text=f"Requested by {requester} • SP4RK", icon_url=icon_url)
    else:
        embed.set_footer(text=f"Requested by {requester} • SP4RK")
    return embed


@bot.tree.command(name="serverinfo", description="Show detailed server information")
async def slash_serverinfo(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    embed = _build_serverinfo_embed(interaction.guild, interaction.user)
    await interaction.response.send_message(embed=embed)


@bot.command(name="serverinfo", aliases=["si"])
async def prefix_serverinfo(ctx):
    """!serverinfo — detailed server snapshot."""
    if not ctx.guild:
        return await ctx.send("Use this in a server.")
    await ctx.send(embed=_build_serverinfo_embed(ctx.guild, ctx.author))

@bot.command(name="help", aliases=["commands", "cmds"])
async def prefix_help(ctx):
    refresh_config()
    await ctx.send(embed=build_help_embed(config.get("prefix", "!")))

@bot.tree.command(name="bot", description="Show SP4RK bot details and status")
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
        title="🛡️ SP4RK — Bot Details",
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
    embed.set_footer(text="SP4RK — Your Server's Guardian")
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
        title="🖥️ SP4RK — Admin Dashboard",
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
    embed.add_field(name="🤖 AI Moderation",   value="ON" if GEMINI_KEY else "OFF",             inline=True)
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
    embed.set_footer(text=f"SP4RK • Reaction Roles — {action}")
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
    # Re-schedule any giveaways that were active when the bot was last restarted.
    # Without this, giveaway timers are lost on every restart and "active" giveaways
    # never roll over into "completed".
    try:
        await giveaway_mod.resume_pending(bot)
    except Exception as e:
        print(f"[GIVEAWAY] resume_pending error: {e}")
    dlog("✅ BOT-START", f"SP4RK online as {bot.user}")
    print(f"✅ SP4RK Online! Logged in as {bot.user}")

@bot.event
async def on_member_join(member):
    refresh_config()
    await roles.assign_auto_role(member, config)
    await moderation.handle_auto_role(member, config)
    await security.anti_raid_check(member, config, dm_callback=dm_all_owners, log_callback=dlog)
    await social.send_welcome_message(bot, member, config)
    await logs.log_template(bot, config, "sp4rk.welcome", user=member, server=member.guild)
    dlog("👋 JOIN", f"{member} joined {member.guild.name}")

@bot.event
async def on_member_update(before, after):
    refresh_config()
    await security.role_monitor_log(bot, config, after, before, after, log_callback=dlog)

@bot.event
async def on_member_ban(guild, user):
    refresh_config()
    await logs.log_template(bot, config, "sp4rk.member_banned", user=user, server=guild)
    dlog("⛔ BAN", f"{user} banned from {guild.name}")

@bot.event
async def on_member_remove(member):
    refresh_config()
    await logs.log_template(bot, config, "sp4rk.leave", user=member, server=member.guild)
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
    await logs.log_template(
        bot, config, "sp4rk.message_edit",
        user=before.author, channel=before.channel,
        before=(before.content[:1000] or "*empty*"),
        after=(after.content[:1000] or "*empty*"),
        jump_url=after.jump_url,
    )
    dlog("✏️ MSG-EDIT", f"{before.author} edited a message in #{before.channel.name}")

# Per-channel snipe cache: { channel_id: {"author": str, "content": str, "ts": datetime} }
_snipe_cache = {}

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    # Cache for !snipe
    try:
        _snipe_cache[message.channel.id] = {
            "author": str(message.author),
            "author_id": message.author.id,
            "content": message.content or "(no text — likely embed/attachment)",
            "ts": datetime.utcnow(),
        }
    except Exception:
        pass
    refresh_config()
    log_ch = config.get("log_channel", "").strip()
    if not log_ch:
        return
    await logs.log_template(
        bot, config, "sp4rk.message_delete",
        user=message.author, channel=message.channel,
        content=(message.content[:1000] or "*empty / attachment*"),
    )
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
        if before.channel is None:
            await logs.log_template(bot, config, "sp4rk.voice_join",
                                    user=member, channel=after.channel)
            dlog("🔊 VC-JOIN", f"{member} joined VC: {after.channel.name}")
        elif after.channel is None:
            await logs.log_template(bot, config, "sp4rk.voice_leave",
                                    user=member, channel=before.channel)
            dlog("🔇 VC-LEAVE", f"{member} left VC: {before.channel.name}")
        else:
            # Moved VC — reuse leave template with combined channel string
            move_chan = f"**{before.channel.name}** → **{after.channel.name}**"
            await logs.log_template(bot, config, "sp4rk.voice_leave",
                                    user=member, channel=move_chan)
            dlog("🔀 VC-MOVE", f"{member} moved: {before.channel.name} → {after.channel.name}")
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

    # ---- Custom Commands (! prefix, AI-formatted reply) ----
    try:
        import custom_commands as _cc_mod
        gid = str(message.guild.id) if message.guild else None
        cmd = _cc_mod.match_message("sp4rk", gid, message.content or "")
        if cmd:
            polished = None
            if _cc_mod.cooldown_ok(message.channel.id):
                polished = _cc_mod.ai_format_response(
                    cmd.get("description", ""),
                    cmd.get("result", ""),
                    message.content or "",
                )
            reply_text = polished or cmd.get("result", "")
            if cmd.get("use_embed"):
                e = discord.Embed(
                    title=f"!{cmd['trigger']}",
                    description=reply_text[:4000],
                    color=0x3a8fff,
                )
                if cmd.get("description"):
                    e.set_footer(text=cmd["description"][:200])
                await message.channel.send(embed=e)
            else:
                await message.channel.send(reply_text[:2000])
    except Exception as _cc_err:
        print(f"[SP4RK] custom command failed: {_cc_err}")

    handled = await automod.handle_message(message, config, add_warning_fn=add_warning)
    if not handled:
        await handle_ai_moderation(message)
        await autoresponder_mod.check_message(message)
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
def _hierarchy_block(ctx, member, action_word):
    """Return error message string if SP4RK can't action this member, else None."""
    allowed, why = automod.can_bot_action(member, ctx.guild.me)
    if not allowed:
        return f"❌ Cannot {action_word} {member.mention} — {why}."
    # Also check the invoking user's role isn't lower than the target's
    if ctx.author.id != ctx.guild.owner_id and member.top_role >= ctx.author.top_role:
        return f"❌ You cannot {action_word} {member.mention} — their role is equal to or higher than yours."
    return None

@bot.command()
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member, duration: str = None):
    """Mute a member. Optional duration: 30m, 2h, 1d. Example: !mute @user 30m"""
    block = _hierarchy_block(ctx, member, "mute")
    if block:
        await ctx.send(block)
        return
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
    block = _hierarchy_block(ctx, member, "warn")
    if block:
        await ctx.send(block)
        return
    await add_warning(member, reason)
    entry = _get_member_entry(str(ctx.guild.id), str(member.id))
    count = entry["count"]
    await ctx.send(f"⚠️ {member.mention} has been warned! Now at **{count}** warning(s).\nReason: {reason}")
    await logs.log_template(bot, config, "sp4rk.warn",
                            user=member, moderator=ctx.author,
                            reason=reason, warn_count=count)

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
    block = _hierarchy_block(ctx, member, "timeout")
    if block:
        await ctx.send(block)
        return
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
        await logs.log_template(bot, config, "sp4rk.timeout",
                                user=member, moderator=ctx.author,
                                duration=label, reason=reason)
        supabase_save_mod_log(ctx.guild.id, "timeout", str(member), str(ctx.author), f"{label} — {reason}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dlog("⏱️ TIMEOUT", f"{member} timed out for {label} by {ctx.author}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to timeout {member.mention} (check role hierarchy or `Moderate Members` permission).")
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
        await logs.log_template(bot, config, "sp4rk.unban",
                                user=user, moderator=ctx.author, reason=reason)
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
    block = _hierarchy_block(ctx, member, "kick")
    if block:
        await ctx.send(block)
        return
    if not ctx.guild.me.guild_permissions.kick_members:
        await ctx.send("❌ I'm missing the **Kick Members** permission. Add it to my role in Server Settings → Roles.")
        return
    allowed = await security.mass_action_prevent(ctx, "kick", config, log_callback=dlog)
    if not allowed:
        return
    try:
        await member.kick(reason=reason)
        await ctx.send(f"🚪 {member.mention} has been kicked! Reason: {reason}")
        await logs.log_template(bot, config, "sp4rk.kick",
                                user=member, moderator=ctx.author, reason=reason)
        await dm_owner("kick", str(member), reason, str(ctx.author))
        supabase_save_mod_log(ctx.guild.id, "kick", str(member), str(ctx.author), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dlog("🚪 KICK", f"{member} kicked by {ctx.author} — {reason}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to kick {member.mention} (role hierarchy or missing perms).")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    block = _hierarchy_block(ctx, member, "ban")
    if block:
        await ctx.send(block)
        return
    if not ctx.guild.me.guild_permissions.ban_members:
        await ctx.send("❌ I'm missing the **Ban Members** permission. Add it to my role in Server Settings → Roles.")
        return
    allowed = await security.mass_action_prevent(ctx, "ban", config, log_callback=dlog)
    if not allowed:
        return
    try:
        await member.ban(reason=reason)
        await ctx.send(f"⛔ {member.mention} has been permanently banned! Reason: {reason}")
        await logs.log_template(bot, config, "sp4rk.ban",
                                user=member, moderator=ctx.author, reason=reason)
        await dm_owner("ban", str(member), reason, str(ctx.author))
        supabase_save_mod_log(ctx.guild.id, "ban", str(member), str(ctx.author), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dlog("⛔ BAN", f"{member} banned by {ctx.author} — {reason}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to ban {member.mention} (role hierarchy or missing perms).")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def tempban(ctx, member: discord.Member, duration: str, *, reason="No reason provided"):
    """Temp-ban a member. Duration: 30m, 2h, 1d, 7d, etc."""
    block = _hierarchy_block(ctx, member, "temp-ban")
    if block:
        await ctx.send(block)
        return
    if not ctx.guild.me.guild_permissions.ban_members:
        await ctx.send("❌ I'm missing the **Ban Members** permission. Add it to my role in Server Settings → Roles.")
        return
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

@bot.command(name="purgeuser")
@commands.has_permissions(manage_messages=True)
async def purge_user(ctx, member: discord.Member, amount: int = 50):
    """Delete up to <amount> recent messages from a specific user. Usage: !purgeuser @user 50"""
    if amount < 1 or amount > 500:
        await ctx.send("❌ Amount must be between 1 and 500.", delete_after=5)
        return
    try:
        deleted = await ctx.channel.purge(limit=amount, check=lambda m: m.author.id == member.id)
        msg = await ctx.send(f"🧹 Deleted **{len(deleted)}** message(s) from {member.mention}.")
        await asyncio.sleep(5)
        try: await msg.delete()
        except: pass
        await logs.log_event(bot, config, "🧹 Purge User",
            f"{ctx.author} purged {len(deleted)} messages from {member} in #{ctx.channel.name}",
            color=0x9b59b6)
        dlog("🧹 PURGEUSER", f"{ctx.author} purged {len(deleted)} from {member} in #{ctx.channel.name}")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to delete messages here.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def lock(ctx, channel: discord.TextChannel = None):
    """Lock a single channel (revoke @everyone send). Usage: !lock [#channel]"""
    channel = channel or ctx.channel
    try:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite,
                                       reason=f"Locked by {ctx.author}")
        await ctx.send(f"🔒 {channel.mention} has been locked.")
        await logs.log_event(bot, config, "🔒 Channel Locked",
            f"#{channel.name} locked by {ctx.author}", color=0xe67e22)
        dlog("🔒 LOCK", f"#{channel.name} locked by {ctx.author}")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to edit that channel.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx, channel: discord.TextChannel = None):
    """Unlock a single channel. Usage: !unlock [#channel]"""
    channel = channel or ctx.channel
    try:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite,
                                       reason=f"Unlocked by {ctx.author}")
        await ctx.send(f"🔓 {channel.mention} has been unlocked.")
        await logs.log_event(bot, config, "🔓 Channel Unlocked",
            f"#{channel.name} unlocked by {ctx.author}", color=0x2ecc71)
        dlog("🔓 UNLOCK", f"#{channel.name} unlocked by {ctx.author}")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to edit that channel.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.group(name="role", invoke_without_command=True)
@commands.has_permissions(manage_roles=True)
async def role_group(ctx):
    """Role management. Usage: !role add @user @role  |  !role remove @user @role"""
    await ctx.send(f"Usage: `{config.get('prefix','!')}role add @user @role` or `{config.get('prefix','!')}role remove @user @role`")

@role_group.command(name="add")
@commands.has_permissions(manage_roles=True)
async def role_add(ctx, member: discord.Member, role: discord.Role):
    if role >= ctx.guild.me.top_role:
        await ctx.send(f"❌ I can't manage **{role.name}** — it's higher than my top role.")
        return
    if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
        await ctx.send(f"❌ You can't assign **{role.name}** — it's equal to or higher than your top role.")
        return
    if role in member.roles:
        await ctx.send(f"ℹ️ {member.mention} already has **{role.name}**.")
        return
    try:
        await member.add_roles(role, reason=f"Added by {ctx.author}")
        await ctx.send(f"✅ Gave **{role.name}** to {member.mention}.")
        await logs.log_event(bot, config, "➕ Role Added",
            f"{ctx.author} gave @{role.name} to {member}", color=0x2ecc71)
        dlog("➕ ROLE-ADD", f"{ctx.author} gave @{role.name} to {member}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to assign that role.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@role_group.command(name="remove")
@commands.has_permissions(manage_roles=True)
async def role_remove(ctx, member: discord.Member, role: discord.Role):
    if role >= ctx.guild.me.top_role:
        await ctx.send(f"❌ I can't manage **{role.name}** — it's higher than my top role.")
        return
    if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
        await ctx.send(f"❌ You can't remove **{role.name}** — it's equal to or higher than your top role.")
        return
    if role not in member.roles:
        await ctx.send(f"ℹ️ {member.mention} doesn't have **{role.name}**.")
        return
    try:
        await member.remove_roles(role, reason=f"Removed by {ctx.author}")
        await ctx.send(f"✅ Removed **{role.name}** from {member.mention}.")
        await logs.log_event(bot, config, "➖ Role Removed",
            f"{ctx.author} removed @{role.name} from {member}", color=0xe67e22)
        dlog("➖ ROLE-REM", f"{ctx.author} removed @{role.name} from {member}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to remove that role.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command(name="nick")
@commands.has_permissions(manage_nicknames=True)
async def nick_cmd(ctx, member: discord.Member, *, name: str = None):
    """Change a member's nickname. Usage: !nick @user New Name  (omit name to reset)"""
    block = _hierarchy_block(ctx, member, "rename")
    if block:
        await ctx.send(block)
        return
    try:
        await member.edit(nick=name, reason=f"Renamed by {ctx.author}")
        if name:
            await ctx.send(f"✅ Renamed {member.mention} to **{name}**.")
        else:
            await ctx.send(f"✅ Reset {member.mention}'s nickname.")
        dlog("📝 NICK", f"{ctx.author} renamed {member} to {name or '(reset)'}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to rename {member.mention}.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command(name="voicekick", aliases=["vkick", "vdc"])
@commands.has_permissions(move_members=True)
async def voicekick(ctx, member: discord.Member):
    """Disconnect a member from voice. Usage: !voicekick @user"""
    if not member.voice or not member.voice.channel:
        await ctx.send(f"ℹ️ {member.mention} isn't in a voice channel.")
        return
    block = _hierarchy_block(ctx, member, "voice-kick")
    if block:
        await ctx.send(block)
        return
    try:
        ch_name = member.voice.channel.name
        await member.move_to(None, reason=f"Voice kick by {ctx.author}")
        await ctx.send(f"🔌 Disconnected {member.mention} from **{ch_name}**.")
        await logs.log_event(bot, config, "🔌 Voice Kick",
            f"{ctx.author} disconnected {member} from #{ch_name}", color=0xf39c12)
        dlog("🔌 VKICK", f"{ctx.author} disconnected {member} from {ch_name}")
    except discord.Forbidden:
        await ctx.send(f"❌ I don't have permission to move that member.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command(name="snipe")
@commands.has_permissions(manage_messages=True)
async def snipe_cmd(ctx):
    """Show the last deleted message in this channel."""
    snipe = _snipe_cache.get(ctx.channel.id)
    if not snipe:
        await ctx.send("ℹ️ Nothing to snipe in this channel.")
        return
    embed = discord.Embed(
        description=snipe["content"][:2000],
        color=0x95a5a6,
        timestamp=snipe["ts"]
    )
    embed.set_author(name=snipe["author"])
    embed.set_footer(text="Deleted message")
    await ctx.send(embed=embed)

@bot.command(name="massban")
@commands.has_permissions(administrator=True)
async def massban(ctx, *user_ids: int):
    """Ban many user IDs at once (raid response). Usage: !massban 123 456 789"""
    if not user_ids:
        await ctx.send("❌ Provide one or more user IDs. Usage: `!massban 123 456 789`")
        return
    if len(user_ids) > 50:
        await ctx.send("❌ Max 50 IDs per command for safety.")
        return
    if not ctx.guild.me.guild_permissions.ban_members:
        await ctx.send("❌ I'm missing the **Ban Members** permission.")
        return
    allowed = await security.mass_action_prevent(ctx, "ban", config, log_callback=dlog)
    if not allowed:
        return
    confirm = await ctx.send(f"⚠️ About to ban **{len(user_ids)}** users. React ✅ within 15s to confirm.")
    await confirm.add_reaction("✅")
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) == "✅" and reaction.message.id == confirm.id
    try:
        await bot.wait_for("reaction_add", timeout=15.0, check=check)
    except asyncio.TimeoutError:
        await ctx.send("❌ Mass-ban cancelled (no confirmation).")
        return
    success, fail = 0, 0
    for uid in user_ids:
        try:
            await ctx.guild.ban(discord.Object(id=uid), reason=f"Mass-ban by {ctx.author}")
            success += 1
        except Exception:
            fail += 1
    await ctx.send(f"⛔ Mass-ban complete: **{success}** banned, **{fail}** failed.")
    await logs.log_event(bot, config, "⛔ Mass Ban",
        f"{ctx.author} mass-banned {success} users ({fail} failed)", color=0xe74c3c)
    dlog("⛔ MASSBAN", f"{ctx.author} mass-banned {success}/{len(user_ids)}")

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

# ============================================================
# 👤 RICH userinfo  (big, informative embed — shared by ! and /)
# ============================================================
def _build_userinfo_embed(member: discord.Member, requester: discord.abc.User) -> discord.Embed:
    """Build a large, detailed user info embed for SP4RK."""
    guild = member.guild
    guild_id  = str(guild.id)
    member_id = str(member.id)

    # Warning count
    try:
        entry = _get_member_entry(guild_id, member_id)
        warn_count = entry.get("count", 0)
    except Exception:
        warn_count = 0
    warn_limit = config.get('warning_limit', 30)

    # Roles
    roles_sorted = sorted(
        [r for r in member.roles if r.name != "@everyone"],
        key=lambda r: r.position, reverse=True,
    )
    role_count   = len(roles_sorted)
    roles_str    = " ".join(r.mention for r in roles_sorted[:12]) if roles_sorted else "_No roles_"
    if role_count > 12:
        roles_str += f"  …  **+{role_count - 12} more**"
    top_role     = roles_sorted[0].mention if roles_sorted else "_None_"

    # Member position (e.g. 5th to join the server)
    join_position = "—"
    if guild.members and member.joined_at:
        sorted_joined = sorted(
            (m for m in guild.members if m.joined_at),
            key=lambda m: m.joined_at,
        )
        try:
            join_position = f"#{sorted_joined.index(member) + 1:,} of {guild.member_count:,}"
        except ValueError:
            join_position = "—"

    # Status / activity
    status_emoji = {
        discord.Status.online:  "🟢 Online",
        discord.Status.idle:    "🟡 Idle",
        discord.Status.dnd:     "🔴 Do Not Disturb",
        discord.Status.offline: "⚫ Offline",
    }
    status_str = status_emoji.get(member.status, "❔ Unknown")
    if any(isinstance(a, discord.Activity) and a.type == discord.ActivityType.streaming for a in member.activities):
        status_str = "🟣 Streaming"

    # Top activity
    activity_str = "—"
    for act in (member.activities or []):
        if isinstance(act, discord.CustomActivity) and act.name:
            activity_str = f"💭 {act.name}"
            break
        if isinstance(act, discord.Spotify):
            activity_str = f"🎵 Listening to **{act.title}** by {', '.join(act.artists)}"
            break
        if isinstance(act, discord.Game):
            activity_str = f"🎮 Playing **{act.name}**"
            break
        if isinstance(act, discord.Streaming):
            activity_str = f"📡 Streaming **{act.name}**"
            break
        if isinstance(act, discord.Activity):
            verb = {discord.ActivityType.playing: "Playing", discord.ActivityType.watching: "Watching",
                    discord.ActivityType.listening: "Listening to", discord.ActivityType.competing: "Competing in"}.get(act.type, "Doing")
            activity_str = f"🎯 {verb} **{act.name}**"
            break

    # Acknowledgements / badges — guard against User (not Member) which has no guild_permissions
    is_member = isinstance(member, discord.Member)
    acks = []
    if is_member and guild.owner_id == member.id:
        acks.append("👑 Server Owner")
    if is_member and member.guild_permissions.administrator:
        acks.append("🛡️ Administrator")
    elif is_member and member.guild_permissions.manage_guild:
        acks.append("⚙️ Manage Server")
    elif is_member and (member.guild_permissions.manage_messages or member.guild_permissions.kick_members):
        acks.append("🔧 Moderator")
    if member.bot:
        acks.append("🤖 Bot")
    if getattr(member, "premium_since", None):
        acks.append("🚀 Server Booster")
    ack_str = " · ".join(acks) if acks else "_Member_"

    # Key permissions (compact) — only available on Member objects
    perms = []
    gp = member.guild_permissions if is_member else None
    perm_map = [
        (gp.administrator,    "Administrator"),
        (gp.manage_guild,     "Manage Server"),
        (gp.manage_channels,  "Manage Channels"),
        (gp.manage_roles,     "Manage Roles"),
        (gp.manage_messages,  "Manage Messages"),
        (gp.ban_members,      "Ban Members"),
        (gp.kick_members,     "Kick Members"),
        (gp.mention_everyone, "Mention Everyone"),
    ] if gp is not None else []
    for has, label in perm_map:
        if has:
            perms.append(label)
    perms_str = ", ".join(perms) if perms else "_None_"

    # Embed color from top role, fallback to brand blue
    member_color = getattr(member, "color", None)
    color = member_color if (member_color and member_color != discord.Color.default()) else discord.Color(0x4f8ef7)

    embed = discord.Embed(
        title=f"👤 {member.display_name}",
        description=(
            f"**{member}**  ·  {ack_str}\n"
            f"🆔 `{member.id}`"
        ),
        color=color,
        timestamp=datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    # Banner if available (requires fetch — best effort, skip if not pre-loaded)
    banner = getattr(member, "banner", None)
    if banner:
        try:
            embed.set_image(url=banner.url)
        except Exception:
            pass

    # 📅 Account Created
    embed.add_field(
        name="📅 Account Created",
        value=f"<t:{int(member.created_at.timestamp())}:D>\n<t:{int(member.created_at.timestamp())}:R>",
        inline=True,
    )
    # 📥 Joined Server
    if member.joined_at:
        embed.add_field(
            name="📥 Joined Server",
            value=f"<t:{int(member.joined_at.timestamp())}:D>\n<t:{int(member.joined_at.timestamp())}:R>",
            inline=True,
        )
    else:
        embed.add_field(name="📥 Joined Server", value="Unknown", inline=True)

    # 🏷️ Join order
    embed.add_field(name="🏷️ Join Position", value=join_position, inline=True)

    # 📶 Status & 🎯 Activity
    embed.add_field(name="📶 Status",   value=status_str,   inline=True)
    embed.add_field(name="🎯 Activity", value=activity_str, inline=True)

    # ⭐ Top role
    embed.add_field(name="⭐ Top Role", value=top_role, inline=True)

    # 🚀 Boosting since
    if getattr(member, "premium_since", None):
        embed.add_field(
            name="🚀 Boosting Since",
            value=f"<t:{int(member.premium_since.timestamp())}:R>",
            inline=False,
        )

    # 🎭 Roles
    embed.add_field(name=f"🎭 Roles — {role_count}", value=roles_str[:1024], inline=False)

    # 🔑 Permissions
    embed.add_field(name="🔑 Key Permissions", value=perms_str, inline=False)

    # ⚠️ Warnings (with progress bar)
    bar_len = 12
    pct = min(1.0, warn_count / max(1, warn_limit))
    filled = int(bar_len * pct)
    bar = "▰" * filled + "▱" * (bar_len - filled)
    bar_emoji = "🟩" if warn_count == 0 else ("🟧" if pct < 0.5 else "🟥")
    embed.add_field(
        name="⚠️ Warnings",
        value=f"{bar_emoji} `{bar}`  **{warn_count}** / {warn_limit}",
        inline=False,
    )

    # Footer
    try:
        icon_url = requester.display_avatar.url
        embed.set_footer(text=f"Requested by {requester} • SP4RK", icon_url=icon_url)
    except Exception:
        embed.set_footer(text=f"Requested by {requester} • SP4RK")
    return embed


@bot.command(name="userinfo", aliases=["whois", "ui"])
@commands.has_permissions(manage_messages=True)
async def userinfo(ctx, member: discord.Member = None):
    """!userinfo [@member] — detailed profile card for a member."""
    member = member or ctx.author
    await ctx.send(embed=_build_userinfo_embed(member, ctx.author))


@bot.tree.command(name="userinfo", description="Show a detailed profile card for a member")
@app_commands.describe(member="The member to inspect (defaults to yourself)")
async def slash_userinfo(interaction: discord.Interaction,
                         member: discord.Member = None):
    if not interaction.guild:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    target = member or interaction.user
    await interaction.response.send_message(
        embed=_build_userinfo_embed(target, interaction.user)
    )

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
        embed.set_footer(text="SP4RK — Server Template")
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
                description=f"Everything pasted by SP4RK has been removed from **{self.guild.name}**.",
                color=0xe74c3c
            )
            embed.add_field(name="🎭 Roles Deleted",          value=str(summary["roles"]),         inline=True)
            embed.add_field(name="📁 Categories Deleted",     value=str(summary["categories"]),    inline=True)
            embed.add_field(name="💬 Text Channels Deleted",  value=str(summary["text_channels"]), inline=True)
            embed.add_field(name="🔊 Voice Channels Deleted", value=str(summary["voice_channels"]),inline=True)
            if summary["failed"]:
                embed.add_field(name="⚠️ Failed (already gone or no permission)",
                                value=str(summary["failed"]), inline=True)
            embed.set_footer(text="SP4RK — Server Template • Paste log cleared")
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
        embed.set_footer(text="SP4RK — Server Template • Existing channels/roles were not duplicated")
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
            f"This will **permanently delete** everything SP4RK created with `/paste` "
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
    embed.set_footer(text="SP4RK — Server Template • You have 60 seconds to decide")
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

# ============================================================
# 🛡️ SLASH-COMMAND COUNTERPARTS FOR MODERATION
# ============================================================
# These mirror the prefix commands above but expose the same actions through
# Discord's slash UI. Each one re-uses the exact same helpers (add_warning,
# _hierarchy_block, security.mass_action_prevent, supabase_save_mod_log) so
# behaviour is identical to the `!` versions — no double maintenance.
# ============================================================


async def _slash_guard_guild(interaction: discord.Interaction) -> bool:
    """Reject DM usage with a friendly message. Returns False if blocked."""
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.", ephemeral=True
        )
        return False
    return True


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    """User-friendly slash command error replies (was: silent failures)."""
    try:
        if isinstance(error, app_commands.MissingPermissions):
            msg = f"❌ You need the **{', '.join(error.missing_permissions)}** permission for that."
        elif isinstance(error, app_commands.BotMissingPermissions):
            msg = f"❌ I'm missing the **{', '.join(error.missing_permissions)}** permission."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "❌ You don't have permission to use this command."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Slow down — try again in **{error.retry_after:.1f}s**."
        else:
            print(f"[slash error] {type(error).__name__}: {error}")
            msg = f"❌ Something went wrong: `{type(error).__name__}`."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        print(f"[slash error handler] failed: {e}")


# ---------------- /warn ----------------
@bot.tree.command(name="warn", description="Warn a member with an optional reason")
@app_commands.describe(member="Who to warn", reason="Why")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_warn(interaction: discord.Interaction,
                     member: discord.Member, reason: str = "Violation"):
    if not await _slash_guard_guild(interaction):
        return
    # Mirror the !warn hierarchy checks (slash commands can't share ctx).
    auth = interaction.user
    ok_bot, why = automod.can_bot_action(member, interaction.guild.me)
    if not ok_bot:
        return await interaction.response.send_message(
            f"❌ Cannot warn {member.mention} — {why}.", ephemeral=True
        )
    if auth.id != interaction.guild.owner_id and member.top_role >= auth.top_role:
        return await interaction.response.send_message(
            f"❌ You cannot warn {member.mention} — their role is equal to or higher than yours.",
            ephemeral=True,
        )
    await interaction.response.defer()
    await add_warning(member, reason)
    entry = _get_member_entry(str(interaction.guild.id), str(member.id))
    count = entry["count"]
    await interaction.followup.send(
        f"⚠️ {member.mention} has been warned! Now at **{count}** warning(s).\nReason: {reason}"
    )
    await logs.log_event(
        bot, config, "⚠️ Warning Issued",
        f"{member} warned by {auth} | Reason: {reason}", color=0xf39c12,
    )


# ---------------- /warnings ----------------
@bot.tree.command(name="warnings", description="Show a member's warning history")
@app_commands.describe(member="Whose warnings to view")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_warnings(interaction: discord.Interaction, member: discord.Member):
    if not await _slash_guard_guild(interaction):
        return
    entry   = _get_member_entry(str(interaction.guild.id), str(member.id))
    count   = entry["count"]
    history = entry["history"]
    limit   = int(config.get("warning_limit", 30))
    embed = discord.Embed(
        title=f"⚠️ Warning History — {member}",
        color=0xf39c12 if count > 0 else 0x2ecc71,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Total Warnings", value=f"**{count}**", inline=True)
    embed.add_field(name="Warning Limit",  value=f"**{limit}**", inline=True)
    embed.add_field(
        name="Status",
        value="🔴 Limit Reached" if count >= limit else
              ("🟠 Watch" if count >= limit // 2 else "🟢 OK"),
        inline=True,
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
    await interaction.response.send_message(embed=embed)


# ---------------- /clearwarnings ----------------
@bot.tree.command(name="clearwarnings", description="Clear all warnings for a member")
@app_commands.describe(member="Whose warnings to clear")
@app_commands.checks.has_permissions(administrator=True)
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    if not await _slash_guard_guild(interaction):
        return
    guild_id, member_id = str(interaction.guild.id), str(member.id)
    if guild_id in warnings_db and member_id in warnings_db[guild_id]:
        warnings_db[guild_id][member_id] = {"count": 0, "history": []}
        save_warnings()
    supabase_clear_warnings(guild_id, member_id)
    dlog("🧹 CLEAR-WARN", f"Warnings cleared for {member} by {interaction.user}")
    await logs.log_event(
        bot, config, "🧹 Warnings Cleared",
        f"{member}'s warnings cleared by {interaction.user}", color=0x2ecc71,
    )
    await interaction.response.send_message(
        f"✅ All warnings cleared for {member.mention}."
    )


# ---------------- /kick ----------------
@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(member="Who to kick", reason="Why")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_kick(interaction: discord.Interaction,
                     member: discord.Member, reason: str = "No reason provided"):
    if not await _slash_guard_guild(interaction):
        return
    ok_bot, why = automod.can_bot_action(member, interaction.guild.me)
    if not ok_bot:
        return await interaction.response.send_message(
            f"❌ Cannot kick {member.mention} — {why}.", ephemeral=True
        )
    if interaction.user.id != interaction.guild.owner_id and member.top_role >= interaction.user.top_role:
        return await interaction.response.send_message(
            f"❌ You cannot kick {member.mention} — their role is equal to or higher than yours.",
            ephemeral=True,
        )
    if not interaction.guild.me.guild_permissions.kick_members:
        return await interaction.response.send_message(
            "❌ I'm missing the **Kick Members** permission.", ephemeral=True
        )
    await interaction.response.defer()
    try:
        await member.kick(reason=reason)
        await interaction.followup.send(
            f"🚪 {member.mention} has been kicked! Reason: {reason}"
        )
        await logs.log_event(bot, config, "🚪 Kick",
            f"{member} kicked by {interaction.user} | Reason: {reason}", color=0xe67e22)
        await dm_owner("kick", str(member), reason, str(interaction.user))
        supabase_save_mod_log(interaction.guild.id, "kick", str(member),
            str(interaction.user), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dlog("🚪 KICK", f"{member} kicked by {interaction.user} — {reason}")
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ I don't have permission to kick {member.mention}.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# ---------------- /ban ----------------
@bot.tree.command(name="ban", description="Permanently ban a member from the server")
@app_commands.describe(member="Who to ban", reason="Why")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction,
                    member: discord.Member, reason: str = "No reason provided"):
    if not await _slash_guard_guild(interaction):
        return
    ok_bot, why = automod.can_bot_action(member, interaction.guild.me)
    if not ok_bot:
        return await interaction.response.send_message(
            f"❌ Cannot ban {member.mention} — {why}.", ephemeral=True
        )
    if interaction.user.id != interaction.guild.owner_id and member.top_role >= interaction.user.top_role:
        return await interaction.response.send_message(
            f"❌ You cannot ban {member.mention} — their role is equal to or higher than yours.",
            ephemeral=True,
        )
    if not interaction.guild.me.guild_permissions.ban_members:
        return await interaction.response.send_message(
            "❌ I'm missing the **Ban Members** permission.", ephemeral=True
        )
    await interaction.response.defer()
    try:
        await member.ban(reason=reason)
        await interaction.followup.send(
            f"⛔ {member.mention} has been permanently banned! Reason: {reason}"
        )
        await logs.log_event(bot, config, "⛔ Ban",
            f"{member} banned by {interaction.user} | Reason: {reason}", color=0xe74c3c)
        await dm_owner("ban", str(member), reason, str(interaction.user))
        supabase_save_mod_log(interaction.guild.id, "ban", str(member),
            str(interaction.user), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dlog("⛔ BAN", f"{member} banned by {interaction.user} — {reason}")
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ I don't have permission to ban {member.mention}.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# ---------------- /unban ----------------
@bot.tree.command(name="unban", description="Unban a user by their ID")
@app_commands.describe(user_id="The user's Discord ID", reason="Why")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_unban(interaction: discord.Interaction,
                      user_id: str, reason: str = "No reason provided"):
    if not await _slash_guard_guild(interaction):
        return
    if not user_id.isdigit():
        return await interaction.response.send_message(
            "❌ User ID must be a number (right-click a user → Copy ID).",
            ephemeral=True,
        )
    await interaction.response.defer()
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        await interaction.followup.send(f"✅ {user} has been unbanned. Reason: {reason}")
        await logs.log_event(bot, config, "✅ Unban",
            f"{user} unbanned by {interaction.user} | Reason: {reason}", color=0x2ecc71)
        supabase_save_mod_log(interaction.guild.id, "unban", str(user),
            str(interaction.user), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dlog("✅ UNBAN", f"{user} unbanned by {interaction.user}")
    except discord.NotFound:
        await interaction.followup.send("❌ User not found or not banned.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I don't have permission to unban users.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# ---------------- /timeout ----------------
@bot.tree.command(name="timeout", description="Temporarily mute a member with Discord's built-in timeout")
@app_commands.describe(member="Who to time out", duration="e.g. 30s, 10m, 2h, 7d (max 28d)", reason="Why")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_timeout(interaction: discord.Interaction,
                        member: discord.Member, duration: str,
                        reason: str = "No reason provided"):
    if not await _slash_guard_guild(interaction):
        return
    ok_bot, why = automod.can_bot_action(member, interaction.guild.me)
    if not ok_bot:
        return await interaction.response.send_message(
            f"❌ Cannot time out {member.mention} — {why}.", ephemeral=True
        )
    if interaction.user.id != interaction.guild.owner_id and member.top_role >= interaction.user.top_role:
        return await interaction.response.send_message(
            f"❌ You cannot time out {member.mention} — their role is equal to or higher than yours.",
            ephemeral=True,
        )
    seconds = parse_duration(duration)
    if not seconds:
        return await interaction.response.send_message(
            "❌ Invalid duration! Use e.g. `30m`, `2h`, `1d`. Max 28 days.", ephemeral=True
        )
    if seconds > 2419200:
        return await interaction.response.send_message(
            "❌ Discord timeout limit is 28 days.", ephemeral=True
        )
    label = duration_label(seconds)
    await interaction.response.defer()
    try:
        until = discord.utils.utcnow() + timedelta(seconds=seconds)
        await member.timeout(until, reason=reason)
        await interaction.followup.send(
            f"⏱️ {member.mention} has been timed out for **{label}**. Reason: {reason}"
        )
        await logs.log_event(bot, config, "⏱️ Timeout",
            f"{member} timed out by {interaction.user} for {label} | Reason: {reason}",
            color=0xf39c12)
        supabase_save_mod_log(interaction.guild.id, "timeout", str(member),
            str(interaction.user), f"{label} — {reason}",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dlog("⏱️ TIMEOUT", f"{member} timed out for {label} by {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ I don't have permission to time out {member.mention}.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# ---------------- /untimeout ----------------
@bot.tree.command(name="untimeout", description="Remove a member's timeout")
@app_commands.describe(member="Who to untimeout")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_untimeout(interaction: discord.Interaction, member: discord.Member):
    if not await _slash_guard_guild(interaction):
        return
    await interaction.response.defer()
    try:
        await member.timeout(None, reason=f"Timeout removed by {interaction.user}")
        await interaction.followup.send(f"✅ Timeout removed for {member.mention}.")
        await logs.log_event(bot, config, "✅ Timeout Removed",
            f"{member}'s timeout removed by {interaction.user}", color=0x2ecc71)
        dlog("✅ UNTIMEOUT", f"Timeout removed for {member} by {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ I don't have permission to remove {member.mention}'s timeout.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# ---------------- /purge ----------------
@bot.tree.command(name="purge", description="Bulk delete the most recent messages in this channel")
@app_commands.describe(amount="How many to delete (1-200)")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge(interaction: discord.Interaction, amount: int = 10):
    if not await _slash_guard_guild(interaction):
        return
    if amount < 1 or amount > 200:
        return await interaction.response.send_message(
            "❌ Amount must be between 1 and 200.", ephemeral=True
        )
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(
            f"🧹 Deleted **{len(deleted)}** message(s).", ephemeral=True
        )
        await logs.log_event(bot, config, "🧹 Purge",
            f"{interaction.user} purged {len(deleted)} messages in #{interaction.channel.name}",
            color=0x9b59b6)
        dlog("🧹 PURGE",
            f"{interaction.user} deleted {len(deleted)} messages in #{interaction.channel.name}")
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I don't have permission to delete messages here.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# ============================================================
# 🖼️ NEW UTILITY SLASH COMMANDS — /avatar /banner /roleinfo
# ============================================================

@bot.tree.command(name="avatar", description="Show a member's full-size avatar")
@app_commands.describe(member="Whose avatar to show (defaults to yourself)")
async def slash_avatar(interaction: discord.Interaction,
                       member: discord.Member = None):
    target = member or interaction.user
    avatar = target.display_avatar
    embed = discord.Embed(
        title=f"🖼️ {target.display_name}'s Avatar",
        color=getattr(target, "color", discord.Color(0x4f8ef7)) or discord.Color(0x4f8ef7),
    )
    embed.set_image(url=avatar.replace(size=1024).url)
    links = [
        f"[PNG]({avatar.replace(format='png', size=1024).url})",
        f"[JPG]({avatar.replace(format='jpg', size=1024).url})",
        f"[WEBP]({avatar.replace(format='webp', size=1024).url})",
    ]
    if avatar.is_animated():
        links.append(f"[GIF]({avatar.replace(format='gif', size=1024).url})")
    embed.description = " · ".join(links)
    embed.set_footer(text=f"User ID: {target.id}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="banner", description="Show a member's profile banner (if they have one)")
@app_commands.describe(member="Whose banner to show (defaults to yourself)")
async def slash_banner(interaction: discord.Interaction,
                       member: discord.Member = None):
    target = member or interaction.user
    # Fetch the full user object so banner is populated.
    try:
        full = await bot.fetch_user(target.id)
    except Exception:
        full = target
    banner = getattr(full, "banner", None)
    if not banner:
        return await interaction.response.send_message(
            f"😔 **{target}** doesn't have a profile banner set.",
            ephemeral=True,
        )
    embed = discord.Embed(
        title=f"🎴 {target.display_name}'s Banner",
        color=getattr(target, "color", discord.Color(0x4f8ef7)) or discord.Color(0x4f8ef7),
    )
    embed.set_image(url=banner.replace(size=1024).url)
    embed.set_footer(text=f"User ID: {target.id}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="roleinfo", description="Show detailed info about a role")
@app_commands.describe(role="The role to inspect")
async def slash_roleinfo(interaction: discord.Interaction, role: discord.Role):
    if not await _slash_guard_guild(interaction):
        return
    members_with = [m for m in interaction.guild.members if role in m.roles]
    perms_on = [name.replace("_", " ").title()
                for name, val in role.permissions if val]
    perms_str = ", ".join(perms_on[:15]) if perms_on else "_None_"
    if len(perms_on) > 15:
        perms_str += f"  …  **+{len(perms_on) - 15} more**"
    color_hex = f"#{role.color.value:06x}" if role.color.value else "#000000 (default)"
    embed = discord.Embed(
        title=f"🎭 {role.name}",
        description=(
            f"{role.mention}\n"
            f"🆔 Role ID: `{role.id}`"
        ),
        color=role.color if role.color.value else discord.Color(0x4f8ef7),
        timestamp=datetime.now(),
    )
    embed.add_field(name="🎨 Color", value=f"`{color_hex}`", inline=True)
    embed.add_field(name="📍 Position",
                    value=f"#{role.position} of {len(interaction.guild.roles) - 1}",
                    inline=True)
    embed.add_field(name="👥 Members", value=f"**{len(members_with):,}**", inline=True)
    embed.add_field(name="🪧 Hoisted",
                    value="✅ Yes" if role.hoist else "❌ No", inline=True)
    embed.add_field(name="📣 Mentionable",
                    value="✅ Yes" if role.mentionable else "❌ No", inline=True)
    embed.add_field(name="🤖 Managed",
                    value="✅ By integration" if role.managed else "❌ No",
                    inline=True)
    embed.add_field(
        name="📅 Created",
        value=f"<t:{int(role.created_at.timestamp())}:D>\n<t:{int(role.created_at.timestamp())}:R>",
        inline=False,
    )
    embed.add_field(
        name=f"🔑 Permissions ({len(perms_on)})",
        value=perms_str,
        inline=False,
    )
    if role.icon:
        embed.set_thumbnail(url=role.icon.url)
    embed.set_footer(text=f"Requested by {interaction.user} • SP4RK")
    await interaction.response.send_message(embed=embed)


# ---------------- DYNO/CARL-BOT EXTRAS ----------------
# Tags, Softban, AFK, and Highlights — registered via a single setup() call
# so this already-large file stays readable.
try:
    import extras as _sp4rk_extras
    _sp4rk_extras.setup(bot, dlog=dlog)
except Exception as _e:
    print(f"⚠️  Failed to load extras module: {_e}")


# ---------------- RUN ----------------
_bot_thread = None

def run_bot():
    if not TOKEN:
        print("⚠️  No DISCORD_TOKEN set. Bot will not start.")
        return
    import time
    retry_delay = 5
    while True:
        print("🤖 Starting SP4RK Bot V2.0.4...")
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
