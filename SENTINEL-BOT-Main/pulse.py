"""
Pulse — Byte Labs Analytics Bot
Token env var: P_TOKEN

Features:
  • Message activity tracking (per guild / channel / user)
  • Member join / leave growth tracking
  • Voice-time tracking (seconds in VC per user)
  • Slash commands: /stats, /activity, /top, /growth,
                   /voicetime, /channelstats, /memberstats,
                   /report, /heatmap, /pulse-help
  • Auto weekly report (posted every Monday at 09:00 UTC)
  • All data persisted to pulse_data.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

logger = logging.getLogger("pulse")
logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("P_TOKEN", "").strip()

# ── Storage ────────────────────────────────────────────────────────────────────
_DIR       = Path(__file__).parent
_DATA_FILE = _DIR / "pulse_data.json"
_CFG_FILE  = _DIR / "pulse_config.json"
_lock      = threading.Lock()

# In-memory state (loaded from disk on start)
# Structure:
#   data[guild_id]["messages"][user_id]   = int   (total messages)
#   data[guild_id]["chan_msgs"][chan_id]   = int   (total per channel)
#   data[guild_id]["joins"]               = [ISO date strings]
#   data[guild_id]["leaves"]              = [ISO date strings]
#   data[guild_id]["voice"][user_id]      = int   (total seconds)
#   data[guild_id]["hourly"][0..23]       = int   (messages per hour)
#   data[guild_id]["reactions"][user_id]  = int   (total reactions given)
_data: Dict[str, Any] = {}

# Voice session tracking: guild_id -> user_id -> start_epoch
_voice_sessions: Dict[str, Dict[str, float]] = {}

# ── JSON helpers ───────────────────────────────────────────────────────────────
def _read(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}

def _write(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)

def _load():
    global _data
    _data = _read(_DATA_FILE)

def _save():
    with _lock:
        _write(_DATA_FILE, _data)

# ── Guild data accessors ───────────────────────────────────────────────────────
def _g(gid: str) -> dict:
    if gid not in _data:
        _data[gid] = {
            "messages": {}, "chan_msgs": {}, "joins": [],
            "leaves": [], "voice": {}, "hourly": [0]*24, "reactions": {}
        }
    g = _data[gid]
    for key, default in [("messages",{}),("chan_msgs",{}),("joins",[]),
                          ("leaves",[]),("voice",{}),("hourly",[0]*24),("reactions",{})]:
        if key not in g:
            g[key] = default
    return g

# ── Voice helpers ──────────────────────────────────────────────────────────────
def _voice_start(gid: str, uid: str):
    _voice_sessions.setdefault(gid, {})[uid] = time.time()

def _voice_stop(gid: str, uid: str):
    start = _voice_sessions.get(gid, {}).pop(uid, None)
    if start is None:
        return
    elapsed = int(time.time() - start)
    g = _g(gid)
    g["voice"][uid] = g["voice"].get(uid, 0) + elapsed
    _save()

# ── Formatting helpers ─────────────────────────────────────────────────────────
def _fmt_time(secs: int) -> str:
    h, rem = divmod(secs, 3600)
    m = rem // 60
    if h:
        return f"{h}h {m}m"
    return f"{m}m"

def _fmt_num(n: int) -> str:
    return f"{n:,}"

CYAN  = 0x06b6d4
TEAL  = 0x0891b2

def _medal(i: int) -> str:
    return ["🥇","🥈","🥉"][i] if i < 3 else f"`{i+1}.`"

def _days_ago(n: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=n)

def _count_in_range(dates: List[str], since: datetime) -> int:
    count = 0
    for d in dates:
        try:
            if datetime.fromisoformat(d) >= since:
                count += 1
        except Exception:
            pass
    return count

# ══════════════════════════════════════════════════════════════════════════════
# BOT SETUP
# ══════════════════════════════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
intents.members          = True
intents.voice_states     = True
intents.reactions        = True

bot = commands.Bot(command_prefix="p!", intents=intents, help_command=None)

# ── Global slash-command gate: admin / owner only ──────────────────────────────
@bot.tree.interaction_check
async def _admin_only(interaction: discord.Interaction) -> bool:
    """All Pulse slash commands are restricted to server admins and the owner."""
    if interaction.guild is None:
        await interaction.response.send_message(
            "⚠️ Pulse commands can only be used inside a server.", ephemeral=True
        )
        return False
    perms = interaction.user.guild_permissions
    if perms.administrator or interaction.guild.owner_id == interaction.user.id:
        return True
    await interaction.response.send_message(
        "🔒 Only **server administrators** and the **server owner** can use Pulse commands.",
        ephemeral=True,
    )
    return False

# ══════════════════════════════════════════════════════════════════════════════
# EVENTS
# ══════════════════════════════════════════════════════════════════════════════
@bot.event
async def on_ready():
    _load()
    try:
        synced = await bot.tree.sync()
        logger.info(f"[Pulse] Logged in as {bot.user} — synced {len(synced)} commands")
    except Exception as e:
        logger.error(f"[Pulse] Sync failed: {e}")
    weekly_report.start()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    gid = str(message.guild.id)
    uid = str(message.author.id)
    cid = str(message.channel.id)
    g = _g(gid)
    g["messages"][uid]  = g["messages"].get(uid, 0)  + 1
    g["chan_msgs"][cid] = g["chan_msgs"].get(cid, 0) + 1
    hour = datetime.now(timezone.utc).hour
    g["hourly"][hour]  += 1
    _save()
    await bot.process_commands(message)

@bot.event
async def on_member_join(member: discord.Member):
    if member.bot: return
    g = _g(str(member.guild.id))
    g["joins"].append(datetime.now(timezone.utc).isoformat())
    _save()

@bot.event
async def on_member_remove(member: discord.Member):
    if member.bot: return
    g = _g(str(member.guild.id))
    g["leaves"].append(datetime.now(timezone.utc).isoformat())
    _save()

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot: return
    gid = str(member.guild.id)
    uid = str(member.id)
    if before.channel is None and after.channel is not None:
        _voice_start(gid, uid)
    elif before.channel is not None and after.channel is None:
        _voice_stop(gid, uid)

@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    if user.bot or not reaction.message.guild: return
    gid = str(reaction.message.guild.id)
    uid = str(user.id)
    g = _g(gid)
    g["reactions"][uid] = g["reactions"].get(uid, 0) + 1
    _save()

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

# ─── /stats ───────────────────────────────────────────────────────────────────
@bot.tree.command(name="stats", description="Full server overview — members, activity and more", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_stats(interaction: discord.Interaction):
    await interaction.response.defer()
    guild = interaction.guild
    gid   = str(guild.id)
    g     = _g(gid)

    total_msgs = sum(g["messages"].values())
    total_voice = sum(g["voice"].values())
    joins_7d  = _count_in_range(g["joins"],  _days_ago(7))
    leaves_7d = _count_in_range(g["leaves"], _days_ago(7))
    net_7d    = joins_7d - leaves_7d

    online = sum(1 for m in guild.members if m.status != discord.Status.offline and not m.bot)

    e = discord.Embed(title=f"📊 {guild.name} — Server Stats", color=CYAN,
                      timestamp=datetime.now(timezone.utc))
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)
    e.add_field(name="👥 Members",      value=f"**{guild.member_count:,}** total\n{online:,} online", inline=True)
    e.add_field(name="📝 Messages",     value=f"**{_fmt_num(total_msgs)}** tracked", inline=True)
    e.add_field(name="🎙 Voice Time",   value=_fmt_time(total_voice), inline=True)
    e.add_field(name="📈 Growth (7d)",  value=f"+{joins_7d} joined · -{leaves_7d} left · **net {net_7d:+}**", inline=False)
    e.add_field(name="💬 Channels",     value=str(len(guild.text_channels)), inline=True)
    e.add_field(name="🔊 Voice",        value=str(len(guild.voice_channels)), inline=True)
    e.add_field(name="🎭 Roles",        value=str(len(guild.roles)), inline=True)
    e.add_field(name="✨ Boosts",       value=str(guild.premium_subscription_count or 0), inline=True)
    e.set_footer(text="Pulse · Byte Labs")
    await interaction.followup.send(embed=e)


# ─── /activity ────────────────────────────────────────────────────────────────
@bot.tree.command(name="activity", description="Message activity breakdown by channel", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(top="How many channels to show (default 10)")
async def cmd_activity(interaction: discord.Interaction, top: int = 10):
    await interaction.response.defer()
    gid  = str(interaction.guild_id)
    g    = _g(gid)
    data = sorted(g["chan_msgs"].items(), key=lambda x: x[1], reverse=True)[:top]

    if not data:
        return await interaction.followup.send("No message data yet — Pulse needs to observe some messages first.")

    lines = []
    for i, (cid, cnt) in enumerate(data):
        ch = interaction.guild.get_channel(int(cid))
        name = f"#{ch.name}" if ch else f"<#{cid}>"
        bar = "█" * min(20, int(cnt / max(data[0][1], 1) * 20))
        lines.append(f"{_medal(i)} **{name}** — {_fmt_num(cnt)} msgs\n  `{bar}`")

    e = discord.Embed(title="💬 Channel Activity", description="\n".join(lines), color=CYAN)
    e.set_footer(text="Pulse · Byte Labs · all-time totals")
    await interaction.followup.send(embed=e)


# ─── /top ─────────────────────────────────────────────────────────────────────
@bot.tree.command(name="top", description="Top members leaderboard by messages, voice time, or reactions", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(by="Sort by: messages / voice / reactions")
@app_commands.choices(by=[
    app_commands.Choice(name="messages",  value="messages"),
    app_commands.Choice(name="voice",     value="voice"),
    app_commands.Choice(name="reactions", value="reactions"),
])
async def cmd_top(interaction: discord.Interaction, by: str = "messages"):
    await interaction.response.defer()
    gid = str(interaction.guild_id)
    g   = _g(gid)
    raw = sorted(g.get(by, {}).items(), key=lambda x: x[1], reverse=True)[:10]

    if not raw:
        return await interaction.followup.send(f"No {by} data yet.")

    lines = []
    for i, (uid, val) in enumerate(raw):
        try:
            m = interaction.guild.get_member(int(uid)) or await interaction.guild.fetch_member(int(uid))
            name = m.display_name
        except Exception:
            name = f"User {uid}"
        display = _fmt_time(val) if by == "voice" else _fmt_num(val)
        label   = {"messages":"msg","voice":"VC","reactions":"rxn"}[by]
        lines.append(f"{_medal(i)} **{name}** — {display} {label}")

    title = {"messages":"📝 Top Chatters","voice":"🎙 Voice Leaders","reactions":"⭐ Reaction Kings"}[by]
    e = discord.Embed(title=title, description="\n".join(lines), color=CYAN)
    e.set_footer(text="Pulse · Byte Labs")
    await interaction.followup.send(embed=e)


# ─── /growth ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="growth", description="Member join/leave count and net growth over N days", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(days="Look-back window: 7, 30, or 90 days")
@app_commands.choices(days=[
    app_commands.Choice(name="7 days",  value=7),
    app_commands.Choice(name="30 days", value=30),
    app_commands.Choice(name="90 days", value=90),
])
async def cmd_growth(interaction: discord.Interaction, days: int = 7):
    await interaction.response.defer()
    gid  = str(interaction.guild_id)
    g    = _g(gid)
    since = _days_ago(days)
    joins  = _count_in_range(g["joins"],  since)
    leaves = _count_in_range(g["leaves"], since)
    net    = joins - leaves
    arrow  = "📈" if net >= 0 else "📉"

    e = discord.Embed(
        title=f"{arrow} Member Growth — Last {days} Days",
        color=CYAN,
        timestamp=datetime.now(timezone.utc)
    )
    e.add_field(name="✅ Joined",   value=f"**+{joins}**",          inline=True)
    e.add_field(name="👋 Left",     value=f"**-{leaves}**",          inline=True)
    e.add_field(name="📊 Net",      value=f"**{net:+}**",            inline=True)
    e.add_field(name="👥 Current",  value=f"**{interaction.guild.member_count:,}**", inline=True)
    e.set_footer(text="Pulse · Byte Labs")
    await interaction.followup.send(embed=e)


# ─── /voicetime ───────────────────────────────────────────────────────────────
@bot.tree.command(name="voicetime", description="Voice activity leaderboard — who's spent most time in VC", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_voicetime(interaction: discord.Interaction):
    await interaction.response.defer()
    gid  = str(interaction.guild_id)
    g    = _g(gid)
    raw  = sorted(g["voice"].items(), key=lambda x: x[1], reverse=True)[:10]
    if not raw:
        return await interaction.followup.send("No voice data yet. Members need to spend time in voice channels first.")

    lines = []
    for i, (uid, secs) in enumerate(raw):
        try:
            m = interaction.guild.get_member(int(uid)) or await interaction.guild.fetch_member(int(uid))
            name = m.display_name
        except Exception:
            name = f"User {uid}"
        lines.append(f"{_medal(i)} **{name}** — {_fmt_time(secs)}")

    e = discord.Embed(title="🎙 Voice Time Leaderboard", description="\n".join(lines), color=CYAN)
    e.set_footer(text="Pulse · Byte Labs · all-time totals")
    await interaction.followup.send(embed=e)


# ─── /channelstats ────────────────────────────────────────────────────────────
@bot.tree.command(name="channelstats", description="Ranked channel activity list from most to least active", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_channelstats(interaction: discord.Interaction):
    await interaction.response.defer()
    gid  = str(interaction.guild_id)
    g    = _g(gid)
    data = sorted(g["chan_msgs"].items(), key=lambda x: x[1], reverse=True)
    if not data:
        return await interaction.followup.send("No channel data yet.")

    total = sum(v for _, v in data) or 1
    lines = []
    for i, (cid, cnt) in enumerate(data[:15]):
        ch = interaction.guild.get_channel(int(cid))
        name = f"#{ch.name}" if ch else f"<#{cid}>"
        pct  = int(cnt / total * 100)
        bar  = "█" * (pct // 5) + "░" * (20 - pct // 5)
        lines.append(f"`{i+1:2}.` **{name}** `{bar}` {pct}% · {_fmt_num(cnt)}")

    e = discord.Embed(title="❤️ Channel Health", description="\n".join(lines), color=CYAN)
    e.set_footer(text="Pulse · Byte Labs · % of all tracked messages")
    await interaction.followup.send(embed=e)


# ─── /memberstats ─────────────────────────────────────────────────────────────
@bot.tree.command(name="memberstats", description="Detailed stats for a specific member", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(member="The member to look up (defaults to you)")
async def cmd_memberstats(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    await interaction.response.defer()
    member = member or interaction.user
    gid    = str(interaction.guild_id)
    uid    = str(member.id)
    g      = _g(gid)

    msgs     = g["messages"].get(uid, 0)
    voice    = g["voice"].get(uid, 0)
    rxns     = g["reactions"].get(uid, 0)

    # Rank by messages
    sorted_users = sorted(g["messages"].items(), key=lambda x: x[1], reverse=True)
    rank = next((i+1 for i, (u, _) in enumerate(sorted_users) if u == uid), "?")

    e = discord.Embed(
        title=f"📊 {member.display_name}",
        color=CYAN,
        timestamp=datetime.now(timezone.utc)
    )
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="📝 Messages",    value=_fmt_num(msgs),       inline=True)
    e.add_field(name="🎙 Voice Time",  value=_fmt_time(voice),     inline=True)
    e.add_field(name="⭐ Reactions",   value=_fmt_num(rxns),       inline=True)
    e.add_field(name="🏆 Msg Rank",    value=f"#{rank}",            inline=True)
    e.add_field(name="📅 Joined",
                value=discord.utils.format_dt(member.joined_at, 'D') if member.joined_at else "?",
                inline=True)
    e.add_field(name="🎭 Roles",       value=str(len(member.roles)-1), inline=True)
    e.set_footer(text="Pulse · Byte Labs")
    await interaction.followup.send(embed=e)


# ─── /heatmap ─────────────────────────────────────────────────────────────────
@bot.tree.command(name="heatmap", description="Hour-of-day message activity heatmap", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_heatmap(interaction: discord.Interaction):
    gid   = str(interaction.guild_id)
    g     = _g(gid)
    h     = g["hourly"]
    peak  = max(h) or 1
    BLOCKS = ["░","▒","▓","█"]

    lines = []
    for hour in range(24):
        level = int(h[hour] / peak * 3)
        blk   = BLOCKS[level]
        bar   = blk * min(20, int(h[hour] / peak * 20) + 1)
        label = f"{hour:02d}:00"
        lines.append(f"`{label}` {bar} {_fmt_num(h[hour])}")

    e = discord.Embed(
        title="🕐 Message Activity by Hour (UTC)",
        description="\n".join(lines),
        color=CYAN
    )
    e.set_footer(text="Pulse · Byte Labs · all-time hourly totals")
    await interaction.response.send_message(embed=e)


# ─── /report ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="report", description="Generate a full weekly summary report", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_report(interaction: discord.Interaction):
    await interaction.response.defer()
    guild = interaction.guild
    gid   = str(guild.id)
    g     = _g(gid)

    joins_7  = _count_in_range(g["joins"],  _days_ago(7))
    leaves_7 = _count_in_range(g["leaves"], _days_ago(7))
    net_7    = joins_7 - leaves_7

    joins_14  = _count_in_range(g["joins"],  _days_ago(14))
    leaves_14 = _count_in_range(g["leaves"], _days_ago(14))
    net_14    = joins_14 - leaves_14
    trend     = "📈" if net_7 >= net_14 else "📉"

    top_chatters = sorted(g["messages"].items(), key=lambda x: x[1], reverse=True)[:3]
    chatter_lines = []
    for i, (uid, cnt) in enumerate(top_chatters):
        m = guild.get_member(int(uid))
        name = m.display_name if m else f"User {uid}"
        chatter_lines.append(f"{_medal(i)} {name} — {_fmt_num(cnt)}")

    top_voice = sorted(g["voice"].items(), key=lambda x: x[1], reverse=True)[:3]
    voice_lines = []
    for i, (uid, secs) in enumerate(top_voice):
        m = guild.get_member(int(uid))
        name = m.display_name if m else f"User {uid}"
        voice_lines.append(f"{_medal(i)} {name} — {_fmt_time(secs)}")

    top_chan = sorted(g["chan_msgs"].items(), key=lambda x: x[1], reverse=True)[:3]
    chan_lines = []
    for i, (cid, cnt) in enumerate(top_chan):
        ch = guild.get_channel(int(cid))
        name = f"#{ch.name}" if ch else f"<#{cid}>"
        chan_lines.append(f"{_medal(i)} {name} — {_fmt_num(cnt)}")

    e = discord.Embed(
        title=f"📋 Weekly Report — {guild.name}",
        description=f"**Period:** {discord.utils.format_dt(_days_ago(7), 'D')} → today",
        color=CYAN,
        timestamp=datetime.now(timezone.utc)
    )
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)
    e.add_field(name=f"{trend} Growth (7d)", value=f"+{joins_7} joined · -{leaves_7} left · **net {net_7:+}**", inline=False)
    e.add_field(name="💬 Current Members",   value=f"**{guild.member_count:,}**", inline=True)
    e.add_field(name="📊 Trend vs prev wk",  value=f"{'▲' if net_7>=net_14 else '▼'} {abs(net_7-net_14):+} net", inline=True)
    if chatter_lines:
        e.add_field(name="🏆 Top Chatters", value="\n".join(chatter_lines), inline=False)
    if voice_lines:
        e.add_field(name="🎙 Voice Leaders", value="\n".join(voice_lines), inline=False)
    if chan_lines:
        e.add_field(name="🔥 Hottest Channels", value="\n".join(chan_lines), inline=False)
    e.set_footer(text="Pulse · Byte Labs · Weekly Report")
    await interaction.followup.send(embed=e)


# ─── /pulse-help ──────────────────────────────────────────────────────────────
@bot.tree.command(name="pulse-help", description="Show all Pulse analytics commands", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_help(interaction: discord.Interaction):
    e = discord.Embed(
        title="📊 Pulse — Command Reference",
        description="Server analytics and insights for Discord communities.",
        color=CYAN
    )
    e.add_field(name="📈 Overview", value=(
        "`/stats` — Full server overview\n"
        "`/growth [7|30|90]` — Member join/leave trends\n"
        "`/report` — On-demand weekly summary"
    ), inline=False)
    e.add_field(name="💬 Activity", value=(
        "`/activity [top]` — Message counts by channel\n"
        "`/channelstats` — Ranked channel health list\n"
        "`/heatmap` — Hour-of-day message heatmap"
    ), inline=False)
    e.add_field(name="🏆 Leaderboards", value=(
        "`/top [messages|voice|reactions]` — Top members\n"
        "`/voicetime` — Voice time leaders\n"
        "`/memberstats [@member]` — Per-member breakdown"
    ), inline=False)
    e.set_footer(text="Pulse · Byte Labs · Analytics & Insights")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULED WEEKLY REPORT
# ══════════════════════════════════════════════════════════════════════════════
@tasks.loop(hours=24)
async def weekly_report():
    now = datetime.now(timezone.utc)
    if now.weekday() != 0 or now.hour != 9:   # Monday 09:00 UTC
        return
    for guild in bot.guilds:
        gid = str(guild.id)
        cfg = _read(_CFG_FILE)
        report_channel_id = cfg.get(gid, {}).get("report_channel")
        if not report_channel_id:
            continue
        ch = guild.get_channel(int(report_channel_id))
        if not ch:
            continue
        try:
            g = _g(gid)
            joins_7  = _count_in_range(g["joins"],  _days_ago(7))
            leaves_7 = _count_in_range(g["leaves"], _days_ago(7))
            net_7    = joins_7 - leaves_7
            e = discord.Embed(
                title=f"📋 Weekly Report — {guild.name}",
                color=CYAN,
                timestamp=now
            )
            e.add_field(name="📈 Growth (7d)",
                        value=f"+{joins_7} joined · -{leaves_7} left · net {net_7:+}", inline=False)
            e.add_field(name="👥 Members", value=f"**{guild.member_count:,}**", inline=True)
            e.set_footer(text="Pulse · Byte Labs · Auto Weekly Report")
            await ch.send(embed=e)
        except Exception as err:
            logger.warning(f"[Pulse] weekly report error for {guild.id}: {err}")

@weekly_report.before_loop
async def before_weekly():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════════════════════════════════════
# THREAD ENTRY  (called from wsgi.py)
# ══════════════════════════════════════════════════════════════════════════════
_bot_thread: Optional[threading.Thread] = None

def start_bot_thread():
    global _bot_thread
    if not TOKEN:
        logger.warning("[Pulse] P_TOKEN not set — Pulse bot will not start.")
        return

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.start(TOKEN))
        except Exception as e:
            logger.error(f"[Pulse] Bot crashed: {e}")
        finally:
            loop.close()

    _bot_thread = threading.Thread(target=_run, name="pulse-bot", daemon=True)
    _bot_thread.start()
    logger.info("[Pulse] Bot thread started.")
