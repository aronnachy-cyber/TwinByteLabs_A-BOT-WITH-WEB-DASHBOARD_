"""
stat_channels.py — MEE6-style Auto-Updating Stat Voice Channels for SP4RK

Creates / updates voice channels whose names show live server stats:
  • Total members
  • Online members (requires presence intent — falls back gracefully)
  • Bot count
  • Server boost count

Admin setup:
  /statschannels setup  — creates all stat channels
  /statschannels remove — removes them
  /statschannels update — force-refresh right now

Background task: call update_all_stat_channels(bot) every 10 minutes.
Discord rate-limits channel renames to 2/10 min per channel, so 10-min
intervals stay safely under that limit.

Storage: db_spark.get_blob / set_blob  (scope "stat_channels", key = guild_id)
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional, Any

import discord
from discord import app_commands

try:
    import db_spark as _db
    _DB = True
except ImportError:
    _db = None
    _DB = False

_LOCAL: Dict[str, Any] = {}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _load(guild_id: str) -> dict:
    if _DB and _db.is_available():
        data = _db.get_blob("stat_channels", guild_id)
        return data if isinstance(data, dict) else {}
    return _LOCAL.get(guild_id, {})


def _save(guild_id: str, data: dict) -> None:
    if _DB and _db.is_available():
        _db.set_blob("stat_channels", data, guild_id)
    else:
        _LOCAL[guild_id] = data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _online_count(guild: discord.Guild) -> int:
    """Count members currently online/idle/dnd. Returns 0 if presences not available."""
    try:
        return sum(
            1 for m in guild.members
            if m.status not in (discord.Status.offline, discord.Status.invisible)
        )
    except Exception:
        return 0


def _bot_count(guild: discord.Guild) -> int:
    return sum(1 for m in guild.members if m.bot)


STAT_DEFS = {
    "members":  lambda g: f"👥 Members: {g.member_count or len(g.members)}",
    "online":   lambda g: f"🟢 Online: {_online_count(g)}",
    "bots":     lambda g: f"🤖 Bots: {_bot_count(g)}",
    "boosts":   lambda g: f"🚀 Boosts: {g.premium_subscription_count or 0}",
}

# ---------------------------------------------------------------------------
# Core update logic
# ---------------------------------------------------------------------------

async def _update_guild(bot: discord.Client, guild: discord.Guild) -> None:
    gid = str(guild.id)
    cfg = _load(gid)
    if not cfg:
        return
    for key, name_fn in STAT_DEFS.items():
        ch_id = cfg.get(key)
        if not ch_id:
            continue
        channel = guild.get_channel(int(ch_id))
        if not channel:
            continue
        new_name = name_fn(guild)
        if channel.name == new_name:
            continue
        try:
            await channel.edit(name=new_name, reason="SP4RK stat update")
        except discord.RateLimited as e:
            await asyncio.sleep(e.retry_after)
        except Exception as ex:
            print(f"[stat_channels] edit error ({key}): {ex}")
        await asyncio.sleep(1.5)  # small pause between edits


async def update_all_stat_channels(bot: discord.Client) -> None:
    """Update stat channels for every guild. Runs every 10 minutes."""
    for guild in bot.guilds:
        try:
            await _update_guild(bot, guild)
        except Exception as e:
            print(f"[stat_channels] guild {guild.id} error: {e}")
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Setup / teardown helpers
# ---------------------------------------------------------------------------

async def setup_stat_channels(guild: discord.Guild) -> dict:
    """Create a category + 4 stat voice channels. Returns {key: channel_id}."""
    try:
        category = await guild.create_category(
            "📊 Server Stats",
            reason="SP4RK Stat Channels setup"
        )
    except Exception as e:
        print(f"[stat_channels] category create error: {e}")
        return {}

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)
    }
    ids = {}
    for key, name_fn in STAT_DEFS.items():
        try:
            ch = await guild.create_voice_channel(
                name_fn(guild),
                category=category,
                overwrites=overwrites,
                reason="SP4RK Stat Channels"
            )
            ids[key] = str(ch.id)
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[stat_channels] create {key} error: {e}")

    ids["category_id"] = str(category.id)
    return ids


async def remove_stat_channels(guild: discord.Guild, cfg: dict) -> None:
    """Delete all tracked stat channels and their category."""
    for key in list(STAT_DEFS.keys()) + ["category_id"]:
        ch_id = cfg.get(key)
        if not ch_id:
            continue
        ch = guild.get_channel(int(ch_id))
        if ch:
            try:
                await ch.delete(reason="SP4RK Stat Channels removed")
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"[stat_channels] delete {key} error: {e}")


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

stats_group = app_commands.Group(name="statschannels", description="📊 Auto-updating stat channels")


@stats_group.command(name="setup", description="[Admin] Create auto-updating stat voice channels")
async def statschannels_setup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    gid = str(interaction.guild_id)
    existing = _load(gid)
    if existing:
        await interaction.followup.send(
            "⚠️ Stat channels are already set up. Use `/statschannels remove` first to recreate them.",
            ephemeral=True
        )
        return
    ids = await setup_stat_channels(interaction.guild)
    if not ids:
        await interaction.followup.send("❌ Failed to create stat channels — check bot permissions.", ephemeral=True)
        return
    _save(gid, ids)
    await interaction.followup.send(
        "✅ **Stat channels created!** They'll update automatically every 10 minutes.\n"
        "Channels created: Members, Online, Bots, Boosts",
        ephemeral=True
    )


@stats_group.command(name="remove", description="[Admin] Remove stat voice channels")
async def statschannels_remove(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    gid = str(interaction.guild_id)
    cfg = _load(gid)
    if not cfg:
        await interaction.followup.send("No stat channels are configured.", ephemeral=True)
        return
    await remove_stat_channels(interaction.guild, cfg)
    _save(gid, {})
    await interaction.followup.send("✅ Stat channels removed.", ephemeral=True)


@stats_group.command(name="update", description="[Admin] Force-refresh stat channel names now")
async def statschannels_update(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await _update_guild(interaction.client, interaction.guild)
    await interaction.followup.send("✅ Stat channels refreshed.", ephemeral=True)
