"""
birthday.py — MEE6-style Birthday System for SP4RK

Features:
  • /birthday set <month> <day>   — register your birthday
  • /birthday check [@user]       — see when someone's birthday is
  • /birthday remove              — remove your birthday
  • /birthday config              — (admin) set birthday channel + role
  • Daily background task fires at 08:00 UTC and announces today's birthdays
  • Optional: gives a configurable birthday role for the day

Storage: db_spark.get_blob / set_blob  (scope "birthdays", key = guild_id)
Config:  scope "birthday_cfg",         key = guild_id
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands

try:
    import db_spark as _db
    _DB = True
except ImportError:
    _db = None
    _DB = False

import json, os

_LOCAL: Dict[str, Any] = {}   # fallback: guild_id -> {user_id: {"month": int, "day": int}}

MONTHS = [
    "January","February","March","April","May","June",
    "July","August","September","October","November","December"
]

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _load(guild_id: str) -> Dict[str, Any]:
    if _DB and _db.is_available():
        data = _db.get_blob("birthdays", guild_id)
        return data if isinstance(data, dict) else {}
    return _LOCAL.get(guild_id, {})


def _save(guild_id: str, data: Dict[str, Any]) -> None:
    if _DB and _db.is_available():
        _db.set_blob("birthdays", data, guild_id)
    else:
        _LOCAL[guild_id] = data


def _load_cfg(guild_id: str) -> Dict[str, Any]:
    if _DB and _db.is_available():
        data = _db.get_blob("birthday_cfg", guild_id)
        return data if isinstance(data, dict) else {}
    return _LOCAL.get(f"cfg_{guild_id}", {})


def _save_cfg(guild_id: str, data: Dict[str, Any]) -> None:
    if _DB and _db.is_available():
        _db.set_blob("birthday_cfg", data, guild_id)
    else:
        _LOCAL[f"cfg_{guild_id}"] = data


# ---------------------------------------------------------------------------
# Slash commands (registered via bot.tree)
# ---------------------------------------------------------------------------

birthday_group = app_commands.Group(name="birthday", description="🎂 Birthday system")


@birthday_group.command(name="set", description="Register your birthday")
@app_commands.describe(month="Birth month (1-12)", day="Birth day (1-31)")
async def birthday_set(interaction: discord.Interaction, month: int, day: int):
    if not 1 <= month <= 12:
        await interaction.response.send_message("❌ Month must be between 1 and 12.", ephemeral=True)
        return
    if not 1 <= day <= 31:
        await interaction.response.send_message("❌ Day must be between 1 and 31.", ephemeral=True)
        return
    guild_id = str(interaction.guild_id)
    data = _load(guild_id)
    data[str(interaction.user.id)] = {"month": month, "day": day, "username": str(interaction.user)}
    _save(guild_id, data)
    await interaction.response.send_message(
        f"🎂 Birthday set to **{MONTHS[month-1]} {day}**! I'll celebrate it on the day. 🎉",
        ephemeral=True
    )


@birthday_group.command(name="check", description="See when someone's birthday is")
@app_commands.describe(member="The member to check (leave blank for yourself)")
async def birthday_check(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    target = member or interaction.user
    guild_id = str(interaction.guild_id)
    data = _load(guild_id)
    entry = data.get(str(target.id))
    if not entry:
        name = "You have" if target == interaction.user else f"{target.display_name} has"
        await interaction.response.send_message(f"🎂 {name} no birthday set.", ephemeral=True)
        return
    m, d = entry["month"], entry["day"]
    now = datetime.now(timezone.utc)
    next_bday = datetime(now.year, m, d, tzinfo=timezone.utc)
    if next_bday < now:
        next_bday = next_bday.replace(year=now.year + 1)
    days_left = (next_bday.date() - now.date()).days
    if days_left == 0:
        days_str = "🎉 **Today!**"
    elif days_left == 1:
        days_str = "tomorrow!"
    else:
        days_str = f"in **{days_left} days**"
    embed = discord.Embed(
        title=f"🎂 {target.display_name}'s Birthday",
        description=f"**{MONTHS[m-1]} {d}** — {days_str}",
        color=0xff69b4
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=False)


@birthday_group.command(name="remove", description="Remove your registered birthday")
async def birthday_remove(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    data = _load(guild_id)
    uid = str(interaction.user.id)
    if uid not in data:
        await interaction.response.send_message("🎂 You have no birthday set.", ephemeral=True)
        return
    del data[uid]
    _save(guild_id, data)
    await interaction.response.send_message("✅ Your birthday has been removed.", ephemeral=True)


@birthday_group.command(name="list", description="Show all upcoming birthdays in this server")
async def birthday_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    guild_id = str(interaction.guild_id)
    data = _load(guild_id)
    if not data:
        await interaction.followup.send("🎂 No birthdays registered yet in this server!")
        return
    now = datetime.now(timezone.utc)
    entries = []
    for uid, info in data.items():
        m, d = info["month"], info["day"]
        next_bday = datetime(now.year, m, d, tzinfo=timezone.utc)
        if next_bday.date() < now.date():
            next_bday = next_bday.replace(year=now.year + 1)
        days_left = (next_bday.date() - now.date()).days
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else info.get("username", f"User {uid}")
        entries.append((days_left, name, m, d))
    entries.sort(key=lambda x: x[0])
    lines = []
    for days_left, name, m, d in entries[:20]:
        if days_left == 0:
            suffix = "🎉 Today!"
        elif days_left == 1:
            suffix = "tomorrow"
        else:
            suffix = f"in {days_left}d"
        lines.append(f"🎂 **{name}** — {MONTHS[m-1]} {d} ({suffix})")
    embed = discord.Embed(
        title="🎂 Upcoming Birthdays",
        description="\n".join(lines) or "No birthdays found.",
        color=0xff69b4
    )
    embed.set_footer(text=f"Showing up to 20 birthdays • {len(data)} total registered")
    await interaction.followup.send(embed=embed)


@birthday_group.command(name="config", description="[Admin] Configure birthday channel and role")
@app_commands.describe(
    channel="Channel to post birthday announcements in",
    role="Role to give members on their birthday (removed next day)"
)
async def birthday_config(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    role: Optional[discord.Role] = None,
):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    guild_id = str(interaction.guild_id)
    cfg = _load_cfg(guild_id)
    changes = []
    if channel is not None:
        cfg["channel_id"] = str(channel.id)
        changes.append(f"Birthday channel → {channel.mention}")
    if role is not None:
        cfg["role_id"] = str(role.id)
        changes.append(f"Birthday role → {role.mention}")
    if not changes:
        ch_id = cfg.get("channel_id")
        r_id = cfg.get("role_id")
        ch_txt = f"<#{ch_id}>" if ch_id else "not set"
        r_txt = f"<@&{r_id}>" if r_id else "not set"
        await interaction.response.send_message(
            f"🎂 **Birthday Config**\nChannel: {ch_txt}\nRole: {r_txt}",
            ephemeral=True
        )
        return
    _save_cfg(guild_id, cfg)
    await interaction.response.send_message("✅ " + " | ".join(changes), ephemeral=True)


# ---------------------------------------------------------------------------
# Daily birthday check task (call from main.py task loop)
# ---------------------------------------------------------------------------

async def run_daily_birthdays(bot: discord.Client) -> None:
    """Check all guilds for today's birthdays. Call once per day at 08:00 UTC."""
    today = datetime.now(timezone.utc)
    for guild in bot.guilds:
        gid = str(guild.id)
        try:
            await _process_guild_birthdays(bot, guild, gid, today)
        except Exception as e:
            print(f"[birthday] guild {gid} error: {e}")


async def _process_guild_birthdays(
    bot: discord.Client, guild: discord.Guild, gid: str, today: datetime
) -> None:
    data = _load(gid)
    cfg = _load_cfg(gid)
    ch_id = cfg.get("channel_id")
    role_id = cfg.get("role_id")

    birthday_members: List[discord.Member] = []
    for uid, info in data.items():
        if info["month"] == today.month and info["day"] == today.day:
            member = guild.get_member(int(uid))
            if member:
                birthday_members.append(member)

    if not birthday_members:
        return

    # Announce
    if ch_id:
        channel = bot.get_channel(int(ch_id))
        if channel:
            for member in birthday_members:
                embed = discord.Embed(
                    title="🎂 Happy Birthday!",
                    description=(
                        f"Everyone wish **{member.display_name}** a happy birthday! 🎉🥳\n\n"
                        f"Hope your day is absolutely amazing, {member.mention}! 🎈"
                    ),
                    color=0xff69b4
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"🎂 SP4RK Birthday System • {today.strftime('%B %d')}")
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    print(f"[birthday] send error: {e}")

    # Assign birthday role
    if role_id:
        role = guild.get_role(int(role_id))
        if role:
            for member in birthday_members:
                try:
                    if role not in member.roles:
                        await member.add_roles(role, reason="SP4RK Birthday System")
                except Exception as e:
                    print(f"[birthday] role assign error: {e}")


async def remove_expired_birthday_roles(bot: discord.Client) -> None:
    """Remove birthday roles from members whose birthday was yesterday. Call daily."""
    yesterday = datetime.now(timezone.utc)
    prev_month = yesterday.month
    prev_day = yesterday.day - 1
    if prev_day == 0:
        import calendar
        prev_month = yesterday.month - 1 if yesterday.month > 1 else 12
        prev_day = calendar.monthrange(yesterday.year, prev_month)[1]

    for guild in bot.guilds:
        gid = str(guild.id)
        try:
            cfg = _load_cfg(gid)
            role_id = cfg.get("role_id")
            if not role_id:
                continue
            role = guild.get_role(int(role_id))
            if not role:
                continue
            data = _load(gid)
            for uid, info in data.items():
                if info["month"] == prev_month and info["day"] == prev_day:
                    member = guild.get_member(int(uid))
                    if member and role in member.roles:
                        try:
                            await member.remove_roles(role, reason="SP4RK Birthday — day over")
                        except Exception:
                            pass
        except Exception as e:
            print(f"[birthday] remove roles error in {gid}: {e}")
