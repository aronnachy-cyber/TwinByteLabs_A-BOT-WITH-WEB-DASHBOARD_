"""
Server Official — second Discord bot under the Byte Labs umbrella.

Runs as its own Discord application (separate token: SERVER_TOKEN) on a
background daemon thread inside the same Flask process as SP4RK. Focused
purely on server management essentials:

    /announce          — post a styled announcement to a channel now
    /schedule          — schedule an announcement for later
    /cancel_schedule   — cancel a pending scheduled announcement
    /list_schedules    — list all pending scheduled announcements
    /giveaway          — start a timed giveaway with reaction-pick winners
    /end_giveaway      — end an active giveaway early
    /list_giveaways    — list active giveaways
    /invites           — show invite leaderboard / a user's invite count
    /stats             — show full server statistics
    /serverinfo        — quick server snapshot
    /help              — list commands

Storage: a single JSON file at server_official_state.json (see helpers
below). The Flask dashboard at /server-official/dashboard reads/writes
the same file so the web UI and bot stay in sync.
"""
from __future__ import annotations

import os
import json
import asyncio
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import uuid

import discord
from discord.ext import commands, tasks

logger = logging.getLogger("server_official")
logging.basicConfig(level=logging.INFO)

# ============================================================
# STORAGE
# ============================================================
_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_official_state.json")
_state_lock = threading.Lock()

DEFAULT_STATE = {
    "scheduled_announcements": [],   # see schema in /server-official routes
    "giveaways": [],                 # active + ended giveaways
    "invite_counts": {},             # {guild_id: {user_id: int}}
    "invite_cache": {},              # {guild_id: {invite_code: {"uses": int, "inviter_id": str}}}
    "settings": {
        "default_announce_color": 0x10b981,
        "embed_footer": "Server Official • Byte Labs",
    },
}


def load_state() -> Dict[str, Any]:
    with _state_lock:
        if not os.path.exists(_STATE_FILE):
            return json.loads(json.dumps(DEFAULT_STATE))
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"[server_official] load_state error: {e}; resetting")
            return json.loads(json.dumps(DEFAULT_STATE))
        # Merge defaults
        for k, v in DEFAULT_STATE.items():
            data.setdefault(k, v if not isinstance(v, dict) else dict(v))
        return data


def save_state(state: Dict[str, Any]) -> None:
    with _state_lock:
        tmp = _STATE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
            os.replace(tmp, _STATE_FILE)
        except Exception as e:
            logger.error(f"[server_official] save_state error: {e}")


def _gen_id() -> str:
    return uuid.uuid4().hex[:10]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def parse_duration(s: str) -> Optional[int]:
    """Parse '10m', '2h', '3d', '90s' or a plain integer (seconds)."""
    if not s:
        return None
    s = s.strip().lower()
    try:
        return int(s)
    except ValueError:
        pass
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    if len(s) >= 2 and s[-1] in units:
        try:
            n = float(s[:-1])
            return int(n * units[s[-1]])
        except ValueError:
            return None
    return None


# ============================================================
# DASHBOARD HELPERS (called from Flask routes)
# ============================================================
def add_scheduled_announcement(
    guild_id: Optional[str],
    channel_id: str,
    title: str,
    message: str,
    scheduled_for_iso: str,
    color: Optional[int] = None,
    created_by: str = "dashboard",
) -> Dict[str, Any]:
    state = load_state()
    item = {
        "id": _gen_id(),
        "guild_id": str(guild_id) if guild_id else None,
        "channel_id": str(channel_id),
        "title": title or "📣 Announcement",
        "message": message,
        "scheduled_for": scheduled_for_iso,
        "color": int(color) if color is not None else state["settings"]["default_announce_color"],
        "posted": False,
        "created_by": created_by,
        "created_at": _now_iso(),
    }
    state["scheduled_announcements"].append(item)
    save_state(state)
    return item


def list_scheduled_announcements(include_posted: bool = False) -> List[Dict[str, Any]]:
    state = load_state()
    items = state.get("scheduled_announcements", [])
    if not include_posted:
        items = [a for a in items if not a.get("posted")]
    return sorted(items, key=lambda a: a.get("scheduled_for", ""))


def delete_scheduled_announcement(announce_id: str) -> bool:
    state = load_state()
    before = len(state["scheduled_announcements"])
    state["scheduled_announcements"] = [a for a in state["scheduled_announcements"] if a.get("id") != announce_id]
    save_state(state)
    return len(state["scheduled_announcements"]) < before


def list_giveaways(include_ended: bool = True) -> List[Dict[str, Any]]:
    state = load_state()
    items = state.get("giveaways", [])
    if not include_ended:
        items = [g for g in items if not g.get("ended")]
    return sorted(items, key=lambda g: g.get("ends_at", ""), reverse=True)


def get_invite_leaderboard(guild_id: Optional[str] = None) -> List[Dict[str, Any]]:
    state = load_state()
    counts = state.get("invite_counts", {})
    if guild_id:
        guild_counts = counts.get(str(guild_id), {})
        return [{"guild_id": str(guild_id), "user_id": uid, "joins": n}
                for uid, n in sorted(guild_counts.items(), key=lambda kv: kv[1], reverse=True)]
    out = []
    for gid, gmap in counts.items():
        for uid, n in gmap.items():
            out.append({"guild_id": gid, "user_id": uid, "joins": n})
    return sorted(out, key=lambda x: x["joins"], reverse=True)


# ============================================================
# THE BOT
# ============================================================
TOKEN = os.environ.get("SERVER_TOKEN", "")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.invites = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def _embed(title: str, description: str = "", color: int = 0x10b981) -> discord.Embed:
    state = load_state()
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    e.set_footer(text=state["settings"].get("embed_footer", "Server Official"))
    return e


# ---------- INVITE TRACKING ----------
async def _refresh_invite_cache():
    """Pull every invite from every guild and store its current usage count."""
    state = load_state()
    cache = state.get("invite_cache", {})
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            continue
        cache[str(guild.id)] = {
            inv.code: {"uses": inv.uses or 0, "inviter_id": str(inv.inviter.id) if inv.inviter else None}
            for inv in invites
        }
    state["invite_cache"] = cache
    save_state(state)


@bot.event
async def on_ready():
    logger.info(f"[server_official] Logged in as {bot.user} (id={bot.user.id})")
    await _refresh_invite_cache()
    try:
        synced = await bot.tree.sync()
        logger.info(f"[server_official] Synced {len(synced)} slash commands")
    except Exception as e:
        logger.error(f"[server_official] sync error: {e}")
    if not scheduler_tick.is_running():
        scheduler_tick.start()


@bot.event
async def on_invite_create(invite: discord.Invite):
    if not invite.guild:
        return
    state = load_state()
    cache = state.setdefault("invite_cache", {})
    g = cache.setdefault(str(invite.guild.id), {})
    g[invite.code] = {
        "uses": invite.uses or 0,
        "inviter_id": str(invite.inviter.id) if invite.inviter else None,
    }
    save_state(state)


@bot.event
async def on_invite_delete(invite: discord.Invite):
    if not invite.guild:
        return
    state = load_state()
    g = state.get("invite_cache", {}).get(str(invite.guild.id), {})
    g.pop(invite.code, None)
    save_state(state)


@bot.event
async def on_member_join(member: discord.Member):
    """Identify which invite was used and increment that inviter's counter."""
    state = load_state()
    cache = state.get("invite_cache", {})
    counts = state.setdefault("invite_counts", {}).setdefault(str(member.guild.id), {})

    try:
        current = await member.guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        return

    old_for_guild = cache.get(str(member.guild.id), {})
    inviter_id = None
    new_cache_for_guild = {}
    for inv in current:
        new_cache_for_guild[inv.code] = {
            "uses": inv.uses or 0,
            "inviter_id": str(inv.inviter.id) if inv.inviter else None,
        }
        prev = old_for_guild.get(inv.code, {"uses": 0})
        if (inv.uses or 0) > prev.get("uses", 0) and inviter_id is None and inv.inviter:
            inviter_id = str(inv.inviter.id)

    cache[str(member.guild.id)] = new_cache_for_guild
    if inviter_id:
        counts[inviter_id] = counts.get(inviter_id, 0) + 1
    save_state(state)


# ---------- /announce ----------
@bot.tree.command(name="announce", description="Post a styled announcement to a channel right now.")
@discord.app_commands.describe(
    message="The body of the announcement",
    channel="Where to post (defaults to current channel)",
    title="Optional title (default: 📣 Announcement)",
    ping_everyone="Ping @everyone with the announcement",
)
async def slash_announce(
    interaction: discord.Interaction,
    message: str,
    channel: Optional[discord.TextChannel] = None,
    title: Optional[str] = None,
    ping_everyone: Optional[bool] = False,
):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message(
            "❌ You need the **Manage Server** permission to use this.", ephemeral=True)
    target = channel or interaction.channel
    embed = _embed(title or "📣 Announcement", message)
    embed.set_author(
        name=f"Posted by {interaction.user.display_name}",
        icon_url=interaction.user.display_avatar.url,
    )
    content = "@everyone" if ping_everyone else None
    try:
        await target.send(content=content, embed=embed,
                          allowed_mentions=discord.AllowedMentions(everyone=ping_everyone))
        await interaction.response.send_message(f"✅ Announcement posted in {target.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"❌ I don't have permission to post in {target.mention}.", ephemeral=True)


# ---------- /schedule ----------
@bot.tree.command(name="schedule", description="Schedule an announcement for later (uses durations like 10m, 2h, 1d).")
@discord.app_commands.describe(
    message="The body of the announcement",
    when="When to post — duration like '30m', '2h', '1d' from now",
    channel="Where to post (defaults to current channel)",
    title="Optional title",
)
async def slash_schedule(
    interaction: discord.Interaction,
    message: str,
    when: str,
    channel: Optional[discord.TextChannel] = None,
    title: Optional[str] = None,
):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message(
            "❌ You need the **Manage Server** permission to use this.", ephemeral=True)
    seconds = parse_duration(when)
    if not seconds or seconds < 5:
        return await interaction.response.send_message(
            "❌ Invalid duration. Try `30m`, `2h`, `1d`, or `90s`.", ephemeral=True)
    when_dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    target = channel or interaction.channel
    item = add_scheduled_announcement(
        guild_id=str(interaction.guild_id) if interaction.guild_id else None,
        channel_id=str(target.id),
        title=title or "📣 Announcement",
        message=message,
        scheduled_for_iso=when_dt.isoformat(),
        created_by=str(interaction.user.id),
    )
    await interaction.response.send_message(
        f"✅ Scheduled — will post in {target.mention} <t:{int(when_dt.timestamp())}:R>\n`ID: {item['id']}`",
        ephemeral=True,
    )


# ---------- /list_schedules ----------
@bot.tree.command(name="list_schedules", description="List pending scheduled announcements.")
async def slash_list_schedules(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    items = [a for a in list_scheduled_announcements()
             if a.get("guild_id") == str(interaction.guild_id) or a.get("guild_id") is None]
    if not items:
        return await interaction.response.send_message("No pending scheduled announcements.", ephemeral=True)
    desc = []
    for a in items[:15]:
        when = _parse_iso(a["scheduled_for"])
        ts = f"<t:{int(when.timestamp())}:R>" if when else a["scheduled_for"]
        snippet = (a["message"][:60] + "…") if len(a["message"]) > 60 else a["message"]
        desc.append(f"`{a['id']}` — {ts}\n> {snippet}")
    await interaction.response.send_message(
        embed=_embed("⏰ Pending Scheduled Announcements", "\n\n".join(desc)),
        ephemeral=True,
    )


# ---------- /cancel_schedule ----------
@bot.tree.command(name="cancel_schedule", description="Cancel a pending scheduled announcement by ID.")
@discord.app_commands.describe(announcement_id="The ID shown in /list_schedules")
async def slash_cancel_schedule(interaction: discord.Interaction, announcement_id: str):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    if delete_scheduled_announcement(announcement_id):
        await interaction.response.send_message(f"🗑️ Cancelled `{announcement_id}`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ No announcement found with ID `{announcement_id}`.", ephemeral=True)


# ---------- /giveaway ----------
@bot.tree.command(name="giveaway", description="Start a giveaway with a 🎉 reaction.")
@discord.app_commands.describe(
    prize="What's being given away",
    duration="How long the giveaway runs — e.g. '1h', '2d', '30m'",
    winners="Number of winners (default: 1)",
    channel="Channel to post in (defaults to current)",
)
async def slash_giveaway(
    interaction: discord.Interaction,
    prize: str,
    duration: str,
    winners: Optional[int] = 1,
    channel: Optional[discord.TextChannel] = None,
):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    seconds = parse_duration(duration)
    if not seconds or seconds < 10:
        return await interaction.response.send_message(
            "❌ Invalid duration. Try `30m`, `2h`, `1d`.", ephemeral=True)
    winners = max(1, min(int(winners or 1), 25))
    target = channel or interaction.channel
    ends_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    embed = _embed(f"🎉 GIVEAWAY: {prize}",
                   f"React with 🎉 to enter!\n\n"
                   f"**Winners:** {winners}\n"
                   f"**Ends:** <t:{int(ends_at.timestamp())}:R>\n"
                   f"**Hosted by:** {interaction.user.mention}",
                   color=0xf59e0b)
    try:
        await interaction.response.defer(ephemeral=True)
        msg = await target.send(embed=embed)
        await msg.add_reaction("🎉")
    except discord.Forbidden:
        return await interaction.followup.send(f"❌ I can't post in {target.mention}.", ephemeral=True)

    state = load_state()
    state["giveaways"].append({
        "id": _gen_id(),
        "guild_id": str(interaction.guild_id),
        "channel_id": str(target.id),
        "message_id": str(msg.id),
        "prize": prize,
        "winners": winners,
        "host_id": str(interaction.user.id),
        "ends_at": ends_at.isoformat(),
        "created_at": _now_iso(),
        "ended": False,
        "winner_ids": [],
    })
    save_state(state)
    await interaction.followup.send(f"✅ Giveaway started in {target.mention} — ends <t:{int(ends_at.timestamp())}:R>", ephemeral=True)


# ---------- /list_giveaways ----------
@bot.tree.command(name="list_giveaways", description="List active giveaways in this server.")
async def slash_list_giveaways(interaction: discord.Interaction):
    items = [g for g in list_giveaways(include_ended=False) if g.get("guild_id") == str(interaction.guild_id)]
    if not items:
        return await interaction.response.send_message("No active giveaways.", ephemeral=True)
    desc = []
    for g in items[:15]:
        when = _parse_iso(g["ends_at"])
        ts = f"<t:{int(when.timestamp())}:R>" if when else g["ends_at"]
        desc.append(f"`{g['id']}` — **{g['prize']}** ({g['winners']} winner(s)) — ends {ts}")
    await interaction.response.send_message(
        embed=_embed("🎁 Active Giveaways", "\n".join(desc)),
        ephemeral=True,
    )


# ---------- /end_giveaway ----------
@bot.tree.command(name="end_giveaway", description="End a giveaway early and pick winners now.")
@discord.app_commands.describe(giveaway_id="ID from /list_giveaways")
async def slash_end_giveaway(interaction: discord.Interaction, giveaway_id: str):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    state = load_state()
    target = next((g for g in state["giveaways"] if g["id"] == giveaway_id and not g.get("ended")), None)
    if not target:
        return await interaction.response.send_message(
            f"❌ No active giveaway with ID `{giveaway_id}`.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    await _finalize_giveaway(target)
    await interaction.followup.send(f"✅ Ended giveaway `{giveaway_id}`.", ephemeral=True)


# ---------- /invites ----------
@bot.tree.command(name="invites", description="Show invite counts for this server.")
@discord.app_commands.describe(user="A specific user (defaults to whole leaderboard)")
async def slash_invites(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    state = load_state()
    counts = state.get("invite_counts", {}).get(str(interaction.guild_id), {})
    if user:
        n = counts.get(str(user.id), 0)
        return await interaction.response.send_message(
            embed=_embed(f"📨 Invites — {user.display_name}", f"**{n}** member(s) invited"),
            ephemeral=True,
        )
    if not counts:
        return await interaction.response.send_message(
            "No invite data yet. As people join via invites, I'll start counting.", ephemeral=True)
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
    lines = []
    for i, (uid, n) in enumerate(top, 1):
        member = interaction.guild.get_member(int(uid)) if interaction.guild else None
        name = member.mention if member else f"<@{uid}>"
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"`#{i}`"
        lines.append(f"{medal} {name} — **{n}** invite(s)")
    await interaction.response.send_message(
        embed=_embed("📨 Invite Leaderboard", "\n".join(lines)),
    )


# ---------- /stats ----------
@bot.tree.command(name="stats", description="Detailed server statistics.")
async def slash_stats(interaction: discord.Interaction):
    g = interaction.guild
    if not g:
        return await interaction.response.send_message("This must be used in a server.", ephemeral=True)

    bots_count = sum(1 for m in g.members if m.bot)
    humans = g.member_count - bots_count if g.member_count else 0
    online = sum(1 for m in g.members if m.status != discord.Status.offline)
    text_chans = sum(1 for c in g.channels if isinstance(c, discord.TextChannel))
    voice_chans = sum(1 for c in g.channels if isinstance(c, discord.VoiceChannel))

    desc = (
        f"**Members:** {g.member_count}  •  👤 {humans} humans  •  🤖 {bots_count} bots\n"
        f"**Online:** {online}\n"
        f"**Channels:** 💬 {text_chans} text  •  🔊 {voice_chans} voice\n"
        f"**Roles:** {len(g.roles)}\n"
        f"**Boosts:** {g.premium_subscription_count} (Tier {g.premium_tier})\n"
        f"**Created:** <t:{int(g.created_at.timestamp())}:D>\n"
        f"**Owner:** {g.owner.mention if g.owner else '—'}"
    )
    embed = _embed(f"📊 {g.name} — Stats", desc, color=0x5b8cff)
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=embed)


# ---------- /serverinfo ----------
@bot.tree.command(name="serverinfo", description="Quick server snapshot.")
async def slash_serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    if not g:
        return await interaction.response.send_message("Must be used in a server.", ephemeral=True)
    embed = _embed(g.name,
                   f"ID: `{g.id}`\nMembers: {g.member_count}\nCreated: <t:{int(g.created_at.timestamp())}:R>")
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=embed)


# ---------- /help ----------
@bot.tree.command(name="help", description="Show what Server Official can do.")
async def slash_help(interaction: discord.Interaction):
    desc = (
        "**📣 Announcements**\n"
        "`/announce` — post immediately\n"
        "`/schedule` — post later (e.g. `30m`, `2h`)\n"
        "`/list_schedules` • `/cancel_schedule`\n\n"
        "**🎉 Giveaways**\n"
        "`/giveaway` — start one (react 🎉 to enter)\n"
        "`/list_giveaways` • `/end_giveaway`\n\n"
        "**📨 Invites & Stats**\n"
        "`/invites` — leaderboard or per-user count\n"
        "`/stats` — full server stats\n"
        "`/serverinfo` — quick snapshot\n\n"
        "Open the dashboard at **/server-official** for a web UI."
    )
    await interaction.response.send_message(embed=_embed("🛡️ Server Official — Help", desc))


# ============================================================
# SCHEDULER LOOP — checks for due announcements and giveaways
# ============================================================
async def _post_scheduled(item: Dict[str, Any]) -> bool:
    chan = bot.get_channel(int(item["channel_id"]))
    if chan is None:
        try:
            chan = await bot.fetch_channel(int(item["channel_id"]))
        except Exception as e:
            logger.warning(f"[server_official] scheduled post: cannot fetch channel: {e}")
            return False
    embed = _embed(item.get("title") or "📣 Announcement",
                   item["message"],
                   color=item.get("color", 0x10b981))
    embed.set_author(name="Scheduled Announcement")
    try:
        await chan.send(embed=embed)
        return True
    except Exception as e:
        logger.warning(f"[server_official] scheduled post failed: {e}")
        return False


async def _finalize_giveaway(g: Dict[str, Any]) -> None:
    """End a giveaway: pick winners from 🎉 reactions, announce, mark ended."""
    import random
    state = load_state()
    target_g = next((x for x in state["giveaways"] if x["id"] == g["id"]), None)
    if not target_g or target_g.get("ended"):
        return

    chan = bot.get_channel(int(g["channel_id"]))
    if chan is None:
        try:
            chan = await bot.fetch_channel(int(g["channel_id"]))
        except Exception:
            target_g["ended"] = True
            save_state(state)
            return
    try:
        msg = await chan.fetch_message(int(g["message_id"]))
    except Exception:
        target_g["ended"] = True
        save_state(state)
        return

    # Collect entrants (skip bots)
    entrants: List[discord.User] = []
    for reaction in msg.reactions:
        if str(reaction.emoji) == "🎉":
            async for u in reaction.users():
                if not u.bot and u not in entrants:
                    entrants.append(u)
            break

    winners_n = max(1, int(g.get("winners", 1)))
    if entrants:
        chosen = random.sample(entrants, k=min(winners_n, len(entrants)))
        winner_ids = [str(u.id) for u in chosen]
        winner_text = ", ".join(u.mention for u in chosen)
        result = f"🎉 **Winner(s):** {winner_text}\n**Prize:** {g['prize']}"
    else:
        winner_ids = []
        result = f"😞 No valid entries — couldn't pick a winner for **{g['prize']}**."

    embed = _embed("🎁 Giveaway Ended", result, color=0xf59e0b)
    try:
        await chan.send(embed=embed)
    except Exception:
        pass

    # Edit original embed to mark ended
    try:
        original = msg.embeds[0] if msg.embeds else None
        if original:
            new_embed = original.copy()
            new_embed.title = f"🎁 ENDED — {g['prize']}"
            new_embed.color = 0x6b7280
            await msg.edit(embed=new_embed)
    except Exception:
        pass

    target_g["ended"] = True
    target_g["winner_ids"] = winner_ids
    target_g["ended_at"] = _now_iso()
    save_state(state)


@tasks.loop(seconds=30)
async def scheduler_tick():
    """Every 30s: post any due scheduled announcements and end any due giveaways."""
    if not bot.is_ready():
        return
    now = datetime.now(timezone.utc)
    state = load_state()
    changed = False

    for ann in state.get("scheduled_announcements", []):
        if ann.get("posted"):
            continue
        when = _parse_iso(ann.get("scheduled_for", ""))
        if when and when <= now:
            ok = await _post_scheduled(ann)
            ann["posted"] = bool(ok)
            ann["posted_at"] = _now_iso() if ok else None
            changed = True

    if changed:
        save_state(state)

    # Giveaways
    for g in list(state.get("giveaways", [])):
        if g.get("ended"):
            continue
        ends = _parse_iso(g.get("ends_at", ""))
        if ends and ends <= now:
            await _finalize_giveaway(g)


# ============================================================
# THREAD LAUNCHER
# ============================================================
_bot_thread: Optional[threading.Thread] = None


def run_bot():
    if not TOKEN:
        logger.warning("[server_official] SERVER_TOKEN not set — Server Official bot will not start.")
        return
    import time
    while True:
        logger.info("[server_official] Starting Server Official bot…")
        try:
            bot.run(TOKEN, reconnect=True, log_handler=None)
        except discord.errors.LoginFailure:
            logger.error("[server_official] Invalid SERVER_TOKEN.")
            return
        except discord.errors.HTTPException as e:
            if getattr(e, "status", None) == 429:
                logger.warning("[server_official] Rate limited; sleeping 60s")
                time.sleep(60)
                continue
            logger.error(f"[server_official] HTTPException: {e}")
            time.sleep(15)
        except Exception as e:
            logger.error(f"[server_official] crashed: {e}; restarting in 10s")
            time.sleep(10)


def start_bot_thread():
    global _bot_thread
    if _bot_thread and _bot_thread.is_alive():
        return _bot_thread
    _bot_thread = threading.Thread(target=run_bot, name="ServerOfficialBot", daemon=True)
    _bot_thread.start()
    return _bot_thread


def is_running() -> bool:
    return bool(_bot_thread and _bot_thread.is_alive() and TOKEN)
