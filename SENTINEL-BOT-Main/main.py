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
    get_guild_config as _get_guild_config,
)
import moderation, automod, roles, logs, security

BOT_START_TIME = datetime.utcnow()

# ---------------- CONFIG LOADER ----------------
def load_config():
    # The bot and dashboard run in the same process — read config from the
    # shared in-memory dict directly instead of making an HTTP self-request
    # that would queue up against the single Flask worker and block dashboards.
    try:
        import dashboard as _dash
        if isinstance(_dash.config, dict) and _dash.config:
            return dict(_dash.config)
    except Exception:
        pass
    if not os.path.exists('settings.json'):
        return {}
    with open('settings.json', 'r') as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def refresh_config():
    """Reload config from the dashboard's in-memory dict (same process, no HTTP)."""
    global config
    try:
        import dashboard as _dash
        config = {**DEFAULT_CONFIG, **_dash.config}
    except Exception as e:
        print(f"refresh_config error, falling back to settings.json: {e}")
        config = {**DEFAULT_CONFIG, **load_config()}

DEFAULT_CONFIG = {
    "prefix": "!",
    "bot_status": "online",
    "theme_color": "#3498db",
    "welcome_msg": "",
    "welcome_image": "",
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
    # --- SP4RK Server Knowledge (used when the bot is @mentioned) ---
    "server_description": "",
    "server_rules": "",
    # --- Staff role overrides ---
    # Set these to a Discord role ID to let that role use admin/mod commands
    # even if they don't have the matching Discord permission flag.
    "bot_admin_role": "",   # role that can use admin-level commands
    "bot_mod_role":   "",   # role that can use mod-level commands
}

# Bootstrap from the dashboard's Supabase-backed config (already loaded at import time)
config = {**DEFAULT_CONFIG, **dash_config}

def get_config_for_guild(guild_id: str) -> dict:
    """Return the effective config for a specific guild (per-guild overrides merged over global defaults)."""
    try:
        return _get_guild_config(str(guild_id))
    except Exception:
        return config


# ── Permission helpers ───────────────────────────────────────────────────────

def _member_has_role_id(member: discord.Member, role_id_str: str) -> bool:
    """True if the member has the role whose ID matches role_id_str."""
    if not role_id_str or not str(role_id_str).strip().isdigit():
        return False
    rid = int(str(role_id_str).strip())
    return any(r.id == rid for r in member.roles)


def _is_bot_admin(interaction: discord.Interaction) -> bool:
    """True if the user passes the admin permission bar for SP4RK commands.

    Passes when the user has Discord's Administrator or Manage Server permission,
    OR when they hold the role configured as `bot_admin_role` in the dashboard.
    """
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    gcfg = get_config_for_guild(str(interaction.guild_id or 0))
    p = member.guild_permissions
    return (
        p.administrator
        or p.manage_guild
        or _member_has_role_id(member, gcfg.get("bot_admin_role", ""))
    )


def _is_bot_mod(interaction: discord.Interaction) -> bool:
    """True if the user passes the mod permission bar for SP4RK commands.

    Passes when the user has any standard moderation permission, OR holds the
    `bot_admin_role` or `bot_mod_role` configured in the dashboard.
    """
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    gcfg = get_config_for_guild(str(interaction.guild_id or 0))
    p = member.guild_permissions
    return (
        p.administrator or p.manage_guild or p.manage_messages
        or p.kick_members or p.ban_members or p.moderate_members
        or _member_has_role_id(member, gcfg.get("bot_admin_role", ""))
        or _member_has_role_id(member, gcfg.get("bot_mod_role", ""))
    )

TOKEN         = os.environ.get("DISCORD_TOKEN", "")
GEMINI_KEY    = os.environ.get("GEMINI_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
_PORT         = int(os.environ.get("PORT", 5000))
DASHBOARD_URL = f"http://127.0.0.1:{_PORT}"

intents = discord.Intents.none()
intents.guilds          = True   # guild info, roles, channels
intents.members         = True   # join / leave / update events
intents.moderation      = True   # ban / unban events
intents.reactions       = True   # reaction add/remove (raw handlers)
intents.voice_states    = True   # voice channel tracking
intents.messages        = True   # message events in guilds
intents.message_content = True   # read message text for automod/AI
intents.dm_messages     = True   # DM commands / warnings
bot = commands.Bot(
    command_prefix=lambda b, m: get_config_for_guild(str(m.guild.id) if m.guild else '0').get('prefix', '!') or '!',
    intents=intents,
    help_command=None,
    max_messages=100,              # default is 1 000; keep only recent 100
    chunk_guilds_at_startup=False, # don't fetch all members at boot
)

def dlog(event_type, description, guild_id=None):
    try:
        add_dashboard_log(event_type, description, guild_id=guild_id)
    except Exception:
        pass

# ---------------- WARNING STORAGE ----------------
# Structure: {guild_id: {member_id: {"count": int, "history": [{"reason": str, "time": str}]}}}
warnings_file = "warnings.json"

def load_warnings():
    """
    Load warnings — priority order:
      1. Postgres spark_warning_history (full history, survives Render redeploys)
      2. Supabase sentinel_warnings (full history, if configured)
      3. Local JSON file (last-resort fallback)
    Whichever wins, refresh the JSON cache so the next fast read is instant.
    """
    # 1) Postgres (primary durable store)
    try:
        import db_spark as _ds
        if _ds.is_available():
            pg_data = _ds.get_all_warning_history()
            if pg_data:
                try:
                    with open(warnings_file, "w") as f:
                        json.dump(pg_data, f, indent=4)
                except Exception:
                    pass
                return pg_data
    except Exception as e:
        print(f"[WARN] Postgres warning load failed: {e}")

    # 2) Supabase (mirror)
    try:
        sb_data = supabase_load_all_warnings()
    except Exception as e:
        print(f"[WARN] Supabase warning load failed: {e}")
        sb_data = {}
    if sb_data:
        try:
            with open(warnings_file, "w") as f:
                json.dump(sb_data, f, indent=4)
        except Exception:
            pass
        return sb_data

    # 3) Local JSON cache
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
    dlog("⚠️ APPROVAL", f"Approval request sent for {member} ({count} warnings)", guild_id=member.guild.id)

async def check_warning_limit(member, count):
    refresh_config()
    gcfg           = get_config_for_guild(str(member.guild.id))
    limit           = int(gcfg.get("warning_limit", 30))
    owner_approval  = gcfg.get("owner_approval", "OFF") == "ON"
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
    _increment_report_action()
    dlog("⚠️ WARNING", f"{member} now has {count} warning(s) — {reason}", guild_id=member.guild.id)
    print(f"⚠️ Warning added to {member}: {count} total | Reason: {reason}")
    await check_warning_limit(member, count)
    return count

# ---------------- AI MODERATION ----------------
async def handle_ai_moderation(message):
    if not GEMINI_KEY:
        return
    # Hierarchy guard — never action staff, server owner, or anyone equal/higher than the bot.
    if message.guild:
        bot_member = message.guild.me
        allowed, _ = automod.can_bot_action(message.author, bot_member)
        if not allowed:
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
                f"⚠️ {message.author.mention}, your message was removed by AI moderation.\n> **Reason:** {reason}",
                delete_after=8
            )
            dlog("🤖 GEMINI-MOD", f"{message.author} flagged — {reason}", guild_id=message.guild.id if message.guild else None)
    except Exception as e:
        print(f"Gemini moderation error (ignored): {e}")

def build_server_context(guild) -> str:
    """Build a concise server knowledge block for the Gemini prompt."""
    if guild is None:
        return ""

    lines = []
    lines.append(f"SERVER NAME: {guild.name}")
    if guild.description:
        lines.append(f"SERVER DESCRIPTION (Discord): {guild.description}")

    gcfg = get_config_for_guild(str(guild.id))

    # Custom admin-written description and rules from the dashboard
    custom_desc = gcfg.get("server_description", "").strip()
    custom_rules = gcfg.get("server_rules", "").strip()
    if custom_desc:
        lines.append(f"ABOUT THIS SERVER: {custom_desc}")
    if custom_rules:
        lines.append(f"SERVER RULES:\n{custom_rules}")

    lines.append(f"MEMBER COUNT: {guild.member_count}")
    lines.append(f"BOOST LEVEL: {guild.premium_tier} (boosts: {guild.premium_subscription_count})")

    # Text channels (name only, up to 30 to keep prompt compact)
    text_channels = [c.name for c in guild.text_channels][:30]
    if text_channels:
        lines.append(f"TEXT CHANNELS: {', '.join('#' + c for c in text_channels)}")

    # Voice channels (name only, up to 15)
    voice_channels = [c.name for c in guild.voice_channels][:15]
    if voice_channels:
        lines.append(f"VOICE CHANNELS: {', '.join(voice_channels)}")

    # Roles (skip @everyone, up to 25)
    roles = [r.name for r in guild.roles if r.name != "@everyone"][:25]
    if roles:
        lines.append(f"ROLES: {', '.join(roles)}")

    # SP4RK config hints that are useful context
    prefix = gcfg.get("prefix", "!")
    lines.append(f"BOT COMMAND PREFIX: {prefix}")
    welcome = gcfg.get("welcome_msg", "").strip()
    if welcome:
        lines.append(f"WELCOME MESSAGE: {welcome}")

    return "\n".join(lines)


# ---------------- BACKGROUND TASKS ----------------
_last_avatar_url  = ""
_last_status      = ""
_last_act_type    = ""
_last_act_text    = ""

@tasks.loop(seconds=60)
async def sync_bot_settings():
    """Pull dashboard config every minute and update presence/avatar.

    Wrapped in a top-level try so a single bad iteration (network blip,
    Discord API hiccup) can never kill the loop and leave the bot frozen
    on an old presence forever.
    """
    global _last_avatar_url, _last_status, _last_act_type, _last_act_text
    try:
        refresh_config()
        current_status   = config.get("bot_status", "online")
        current_act_type = config.get("bot_activity_type", "none")
        current_act_text = config.get("bot_activity_text", "").strip()
        presence_changed = (
            current_status   != _last_status or
            current_act_type != _last_act_type or
            current_act_text != _last_act_text
        )
        if presence_changed:
            s_map = {
                "online":    discord.Status.online,
                "idle":      discord.Status.idle,
                "dnd":       discord.Status.dnd,
                "invisible": discord.Status.invisible,
            }
            ds = s_map.get(current_status, discord.Status.online)
            # Build activity object
            activity = None
            if current_act_text:
                if current_act_type == "playing":
                    activity = discord.Game(name=current_act_text)
                elif current_act_type == "watching":
                    activity = discord.Activity(type=discord.ActivityType.watching, name=current_act_text)
                elif current_act_type == "listening":
                    activity = discord.Activity(type=discord.ActivityType.listening, name=current_act_text)
                elif current_act_type == "competing":
                    activity = discord.Activity(type=discord.ActivityType.competing, name=current_act_text)
                elif current_act_type == "custom":
                    activity = discord.CustomActivity(name=current_act_text)
            try:
                await bot.change_presence(status=ds, activity=activity)
                _last_status   = current_status
                _last_act_type = current_act_type
                _last_act_text = current_act_text
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

# Track mod actions between report windows
_report_action_count = 0

def _increment_report_action():
    global _report_action_count
    _report_action_count += 1

def _reset_report_action_count():
    global _report_action_count
    count = _report_action_count
    _report_action_count = 0
    return count

def _report_period() -> str:
    """Return a labelled period string based on current UTC hour."""
    h = datetime.utcnow().hour
    if 4 <= h < 12:
        return "🌅 Morning Report"
    elif 12 <= h < 20:
        return "☀️ Afternoon Report"
    else:
        return "🌙 Evening Report"

# Runs 3× per 24 hours (every 8 hours → morning / afternoon / evening)
@tasks.loop(hours=8)
async def daily_report_task():
    try:
        refresh_config()
        if config.get("daily_report") != "ON":
            return
        log_channel_id = config.get("log_channel", "").strip()
        if not log_channel_id:
            return

        channel = bot.get_channel(int(log_channel_id))
        if not channel:
            return

        # ── Collect stats ──────────────────────────────────────────────────────
        total_warnings = sum(
            (v.get("count", 0) if isinstance(v, dict) else v)
            for guild in warnings_db.values()
            for v in (guild.values() if isinstance(guild, dict) else [])
        )
        recent_actions = _reset_report_action_count()

        # Member count — use first available guild the bot is in
        member_count = -1
        try:
            guild = channel.guild
            if guild:
                member_count = guild.member_count
        except Exception:
            pass

        # Uptime
        uptime = datetime.utcnow() - BOT_START_TIME
        hours_up, rem = divmod(int(uptime.total_seconds()), 3600)
        mins_up = rem // 60
        uptime_str = f"{hours_up}h {mins_up}m"

        # Active features count
        feature_keys = [
            "auto_block_links", "auto_block_invites", "auto_block_mentions",
            "anti_raid", "security_2fa", "role_monitor", "mass_ban_prevent",
            "temp_lockdown", "spam_filter", "caps_filter", "emoji_filter",
            "duplicate_filter", "wall_filter", "anti_zalgo", "automod_on",
        ]
        active_features = sum(1 for k in feature_keys if config.get(k) == "ON")
        total_features  = len(feature_keys)

        period   = _report_period()
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

        # ── Generate black-paper image ─────────────────────────────────────────
        img_file = None
        try:
            import report_generator as _rg
            import discord as _dc
            buf = _rg.generate_report_image(
                period         = period,
                date_str       = date_str,
                warnings_total = total_warnings,
                member_count   = member_count,
                uptime_str     = uptime_str,
                active_features= active_features,
                total_features = total_features,
                recent_actions = recent_actions,
                bot_status     = "Online",
            )
            img_file = _dc.File(buf, filename="daily_report.png")
        except Exception as img_err:
            print(f"[daily_report] image generation failed: {img_err}")

        # ── Build embed ────────────────────────────────────────────────────────
        server_name = channel.guild.name if channel.guild else "your server"
        embed = discord.Embed(
            title=f"📊 SP4RK — {period}",
            description=(
                f"**Automated Moderation Report**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Showing the latest moderation health snapshot for **{server_name}**.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=0x0d0d1a,
            timestamp=datetime.utcnow(),
        )
        embed.add_field(
            name="⚠️ Warnings (all-time)",
            value=f"**{total_warnings:,}**",
            inline=True
        )
        embed.add_field(
            name="👥 Server Members",
            value=f"**{member_count:,}**" if member_count >= 0 else "N/A",
            inline=True
        )
        embed.add_field(
            name="⏱️ Bot Uptime",
            value=f"**{uptime_str}**",
            inline=True
        )
        embed.add_field(
            name="🛡️ Active Features",
            value=f"**{active_features}** / {total_features} enabled",
            inline=True
        )
        embed.add_field(
            name="📋 Mod Actions (this window)",
            value=f"**{recent_actions}**",
            inline=True
        )
        embed.add_field(
            name="🟢 Bot Status",
            value="**Online**",
            inline=True
        )

        if img_file:
            embed.set_image(url="attachment://daily_report.png")

        embed.set_footer(
            text=f"SP4RK  ·  Byte Labs  ·  Reports every 8 h  ·  {date_str}"
        )

        if img_file:
            await channel.send(embed=embed, file=img_file)
        else:
            await channel.send(embed=embed)

        dlog("📊 DAILY-REPORT", f"{period} posted to log channel")
    except Exception as e:
        print(f"[daily_report_task] iteration crashed: {e}")

# ---------------- SLASH COMMANDS ----------------
def build_help_embed(prefix):
    embed = discord.Embed(
        title="🛡️ SP4RK — Command Reference",
        description=(
            f"**Prefix:** `{prefix}`  ·  **Version:** V2.5.0\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"All slash commands also work via Discord's `/` menu.\n"
            f"Covers moderation, safety, automod, and server security.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x4f8ef7,
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
        f"`{prefix}purgereaction <1-100>` — clear reactions from last N messages\n"
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
    embed.add_field(name="🛡️ Security", inline=False, value=(
        "`/security` — configure 2FA enforcement, anti-raid, role monitor,\n"
        "mass-ban prevention, and temp-lockdown thresholds"
    ))
    embed.add_field(name="🔔 Reminders", inline=False, value=(
        "`/remind <duration> <message>` — get a DM reminder\n"
        "Supports: `30s`, `5m`, `2h`, `1d`, etc."
    ))
    embed.add_field(name="🤖 Bot / Setup", inline=False, value=(
        f"`{prefix}refresh` — reload dashboard config\n"
        f"`{prefix}help` — show this menu  (alias `!commands`, `!cmds`)\n"
        "`/commands` — slash version of this menu\n"
        "`/about` — who made SP4RK & its official birthday\n"
        "`/bot` — bot stats\n"
        "`/dashboard` — admin dashboard link"
    ))
    embed.set_footer(text=f"SP4RK V2.5.0  •  Tip: change the prefix & custom commands in the web dashboard")
    return embed

@bot.tree.command(name="commands", description="List all SP4RK commands")
async def slash_commands(interaction: discord.Interaction):
    refresh_config()
    gcfg = get_config_for_guild(str(interaction.guild.id) if interaction.guild else '0')
    p = gcfg.get('prefix', '!')
    embed = build_help_embed(p)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# 📖 /manual  — Paginated bot manual
# ============================================================

def _build_manual_pages() -> list:
    """Return a list of discord.Embed objects — one per manual page."""
    C = 0x4f8ef7  # SP4RK brand blue

    def page(title, description, fields):
        e = discord.Embed(title=title, description=description, color=C)
        for name, value, inline in fields:
            e.add_field(name=name, value=value, inline=inline)
        return e

    pages = []
    # ── Page 1: Overview ──────────────────────────────────────────────────
    pages.append(page(
        "📖 SP4RK Manual",
        "Use **‹** and **›** to navigate.  Syntax: `<required>` · `[optional]`",
        [
            ("📋 Pages", (
                "**1.** Overview\n"
                "**2.** Moderation\n"
                "**3.** Channel & Member Tools\n"
                "**4.** Features (XP, Giveaways, Tickets, Reaction Roles)\n"
                "**5.** Utility, Auto-Mod & Prefix Commands\n"
                "**6.** 👑 Advanced Reaction Role Panels\n"
                "**7.** 📜 Logging & AutoMod Config\n"
                "**8.** 🧾 Tag System\n"
                "**9.** 📁 Case Management\n"
                "**10.** 📊 Server Analytics\n"
                "**11.** ⚙️ Automation Rules & Permissions\n"
                "**12.** 💎 Premium & God Panel"
            ), False),
            ("Quick start", (
                "Type `/` to browse all slash commands\n"
                "Default prefix: `!` — change with `/setprefix`\n"
                "Run `/dashboard` to manage all settings visually\n"
                "Run `/godpanel` for a full server admin overview"
            ), False),
        ]
    ))
    # ── Page 2: Moderation ────────────────────────────────────────────────
    pages.append(page(
        "⚠️ Moderation",
        "Commands for warnings, bans, mutes, and mod records.",
        [
            ("Warnings", (
                "`/warn @member [reason]` — Add a warning\n"
                "`/warnings @member` — View warning history\n"
                "`/clearwarnings @member` — Wipe all warnings *(Admin)*"
            ), False),
            ("Bans & Kicks", (
                "`/kick @member [reason]` — Kick a member\n"
                "`/ban @member [reason]` — Permanent ban\n"
                "`/softban @member [reason] [days]` — Ban + unban to clear messages\n"
                "`/unban <user_id>` — Unban by Discord ID\n"
                "`/tempban @member <duration> [reason]` — Timed ban (e.g. `2h`, `7d`)\n"
                "`/massban <id1 id2 ...>` — Ban up to 50 users at once"
            ), False),
            ("Timeouts & Mutes", (
                "`/timeout @member <duration>` — Discord timeout (max 28d)\n"
                "`/untimeout @member` — Remove timeout\n"
                "`/mute @member [duration]` — Mute via mute role\n"
                "`/unmute @member` — Unmute"
            ), False),
            ("Notes & Logs", (
                "`/note @member <note>` — Add a private staff note\n"
                "`/notes @member` — View all staff notes\n"
                "`/modlogs @member` — Full mod history"
            ), False),
        ]
    ))
    # ── Page 3: Channel & Member Tools ────────────────────────────────────
    pages.append(page(
        "🔒 Channel & Member Tools",
        "Lock channels, clean messages, manage roles and voice.",
        [
            ("Locking", (
                "`/lock [#channel]` — Block @everyone from sending\n"
                "`/unlock [#channel]` — Restore send permissions\n"
                "`/lockdown` — Lock every channel instantly *(emergency)*\n"
                "`/unlockdown` — Lift the server lockdown"
            ), False),
            ("Cleanup", (
                "`/purge <amount>` — Delete last N messages (max 200)\n"
                "`/purge-user @member [amount]` — Delete a user's messages\n"
                "`/purge-reactions [amount]` — Strip reactions from last N msgs\n"
                "`/slowmode <seconds> [#channel]` — Set message slowmode (0 = off)"
            ), False),
            ("Members", (
                "`/role-add @member @role` — Give a role\n"
                "`/role-remove @member @role` — Remove a role\n"
                "`/nick @member [name]` — Set nickname (blank = reset)\n"
                "`/voicekick @member` — Disconnect from voice\n"
                "`/snipe` — Show last deleted message in this channel"
            ), False),
        ]
    ))
    # ── Page 4: Security & AutoMod Setup ─────────────────────────────────
    pages.append(page(
        "🛡️ Security & AutoMod Setup",
        "Server security hardening and automated moderation configuration.",
        [
            ("Security", (
                "`/security 2fa` — Enforce 2FA for admin roles\n"
                "`/security role-monitor` — Alert on suspicious role changes\n"
                "`/security anti-raid` — Enable anti-raid join protection\n"
                "`/security mass-ban-prevent` — Block mass bans\n"
                "`/security temp-lockdown` — Auto-lock server on raid trigger"
            ), False),
            ("AutoMod Filters *(dashboard)*", (
                "Block links · Block invites · Block mass mentions\n"
                "Spam · Caps · Emoji · Duplicate · Wall · Anti-Zalgo\n"
                "Graduated punishments: warns → mute → tempban → kick\n"
                "AI moderation (Gemini) · Anti-raid · Role monitor · Log channel"
            ), False),
        ]
    ))
    # ── Page 5: Utility & Prefix Commands ────────────────────────────────
    pages.append(page(
        "🔧 Utility & Prefix Commands",
        "Info commands, auto-moderation overview, and prefix equivalents.",
        [
            ("Info & Utility", (
                "`/userinfo [@member]` — Member profile card\n"
                "`/serverinfo` — Server stats\n"
                "`/avatar [@member]` — Full-size avatar\n"
                "`/roleinfo @role` — Role details\n"
                "`/botinfo` — SP4RK stats\n"
                "`/about` — Who made SP4RK & its birthday\n"
                "`/remind <duration> <msg>` — Set a DM reminder\n"
                "`/setprefix <p>` — Change server prefix *(Admin)*\n"
                "`/dashboard` — Get your dashboard link *(Admin)*"
            ), False),
            ("Auto-Moderation *(dashboard)*", (
                "Block links · Block invites · Block mass mentions\n"
                "Spam · Caps · Emoji · Duplicate · Wall · Anti-Zalgo filters\n"
                "Graduated punishments: warns → mute → tempban → kick\n"
                "AI moderation (Gemini) · Anti-raid · Role monitor · Log channel\n"
                "For slash-based AutoMod config → see **Page 7**"
            ), False),
            ("Prefix Commands", (
                "`!mute` · `!unmute` · `!tempban`\n"
                "`!note` · `!notes` · `!modlogs`\n"
                "`!lock` · `!unlock` · `!lockdown` · `!unlockdown`\n"
                "`!slowmode` · `!purgeuser` · `!purgereaction` · `!massban`\n"
                "`!role add/remove` · `!nick` · `!voicekick` · `!snipe`\n"
                "`!refresh` *(reload config)* · `!sync` *(re-sync slash cmds)*"
            ), False),
        ]
    ))
    # ── Page 6: Permissions & Roles ───────────────────────────────────────
    pages.append(page(
        "🔑 Permissions & Roles",
        "Control who can use SP4RK's moderation commands.",
        [
            ("Bot Admin Role", (
                "Set a **Bot Admin Role** in the dashboard — members with this role can\n"
                "use all SP4RK admin commands without needing Discord's Manage Server perm."
            ), False),
            ("Bot Mod Role", (
                "Set a **Bot Mod Role** in the dashboard — members with this role can\n"
                "use moderation commands (warn, kick, ban, timeout, purge, lock, etc.)."
            ), False),
            ("Auto Role", (
                "Automatically assign a role when a new member joins.\n"
                "Configure the role in the dashboard under **Member Settings**."
            ), False),
        ]
    ))
    # ── Page 7: Logging & AutoMod Config ──────────────────────────────────
    pages.append(page(
        "📜 Logging & AutoMod Config",
        "Slash command controls for the Ultra Logging System and Smart AutoMod.",
        [
            ("Ultra Logging — /logs", (
                "`/logs enable` — Toggle logging on/off\n"
                "`/logs channel #channel` — Set where logs are sent\n"
                "`/logs events <preset>` — Configure tracked events\n"
                "  Presets: `all` · `messages` · `members` · `roles` · `voice` · `channels`\n"
                "`/logs filter <event>` — Toggle a single event on/off\n"
                "`/logs status` — View current logging config\n"
                "`/logs export` — Download recent action logs as a `.txt` file"
            ), False),
            ("Smart AutoMod — /automod", (
                "`/automod enable` — Toggle AutoMod on/off\n"
                "`/automod antispam <on/off>` — Anti-spam detection\n"
                "`/automod antilink <on/off>` — Block external links\n"
                "`/automod antiinvite <on/off>` — Block Discord invite links\n"
                "`/automod punish <warn/mute/kick/ban>` — Default punishment\n"
                "`/automod escalation` — Toggle auto-escalation (warn → mute → ban)\n"
                "`/automod status` — View full AutoMod config"
            ), False),
        ]
    ))
    # ── Page 8: Tag System ────────────────────────────────────────────────
    pages.append(page(
        "🧾 Tag System",
        "Create reusable server responses that support dynamic variables.",
        [
            ("Managing Tags — /tag", (
                "`/tag create <name> <content>` — Create a new tag\n"
                "`/tag edit <name> <new_content>` — Update an existing tag\n"
                "`/tag delete <name>` — Delete a tag\n"
                "`/tag list` — List all tags with use counts and previews\n"
                "`/tag use <name> [@target]` — Post a tag in chat\n"
                "`/tag embed <name> <title> <description>` — Create an embed-style tag"
            ), False),
            ("Variable Substitution", (
                "Tags support dynamic variables that are filled in when posted:\n\n"
                "`{user}` — Mentions the command user (or @target if provided)\n"
                "`{username}` — Username as plain text\n"
                "`{server}` — Server name\n"
                "`{membercount}` / `{count}` — Current member count\n"
                "`{channel}` — Mentions the current channel"
            ), False),
            ("Example", (
                "`/tag create welcome Welcome {user} to {server}! We now have {count} members.`\n"
                "`/tag use welcome @NewMember`\n"
                "→ Posts: *Welcome @NewMember to My Server! We now have 342 members.*"
            ), False),
        ]
    ))
    # ── Page 9: Case Management ───────────────────────────────────────────
    pages.append(page(
        "📁 Case Management",
        "Track, view, and resolve moderation cases with a full case database.",
        [
            ("Creating Cases — /case", (
                "`/case create @member <type> <reason>` — Open a new case\n"
                "  Types: `warn` · `mute` · `kick` · `ban` · `softban` · `note`\n"
                "  → Each case gets a unique numeric ID"
            ), False),
            ("Viewing Cases", (
                "`/case view <id>` — Full details for a specific case\n"
                "  Shows type, user, moderator, reason, status, and notes\n"
                "`/case list [@member]` — List recent cases (filter by user optional)\n"
                "  🔴 = open  ·  🟢 = closed"
            ), False),
            ("Closing Cases", (
                "`/case close <id> [note]` — Mark a case as resolved\n"
                "  Optional note is saved as the resolution reason"
            ), False),
            ("Softban", (
                "`/softban @member [reason] [delete_days]` — Ban then immediately\n"
                "  unban to clear message history without a permanent ban\n"
                "  `delete_days` controls how many days of messages to remove (1–7)"
            ), False),
        ]
    ))
    # ── Page 10: Server Analytics ─────────────────────────────────────────
    pages.append(page(
        "📊 Server Analytics",
        "Slash commands for server stats and growth data. Full charts live in the Byte Labs dashboard.",
        [
            ("Overview & Activity — /stats", (
                "`/stats server` — Complete server overview: members, channels, roles, boosts\n"
                "`/stats activity` — Message volume: today / this week / last 30 days + peak day\n"
                "`/stats users` — Top 5 chatters and top 5 voice users\n"
                "`/stats growth` — Member growth: joins, leaves, net change, retention rate\n"
                "`/stats export` — Download 60-day analytics + mood snapshot as a JSON file"
            ), False),
            ("Server Mood System", (
                "SP4RK tracks live server atmosphere from messages and reactions:\n\n"
                "😇 **Golden Server** (+40 to +50) — Peak positivity\n"
                "😊 **Chill & Friendly** (+20 to +39) — Good vibes\n"
                "😐 **Normal Activity** (−10 to +19) — Business as usual\n"
                "😬 **Tense Atmosphere** (−11 to −29) — Watch the chat\n"
                "💀 **Chaotic / Dead** (−30 to −50) — Things are rough\n\n"
                "Full mood card with score bar and history chart → **Analytics tab** in Byte Labs"
            ), False),
        ]
    ))
    # ── Page 11: Automation Rules & Permissions ───────────────────────────
    pages.append(page(
        "⚙️ Automation Rules & Permissions",
        "Build IF → THEN automation rules and manage channel/role permissions.",
        [
            ("Automation Rules — /rule", (
                "`/rule create <name> <condition> <value> <action> [action_value]` — Create a rule\n\n"
                "**Conditions:** `spam` · `word` · `newaccount` · `joinrate` · `capslock`\n"
                "**Actions:** `warn` · `mute` · `kick` · `ban` · `delete` · `slowmode`\n\n"
                "`/rule list` — View all rules with enabled status and trigger counts\n"
                "`/rule toggle <name>` — Enable or disable a rule\n"
                "`/rule delete <name>` — Permanently remove a rule"
            ), False),
            ("Examples", (
                "If someone sends 5 spam messages → mute:\n"
                "→ `/rule create antispam spam 5 mute 10m`\n\n"
                "Block a word and delete it:\n"
                "→ `/rule create nolinks word discord.gg delete`\n\n"
                "Flag new accounts under 7 days old:\n"
                "→ `/rule create newaccts newaccount 7 warn`"
            ), False),
            ("Permission Manager — /perm", (
                "`/perm view [#channel]` — Show permission overwrites for a channel\n"
                "`/perm lock #channel [@role]` — Deny send_messages for a role\n"
                "`/perm unlock #channel [@role]` — Restore send_messages for a role\n"
                "`/perm clone @from_role @to_role` — Copy one role's channel overwrites to another\n"
                "`/perm audit [@member]` — Full breakdown of what permissions a member has"
            ), False),
        ]
    ))
    # ── Page 12: Premium & God Panel ──────────────────────────────────────
    pages.append(page(
        "💎 Premium & God Panel",
        "SP4RK Elite Mode features and the admin God Panel overview command.",
        [
            ("God Panel — /godpanel", (
                "`/godpanel` — One-command full server overview *(Admin only)*\n\n"
                "Shows everything at a glance:\n"
                "• Member count (humans / bots) and online count\n"
                "• Total bans on record\n"
                "• Open moderation cases\n"
                "• Live server mood score + label\n"
                "• Active automation rules count\n"
                "• Tag count, AutoMod status, logging status\n"
                "• Boost tier, role count, RR panel count"
            ), False),
            ("SP4RK Elite — /sparkelite", (
                "`/sparkelite enable` — Activate SP4RK Premium mode for this server\n"
                "  Unlocks: advanced logs · smart AutoMod AI · full analytics · automation engine\n\n"
                "`/sparkelite elitemode` — Toggle Elite Mode\n"
                "  Maximum power mode: context detection · severity scoring · auto-escalation\n\n"
                "`/sparkelite status` — View current Premium / Elite tier status"
            ), False),
            ("Premium Feature Summary", (
                "All premium commands are available to any server with SP4RK:\n\n"
                "📖 **p.6** `/rr` — Advanced RR Panels (buttons + dropdowns)\n"
                "📖 **p.7** `/logs` + `/automod` — Logging & AutoMod slash config\n"
                "📖 **p.8** `/tag` — Tag system with variable substitution\n"
                "📖 **p.9** `/case` + `/softban` — Case management\n"
                "📖 **p.10** `/stats` — Analytics & mood system\n"
                "📖 **p.11** `/rule` + `/perm` — Automation & permissions"
            ), False),
        ]
    ))

    # stamp each page with its number
    total = len(pages)
    for i, e in enumerate(pages):
        e.set_footer(text=f"SP4RK Manual  •  Page {i+1}/{total}  •  Use ‹ › to navigate")

    return pages


class ManualView(discord.ui.View):
    """Paginated view for /manual — persistent ‹ / › navigation."""

    def __init__(self, pages: list, author_id: int):
        super().__init__(timeout=300)
        self.pages     = pages
        self.index     = 0
        self.author_id = author_id
        self._sync_buttons()

    def _sync_buttons(self):
        self.btn_prev.disabled = self.index == 0
        self.btn_next.disabled = self.index == len(self.pages) - 1
        self.btn_page.label    = f"{self.index + 1} / {len(self.pages)}"

    @discord.ui.button(label="‹", style=discord.ButtonStyle.secondary, custom_id="manual_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ Only the person who ran `/manual` can navigate this.", ephemeral=True
            )
        self.index -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="1 / 12", style=discord.ButtonStyle.primary, custom_id="manual_page", disabled=True)
    async def btn_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # decorative page counter — never actually called

    @discord.ui.button(label="›", style=discord.ButtonStyle.secondary, custom_id="manual_next")
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ Only the person who ran `/manual` can navigate this.", ephemeral=True
            )
        self.index += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


@bot.tree.command(name="manual", description="Browse the full SP4RK bot manual with ‹ › page navigation")
async def slash_manual(interaction: discord.Interaction):
    pages = _build_manual_pages()
    view  = ManualView(pages, author_id=interaction.user.id)
    await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)


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
        return await interaction.response.send_message(
            "❌ **Server Only** — This command can only be used inside a server, not in DMs.", ephemeral=True
        )
    embed = _build_serverinfo_embed(interaction.guild, interaction.user)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="bot", description="Show SP4RK bot details and status")
async def slash_bot(interaction: discord.Interaction):
    uptime = datetime.utcnow() - BOT_START_TIME
    hours, rem = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    latency_ms = round(bot.latency * 1000)
    guild_count = len(bot.guilds)
    total_members = sum(g.member_count for g in bot.guilds)
    embed = discord.Embed(
        title="🛡️ SP4RK — Sentinel OS",
        description=(
            "**AI-Powered Discord Moderation**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "SP4RK is a powerful moderation and security bot built for Discord servers.\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x3a8fff,
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="⚡ Latency",      value=f"**{latency_ms}ms**",    inline=True)
    embed.add_field(name="⏱️ Uptime",        value=f"**{uptime_str}**",      inline=True)
    embed.add_field(name="🔢 Version",       value="**V2.5.0**",             inline=True)
    embed.add_field(name="🌐 Servers",       value=f"**{guild_count:,}**",   inline=True)
    embed.add_field(name="👥 Total Members", value=f"**{total_members:,}**", inline=True)
    if interaction.guild and interaction.guild.me.display_avatar:
        embed.set_thumbnail(url=interaction.guild.me.display_avatar.url)
    embed.set_footer(text="SP4RK — Your Server's Guardian  •  Byte Labs", icon_url=interaction.guild.me.display_avatar.url if interaction.guild else discord.Embed.Empty)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="dashboard", description="Mini admin dashboard (admin only)")
async def slash_dashboard(interaction: discord.Interaction):
    if not _is_bot_admin(interaction):
        await interaction.response.send_message("❌ **Access Denied** — You need the Admin role or Administrator permission.", ephemeral=True)
        return
    refresh_config()
    guild = interaction.guild
    total_warnings = sum(
        (v.get("count", 0) if isinstance(v, dict) else v)
        for g in warnings_db.values()
        for v in (g.values() if isinstance(g, dict) else [])
    )
    uptime = datetime.utcnow() - BOT_START_TIME
    hours, rem = divmod(int(uptime.total_seconds()), 3600)
    minutes = rem // 60
    _scfg = get_config_for_guild(str(guild.id))
    ai_status = "🟢 **Online**" if GEMINI_KEY else "🔴 **Offline**"
    raid_status = "🟢 **Active**" if _scfg.get("anti_raid") == "ON" else "🔴 **Inactive**"
    warn_status = "🟠 **Elevated**" if total_warnings > 10 else "🟢 **Normal**"
    embed = discord.Embed(
        title="🖥️ SP4RK — Admin Dashboard",
        description=(
            f"**Server:** {guild.name}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Real-time overview of moderation health and bot status.\n"
            f"For full controls, visit your web dashboard.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x3a8fff,
        timestamp=datetime.utcnow(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="👥 Members",          value=f"**{guild.member_count:,}**",                      inline=True)
    embed.add_field(name="⚠️ Total Warnings",   value=f"**{total_warnings}** — {warn_status}",            inline=True)
    embed.add_field(name="⏱️ Uptime",            value=f"**{hours}h {minutes}m**",                         inline=True)
    embed.add_field(name="🔰 Prefix",           value=f"`{_scfg.get('prefix','!')}`",                     inline=True)
    embed.add_field(name="🔍 Profanity Filter", value=f"**{_scfg.get('profanity_level','low').upper()}**", inline=True)
    embed.add_field(name="🛡️ Anti-Raid",        value=raid_status,                                        inline=True)
    embed.add_field(name="🤖 AI Moderation",    value=ai_status,                                          inline=True)
    embed.set_footer(text="SP4RK Admin Dashboard  •  Full controls at your web dashboard URL")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================
# 🎫 /ticket-panel  — post a custom ticket panel with a button
# ============================================================

# ---------------- EVENTS ----------------
@bot.event
async def on_ready():
    refresh_config()
    # Wire up instant config refresh: dashboard save → refresh_config() immediately
    set_bot_config_refresh_callback(refresh_config)
    s_map = {"online": discord.Status.online, "idle": discord.Status.idle, "dnd": discord.Status.dnd}
    await bot.change_presence(status=s_map.get(config.get("bot_status", "online"), discord.Status.online))
    try:
        # Guild-level sync first — propagates INSTANTLY (no 1-hour wait)
        for _guild in bot.guilds:
            try:
                bot.tree.copy_global_to(guild=_guild)
                await bot.tree.sync(guild=_guild)
            except Exception:
                pass
        # Global sync for new guilds the bot joins later
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} global slash command(s) + guild-level instant sync done")
    except Exception as e:
        print(f"Slash command sync error: {e}")
    sync_bot_settings.start()
    daily_report_task.start()
    if not _reminder_tick.is_running():
        _reminder_tick.start()
    # Check security_2fa setting for all connected guilds on startup.
    for guild in bot.guilds:
        gcfg = get_config_for_guild(str(guild.id))
        try:
            await security.check_security_2fa(bot, guild, gcfg, log_callback=dlog)
        except Exception as _e:
            print(f"[2FA] startup check error: {_e}")

    dlog("✅ BOT-START", f"SP4RK online as {bot.user}")
    print(f"✅ SP4RK Online! Logged in as {bot.user}")
    import asyncio as _asyncio2
    await _asyncio2.get_event_loop().run_in_executor(None, _persist_bot_guilds, bot)

def _persist_bot_guilds(b):
    """Write the bot's current guild list to bot_guilds.json so the web
    dashboard can filter the server picker to only guilds the bot is in."""
    try:
        import json as _j, os as _os
        data = [
            {"id": str(g.id), "name": g.name, "member_count": g.member_count or 0}
            for g in b.guilds
        ]
        path = _os.path.join(_os.path.dirname(__file__), "bot_guilds.json")
        with open(path, "w") as _f:
            _j.dump(data, _f)
    except Exception as _e:
        print(f"[bot_guilds] persist error: {_e}")

@bot.event
async def on_guild_join(guild: discord.Guild):
    """When SP4RK joins a new server, create an admin role and assign it to itself."""
    bot_member = guild.me
    try:
        # Already has Administrator — nothing to do
        if bot_member.guild_permissions.administrator:
            dlog("🏠 GUILD-JOIN", f"Joined {guild.name} ({guild.id}) — already Administrator")
            return

        # Create the role with Administrator permission
        admin_role = await guild.create_role(
            name="SP4RK",
            permissions=discord.Permissions(administrator=True),
            colour=discord.Colour.blurple(),
            reason="SP4RK auto-setup: admin role for bot management",
        )

        # Move it to just below the top position so it sits above regular roles
        top_pos = max((r.position for r in guild.roles), default=1)
        try:
            await admin_role.edit(position=max(top_pos - 1, 1))
        except Exception:
            pass  # position edit needs Manage Roles — not critical

        await bot_member.add_roles(admin_role, reason="SP4RK auto-setup")
        dlog("🏠 GUILD-JOIN", f"Joined {guild.name} ({guild.id}) — created & assigned SP4RK admin role")

        # Try to greet in the first available text channel
        for ch in guild.text_channels:
            if ch.permissions_for(bot_member).send_messages:
                await ch.send(
                    "👋 Thanks for adding **SP4RK**!\n"
                    "I've created a **SP4RK** admin role and assigned it to myself so I can manage the server.\n"
                    "Use `/help` to get started or visit the dashboard to configure me."
                )
                break
    except discord.Forbidden:
        dlog("🏠 GUILD-JOIN", f"Joined {guild.name} ({guild.id}) — missing permissions to create admin role")
    except Exception as e:
        print(f"[GUILD-JOIN] Error setting up admin role in {guild.name}: {e}")
    _persist_bot_guilds(bot)

@bot.event
async def on_guild_remove(guild: discord.Guild):
    """Keep bot_guilds.json up to date when SP4RK leaves a server."""
    _persist_bot_guilds(bot)
    dlog("🚪 GUILD-LEAVE", f"Left {guild.name} ({guild.id})")

@bot.event
async def on_member_join(member):
    refresh_config()
    gcfg = get_config_for_guild(str(member.guild.id))
    await roles.assign_auto_role(member, gcfg)
    await moderation.handle_auto_role(member, gcfg)
    await security.anti_raid_check(member, gcfg, dm_callback=dm_all_owners, log_callback=dlog)
    await logs.log_template(bot, config, "sp4rk.welcome", user=member, server=member.guild)
    dlog("👋 JOIN", f"{member} joined {member.guild.name}", guild_id=member.guild.id)
    try:
        import db_spark as _dbs
        _dbs.analytics_record_join(str(member.guild.id))
    except Exception:
        pass

@bot.event
async def on_member_update(before, after):
    refresh_config()
    gcfg = get_config_for_guild(str(after.guild.id))
    await security.role_monitor_log(bot, gcfg, after, before, after, log_callback=dlog)
    try:
        await security.check_security_2fa(bot, after.guild, gcfg, log_callback=dlog)
    except Exception as _e:
        print(f"[2FA] member_update check error: {_e}")

    gid = str(after.guild.id)
    log_ch = gcfg.get("log_channel", "").strip()
    if not log_ch:
        return

    try:
        # Nickname change
        if before.nick != after.nick:
            old_nick = before.nick or before.name
            new_nick = after.nick or after.name
            await logs.log_event(
                bot, config,
                "✏️ Nickname Changed",
                f"**{after}** — `{old_nick}` → `{new_nick}`\n🆔 User ID: `{after.id}`",
                color=0x3498db, guild_id=gid,
            )
            dlog("✏️ NICK-CHANGE", f"{after} changed nickname: {old_nick} → {new_nick}", guild_id=after.guild.id)

        # Role changes
        before_roles = set(before.roles)
        after_roles  = set(after.roles)
        added_roles   = after_roles - before_roles
        removed_roles = before_roles - after_roles
        for role in added_roles:
            await logs.log_event(
                bot, config,
                "🏷️ Role Added",
                f"**{after}** was given the **{role.name}** role\n🆔 User ID: `{after.id}`",
                color=0x2ecc71, guild_id=gid,
            )
            dlog("🏷️ ROLE-ADD", f"{after} → +{role.name}", guild_id=after.guild.id)
        for role in removed_roles:
            await logs.log_event(
                bot, config,
                "🏷️ Role Removed",
                f"**{after}** lost the **{role.name}** role\n🆔 User ID: `{after.id}`",
                color=0xe67e22, guild_id=gid,
            )
            dlog("🏷️ ROLE-REMOVE", f"{after} → -{role.name}", guild_id=after.guild.id)

        # Timeout applied/removed (distinct from moderation command — catches manual Discord UI action)
        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until:
                await logs.log_event(
                    bot, config,
                    "⏱️ Member Timed Out",
                    f"**{after}** has been timed out until <t:{int(after.timed_out_until.timestamp())}:F>\n🆔 User ID: `{after.id}`",
                    color=0xf39c12, guild_id=gid,
                )
            else:
                await logs.log_event(
                    bot, config,
                    "✅ Timeout Removed",
                    f"**{after}**'s timeout was removed.\n🆔 User ID: `{after.id}`",
                    color=0x2ecc71, guild_id=gid,
                )
    except Exception as _e:
        print(f"[member_update log] {_e}")

@bot.event
async def on_member_ban(guild, user):
    refresh_config()
    await logs.log_template(bot, config, "sp4rk.member_banned", user=user, server=guild)
    _increment_report_action()
    dlog("⛔ BAN", f"{user} banned from {guild.name}", guild_id=guild.id)

@bot.event
async def on_member_remove(member):
    refresh_config()
    await logs.log_template(bot, config, "sp4rk.leave", user=member, server=member.guild)
    dlog("🚪 LEAVE", f"{member} left {member.guild.name}", guild_id=member.guild.id)
    try:
        import db_spark as _dbs
        _dbs.analytics_record_leave(str(member.guild.id))
    except Exception:
        pass

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None:
        return
    # Mood signal: reactions are a positive engagement indicator
    try:
        import db_spark as _dbs
        _dbs.mood_record_signal(
            str(payload.guild_id), 1,
            f"Reaction added: {payload.emoji}",
            str(payload.user_id), "positive",
        )
    except Exception:
        pass

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None:
        return

@bot.event
async def on_message_edit(before, after):
    if before.author.bot:
        return
    if before.content == after.content:
        return
    refresh_config()
    gcfg = get_config_for_guild(str(before.guild.id)) if before.guild else config
    log_ch = gcfg.get("message_log_channel", "").strip() or gcfg.get("log_channel", "").strip()
    if not log_ch:
        return
    await logs.log_template(
        bot, config, "sp4rk.message_edit",
        user=before.author, channel=before.channel,
        before=(before.content[:1000] or "*empty*"),
        after=(after.content[:1000] or "*empty*"),
        jump_url=after.jump_url,
    )
    dlog("✏️ MSG-EDIT", f"{before.author} edited a message in #{before.channel.name}", guild_id=before.guild.id if before.guild else None)

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
    gcfg = get_config_for_guild(str(message.guild.id)) if message.guild else config
    log_ch = gcfg.get("message_log_channel", "").strip() or gcfg.get("log_channel", "").strip()
    if not log_ch:
        return
    await logs.log_template(
        bot, config, "sp4rk.message_delete",
        user=message.author, channel=message.channel,
        content=(message.content[:1000] or "*empty / attachment*"),
    )
    dlog("🗑️ MSG-DELETE", f"{message.author} deleted a message in #{message.channel.name}", guild_id=message.guild.id if message.guild else None)

_voice_sessions: dict = {}  # (guild_id, user_id) -> datetime of VC join

@bot.event
async def on_voice_state_update(member, before, after):
    refresh_config()

    # ---- Analytics: track voice session minutes ----
    try:
        import db_spark as _dbs
        key = (str(member.guild.id), str(member.id))
        if before.channel is None and after.channel is not None:
            _voice_sessions[key] = datetime.utcnow()
        elif before.channel is not None and after.channel is None:
            if key in _voice_sessions:
                delta = datetime.utcnow() - _voice_sessions.pop(key)
                mins = int(delta.total_seconds() / 60)
                if mins > 0:
                    _dbs.analytics_record_voice(
                        str(member.guild.id), str(member.id), str(member), mins
                    )
    except Exception:
        pass

    gcfg = get_config_for_guild(str(member.guild.id))
    log_ch = gcfg.get("voice_log_channel", "").strip() or gcfg.get("log_channel", "").strip()
    if not log_ch:
        return

    if before.channel == after.channel:
        # Channel didn't change — check for mute/deafen/stream state changes
        try:
            gid = str(member.guild.id)
            if before.self_mute != after.self_mute:
                state = "muted" if after.self_mute else "unmuted"
                await logs.log_event(bot, config, "🔇 Voice Mute",
                    f"**{member}** {state} themselves in **{after.channel.name if after.channel else 'VC'}**",
                    color=0x95a5a6, guild_id=gid)
            if before.self_deaf != after.self_deaf:
                state = "deafened" if after.self_deaf else "undeafened"
                await logs.log_event(bot, config, "🔕 Voice Deafen",
                    f"**{member}** {state} themselves in **{after.channel.name if after.channel else 'VC'}**",
                    color=0x95a5a6, guild_id=gid)
            if before.mute != after.mute:
                state = "server-muted" if after.mute else "server-unmuted"
                await logs.log_event(bot, config, "🔇 Server Mute",
                    f"**{member}** was {state} in **{after.channel.name if after.channel else 'VC'}**",
                    color=0xe74c3c, guild_id=gid)
            if before.deaf != after.deaf:
                state = "server-deafened" if after.deaf else "server-undeafened"
                await logs.log_event(bot, config, "🔕 Server Deafen",
                    f"**{member}** was {state} in **{after.channel.name if after.channel else 'VC'}**",
                    color=0xe74c3c, guild_id=gid)
        except Exception as e:
            print(f"Voice state change log error: {e}")
        return

    try:
        if before.channel is None:
            await logs.log_template(bot, config, "sp4rk.voice_join",
                                    user=member, channel=after.channel)
            dlog("🔊 VC-JOIN", f"{member} joined VC: {after.channel.name}", guild_id=member.guild.id)
        elif after.channel is None:
            await logs.log_template(bot, config, "sp4rk.voice_leave",
                                    user=member, channel=before.channel)
            dlog("🔇 VC-LEAVE", f"{member} left VC: {before.channel.name}", guild_id=member.guild.id)
        else:
            move_chan = f"**{before.channel.name}** → **{after.channel.name}**"
            await logs.log_template(bot, config, "sp4rk.voice_leave",
                                    user=member, channel=move_chan)
            dlog("🔀 VC-MOVE", f"{member} moved: {before.channel.name} → {after.channel.name}", guild_id=member.guild.id)
    except Exception as e:
        print(f"Voice log error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  COMPREHENSIVE SERVER LOGGING — every server moment captured
# ─────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_member_unban(guild, user):
    """Fires when any ban is lifted (manual or via !unban command)."""
    refresh_config()
    gid = str(guild.id)
    await logs.log_event(
        bot, config,
        "✅ Member Unbanned",
        f"**{user}** (`{user.id}`) has been unbanned from **{guild.name}**.",
        color=0x2ecc71, guild_id=gid,
    )
    dlog("✅ UNBAN-EVENT", f"{user} unbanned from {guild.name}", guild_id=guild.id)


@bot.event
async def on_guild_role_create(role):
    refresh_config()
    gid = str(role.guild.id)
    perms_val = role.permissions.value
    await logs.log_event(
        bot, config,
        "🏷️ Role Created",
        f"**{role.name}** was created.\n"
        f"🎨 Colour: `{str(role.colour)}`\n"
        f"📋 Permissions value: `{perms_val}`\n"
        f"🔵 Mentionable: `{role.mentionable}` | Hoisted: `{role.hoist}`\n"
        f"🆔 Role ID: `{role.id}`",
        color=0x2ecc71, guild_id=gid,
    )
    dlog("🏷️ ROLE-CREATE", f"Role created: {role.name} ({role.id})", guild_id=role.guild.id)


@bot.event
async def on_guild_role_delete(role):
    refresh_config()
    gid = str(role.guild.id)
    await logs.log_event(
        bot, config,
        "🗑️ Role Deleted",
        f"**{role.name}** was permanently deleted.\n"
        f"🆔 Role ID: `{role.id}`",
        color=0xe74c3c, guild_id=gid,
    )
    dlog("🗑️ ROLE-DELETE", f"Role deleted: {role.name} ({role.id})", guild_id=role.guild.id)


@bot.event
async def on_guild_role_update(before, after):
    refresh_config()
    gid = str(after.guild.id)
    changes = []
    if before.name != after.name:
        changes.append(f"📝 Name: `{before.name}` → `{after.name}`")
    if before.colour != after.colour:
        changes.append(f"🎨 Colour: `{before.colour}` → `{after.colour}`")
    if before.hoist != after.hoist:
        changes.append(f"📌 Hoisted: `{before.hoist}` → `{after.hoist}`")
    if before.mentionable != after.mentionable:
        changes.append(f"🔔 Mentionable: `{before.mentionable}` → `{after.mentionable}`")
    if before.permissions != after.permissions:
        changes.append(f"🔑 Permissions changed (value: `{before.permissions.value}` → `{after.permissions.value}`)")
    if not changes:
        return
    await logs.log_event(
        bot, config,
        "✏️ Role Updated",
        f"**{after.name}** (`{after.id}`) was modified:\n" + "\n".join(changes),
        color=0x3498db, guild_id=gid,
    )
    dlog("✏️ ROLE-UPDATE", f"Role updated: {after.name}", guild_id=after.guild.id)


@bot.event
async def on_guild_channel_create(channel):
    refresh_config()
    gid = str(channel.guild.id)
    ch_type = str(channel.type).replace("_", " ").title()
    category = channel.category.name if channel.category else "No Category"
    await logs.log_event(
        bot, config,
        "📁 Channel Created",
        f"**#{channel.name}** was created.\n"
        f"📂 Type: `{ch_type}`\n"
        f"🗂️ Category: `{category}`\n"
        f"🆔 Channel ID: `{channel.id}`",
        color=0x2ecc71, guild_id=gid,
    )
    dlog("📁 CH-CREATE", f"Channel created: #{channel.name}", guild_id=channel.guild.id)


@bot.event
async def on_guild_channel_delete(channel):
    refresh_config()
    gid = str(channel.guild.id)
    ch_type = str(channel.type).replace("_", " ").title()
    category = channel.category.name if channel.category else "No Category"
    await logs.log_event(
        bot, config,
        "🗑️ Channel Deleted",
        f"**#{channel.name}** was permanently deleted.\n"
        f"📂 Type: `{ch_type}`\n"
        f"🗂️ Category: `{category}`\n"
        f"🆔 Channel ID: `{channel.id}`",
        color=0xe74c3c, guild_id=gid,
    )
    dlog("🗑️ CH-DELETE", f"Channel deleted: #{channel.name}", guild_id=channel.guild.id)


@bot.event
async def on_guild_channel_update(before, after):
    refresh_config()
    gid = str(after.guild.id)
    changes = []
    if before.name != after.name:
        changes.append(f"📝 Name: `#{before.name}` → `#{after.name}`")
    topic_before = getattr(before, "topic", None)
    topic_after  = getattr(after, "topic", None)
    if topic_before != topic_after:
        changes.append(f"📋 Topic: `{topic_before or 'none'}` → `{topic_after or 'none'}`")
    slowmode_before = getattr(before, "slowmode_delay", None)
    slowmode_after  = getattr(after, "slowmode_delay", None)
    if slowmode_before != slowmode_after:
        changes.append(f"🐢 Slowmode: `{slowmode_before}s` → `{slowmode_after}s`")
    nsfw_before = getattr(before, "nsfw", None)
    nsfw_after  = getattr(after, "nsfw", None)
    if nsfw_before != nsfw_after:
        changes.append(f"🔞 NSFW: `{nsfw_before}` → `{nsfw_after}`")
    cat_before = before.category.name if before.category else "None"
    cat_after  = after.category.name if after.category else "None"
    if cat_before != cat_after:
        changes.append(f"🗂️ Category: `{cat_before}` → `{cat_after}`")
    if not changes:
        return
    await logs.log_event(
        bot, config,
        "✏️ Channel Updated",
        f"**#{after.name}** (`{after.id}`) was modified:\n" + "\n".join(changes),
        color=0x3498db, guild_id=gid,
    )
    dlog("✏️ CH-UPDATE", f"Channel updated: #{after.name}", guild_id=after.guild.id)


@bot.event
async def on_guild_update(before, after):
    refresh_config()
    gid = str(after.id)
    changes = []
    if before.name != after.name:
        changes.append(f"📝 Name: `{before.name}` → `{after.name}`")
    if before.icon != after.icon:
        changes.append("🖼️ Server icon was changed.")
    if before.banner != after.banner:
        changes.append("🖼️ Server banner was changed.")
    if before.description != after.description:
        changes.append(f"📋 Description updated.")
    if before.verification_level != after.verification_level:
        changes.append(f"🔒 Verification level: `{before.verification_level}` → `{after.verification_level}`")
    if before.explicit_content_filter != after.explicit_content_filter:
        changes.append(f"🔞 Explicit content filter: `{before.explicit_content_filter}` → `{after.explicit_content_filter}`")
    if before.default_notifications != after.default_notifications:
        changes.append(f"🔔 Default notifications: `{before.default_notifications}` → `{after.default_notifications}`")
    if not changes:
        return
    await logs.log_event(
        bot, config,
        "⚙️ Server Updated",
        f"**{after.name}** server settings were changed:\n" + "\n".join(changes),
        color=0x9b59b6, guild_id=gid,
    )
    dlog("⚙️ SERVER-UPDATE", f"Server updated: {after.name}", guild_id=after.id)


@bot.event
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent):
    if not payload.guild_id:
        return
    refresh_config()
    gid = str(payload.guild_id)
    count = len(payload.message_ids)
    channel = bot.get_channel(payload.channel_id)
    ch_name = f"<#{payload.channel_id}>" if channel else f"Channel `{payload.channel_id}`"
    await logs.log_event(
        bot, config,
        "🗑️ Bulk Messages Deleted",
        f"**{count}** messages were bulk-deleted in {ch_name}.\n"
        f"🆔 Channel ID: `{payload.channel_id}`",
        color=0xe74c3c, guild_id=gid,
    )
    dlog("🗑️ BULK-DELETE", f"{count} messages bulk-deleted", guild_id=payload.guild_id)


@bot.event
async def on_invite_create(invite):
    if not invite.guild:
        return
    refresh_config()
    gid = str(invite.guild.id)
    inviter = invite.inviter or "Unknown"
    max_uses = invite.max_uses or "∞"
    expires = (
        f"<t:{int(invite.expires_at.timestamp())}:R>" if invite.expires_at else "Never"
    )
    await logs.log_event(
        bot, config,
        "📨 Invite Created",
        f"**{inviter}** created an invite.\n"
        f"🔗 Code: `{invite.code}`\n"
        f"📊 Max uses: `{max_uses}`\n"
        f"⏰ Expires: {expires}\n"
        f"📁 Channel: <#{invite.channel.id if invite.channel else 0}>",
        color=0x3498db, guild_id=gid,
    )
    dlog("📨 INVITE-CREATE", f"Invite {invite.code} created by {inviter}", guild_id=invite.guild.id)


@bot.event
async def on_invite_delete(invite):
    if not invite.guild:
        return
    refresh_config()
    gid = str(invite.guild.id)
    await logs.log_event(
        bot, config,
        "❌ Invite Deleted",
        f"Invite code `{invite.code}` was deleted.\n"
        f"📁 Channel: <#{invite.channel.id if invite.channel else 0}>",
        color=0xe67e22, guild_id=gid,
    )
    dlog("❌ INVITE-DELETE", f"Invite {invite.code} deleted", guild_id=invite.guild.id)


@bot.event
async def on_thread_create(thread):
    refresh_config()
    gid = str(thread.guild.id)
    owner = thread.owner or "Unknown"
    await logs.log_event(
        bot, config,
        "🧵 Thread Created",
        f"**{thread.name}** was created in <#{thread.parent_id}>.\n"
        f"👤 Created by: **{owner}**\n"
        f"🆔 Thread ID: `{thread.id}`",
        color=0x2ecc71, guild_id=gid,
    )
    dlog("🧵 THREAD-CREATE", f"Thread created: {thread.name}", guild_id=thread.guild.id)


@bot.event
async def on_thread_delete(thread):
    refresh_config()
    gid = str(thread.guild.id)
    await logs.log_event(
        bot, config,
        "🗑️ Thread Deleted",
        f"Thread **{thread.name}** was deleted.\n"
        f"📁 Parent: <#{thread.parent_id}>\n"
        f"🆔 Thread ID: `{thread.id}`",
        color=0xe74c3c, guild_id=gid,
    )
    dlog("🗑️ THREAD-DELETE", f"Thread deleted: {thread.name}", guild_id=thread.guild.id)


@bot.event
async def on_thread_update(before, after):
    refresh_config()
    gid = str(after.guild.id)
    if not before.archived and after.archived:
        await logs.log_event(
            bot, config,
            "📦 Thread Archived",
            f"Thread **{after.name}** was archived.\n"
            f"📁 Parent: <#{after.parent_id}>\n"
            f"🆔 Thread ID: `{after.id}`",
            color=0x95a5a6, guild_id=gid,
        )
        dlog("📦 THREAD-ARCHIVE", f"Thread archived: {after.name}", guild_id=after.guild.id)
    elif before.name != after.name:
        await logs.log_event(
            bot, config,
            "✏️ Thread Renamed",
            f"Thread renamed: `{before.name}` → `{after.name}`\n"
            f"📁 Parent: <#{after.parent_id}>\n"
            f"🆔 Thread ID: `{after.id}`",
            color=0x3498db, guild_id=gid,
        )


@bot.event
async def on_guild_emojis_update(guild, before, after):
    refresh_config()
    gid = str(guild.id)
    before_ids = {e.id for e in before}
    after_ids  = {e.id for e in after}
    added   = [e for e in after if e.id not in before_ids]
    removed = [e for e in before if e.id not in after_ids]
    for emoji in added:
        await logs.log_event(
            bot, config,
            "😀 Emoji Added",
            f"New emoji **:{emoji.name}:** was added.\n"
            f"🆔 Emoji ID: `{emoji.id}`",
            color=0x2ecc71, guild_id=gid,
        )
    for emoji in removed:
        await logs.log_event(
            bot, config,
            "🗑️ Emoji Removed",
            f"Emoji **:{emoji.name}:** was removed.\n"
            f"🆔 Emoji ID: `{emoji.id}`",
            color=0xe74c3c, guild_id=gid,
        )


@bot.event
async def on_guild_stickers_update(guild, before, after):
    refresh_config()
    gid = str(guild.id)
    before_ids = {s.id for s in before}
    after_ids  = {s.id for s in after}
    added   = [s for s in after if s.id not in before_ids]
    removed = [s for s in before if s.id not in after_ids]
    for sticker in added:
        await logs.log_event(
            bot, config,
            "🏷️ Sticker Added",
            f"New sticker **{sticker.name}** was added.",
            color=0x2ecc71, guild_id=gid,
        )
    for sticker in removed:
        await logs.log_event(
            bot, config,
            "🗑️ Sticker Removed",
            f"Sticker **{sticker.name}** was removed.",
            color=0xe74c3c, guild_id=gid,
        )


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

    # ---- Analytics: track daily message count + server mood signal ----
    if message.guild:
        try:
            import db_spark as _dbs
            gid_str = str(message.guild.id)
            _dbs.analytics_record_message(gid_str)
            # Mood signal — score this message and record it
            score, reason, category = _dbs.classify_mood_signal(message.content or "")
            if score != 0:
                _dbs.mood_record_signal(gid_str, score, reason, str(message.author.id), category)
        except Exception:
            pass

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
                    await message.channel.send(
                        f"❌ **Not Found** — No pending approval for member ID `{target_id}`.\n"
                        f"> Use `!approve ban/kick/ignore <user_id>` with an ID from a pending alert."
                    )
                    return

                if action == "ignore":
                    del pending_approvals[matched_key]
                    await message.channel.send(
                        f"✅ **Alert Dismissed** — `{matched_data['username']}` has been cleared from the approval queue.\n"
                        f"> No action was taken against this member."
                    )
                    for oid in owner_ids:
                        if oid != str(message.author.id):
                            try:
                                o = await bot.fetch_user(int(oid))
                                if o:
                                    await o.send(f"ℹ️ Alert dismissed by {message.author} for `{matched_data['username']}`.")
                            except Exception:
                                pass
                    dlog("🚫 DISMISS", f"{message.author} dismissed alert for {matched_data['username']}", guild_id=message.guild.id if message.guild else None)
                    return

                if action in ("ban", "kick"):
                    guild = bot.get_guild(matched_data["guild_id"])
                    if not guild:
                        await message.channel.send(
                            f"❌ **Server Not Found** — The bot is no longer in the target server.\n"
                            f"> The approval has been cancelled."
                        )
                        return
                    try:
                        target_member = await guild.fetch_member(matched_data["member_id"])
                    except discord.NotFound:
                        await message.channel.send(
                            f"❌ **Member Left** — `{matched_data['username']}` is no longer in the server.\n"
                            f"> The approval has been cleared automatically."
                        )
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
                            f"{'⛔' if action == 'ban' else '🚪'} **{action.upper()} Executed** — `{matched_data['username']}`\n"
                            f"> 📝 **Reason:** {reason}\n"
                            f"> 🛡️ **Approved by:** {message.author}"
                        )
                        await logs.log_event(
                            bot, config,
                            f"{'⛔' if action=='ban' else '🚪'} Owner-Approved {action.capitalize()}",
                            f"`{matched_data['username']}` was {action}ned — approved by {message.author}.",
                            color=0xe74c3c if action == "ban" else 0xe67e22,
                            guild_id=guild.id
                        )
                        dlog(f"{'⛔' if action=='ban' else '🚪'} {action.upper()}", f"{matched_data['username']} {action}ned — approved by {message.author}", guild_id=guild.id)
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
                        await message.channel.send(
                            f"❌ **Permission Denied** — I can't {action} `{matched_data['username']}`.\n"
                            f"> Check my role hierarchy and bot permissions in that server."
                        )
                    except Exception as e:
                        await message.channel.send(f"❌ **Action Failed** — `{e}`")
                else:
                    await message.channel.send(
                        f"❌ **Invalid Action** — `{action}` is not recognised.\n"
                        f"> Usage: `!approve ban/kick/ignore <user_id>`"
                    )
            return

    refresh_config()

    guild_cfg = get_config_for_guild(str(message.guild.id)) if message.guild else config

    handled = await automod.handle_message(message, guild_cfg, add_warning_fn=add_warning)
    if not handled:
        await handle_ai_moderation(message)
        # Only process prefix commands when automod has NOT already deleted/acted
        # on the message — prevents a double-response where automod sends a warning
        # AND a command response both appear for the same message.
        await bot.process_commands(message)

# ---------------- COMMAND ERROR HANDLER ----------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        perms = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
        await ctx.send(f"❌ **Missing Permission** — You need **{perms}** to run this command.", delete_after=8)
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ **Member Not Found** — Make sure you @mention them or provide their exact ID.", delete_after=8)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ **Missing Argument** — `{error.param.name}` is required. Use `!commands` to see the correct usage.", delete_after=8)
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ **Invalid Argument** — Check `!commands` for the correct usage.", delete_after=8)
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"Unhandled command error: {error}")

# ---------------- MODERATION COMMANDS ----------------
def _hierarchy_block(ctx, member, action_word):
    """Return error message string if this COMMANDED action is blocked, else None.

    For human-issued commands we ONLY check role position — not permission flags
    (administrator / manage_guild). Those flags are checked by can_bot_action()
    which is reserved for *autonomous* automod actions.

    Rules:
      • Bot cannot act on itself.
      • Bot cannot act on the server owner.
      • Target's top role must be strictly below the bot's top role
        (Discord enforces this at the API level anyway).
      • Invoker's top role must be strictly above the target's top role,
        unless the invoker IS the server owner (owner can order any action).
    """
    if member.id == ctx.guild.me.id:
        return f"❌ **Action Blocked** — I cannot {action_word} myself."
    if member.id == ctx.guild.owner_id:
        return f"❌ **Action Blocked** — I cannot {action_word} the server owner."
    if member.top_role >= ctx.guild.me.top_role:
        return (
            f"❌ **Action Blocked** — Cannot {action_word} {member.mention}.\n"
            f"> Their role (**{member.top_role.name}**) is equal to or above mine. "
            f"Move SP4RK's role higher in Server Settings → Roles to fix this."
        )
    if ctx.author.id != ctx.guild.owner_id and member.top_role >= ctx.author.top_role:
        return (
            f"❌ **Action Blocked** — Cannot {action_word} {member.mention}.\n"
            f"> Their role is equal to or higher than yours."
        )
    return None

@bot.command()
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member, duration: str = None):
    """Mute a member. Optional duration: 30m, 2h, 1d. Example: !mute @user 30m"""
    block = _hierarchy_block(ctx, member, "mute")
    if block:
        await ctx.send(block)
        return
    await moderation.mute_member(ctx, member, get_config_for_guild(str(ctx.guild.id) if ctx.guild else '0'), duration)
    _increment_report_action()
    dlog("🔇 MUTE", f"{member} muted by {ctx.author}" + (f" for {duration}" if duration else ""), guild_id=ctx.guild.id if ctx.guild else None)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    """Unmute a member. Example: !unmute @user"""
    await moderation.unmute_member(ctx, member, get_config_for_guild(str(ctx.guild.id) if ctx.guild else '0'))
    dlog("🔊 UNMUTE", f"{member} unmuted by {ctx.author}", guild_id=ctx.guild.id if ctx.guild else None)

@bot.command(name="note")
@commands.has_permissions(manage_messages=True)
async def add_note(ctx, member: discord.Member, *, note: str):
    """Add a moderator note to a user. Usage: !note @user <note>"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    supabase_save_note(ctx.guild.id, member.id, str(member), note, str(ctx.author), ts)
    await ctx.send(
        f"📝 **Note Added** — {member.mention}\n"
        f"> 🛡️ **By:** {ctx.author.mention}\n"
        f"> 💬 **Note:** {note}",
        delete_after=10
    )
    dlog("📝 NOTE", f"Note added for {member} by {ctx.author}: {note}", guild_id=ctx.guild.id if ctx.guild else None)

@bot.command(name="notes")
@commands.has_permissions(manage_messages=True)
async def get_member_notes(ctx, member: discord.Member):
    """View all moderator notes for a user. Usage: !notes @user"""
    notes = supabase_get_notes(ctx.guild.id, member.id)
    embed = discord.Embed(
        title=f"📝 Moderator Notes — {member.display_name}",
        description=(
            f"**{member}** · `{member.id}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Private moderator annotations for this member.\n"
            f"Total notes on record: **{len(notes)}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x3a8fff,
        timestamp=datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if not notes:
        embed.add_field(name="📭 No Notes", value="*No moderator notes have been recorded for this member.*", inline=False)
    else:
        for n in notes[-10:]:
            embed.add_field(
                name=f"🕐 {n.get('timestamp', 'Unknown')}  •  by {n.get('moderator', '?')}",
                value=f"> {n.get('note', '—')}",
                inline=False
            )
    embed.set_footer(text=f"🆔 User ID: {member.id}  •  {len(notes)} total note(s)  •  SP4RK")
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

    _wlimit = int(get_config_for_guild(str(ctx.guild.id)).get('warning_limit', 30))
    pct = min(1.0, count / max(1, _wlimit))
    filled = int(10 * pct)
    bar = "▰" * filled + "▱" * (10 - filled)
    severity = "🔴 High Risk" if count >= _wlimit else ("🟠 Moderate" if count >= _wlimit // 2 else "🟢 Low Risk")
    embed = discord.Embed(
        title=f"📋 Full Mod Logs — {member.display_name}",
        description=(
            f"**{member}** · `{member.id}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Risk level: `{bar}` — **{severity}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0xe74c3c if count >= _wlimit else (0xf39c12 if count >= 2 else 0x2ecc71),
        timestamp=datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="⚠️ Warnings",   value=f"**{count}** / {_wlimit}", inline=True)
    embed.add_field(name="📝 Notes",       value=f"**{len(notes)}**",        inline=True)
    embed.add_field(name="📊 Risk Level",  value=severity,                   inline=True)

    if history:
        last5 = history[-5:][::-1]
        warn_text = "\n".join(f"`{i+1}.` {h['reason']} — *{h['time']}*" for i, h in enumerate(last5))
        embed.add_field(name="📋 Recent Warnings", value=warn_text, inline=False)

    if notes:
        last3 = notes[-3:]
        note_text = "\n".join(
            f"> *{n.get('note')}*\n> — {n.get('moderator')} · {n.get('timestamp')}"
            for n in last3
        )
        embed.add_field(name="📝 Recent Notes", value=note_text, inline=False)

    embed.set_footer(text=f"Requested by {ctx.author}  •  SP4RK Moderation")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def tempban(ctx, member: discord.Member, duration: str, *, reason="No reason provided"):
    """Temp-ban a member. Duration: 30m, 2h, 1d, 7d, etc."""
    block = _hierarchy_block(ctx, member, "temp-ban")
    if block:
        await ctx.send(block)
        return
    if not ctx.guild.me.guild_permissions.ban_members:
        await ctx.send("❌ **Missing Permission** — I need the **Ban Members** permission.\n> Add it to my role in Server Settings → Roles.")
        return
    seconds = parse_duration(duration)
    if not seconds:
        await ctx.send("❌ **Invalid Duration** — Use formats like `30m`, `2h`, `1d`, `7d`.")
        return

    allowed = await security.mass_action_prevent(ctx, "ban", config, log_callback=dlog)
    if not allowed:
        return

    label = duration_label(seconds)
    try:
        await member.ban(reason=f"[TEMP-BAN {label}] {reason}")
        await ctx.send(
            f"⏱️ **Member Temp-Banned** — {member.mention}\n"
            f"> 🕐 **Duration:** {label}  |  Unbanned <t:{int((datetime.utcnow() + timedelta(seconds=seconds)).timestamp())}:R>\n"
            f"> 🛡️ **By:** {ctx.author.mention}\n"
            f"> 📝 **Reason:** {reason}"
        )
        await logs.log_event(
            bot, config, "⏱️ Temp Ban",
            f"{member} temp-banned by {ctx.author} for {label} | Reason: {reason}",
            color=0xe74c3c, guild_id=ctx.guild.id if ctx.guild else None
        )
        await dm_owner("temp-ban", str(member), f"{label} — {reason}", str(ctx.author))
        _increment_report_action()
        dlog("⏱️ TEMP-BAN", f"{member} temp-banned for {label} by {ctx.author}", guild_id=ctx.guild.id if ctx.guild else None)

        # Schedule auto-unban
        async def auto_unban():
            await asyncio.sleep(seconds)
            try:
                await ctx.guild.unban(member, reason=f"Temp-ban expired ({label})")
                await logs.log_event(
                    bot, config, "✅ Temp Ban Expired",
                    f"{member} was automatically unbanned after {label}.",
                    color=0x2ecc71, guild_id=ctx.guild.id if ctx.guild else None
                )
                dlog("✅ UNBAN", f"Temp-ban expired for {member} after {label}", guild_id=ctx.guild.id if ctx.guild else None)
            except Exception as e:
                print(f"Auto-unban error for {member}: {e}")

        asyncio.create_task(auto_unban())

    except discord.Forbidden:
        await ctx.send(f"❌ **Permission Denied** — I can't ban {member.mention}.\n> Check role hierarchy and my permissions.")
    except Exception as e:
        await ctx.send(f"❌ **Temp-Ban Failed** — `{e}`")

@bot.command(name="purgereaction")
@commands.has_permissions(manage_messages=True)
async def purge_reaction(ctx, amount: int = 10):
    """Remove all reactions from the last <amount> messages. Usage: !purgereaction 20"""
    if amount < 1 or amount > 100:
        await ctx.send("❌ **Invalid Amount** — Please provide a number between 1 and 100.", delete_after=5)
        return
    try:
        cleared = 0
        async for message in ctx.channel.history(limit=amount):
            if message.reactions:
                await message.clear_reactions()
                cleared += 1
        msg = await ctx.send(
            f"🧹 **Reactions Purged** — Cleared reactions from **{cleared}** message(s) in {ctx.channel.mention}.\n"
            f"> 🛡️ **By:** {ctx.author.mention}"
        )
        await asyncio.sleep(5)
        try: await msg.delete()
        except: pass
        await logs.log_event(
            bot, config, "🧹 Purge Reactions",
            f"{ctx.author} cleared reactions from {cleared} messages in #{ctx.channel.name}",
            color=0x9b59b6, guild_id=ctx.guild.id if ctx.guild else None
        )
        dlog("🧹 PURGEREACTION", f"{ctx.author} cleared reactions in #{ctx.channel.name}", guild_id=ctx.guild.id if ctx.guild else None)
    except discord.Forbidden:
        await ctx.send("❌ **Permission Denied** — I need **Manage Messages** permission to clear reactions.")
    except Exception as e:
        await ctx.send(f"❌ **Purge Reactions Failed** — `{e}`")

@bot.command(name="purgeuser")
@commands.has_permissions(manage_messages=True)
async def purge_user(ctx, member: discord.Member, amount: int = 50):
    """Delete up to <amount> recent messages from a specific user. Usage: !purgeuser @user 50"""
    if amount < 1 or amount > 500:
        await ctx.send("❌ **Invalid Amount** — Please provide a number between 1 and 500.", delete_after=5)
        return
    try:
        deleted = await ctx.channel.purge(limit=amount, check=lambda m: m.author.id == member.id)
        msg = await ctx.send(
            f"🧹 **User Messages Purged** — Deleted **{len(deleted)}** message(s) from {member.mention}.\n"
            f"> 🛡️ **By:** {ctx.author.mention}"
        )
        await asyncio.sleep(5)
        try: await msg.delete()
        except: pass
        await logs.log_event(bot, config, "🧹 Purge User",
            f"{ctx.author} purged {len(deleted)} messages from {member} in #{ctx.channel.name}",
            color=0x9b59b6, guild_id=ctx.guild.id if ctx.guild else None)
        dlog("🧹 PURGEUSER", f"{ctx.author} purged {len(deleted)} from {member} in #{ctx.channel.name}", guild_id=ctx.guild.id if ctx.guild else None)
    except discord.Forbidden:
        await ctx.send("❌ **Permission Denied** — I don't have permission to delete messages in this channel.")
    except Exception as e:
        await ctx.send(f"❌ **Purge Failed** — `{e}`")

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
        await ctx.send(
            f"🔒 **Channel Locked** — {channel.mention}\n"
            f"> 🛡️ **By:** {ctx.author.mention}  |  Members can no longer send messages here."
        )
        await logs.log_event(bot, config, "🔒 Channel Locked",
            f"#{channel.name} locked by {ctx.author}", color=0xe67e22, guild_id=ctx.guild.id if ctx.guild else None)
        dlog("🔒 LOCK", f"#{channel.name} locked by {ctx.author}", guild_id=ctx.guild.id if ctx.guild else None)
    except discord.Forbidden:
        await ctx.send("❌ **Permission Denied** — I don't have permission to edit that channel.")
    except Exception as e:
        await ctx.send(f"❌ **Lock Failed** — `{e}`")

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
        await ctx.send(
            f"🔓 **Channel Unlocked** — {channel.mention}\n"
            f"> 🛡️ **By:** {ctx.author.mention}  |  Members can now send messages again."
        )
        await logs.log_event(bot, config, "🔓 Channel Unlocked",
            f"#{channel.name} unlocked by {ctx.author}", color=0x2ecc71, guild_id=ctx.guild.id if ctx.guild else None)
        dlog("🔓 UNLOCK", f"#{channel.name} unlocked by {ctx.author}", guild_id=ctx.guild.id if ctx.guild else None)
    except discord.Forbidden:
        await ctx.send("❌ **Permission Denied** — I don't have permission to edit that channel.")
    except Exception as e:
        await ctx.send(f"❌ **Unlock Failed** — `{e}`")

@bot.group(name="role", invoke_without_command=True)
@commands.has_permissions(manage_roles=True)
async def role_group(ctx):
    """Role management. Usage: !role add @user @role  |  !role remove @user @role"""
    _p = get_config_for_guild(str(ctx.guild.id) if ctx.guild else '0').get('prefix', '!')
    await ctx.send(f"ℹ️ **Role Management** — Usage:\n> `{_p}role add @user @role` — Assign a role\n> `{_p}role remove @user @role` — Remove a role")

@role_group.command(name="add")
@commands.has_permissions(manage_roles=True)
async def role_add(ctx, member: discord.Member, role: discord.Role):
    if role >= ctx.guild.me.top_role:
        await ctx.send(f"❌ **Action Blocked** — I can't manage **{role.name}**.\n> That role is higher than my top role.")
        return
    if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
        await ctx.send(f"❌ **Action Blocked** — You can't assign **{role.name}**.\n> That role is equal to or higher than your own top role.")
        return
    if role in member.roles:
        await ctx.send(f"ℹ️ **Already Assigned** — {member.mention} already has the **{role.name}** role.")
        return
    try:
        await member.add_roles(role, reason=f"Added by {ctx.author}")
        await ctx.send(
            f"✅ **Role Assigned** — {member.mention}\n"
            f"> 🏷️ **Role:** {role.mention}\n"
            f"> 🛡️ **By:** {ctx.author.mention}"
        )
        await logs.log_event(bot, config, "➕ Role Added",
            f"{ctx.author} gave @{role.name} to {member}", color=0x2ecc71, guild_id=ctx.guild.id if ctx.guild else None)
        dlog("➕ ROLE-ADD", f"{ctx.author} gave @{role.name} to {member}", guild_id=ctx.guild.id if ctx.guild else None)
    except discord.Forbidden:
        await ctx.send("❌ **Permission Denied** — I don't have permission to assign that role.")
    except Exception as e:
        await ctx.send(f"❌ **Role Add Failed** — `{e}`")

@role_group.command(name="remove")
@commands.has_permissions(manage_roles=True)
async def role_remove(ctx, member: discord.Member, role: discord.Role):
    if role >= ctx.guild.me.top_role:
        await ctx.send(f"❌ **Action Blocked** — I can't manage **{role.name}**.\n> That role is higher than my top role.")
        return
    if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
        await ctx.send(f"❌ **Action Blocked** — You can't remove **{role.name}**.\n> That role is equal to or higher than your own top role.")
        return
    if role not in member.roles:
        await ctx.send(f"ℹ️ **Not Assigned** — {member.mention} doesn't have the **{role.name}** role.")
        return
    try:
        await member.remove_roles(role, reason=f"Removed by {ctx.author}")
        await ctx.send(
            f"✅ **Role Removed** — {member.mention}\n"
            f"> 🏷️ **Role:** {role.mention}\n"
            f"> 🛡️ **By:** {ctx.author.mention}"
        )
        await logs.log_event(bot, config, "➖ Role Removed",
            f"{ctx.author} removed @{role.name} from {member}", color=0xe67e22, guild_id=ctx.guild.id if ctx.guild else None)
        dlog("➖ ROLE-REM", f"{ctx.author} removed @{role.name} from {member}", guild_id=ctx.guild.id if ctx.guild else None)
    except discord.Forbidden:
        await ctx.send("❌ **Permission Denied** — I don't have permission to remove that role.")
    except Exception as e:
        await ctx.send(f"❌ **Role Remove Failed** — `{e}`")

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
            await ctx.send(
                f"✅ **Nickname Updated** — {member.mention}\n"
                f"> 🏷️ **New Name:** {name}\n"
                f"> 🛡️ **By:** {ctx.author.mention}"
            )
        else:
            await ctx.send(
                f"✅ **Nickname Reset** — {member.mention}'s nickname has been cleared.\n"
                f"> 🛡️ **By:** {ctx.author.mention}"
            )
        dlog("📝 NICK", f"{ctx.author} renamed {member} to {name or '(reset)'}", guild_id=ctx.guild.id if ctx.guild else None)
    except discord.Forbidden:
        await ctx.send(f"❌ **Permission Denied** — I can't rename {member.mention}.\n> Check role hierarchy and my permissions.")
    except Exception as e:
        await ctx.send(f"❌ **Nickname Failed** — `{e}`")

@bot.command(name="voicekick", aliases=["vkick", "vdc"])
@commands.has_permissions(move_members=True)
async def voicekick(ctx, member: discord.Member):
    """Disconnect a member from voice. Usage: !voicekick @user"""
    if not member.voice or not member.voice.channel:
        await ctx.send(f"ℹ️ **Not in Voice** — {member.mention} is not currently in any voice channel.")
        return
    block = _hierarchy_block(ctx, member, "voice-kick")
    if block:
        await ctx.send(block)
        return
    try:
        ch_name = member.voice.channel.name
        await member.move_to(None, reason=f"Voice kick by {ctx.author}")
        await ctx.send(
            f"🔌 **Voice Disconnected** — {member.mention}\n"
            f"> 🔊 **Channel:** {ch_name}\n"
            f"> 🛡️ **By:** {ctx.author.mention}"
        )
        await logs.log_event(bot, config, "🔌 Voice Kick",
            f"{ctx.author} disconnected {member} from #{ch_name}", color=0xf39c12, guild_id=ctx.guild.id if ctx.guild else None)
        dlog("🔌 VKICK", f"{ctx.author} disconnected {member} from {ch_name}", guild_id=ctx.guild.id if ctx.guild else None)
    except discord.Forbidden:
        await ctx.send("❌ **Permission Denied** — I don't have permission to move that member.")
    except Exception as e:
        await ctx.send(f"❌ **Voice Kick Failed** — `{e}`")


# ---------------- !kickbot ----------------
@bot.command(name="kickbot")
@commands.has_permissions(kick_members=True)
async def cmd_kickbot(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Kick another bot if its role is lower than mine.  Usage: !kickbot @BotName [reason]"""
    ok, why = automod.can_kick_bot(member, ctx.guild.me)
    if not ok:
        await ctx.send(f"❌ **Action Blocked** — {why}")
        return
    if not ctx.guild.me.guild_permissions.kick_members:
        await ctx.send("❌ **Missing Permission** — I need the **Kick Members** permission.")
        return
    try:
        await member.kick(reason=f"Bot kick by {ctx.author} — {reason}")
        await ctx.send(
            f"🤖 **Bot Kicked** — {member.mention} (`{member}`)\n"
            f"> 🛡️ **By:** {ctx.author.mention}\n"
            f"> 📝 **Reason:** {reason}"
        )
        await logs.log_event(bot, config, "🤖 Bot Kick",
            f"{ctx.author} kicked bot {member} | Reason: {reason}",
            color=0xe67e22, guild_id=ctx.guild.id if ctx.guild else None)
        dlog("🤖 BOT-KICK", f"{ctx.author} kicked bot {member} — {reason}", guild_id=ctx.guild.id if ctx.guild else None)
    except discord.Forbidden:
        await ctx.send(
            f"❌ **Permission Denied** — I can't kick {member.mention}.\n"
            f"> Make sure my role is above their highest role in **Server Settings → Roles**."
        )
    except Exception as e:
        await ctx.send(f"❌ **Bot Kick Failed** — `{e}`")


@bot.command(name="snipe")
@commands.has_permissions(manage_messages=True)
async def snipe_cmd(ctx):
    """Show the last deleted message in this channel."""
    snipe = _snipe_cache.get(ctx.channel.id)
    if not snipe:
        await ctx.send("ℹ️ **Nothing to Snipe** — No recently deleted messages found in this channel.")
        return
    embed = discord.Embed(
        title="🔍 Sniped Message",
        description=(
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{snipe['content'][:1900]}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x95a5a6,
        timestamp=snipe["ts"],
    )
    embed.set_author(name=f"{snipe['author']} said:")
    embed.set_footer(text=f"🗑️ Deleted in #{ctx.channel.name}  •  SP4RK Snipe")
    await ctx.send(embed=embed)

@bot.command(name="massban")
@commands.has_permissions(administrator=True)
async def massban(ctx, *user_ids: int):
    """Ban many user IDs at once (raid response). Usage: !massban 123 456 789"""
    if not user_ids:
        await ctx.send("❌ **Missing IDs** — Provide one or more user IDs.\n> Usage: `!massban 123 456 789`")
        return
    if len(user_ids) > 50:
        await ctx.send("❌ **Too Many IDs** — Maximum is 50 IDs per command for safety.")
        return
    if not ctx.guild.me.guild_permissions.ban_members:
        await ctx.send("❌ **Missing Permission** — I need the **Ban Members** permission.")
        return
    allowed = await security.mass_action_prevent(ctx, "ban", config, log_callback=dlog)
    if not allowed:
        return
    confirm = await ctx.send(
        f"⚠️ **Mass Ban Confirmation** — You are about to ban **{len(user_ids)}** users.\n"
        f"> React ✅ within 15 seconds to confirm this action."
    )
    await confirm.add_reaction("✅")
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) == "✅" and reaction.message.id == confirm.id
    try:
        await bot.wait_for("reaction_add", timeout=15.0, check=check)
    except asyncio.TimeoutError:
        await ctx.send("❌ **Cancelled** — Mass ban was not confirmed in time.")
        return
    success, fail = 0, 0
    for uid in user_ids:
        try:
            await ctx.guild.ban(discord.Object(id=uid), reason=f"Mass-ban by {ctx.author}")
            success += 1
        except Exception:
            fail += 1
    await ctx.send(
        f"⛔ **Mass Ban Complete** — {len(user_ids)} targets processed.\n"
        f"> ✅ **Banned:** {success}  |  ❌ **Failed:** {fail}\n"
        f"> 🛡️ **By:** {ctx.author.mention}"
    )
    await logs.log_event(bot, config, "⛔ Mass Ban",
        f"{ctx.author} mass-banned {success} users ({fail} failed)", color=0xe74c3c, guild_id=ctx.guild.id if ctx.guild else None)
    dlog("⛔ MASSBAN", f"{ctx.author} mass-banned {success}/{len(user_ids)}", guild_id=ctx.guild.id if ctx.guild else None)

@bot.command()
@commands.has_permissions(manage_channels=True)
async def slowmode(ctx, seconds: int = 0):
    """Set slowmode for the current channel. 0 = disabled. Max 21600 (6h)."""
    if seconds < 0 or seconds > 21600:
        await ctx.send("❌ **Invalid Value** — Slowmode must be between `0` and `21600` seconds (6 hours).", delete_after=7)
        return
    try:
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await ctx.send(
                f"✅ **Slowmode Disabled** — {ctx.channel.mention} is back to normal speed.\n"
                f"> 🛡️ **By:** {ctx.author.mention}"
            )
            dlog("⏱️ SLOWMODE", f"Slowmode disabled in #{ctx.channel.name} by {ctx.author}", guild_id=ctx.guild.id if ctx.guild else None)
        else:
            label = duration_label(seconds)
            await ctx.send(
                f"🐌 **Slowmode Enabled** — {ctx.channel.mention}\n"
                f"> 🕐 **Delay:** {label} between messages\n"
                f"> 🛡️ **By:** {ctx.author.mention}"
            )
            dlog("⏱️ SLOWMODE", f"Slowmode set to {label} in #{ctx.channel.name} by {ctx.author}", guild_id=ctx.guild.id if ctx.guild else None)
        await logs.log_event(
            bot, config, "⏱️ Slowmode",
            f"#{ctx.channel.name} slowmode set to {seconds}s by {ctx.author}",
            color=0x3498db
        )
    except discord.Forbidden:
        await ctx.send("❌ **Permission Denied** — I don't have permission to edit this channel.")
    except Exception as e:
        await ctx.send(f"❌ **Slowmode Failed** — `{e}`")

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
    warn_limit = get_config_for_guild(str(guild.id)).get('warning_limit', 30)

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


@bot.tree.command(name="userinfo", description="Show a detailed profile card for a member")
@app_commands.describe(member="The member to inspect (defaults to yourself)")
async def slash_userinfo(interaction: discord.Interaction,
                         member: discord.Member = None):
    if not interaction.guild:
        return await interaction.response.send_message(
            "❌ **Server Only** — This command can only be used inside a server, not in DMs.", ephemeral=True
        )
    target = member or interaction.user
    await interaction.response.send_message(
        embed=_build_userinfo_embed(target, interaction.user)
    )


# ---------------- SECURITY COMMANDS ----------------
@bot.command()
async def lockdown(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ **Permission Denied** — Administrator permission required.")
        return
    gcfg = get_config_for_guild(str(ctx.guild.id))
    gcfg["temp_lockdown"] = "ON"
    await security.toggle_lockdown(ctx.guild, gcfg, enable=True, log_callback=dlog)
    await ctx.send("🚨 **Server Lockdown Active** — all channels locked. Use `!unlockdown` to lift it.")
    dlog("🚨 LOCKDOWN", f"Lockdown enabled in {ctx.guild.name} by {ctx.author}", guild_id=ctx.guild.id)

@bot.command()
async def unlockdown(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ **Permission Denied** — Administrator permission required.")
        return
    gcfg = get_config_for_guild(str(ctx.guild.id))
    gcfg["temp_lockdown"] = "OFF"
    await security.toggle_lockdown(ctx.guild, gcfg, enable=False, log_callback=dlog)
    await ctx.send("✅ **Lockdown Lifted** — Server is back to normal.")
    dlog("✅ UNLOCKDOWN", f"Lockdown lifted in {ctx.guild.name} by {ctx.author}", guild_id=ctx.guild.id)

@bot.command()
@commands.has_permissions(administrator=True)
async def refresh(ctx):
    refresh_config()
    await ctx.send(f"🔄 Config refreshed! Prefix for this server: `{get_config_for_guild(str(ctx.guild.id) if ctx.guild else '0').get('prefix', '!')}`")

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
            "❌ **Server Only** — This command can only be used inside a server, not in DMs.", ephemeral=True
        )
        return False
    return True


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    """User-friendly slash command error replies."""
    # Unwrap CommandInvokeError so we check the root cause
    original = getattr(error, "original", error)
    try:
        if isinstance(original, app_commands.MissingPermissions) or isinstance(error, app_commands.MissingPermissions):
            perms = getattr(original, "missing_permissions", None) or getattr(error, "missing_permissions", [])
            msg = f"❌ **Missing Permission** — You need the **{', '.join(perms)}** permission to use this command."
        elif isinstance(original, app_commands.BotMissingPermissions) or isinstance(error, app_commands.BotMissingPermissions):
            perms = getattr(original, "missing_permissions", None) or getattr(error, "missing_permissions", [])
            msg = f"❌ **Bot Missing Permission** — I need the **{', '.join(perms)}** permission to do that."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "❌ **Access Denied** — You don't have the required role or permission to use this command."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ **Slow Down** — Try again in **{error.retry_after:.1f}s**."
        elif isinstance(original, discord.Forbidden):
            msg = "❌ **Permission Denied** — I don't have the required Discord permissions for that action."
        elif isinstance(original, discord.NotFound):
            msg = "❌ **Not Found** — That user or resource could not be found."
        else:
            print(f"[slash error] {type(error).__name__}: {error}")
            if original is not error:
                print(f"[slash error] original: {type(original).__name__}: {original}")
            msg = "❌ **Something went wrong** — An unexpected error occurred. Please try again."

        # Always respond — never let the interaction silently expire.
        # If defer hasn't been called yet, send a direct response; otherwise use followup.
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.NotFound:
            # Interaction expired before we could respond — nothing we can do.
            print(f"[slash error] interaction expired, could not send: {msg}")
        except Exception as send_err:
            # Last resort: defer then followup
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
                await interaction.followup.send(msg, ephemeral=True)
            except Exception:
                print(f"[slash error handler] final fallback failed: {send_err}")
    except Exception as e:
        print(f"[slash error handler] failed: {e}")
        # Absolute last resort — make sure Discord gets SOME response
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An unexpected error occurred.", ephemeral=True
                )
        except Exception:
            pass


# ---------------- /warn ----------------
@bot.tree.command(name="warn", description="Warn a member with an optional reason")
@app_commands.describe(member="Who to warn", reason="Why")
@app_commands.check(_is_bot_mod)
async def slash_warn(interaction: discord.Interaction,
                     member: discord.Member, reason: str = "Violation"):
    if not await _slash_guard_guild(interaction):
        return
    # Role-position hierarchy check for commanded /warn.
    # We do NOT use can_bot_action() here — that blocks anyone with admin/manage_guild
    # perms and is reserved for autonomous automod. For a human-issued command the
    # only constraint is Discord's own role hierarchy.
    auth = interaction.user
    if member.id == interaction.guild.me.id:
        return await interaction.response.send_message("❌ I cannot warn myself.", ephemeral=True)
    if member.id == interaction.guild.owner_id:
        return await interaction.response.send_message("❌ Cannot warn the server owner.", ephemeral=True)
    if member.top_role >= interaction.guild.me.top_role:
        return await interaction.response.send_message(
            f"❌ Cannot warn {member.mention} — their role (**{member.top_role.name}**) is equal to or above mine.",
            ephemeral=True,
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
        f"⚠️ **Warning Issued** — {member.mention} has been warned.\n"
        f"> 📊 **Total Warnings:** {count}\n"
        f"> 📝 **Reason:** {reason}\n"
        f"> 🛡️ **Warned by:** {interaction.user.mention}"
    )
    await logs.log_event(
        bot, config, "⚠️ Warning Issued",
        f"{member} warned by {auth} | Reason: {reason}", color=0xf39c12,
        guild_id=interaction.guild_id if interaction.guild else None,
    )


# ---------------- /warnings ----------------
@bot.tree.command(name="warnings", description="Show a member's warning history")
@app_commands.describe(member="Whose warnings to view")
@app_commands.check(_is_bot_mod)
async def slash_warnings(interaction: discord.Interaction, member: discord.Member):
    if not await _slash_guard_guild(interaction):
        return
    entry   = _get_member_entry(str(interaction.guild.id), str(member.id))
    count   = entry["count"]
    history = entry["history"]
    limit   = int(get_config_for_guild(str(interaction.guild.id)).get("warning_limit", 30))
    pct = min(1.0, count / max(1, limit))
    filled = int(10 * pct)
    bar = "▰" * filled + "▱" * (10 - filled)
    status_emoji = "🔴" if count >= limit else ("🟠" if count >= limit // 2 else "🟢")
    status_label = "Limit Reached" if count >= limit else ("Watch" if count >= limit // 2 else "Clear")
    embed = discord.Embed(
        title=f"⚠️ Warning Record — {member.display_name}",
        description=(
            f"**{member}** · `{member.id}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Severity: `{bar}` {status_emoji} **{status_label}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0xe74c3c if count >= limit else (0xf39c12 if count > 0 else 0x2ecc71),
        timestamp=datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="⚠️ Warnings Issued", value=f"**{count}**",  inline=True)
    embed.add_field(name="🔔 Server Limit",     value=f"**{limit}**",  inline=True)
    embed.add_field(name="📊 Status",           value=f"{status_emoji} **{status_label}**", inline=True)
    if history:
        last5 = history[-5:][::-1]
        history_text = "\n".join(
            f"`{i+1}.` {h['reason']} — *{h['time']}*"
            for i, h in enumerate(last5)
        )
        embed.add_field(name=f"📋 Recent Warnings (last {len(last5)})", value=history_text, inline=False)
    else:
        embed.add_field(name="📋 History", value="*No warnings on record — clean slate!*", inline=False)
    embed.set_footer(text=f"🆔 User ID: {member.id}  •  SP4RK Moderation")
    await interaction.response.send_message(embed=embed)


# ---------------- /clearwarnings ----------------
@bot.tree.command(name="clearwarnings", description="Clear all warnings for a member")
@app_commands.describe(member="Whose warnings to clear")
@app_commands.check(_is_bot_admin)
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    if not await _slash_guard_guild(interaction):
        return
    guild_id, member_id = str(interaction.guild.id), str(member.id)
    if guild_id in warnings_db and member_id in warnings_db[guild_id]:
        warnings_db[guild_id][member_id] = {"count": 0, "history": []}
        save_warnings()
    supabase_clear_warnings(guild_id, member_id)
    dlog("🧹 CLEAR-WARN", f"Warnings cleared for {member} by {interaction.user}", guild_id=interaction.guild_id)
    await logs.log_event(
        bot, config, "🧹 Warnings Cleared",
        f"{member}'s warnings cleared by {interaction.user}", color=0x2ecc71,
        guild_id=interaction.guild_id if interaction.guild else None,
    )
    await interaction.response.send_message(
        f"✅ **Warnings Cleared** — All warnings have been removed for {member.mention}.\n"
        f"> 🛡️ **Cleared by:** {interaction.user.mention}"
    )


# ---------------- /kick ----------------
@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(member="Who to kick", reason="Why")
@app_commands.check(_is_bot_mod)
async def slash_kick(interaction: discord.Interaction,
                     member: discord.Member, reason: str = "No reason provided"):
    if not await _slash_guard_guild(interaction):
        return
    block = _hierarchy_block_slash(interaction, member, "kick")
    if block:
        return await interaction.response.send_message(block, ephemeral=True)
    if not interaction.guild.me.guild_permissions.kick_members:
        return await interaction.response.send_message(
            "❌ **Missing Permission** — I need the **Kick Members** permission.", ephemeral=True
        )
    await interaction.response.defer()
    try:
        await member.kick(reason=reason)
        await interaction.followup.send(
            f"🚪 **Member Kicked** — {member.mention}\n"
            f"> 🛡️ **By:** {interaction.user.mention}\n"
            f"> 📝 **Reason:** {reason}"
        )
        await logs.log_event(bot, config, "🚪 Kick",
            f"{member} kicked by {interaction.user} | Reason: {reason}", color=0xe67e22, guild_id=interaction.guild.id if interaction.guild else None)
        await dm_owner("kick", str(member), reason, str(interaction.user))
        supabase_save_mod_log(interaction.guild.id, "kick", str(member),
            str(interaction.user), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        _increment_report_action()
        dlog("🚪 KICK", f"{member} kicked by {interaction.user} — {reason}", guild_id=interaction.guild_id)
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ **Permission Denied** — I can't kick {member.mention}.\n> Check role hierarchy and my permissions.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ **Kick Failed** — `{e}`", ephemeral=True)


# ---------------- /kickbot ----------------
@bot.tree.command(name="kickbot", description="Kick another bot whose role is below mine")
@app_commands.describe(member="The bot to kick", reason="Why you're kicking this bot")
@app_commands.check(_is_bot_mod)
async def slash_kickbot(interaction: discord.Interaction,
                        member: discord.Member, reason: str = "No reason provided"):
    if not await _slash_guard_guild(interaction):
        return
    ok, why = automod.can_kick_bot(member, interaction.guild.me)
    if not ok:
        return await interaction.response.send_message(
            f"❌ **Action Blocked** — {why}", ephemeral=True
        )
    if not interaction.guild.me.guild_permissions.kick_members:
        return await interaction.response.send_message(
            "❌ **Missing Permission** — I need the **Kick Members** permission.", ephemeral=True
        )
    await interaction.response.defer()
    try:
        await member.kick(reason=f"Bot kick by {interaction.user} — {reason}")
        await interaction.followup.send(
            f"🤖 **Bot Kicked** — {member.mention} (`{member}`)\n"
            f"> 🛡️ **By:** {interaction.user.mention}\n"
            f"> 📝 **Reason:** {reason}"
        )
        await logs.log_event(bot, config, "🤖 Bot Kick",
            f"{interaction.user} kicked bot {member} | Reason: {reason}",
            color=0xe67e22, guild_id=interaction.guild.id if interaction.guild else None)
        supabase_save_mod_log(interaction.guild.id, "bot-kick", str(member),
            str(interaction.user), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        _increment_report_action()
        dlog("🤖 BOT-KICK", f"{member} kicked by {interaction.user} — {reason}", guild_id=interaction.guild_id)
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ **Permission Denied** — I can't kick {member.mention}.\n"
            f"> Make sure my role is above their highest role in **Server Settings → Roles**.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f"❌ **Bot Kick Failed** — `{e}`", ephemeral=True)


# ---------------- /ban ----------------
@bot.tree.command(name="ban", description="Permanently ban a member from the server")
@app_commands.describe(member="Who to ban", reason="Why")
@app_commands.check(_is_bot_mod)
async def slash_ban(interaction: discord.Interaction,
                    member: discord.Member, reason: str = "No reason provided"):
    if not await _slash_guard_guild(interaction):
        return
    block = _hierarchy_block_slash(interaction, member, "ban")
    if block:
        return await interaction.response.send_message(block, ephemeral=True)
    if not interaction.guild.me.guild_permissions.ban_members:
        return await interaction.response.send_message(
            "❌ **Missing Permission** — I need the **Ban Members** permission.", ephemeral=True
        )
    await interaction.response.defer()
    try:
        await member.ban(reason=reason)
        await interaction.followup.send(
            f"⛔ **Member Banned** — {member.mention}\n"
            f"> 🛡️ **By:** {interaction.user.mention}\n"
            f"> 📝 **Reason:** {reason}"
        )
        await logs.log_event(bot, config, "⛔ Ban",
            f"{member} banned by {interaction.user} | Reason: {reason}", color=0xe74c3c, guild_id=interaction.guild.id if interaction.guild else None)
        await dm_owner("ban", str(member), reason, str(interaction.user))
        supabase_save_mod_log(interaction.guild.id, "ban", str(member),
            str(interaction.user), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        _increment_report_action()
        dlog("⛔ BAN", f"{member} banned by {interaction.user} — {reason}", guild_id=interaction.guild_id)
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ **Permission Denied** — I can't ban {member.mention}.\n> Check role hierarchy and my permissions.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ **Ban Failed** — `{e}`", ephemeral=True)


# ---------------- /unban ----------------
@bot.tree.command(name="unban", description="Unban a user by their ID")
@app_commands.describe(user_id="The user's Discord ID", reason="Why")
@app_commands.check(_is_bot_mod)
async def slash_unban(interaction: discord.Interaction,
                      user_id: str, reason: str = "No reason provided"):
    if not await _slash_guard_guild(interaction):
        return
    if not user_id.isdigit():
        return await interaction.response.send_message(
            "❌ **Invalid ID** — User ID must be a number.\n> Right-click a user → Copy ID to get their ID.",
            ephemeral=True,
        )
    await interaction.response.defer()
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        await interaction.followup.send(
            f"✅ **User Unbanned** — **{user}**\n"
            f"> 🛡️ **By:** {interaction.user.mention}\n"
            f"> 📝 **Reason:** {reason}"
        )
        await logs.log_event(bot, config, "✅ Unban",
            f"{user} unbanned by {interaction.user} | Reason: {reason}", color=0x2ecc71, guild_id=interaction.guild.id if interaction.guild else None)
        supabase_save_mod_log(interaction.guild.id, "unban", str(user),
            str(interaction.user), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dlog("✅ UNBAN", f"{user} unbanned by {interaction.user}", guild_id=interaction.guild_id)
    except discord.NotFound:
        await interaction.followup.send("❌ **Not Found** — That user ID doesn't exist or they are not currently banned.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ **Permission Denied** — I don't have permission to unban users.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ **Unban Failed** — `{e}`", ephemeral=True)


# ---------------- /timeout ----------------
@bot.tree.command(name="timeout", description="Temporarily mute a member with Discord's built-in timeout")
@app_commands.describe(member="Who to time out", duration="e.g. 30s, 10m, 2h, 7d (max 28d)", reason="Why")
@app_commands.check(_is_bot_mod)
async def slash_timeout(interaction: discord.Interaction,
                        member: discord.Member, duration: str,
                        reason: str = "No reason provided"):
    if not await _slash_guard_guild(interaction):
        return
    block = _hierarchy_block_slash(interaction, member, "time out")
    if block:
        return await interaction.response.send_message(block, ephemeral=True)
    if member.bot:
        return await interaction.response.send_message(
            "❌ Discord does not support timing out bots — use `/kick` or `/ban` instead.", ephemeral=True
        )
    seconds = parse_duration(duration)
    if not seconds:
        return await interaction.response.send_message(
            "❌ **Invalid Duration** — Use formats like `30s`, `10m`, `2h`, `7d`. Max 28 days.", ephemeral=True
        )
    if seconds > 2419200:
        return await interaction.response.send_message(
            "❌ **Duration Too Long** — Discord's maximum timeout length is 28 days.", ephemeral=True
        )
    label = duration_label(seconds)
    await interaction.response.defer()
    try:
        until = discord.utils.utcnow() + timedelta(seconds=seconds)
        await member.timeout(until, reason=reason)
        await interaction.followup.send(
            f"⏱️ **Member Timed Out** — {member.mention}\n"
            f"> 🕐 **Duration:** {label}\n"
            f"> 🛡️ **By:** {interaction.user.mention}\n"
            f"> 📝 **Reason:** {reason}"
        )
        await logs.log_event(bot, config, "⏱️ Timeout",
            f"{member} timed out by {interaction.user} for {label} | Reason: {reason}",
            color=0xf39c12, guild_id=interaction.guild.id if interaction.guild else None)
        supabase_save_mod_log(interaction.guild.id, "timeout", str(member),
            str(interaction.user), f"{label} — {reason}",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        _increment_report_action()
        dlog("⏱️ TIMEOUT", f"{member} timed out for {label} by {interaction.user}", guild_id=interaction.guild_id)
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ **Permission Denied** — I can't time out {member.mention}.\n> Check role hierarchy and the **Moderate Members** permission.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ **Timeout Failed** — `{e}`", ephemeral=True)


# ---------------- /untimeout ----------------
@bot.tree.command(name="untimeout", description="Remove a member's timeout")
@app_commands.describe(member="Who to untimeout")
@app_commands.check(_is_bot_mod)
async def slash_untimeout(interaction: discord.Interaction, member: discord.Member):
    if not await _slash_guard_guild(interaction):
        return
    await interaction.response.defer()
    try:
        await member.timeout(None, reason=f"Timeout removed by {interaction.user}")
        await interaction.followup.send(
            f"✅ **Timeout Removed** — {member.mention} can now speak again.\n"
            f"> 🛡️ **By:** {interaction.user.mention}"
        )
        await logs.log_event(bot, config, "✅ Timeout Removed",
            f"{member}'s timeout removed by {interaction.user}", color=0x2ecc71, guild_id=interaction.guild.id if interaction.guild else None)
        dlog("✅ UNTIMEOUT", f"Timeout removed for {member} by {interaction.user}", guild_id=interaction.guild_id)
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ **Permission Denied** — I can't remove {member.mention}'s timeout.\n> Check role hierarchy and my permissions.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ **Untimeout Failed** — `{e}`", ephemeral=True)


# ---------------- /purge ----------------
@bot.tree.command(name="purge", description="Bulk delete the most recent messages in this channel")
@app_commands.describe(amount="How many to delete (1-200)")
@app_commands.check(_is_bot_mod)
async def slash_purge(interaction: discord.Interaction, amount: int = 10):
    # Defer immediately — this must be the first await so Discord always gets
    # an acknowledgement within 3 seconds regardless of what happens next.
    await interaction.response.defer(ephemeral=True)

    if not interaction.guild:
        await interaction.followup.send(
            "❌ **Server Only** — This command can only be used inside a server.", ephemeral=True
        )
        return
    if amount < 1 or amount > 200:
        await interaction.followup.send(
            "❌ **Invalid Amount** — Please provide a number between 1 and 200.", ephemeral=True
        )
        return

    # Check that the bot actually has permission to delete messages in this channel.
    # Announcement (news) channels often have per-channel overrides that block the bot
    # even when the invoking user is an admin.
    channel = interaction.channel
    bot_perms = channel.permissions_for(interaction.guild.me)
    if not bot_perms.manage_messages:
        await interaction.followup.send(
            "❌ **Bot Missing Permission** — I need **Manage Messages** in "
            f"{channel.mention} to purge messages.\n"
            "> Check the channel's permission overrides and make sure SP4RK has **Manage Messages** enabled.",
            ephemeral=True
        )
        return

    try:
        deleted = await channel.purge(limit=amount)
        await interaction.followup.send(
            f"🧹 **Channel Purged** — Deleted **{len(deleted)}** message(s) in {channel.mention}.\n"
            f"> 🛡️ **By:** {interaction.user.mention}",
            ephemeral=True
        )
        await logs.log_event(bot, config, "🧹 Purge",
            f"{interaction.user} purged {len(deleted)} messages in #{channel.name}",
            color=0x9b59b6, guild_id=interaction.guild.id if interaction.guild else None)
        dlog("🧹 PURGE",
            f"{interaction.user} deleted {len(deleted)} messages in #{channel.name}",
            guild_id=interaction.guild_id)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ **Permission Denied** — I don't have permission to delete messages in this channel.\n"
            "> For announcement channels, also ensure SP4RK has **Manage Messages** in the channel overrides.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ **Purge Failed** — `{e}`", ephemeral=True)


# ============================================================
# 🖼️ NEW UTILITY SLASH COMMANDS — /avatar /banner /roleinfo
# ============================================================

@bot.tree.command(name="avatar", description="Show a member's full-size avatar")
@app_commands.describe(member="Whose avatar to show (defaults to yourself)")
async def slash_avatar(interaction: discord.Interaction,
                       member: discord.Member = None):
    target = member or interaction.user
    avatar = target.display_avatar
    role_color = getattr(target, "color", discord.Color(0x4f8ef7)) or discord.Color(0x4f8ef7)
    embed = discord.Embed(
        title=f"🖼️ {target.display_name}'s Avatar",
        color=role_color,
        timestamp=datetime.utcnow(),
    )
    embed.set_image(url=avatar.replace(size=1024).url)
    links = [
        f"[PNG]({avatar.replace(format='png', size=1024).url})",
        f"[JPG]({avatar.replace(format='jpg', size=1024).url})",
        f"[WEBP]({avatar.replace(format='webp', size=1024).url})",
    ]
    if avatar.is_animated():
        links.append(f"[GIF]({avatar.replace(format='gif', size=1024).url})")
    embed.description = "**Download:** " + " · ".join(links)
    embed.set_footer(text=f"🆔 User ID: {target.id}  •  SP4RK")
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
        description=(
            f"**Download:** "
            f"[PNG]({banner.replace(format='png', size=1024).url}) · "
            f"[WEBP]({banner.replace(format='webp', size=1024).url})"
            + (f" · [GIF]({banner.replace(format='gif', size=1024).url})" if banner.is_animated() else "")
        ),
        color=getattr(target, "color", discord.Color(0x4f8ef7)) or discord.Color(0x4f8ef7),
        timestamp=datetime.utcnow(),
    )
    embed.set_image(url=banner.replace(size=1024).url)
    embed.set_footer(text=f"🆔 User ID: {target.id}  •  SP4RK")
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
        title=f"🎭 Role Info — {role.name}",
        description=(
            f"{role.mention}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 Role ID: `{role.id}`\n"
            f"📅 Created: <t:{int(role.created_at.timestamp())}:R>"
        ),
        color=role.color if role.color.value else discord.Color(0x4f8ef7),
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="🎨 Color",        value=f"`{color_hex}`",                         inline=True)
    embed.add_field(name="📍 Position",     value=f"**#{role.position}** of {len(interaction.guild.roles) - 1}", inline=True)
    embed.add_field(name="👥 Members",      value=f"**{len(members_with):,}**",              inline=True)
    embed.add_field(name="🪧 Hoisted",      value="✅ **Yes**" if role.hoist else "❌ **No**",      inline=True)
    embed.add_field(name="📣 Mentionable",  value="✅ **Yes**" if role.mentionable else "❌ **No**", inline=True)
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
    try:
        embed.set_footer(text=f"Requested by {interaction.user}  •  SP4RK", icon_url=interaction.user.display_avatar.url)
    except Exception:
        embed.set_footer(text=f"Requested by {interaction.user}  •  SP4RK")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="botinfo", description="Show SP4RK bot statistics and information")
async def slash_botinfo(interaction: discord.Interaction):
    guild_count = len(bot.guilds)
    user_count = sum(g.member_count or 0 for g in bot.guilds)
    latency_ms = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🤖 SP4RK — Bot Info",
        description=(
            "SP4RK is a powerful moderation and server security bot for Discord — "
            "with AI-powered automod, anti-raid, role monitoring, and advanced mod tools."
        ),
        color=0x5b8cff,
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="🏠 Servers", value=f"**{guild_count:,}**", inline=True)
    embed.add_field(name="👥 Users", value=f"**{user_count:,}**", inline=True)
    embed.add_field(name="🏓 Latency", value=f"**{latency_ms} ms**", inline=True)
    embed.add_field(name="📦 Version", value="**V2.5.0**", inline=True)
    embed.add_field(name="⚙️ Library", value="**discord.py 2.x**", inline=True)
    embed.add_field(name="🐍 Python", value="**3.11**", inline=True)
    embed.add_field(
        name="🔗 Links",
        value=f"[Dashboard](/dashboard)  •  [Invite](https://discord.com/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot%20applications.commands)  •  [Support](https://discord.gg/)",
        inline=False,
    )
    if bot.user and bot.user.display_avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text="SP4RK  •  Byte Labs")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="about", description="Learn about SP4RK and who made it")
async def slash_about(interaction: discord.Interaction):
    embed = discord.Embed(
        title="✨ About SP4RK",
        description=(
            "SP4RK is a powerful Discord moderation and server security bot, "
            "built to keep communities safe with AI-powered automod, anti-raid "
            "protection, role monitoring, and advanced moderation tools."
        ),
        color=0x7c5cbf,
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="👤 Created by", value="**Aronna Chowdhury**", inline=True)
    embed.add_field(name="🎂 Bot Birthday", value="**19 February 2026**", inline=True)
    embed.add_field(name="📦 Version", value="**V2.5.0**", inline=True)
    embed.add_field(
        name="🛡️ What SP4RK does",
        value=(
            "• AI-powered automod (Gemini)\n"
            "• Anti-raid & mass-ban prevention\n"
            "• Role monitoring & security enforcement\n"
            "• Full moderation suite (warn, ban, mute, timeout, purge…)\n"
            "• Detailed action logs & daily reports"
        ),
        inline=False,
    )
    if bot.user and bot.user.display_avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text="SP4RK  •  Made with ❤️ by Aronna Chowdhury")
    await interaction.response.send_message(embed=embed)


# ---- Persistent reminders for SP4RK ----
import pathlib as _pathlib

_REMIND_FILE = _pathlib.Path(__file__).parent / "sp4rk_reminders.json"

def _load_reminders() -> list:
    try:
        import db_spark as _ds
        if _ds.is_available():
            val = _ds.get_blob("reminders")
            if val is not None:
                return val if isinstance(val, list) else val.get("items", [])
    except Exception:
        pass
    try:
        if _REMIND_FILE.exists():
            import json as _json
            return _json.loads(_REMIND_FILE.read_text()) or []
    except Exception:
        pass
    return []

def _save_reminders(items: list) -> None:
    try:
        import db_spark as _ds
        if _ds.is_available():
            _ds.set_blob("reminders", items)
    except Exception:
        pass
    try:
        import json as _json
        _REMIND_FILE.write_text(_json.dumps(items))
    except Exception:
        pass


@tasks.loop(seconds=20)
async def _reminder_tick():
    if not bot.is_ready():
        return
    try:
        items = _load_reminders()
        now = datetime.utcnow()
        changed = False
        for r in items:
            if r.get("sent"):
                continue
            try:
                due = datetime.fromisoformat(r["due_at"])
                if due.tzinfo is not None:
                    due = due.replace(tzinfo=None)
            except Exception:
                continue
            if due <= now:
                r["sent"] = True
                changed = True
                try:
                    ch = bot.get_channel(int(r["channel_id"]))
                    if ch:
                        await ch.send(f"⏰ <@{r['user_id']}> **Reminder:** {r['text']}")
                except Exception:
                    pass
        if changed:
            _save_reminders(items)
    except Exception as e:
        print(f"[reminder_tick] error: {e}")


@bot.tree.command(name="remind", description="Set a personal reminder")
@app_commands.describe(when="Duration like 10m, 1h, 2d", reminder="What to remind you about")
async def slash_remind(interaction: discord.Interaction, when: str, reminder: str):
    if not await _slash_guard_guild(interaction):
        return
    seconds = parse_duration(when)
    if not seconds or seconds < 5:
        return await interaction.response.send_message(
            "❌ **Invalid Duration** — Use formats like `10m`, `1h`, `2d`.", ephemeral=True
        )
    due = datetime.utcnow() + timedelta(seconds=seconds)
    items = _load_reminders()
    items.append({
        "user_id": str(interaction.user.id),
        "channel_id": str(interaction.channel.id),
        "text": reminder[:500],
        "due_at": due.isoformat(),
        "sent": False,
    })
    _save_reminders(items)
    await interaction.response.send_message(
        f"⏰ **Reminder set!** I'll remind you <t:{int(due.timestamp())}:R>.",
        ephemeral=True,
    )




# ---- Setprefix ----
@bot.tree.command(name="setprefix", description="Change the bot's command prefix for this server")
@app_commands.describe(prefix="New prefix (e.g. !, ?, $, .)")
async def slash_setprefix(interaction: discord.Interaction, prefix: str):
    if not await _slash_guard_guild(interaction):
        return
    if not _is_bot_admin(interaction):
        return await interaction.response.send_message(
            "❌ **Access Denied** — You need the Admin role or Administrator permission.", ephemeral=True
        )
    prefix = prefix.strip()[:5]
    if not prefix:
        return await interaction.response.send_message("❌ Prefix cannot be empty.", ephemeral=True)
    try:
        from dashboard import save_guild_config, get_guild_config as _ggc
        gcfg = _ggc(str(interaction.guild.id))
        gcfg["prefix"] = prefix
        save_guild_config(str(interaction.guild.id), gcfg)
    except Exception as e:
        return await interaction.response.send_message(f"❌ Failed to save prefix: `{e}`", ephemeral=True)
    await interaction.response.send_message(
        f"✅ **Prefix updated!** New prefix: `{prefix}`\nExample: `{prefix}ban @user`",
        ephemeral=True,
    )

# ============================================================
# 🆕 NEW SLASH COMMANDS (replacing deleted prefix duplicates)
# These cover commands that previously only existed as prefix
# commands, giving them a proper Discord slash-command UI.
# ============================================================

@bot.tree.command(name="mute", description="Mute a member using a mute role. Optional duration (30m, 2h, 1d).")
@app_commands.describe(member="Member to mute", duration="Optional duration: 30m, 2h, 1d (leave blank = indefinite)")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member, duration: str = None):
    if not await _slash_guard_guild(interaction): return
    block = _hierarchy_block_slash(interaction, member, "mute")
    if block: return await interaction.response.send_message(block, ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    ctx_like = _FakeCtx(interaction)
    await moderation.mute_member(ctx_like, member, get_config_for_guild(str(interaction.guild.id)), duration)
    _increment_report_action()
    dlog("🔇 MUTE", f"{member} muted by {interaction.user}" + (f" for {duration}" if duration else ""), guild_id=interaction.guild_id)
    await interaction.followup.send(f"🔇 **Muted** — {member.mention}" + (f" for **{duration}**" if duration else " indefinitely."), ephemeral=True)

@bot.tree.command(name="unmute", description="Unmute a previously muted member.")
@app_commands.describe(member="Member to unmute")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_unmute(interaction: discord.Interaction, member: discord.Member):
    if not await _slash_guard_guild(interaction): return
    await interaction.response.defer(ephemeral=True)
    ctx_like = _FakeCtx(interaction)
    await moderation.unmute_member(ctx_like, member, get_config_for_guild(str(interaction.guild.id)))
    dlog("🔊 UNMUTE", f"{member} unmuted by {interaction.user}", guild_id=interaction.guild_id)
    await interaction.followup.send(f"🔊 **Unmuted** — {member.mention}", ephemeral=True)

@bot.tree.command(name="tempban", description="Temporarily ban a member. Duration: 30m, 2h, 7d, etc.")
@app_commands.describe(member="Member to temp-ban", duration="Ban duration (e.g. 1h, 3d, 1w)", reason="Reason for the ban")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_tempban(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason provided"):
    if not await _slash_guard_guild(interaction): return
    block = _hierarchy_block_slash(interaction, member, "temp-ban")
    if block: return await interaction.response.send_message(block, ephemeral=True)
    seconds = parse_duration(duration)
    if not seconds:
        return await interaction.response.send_message("❌ Invalid duration — use formats like `30m`, `2h`, `7d`.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    label = duration_label(seconds)
    try:
        await member.ban(reason=f"[TEMP-BAN {label}] {reason}")
        _increment_report_action()
        dlog("⏱️ TEMP-BAN", f"{member} temp-banned for {label} by {interaction.user}", guild_id=interaction.guild_id)
        asyncio.create_task(_auto_unban_task(interaction.guild, member, seconds, label))
        await interaction.followup.send(
            f"⏱️ **Temp-Banned** — {member.mention}\n> 🕐 **Duration:** {label}\n> 📝 **Reason:** {reason}", ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(f"❌ Cannot ban {member.mention} — check hierarchy and permissions.", ephemeral=True)

@bot.tree.command(name="lock", description="Lock a channel — @everyone can no longer send messages.")
@app_commands.describe(channel="Channel to lock (defaults to current channel)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not await _slash_guard_guild(interaction): return
    target = channel or interaction.channel
    try:
        ow = target.overwrites_for(interaction.guild.default_role)
        ow.send_messages = False
        await target.set_permissions(interaction.guild.default_role, overwrite=ow, reason=f"Locked by {interaction.user}")
        dlog("🔒 LOCK", f"#{target.name} locked by {interaction.user}", guild_id=interaction.guild_id)
        await interaction.response.send_message(f"🔒 **Locked** — {target.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to edit that channel.", ephemeral=True)

@bot.tree.command(name="unlock", description="Unlock a channel — restore @everyone send permissions.")
@app_commands.describe(channel="Channel to unlock (defaults to current channel)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not await _slash_guard_guild(interaction): return
    target = channel or interaction.channel
    try:
        ow = target.overwrites_for(interaction.guild.default_role)
        ow.send_messages = None
        await target.set_permissions(interaction.guild.default_role, overwrite=ow, reason=f"Unlocked by {interaction.user}")
        dlog("🔓 UNLOCK", f"#{target.name} unlocked by {interaction.user}", guild_id=interaction.guild_id)
        await interaction.response.send_message(f"🔓 **Unlocked** — {target.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to edit that channel.", ephemeral=True)

@bot.tree.command(name="role-add", description="Add a role to a member.")
@app_commands.describe(member="Target member", role="Role to assign")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_role_add(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not await _slash_guard_guild(interaction): return
    if role >= interaction.guild.me.top_role:
        return await interaction.response.send_message(f"❌ I can't manage **{role.name}** — it's above my top role.", ephemeral=True)
    if interaction.user.id != interaction.guild.owner_id and role >= interaction.user.top_role:
        return await interaction.response.send_message(f"❌ You can't assign **{role.name}** — it's equal to or above your top role.", ephemeral=True)
    if role in member.roles:
        return await interaction.response.send_message(f"ℹ️ {member.mention} already has **{role.name}**.", ephemeral=True)
    try:
        await member.add_roles(role, reason=f"Added by {interaction.user}")
        dlog("➕ ROLE-ADD", f"{interaction.user} gave @{role.name} to {member}", guild_id=interaction.guild_id)
        await interaction.response.send_message(f"✅ **Role Added** — {member.mention} now has {role.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Permission denied — check role hierarchy.", ephemeral=True)

@bot.tree.command(name="role-remove", description="Remove a role from a member.")
@app_commands.describe(member="Target member", role="Role to remove")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_role_remove(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not await _slash_guard_guild(interaction): return
    if role >= interaction.guild.me.top_role:
        return await interaction.response.send_message(f"❌ I can't manage **{role.name}** — it's above my top role.", ephemeral=True)
    if role not in member.roles:
        return await interaction.response.send_message(f"ℹ️ {member.mention} doesn't have **{role.name}**.", ephemeral=True)
    try:
        await member.remove_roles(role, reason=f"Removed by {interaction.user}")
        dlog("➖ ROLE-REMOVE", f"{interaction.user} removed @{role.name} from {member}", guild_id=interaction.guild_id)
        await interaction.response.send_message(f"✅ **Role Removed** — {member.mention} no longer has {role.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Permission denied — check role hierarchy.", ephemeral=True)

@bot.tree.command(name="nick", description="Change or reset a member's nickname.")
@app_commands.describe(member="Target member", name="New nickname (leave blank to reset)")
@app_commands.checks.has_permissions(manage_nicknames=True)
async def slash_nick(interaction: discord.Interaction, member: discord.Member, name: str = None):
    if not await _slash_guard_guild(interaction): return
    block = _hierarchy_block_slash(interaction, member, "rename")
    if block: return await interaction.response.send_message(block, ephemeral=True)
    try:
        await member.edit(nick=name, reason=f"Renamed by {interaction.user}")
        msg = f"✅ **Nickname set** to `{name}` for {member.mention}." if name else f"✅ **Nickname reset** for {member.mention}."
        dlog("📝 NICK", f"{interaction.user} renamed {member} to {name or '(reset)'}", guild_id=interaction.guild_id)
        await interaction.response.send_message(msg, ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"❌ Can't rename {member.mention} — check hierarchy.", ephemeral=True)

@bot.tree.command(name="voicekick", description="Disconnect a member from their voice channel.")
@app_commands.describe(member="Member to disconnect from voice")
@app_commands.checks.has_permissions(move_members=True)
async def slash_voicekick(interaction: discord.Interaction, member: discord.Member):
    if not await _slash_guard_guild(interaction): return
    if not member.voice or not member.voice.channel:
        return await interaction.response.send_message(f"ℹ️ {member.mention} is not in any voice channel.", ephemeral=True)
    block = _hierarchy_block_slash(interaction, member, "voice-kick")
    if block: return await interaction.response.send_message(block, ephemeral=True)
    try:
        ch = member.voice.channel.name
        await member.move_to(None, reason=f"Voice kick by {interaction.user}")
        dlog("🔌 VKICK", f"{interaction.user} disconnected {member} from {ch}", guild_id=interaction.guild_id)
        await interaction.response.send_message(f"🔌 **Voice Kicked** — {member.mention} disconnected from **{ch}**.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to move that member.", ephemeral=True)

@bot.tree.command(name="snipe", description="Show the last deleted message in this channel.")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_snipe(interaction: discord.Interaction):
    if not await _slash_guard_guild(interaction): return
    snipe = _snipe_cache.get(interaction.channel.id)
    if not snipe:
        return await interaction.response.send_message("ℹ️ No recently deleted messages found in this channel.", ephemeral=True)
    embed = discord.Embed(
        title="🔍 Sniped Message",
        description=f"━━━━━━━━━━━━━━━━━━━━━━\n{snipe['content'][:1900]}\n━━━━━━━━━━━━━━━━━━━━━━",
        color=0x95a5a6, timestamp=snipe["ts"],
    )
    embed.set_author(name=f"{snipe['author']} said:")
    embed.set_footer(text=f"🗑️ Deleted in #{interaction.channel.name}  •  SP4RK Snipe")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="slowmode", description="Set slowmode delay on this channel. Use 0 to disable.")
@app_commands.describe(seconds="Delay in seconds (0 = off, max 21600)", channel="Channel to apply (defaults to current)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_slowmode_cmd(interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
    if not await _slash_guard_guild(interaction): return
    if seconds < 0 or seconds > 21600:
        return await interaction.response.send_message("❌ Seconds must be between 0 and 21600.", ephemeral=True)
    target = channel or interaction.channel
    try:
        await target.edit(slowmode_delay=seconds)
        label = duration_label(seconds) if seconds else "disabled"
        dlog("⏱️ SLOWMODE", f"Slowmode set to {seconds}s in #{target.name} by {interaction.user}", guild_id=interaction.guild_id)
        await interaction.response.send_message(
            f"{'🐌 **Slowmode set** to ' + label if seconds else '✅ **Slowmode disabled**'} in {target.mention}.", ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to edit that channel.", ephemeral=True)

@bot.tree.command(name="purge-reactions", description="Remove all reactions from the last N messages in this channel.")
@app_commands.describe(amount="Number of messages to clear reactions from (max 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge_reactions(interaction: discord.Interaction, amount: int = 10):
    if not await _slash_guard_guild(interaction): return
    if amount < 1 or amount > 100:
        return await interaction.response.send_message("❌ Amount must be between 1 and 100.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    cleared = 0
    async for message in interaction.channel.history(limit=amount):
        if message.reactions:
            await message.clear_reactions()
            cleared += 1
    dlog("🧹 PURGEREACTION", f"{interaction.user} cleared reactions in #{interaction.channel.name}", guild_id=interaction.guild_id)
    await interaction.followup.send(f"🧹 Cleared reactions from **{cleared}** message(s) in {interaction.channel.mention}.", ephemeral=True)

@bot.tree.command(name="purge-user", description="Delete up to N recent messages from a specific user in this channel.")
@app_commands.describe(member="User whose messages to delete", amount="Max messages to delete (max 500)")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge_user(interaction: discord.Interaction, member: discord.Member, amount: int = 50):
    if not await _slash_guard_guild(interaction): return
    if amount < 1 or amount > 500:
        return await interaction.response.send_message("❌ Amount must be between 1 and 500.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount, check=lambda m: m.author.id == member.id)
    dlog("🧹 PURGEUSER", f"{interaction.user} purged {len(deleted)} from {member} in #{interaction.channel.name}", guild_id=interaction.guild_id)
    await interaction.followup.send(f"🧹 Deleted **{len(deleted)}** message(s) from {member.mention}.", ephemeral=True)

@bot.tree.command(name="massban", description="Ban multiple users by ID (raid response). Max 50 IDs, space-separated.")
@app_commands.describe(user_ids="Space-separated list of user IDs to ban", reason="Reason for the mass ban")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_massban(interaction: discord.Interaction, user_ids: str, reason: str = "Mass-ban"):
    if not await _slash_guard_guild(interaction): return
    ids = [i.strip() for i in user_ids.split() if i.strip().isdigit()]
    if not ids:
        return await interaction.response.send_message("❌ No valid user IDs provided.", ephemeral=True)
    if len(ids) > 50:
        return await interaction.response.send_message("❌ Maximum is 50 IDs per command.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    success, fail = 0, 0
    for uid in ids:
        try:
            await interaction.guild.ban(discord.Object(id=int(uid)), reason=f"Mass-ban by {interaction.user}: {reason}")
            success += 1
        except Exception:
            fail += 1
    dlog("⛔ MASSBAN", f"{interaction.user} mass-banned {success}/{len(ids)}", guild_id=interaction.guild_id)
    await interaction.followup.send(
        f"⛔ **Mass Ban Complete** — {len(ids)} target(s).\n> ✅ Banned: {success}  |  ❌ Failed: {fail}", ephemeral=True
    )

@bot.tree.command(name="lockdown", description="Lock all channels in the server immediately (emergency use).")
@app_commands.checks.has_permissions(administrator=True)
async def slash_lockdown(interaction: discord.Interaction):
    if not await _slash_guard_guild(interaction): return
    gcfg = get_config_for_guild(str(interaction.guild.id))
    gcfg["temp_lockdown"] = "ON"
    await interaction.response.defer(ephemeral=True)
    await security.toggle_lockdown(interaction.guild, gcfg, enable=True, log_callback=dlog)
    dlog("🚨 LOCKDOWN", f"Lockdown enabled in {interaction.guild.name} by {interaction.user}", guild_id=interaction.guild_id)
    await interaction.followup.send("🚨 **Server Lockdown Active** — all channels locked. Use `/unlockdown` to lift it.", ephemeral=True)

@bot.tree.command(name="unlockdown", description="Lift the server lockdown and restore normal channel permissions.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_unlockdown(interaction: discord.Interaction):
    if not await _slash_guard_guild(interaction): return
    gcfg = get_config_for_guild(str(interaction.guild.id))
    gcfg["temp_lockdown"] = "OFF"
    await interaction.response.defer(ephemeral=True)
    await security.toggle_lockdown(interaction.guild, gcfg, enable=False, log_callback=dlog)
    dlog("✅ UNLOCKDOWN", f"Lockdown lifted in {interaction.guild.name} by {interaction.user}", guild_id=interaction.guild_id)
    await interaction.followup.send("✅ **Lockdown Lifted** — server is back to normal.", ephemeral=True)

@bot.tree.command(name="note", description="Add a private moderator note to a user.")
@app_commands.describe(member="Member to annotate", note="The note text (private to staff)")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_note(interaction: discord.Interaction, member: discord.Member, note: str):
    if not await _slash_guard_guild(interaction): return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    supabase_save_note(interaction.guild.id, member.id, str(member), note, str(interaction.user), ts)
    dlog("📝 NOTE", f"Note added for {member} by {interaction.user}: {note}", guild_id=interaction.guild_id)
    await interaction.response.send_message(
        f"📝 **Note Added** — {member.mention}\n> 🛡️ **By:** {interaction.user.mention}\n> 💬 **Note:** {note}",
        ephemeral=True,
    )

@bot.tree.command(name="notes", description="View all moderator notes for a user.")
@app_commands.describe(member="Member whose notes to view")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_notes(interaction: discord.Interaction, member: discord.Member):
    if not await _slash_guard_guild(interaction): return
    notes = supabase_get_notes(interaction.guild.id, member.id)
    embed = discord.Embed(
        title=f"📝 Moderator Notes — {member.display_name}",
        description=f"**{member}** · `{member.id}`\n━━━━━━━━━━━━━━━━━━━━━━\nTotal notes: **{len(notes)}**",
        color=0x3a8fff, timestamp=datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if not notes:
        embed.add_field(name="📭 No Notes", value="*No moderator notes recorded.*", inline=False)
    else:
        for n in notes[-10:]:
            embed.add_field(
                name=f"🕐 {n.get('timestamp','?')}  •  by {n.get('moderator','?')}",
                value=f"> {n.get('note','—')}", inline=False,
            )
    embed.set_footer(text=f"🆔 {member.id}  •  SP4RK")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="modlogs", description="View full mod history (warnings + notes) for a member.")
@app_commands.describe(member="Member to inspect")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_modlogs(interaction: discord.Interaction, member: discord.Member):
    if not await _slash_guard_guild(interaction): return
    guild_id  = str(interaction.guild.id)
    member_id = str(member.id)
    entry   = _get_member_entry(guild_id, member_id)
    count   = entry["count"]
    history = entry["history"]
    notes   = supabase_get_notes(interaction.guild.id, member.id)
    _wlimit = int(get_config_for_guild(guild_id).get('warning_limit', 30))
    pct = min(1.0, count / max(1, _wlimit))
    bar = "▰" * int(10 * pct) + "▱" * (10 - int(10 * pct))
    severity = "🔴 High Risk" if count >= _wlimit else ("🟠 Moderate" if count >= _wlimit // 2 else "🟢 Low Risk")
    embed = discord.Embed(
        title=f"📋 Mod Logs — {member.display_name}",
        description=f"**{member}** · `{member.id}`\n━━━━━━━━━━━━━━━━━━━━━━\nRisk: `{bar}` — **{severity}**",
        color=0xe74c3c if count >= _wlimit else (0xf39c12 if count >= 2 else 0x2ecc71),
        timestamp=datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="⚠️ Warnings", value=f"**{count}** / {_wlimit}", inline=True)
    embed.add_field(name="📝 Notes",     value=f"**{len(notes)}**",        inline=True)
    embed.add_field(name="📊 Risk",      value=severity,                   inline=True)
    if history:
        warn_text = "\n".join(f"`{i+1}.` {h['reason']} — *{h['time']}*" for i, h in enumerate(history[-5:][::-1]))
        embed.add_field(name="📋 Recent Warnings", value=warn_text, inline=False)
    if notes:
        note_text = "\n".join(f"> *{n.get('note')}*\n> — {n.get('moderator')} · {n.get('timestamp')}" for n in notes[-3:])
        embed.add_field(name="📝 Recent Notes", value=note_text, inline=False)
    embed.set_footer(text=f"Requested by {interaction.user}  •  SP4RK")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── helpers for new slash commands ───────────────────────────────────────────
def _hierarchy_block_slash(interaction: discord.Interaction, member: discord.Member, action_word: str):
    """Same as _hierarchy_block but takes an Interaction instead of Context.

    Role-position only — does NOT block by permission flags (administrator /
    manage_guild). Only Discord's role ordering matters for human-issued commands.
    """
    if member.id == interaction.guild.me.id:
        return f"❌ I can't {action_word} myself."
    if member.id == interaction.guild.owner_id:
        return f"❌ I can't {action_word} the server owner."
    if member.top_role >= interaction.guild.me.top_role:
        return (
            f"❌ Cannot {action_word} {member.mention} — their role (**{member.top_role.name}**) "
            f"is equal to or above mine. Move SP4RK's role higher in Server Settings → Roles."
        )
    if interaction.user.id != interaction.guild.owner_id and member.top_role >= interaction.user.top_role:
        return f"❌ Cannot {action_word} {member.mention} — their role is equal to or higher than yours."
    return None

class _FakeCtx:
    """Minimal ctx-like object so old moderation helpers work from slash commands."""
    def __init__(self, interaction: discord.Interaction):
        self.guild  = interaction.guild
        self.author = interaction.user
        self.channel = interaction.channel
        self._interaction = interaction
    async def send(self, content=None, **kwargs):
        try:
            if not self._interaction.response.is_done():
                await self._interaction.response.send_message(content or "", **{k:v for k,v in kwargs.items() if k not in ('delete_after',)})
            else:
                await self._interaction.followup.send(content or "", ephemeral=True)
        except Exception:
            pass

async def _auto_unban_task(guild: discord.Guild, member: discord.Member, seconds: int, label: str):
    await asyncio.sleep(seconds)
    try:
        await guild.unban(member, reason=f"Temp-ban expired ({label})")
    except Exception:
        pass


# ---------------- DYNO/CARL-BOT EXTRAS ----------------
# Tags, Softban, AFK, and Highlights — registered via a single setup() call
# so this already-large file stays readable.
try:
    import extras as _sp4rk_extras
    _sp4rk_extras.setup(bot, dlog=dlog)
except Exception as _e:
    print(f"⚠️  Failed to load extras module: {_e}")

# ---------------- MISSING COMMANDS ----------------
# All remaining slash + prefix commands from the v2.5 command list.
try:
    import premium_features as _prem
    _prem.setup(bot, dlog=dlog)
    print("✅ premium_features loaded successfully.")
except Exception as _e:
    import traceback
    print(f"⚠️  Failed to load premium_features: {_e}")
    traceback.print_exc()

try:
    import commands_missing as _cmds_missing
    _cmds_missing.setup(bot, dlog=dlog)
    print("✅ commands_missing loaded successfully.")
except Exception as _e:
    import traceback
    print(f"⚠️  Failed to load commands_missing: {_e}")
    traceback.print_exc()


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
