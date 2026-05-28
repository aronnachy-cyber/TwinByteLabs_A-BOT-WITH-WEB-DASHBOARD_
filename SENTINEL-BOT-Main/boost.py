"""
boost.py — MEE6-style Server Boost Announcements for SP4RK

Features:
  • Auto-detects when a member starts or stops boosting
  • Posts a configurable announcement embed in a boost channel
  • Optionally assigns a booster role
  • /boost config  — (admin) set channel + role + message
  • /boost stats   — show total boosters and boost level
  • /boost leaderboard — top boosters by boost count (if tracked)

Storage: db_spark.get_blob / set_blob  (scope "boost_cfg", key = guild_id)
Hook:    call handle_boost_update(before, after) from on_member_update
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands

try:
    import db_spark as _db
    _DB = True
except ImportError:
    _db = None
    _DB = False

_LOCAL = {}

# ---------------------------------------------------------------------------
# Config storage
# ---------------------------------------------------------------------------

def _load_cfg(guild_id: str) -> dict:
    if _DB and _db.is_available():
        data = _db.get_blob("boost_cfg", guild_id)
        return data if isinstance(data, dict) else {}
    return _LOCAL.get(guild_id, {})


def _save_cfg(guild_id: str, data: dict) -> None:
    if _DB and _db.is_available():
        _db.set_blob("boost_cfg", data, guild_id)
    else:
        _LOCAL[guild_id] = data


# ---------------------------------------------------------------------------
# Event hook — call from on_member_update
# ---------------------------------------------------------------------------

async def handle_boost_update(before: discord.Member, after: discord.Member) -> None:
    """Detect boost start / stop and react accordingly."""
    was_boosting = before.premium_since is not None
    is_boosting  = after.premium_since  is not None

    if was_boosting == is_boosting:
        return  # no change

    guild = after.guild
    gid = str(guild.id)
    cfg = _load_cfg(gid)

    if is_boosting and not was_boosting:
        await _on_boost_start(after, guild, cfg)
    elif was_boosting and not is_boosting:
        await _on_boost_end(after, guild, cfg)


async def _on_boost_start(member: discord.Member, guild: discord.Guild, cfg: dict) -> None:
    ch_id   = cfg.get("channel_id")
    role_id = cfg.get("role_id")
    msg_tpl = cfg.get("message", "")

    # Assign booster role
    if role_id:
        role = guild.get_role(int(role_id))
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="Server Boost — SP4RK")
            except Exception as e:
                print(f"[boost] role assign error: {e}")

    # Post announcement
    if not ch_id:
        return
    channel = guild.get_channel(int(ch_id))
    if not channel:
        return

    boosts = guild.premium_subscription_count or 0
    level  = guild.premium_tier

    if msg_tpl:
        text = (msg_tpl
                .replace("{user}", member.mention)
                .replace("{username}", member.display_name)
                .replace("{server}", guild.name)
                .replace("{boosts}", str(boosts))
                .replace("{level}", str(level)))
        try:
            await channel.send(text)
        except Exception as e:
            print(f"[boost] send error: {e}")
        return

    embed = discord.Embed(
        title="🚀 New Server Boost!",
        description=(
            f"{member.mention} just boosted **{guild.name}**! 🎉\n\n"
            f"Thank you so much for the support, **{member.display_name}**! 💜"
        ),
        color=0xff73fa
    )
    embed.add_field(name="Total Boosts", value=str(boosts), inline=True)
    embed.add_field(name="Server Level", value=f"Level {level}", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="SP4RK Boost System", icon_url=guild.icon.url if guild.icon else None)
    embed.timestamp = datetime.now(timezone.utc)

    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"[boost] embed send error: {e}")


async def _on_boost_end(member: discord.Member, guild: discord.Guild, cfg: dict) -> None:
    role_id = cfg.get("role_id")
    ch_id   = cfg.get("channel_id")

    # Remove booster role
    if role_id:
        role = guild.get_role(int(role_id))
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason="Boost ended — SP4RK")
            except Exception:
                pass

    # Log quietly
    if ch_id:
        channel = guild.get_channel(int(ch_id))
        if channel:
            try:
                embed = discord.Embed(
                    description=f"💔 **{member.display_name}** is no longer boosting {guild.name}.",
                    color=0x808080
                )
                await channel.send(embed=embed)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

boost_group = app_commands.Group(name="boost", description="🚀 Server boost tools")


@boost_group.command(name="config", description="[Admin] Configure boost announcements")
@app_commands.describe(
    channel="Channel for boost announcements",
    role="Role to give to boosters",
    message="Custom boost message (use {user}, {username}, {server}, {boosts}, {level})"
)
async def boost_config(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    role: Optional[discord.Role] = None,
    message: Optional[str] = None,
):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    gid = str(interaction.guild_id)
    cfg = _load_cfg(gid)
    changes = []
    if channel is not None:
        cfg["channel_id"] = str(channel.id)
        changes.append(f"Boost channel → {channel.mention}")
    if role is not None:
        cfg["role_id"] = str(role.id)
        changes.append(f"Booster role → {role.mention}")
    if message is not None:
        cfg["message"] = message[:500]
        changes.append("Custom boost message saved.")
    if not changes:
        ch_id   = cfg.get("channel_id")
        role_id = cfg.get("role_id")
        msg_tpl = cfg.get("message", "")
        ch_txt  = f"<#{ch_id}>" if ch_id else "not set"
        r_txt   = f"<@&{role_id}>" if role_id else "not set"
        await interaction.response.send_message(
            f"🚀 **Boost Config**\nChannel: {ch_txt}\nRole: {r_txt}\n"
            f"Message: `{msg_tpl or 'default embed'}`",
            ephemeral=True
        )
        return
    _save_cfg(gid, cfg)
    await interaction.response.send_message("✅ " + " | ".join(changes), ephemeral=True)


@boost_group.command(name="stats", description="Show server boost stats")
async def boost_stats(interaction: discord.Interaction):
    guild = interaction.guild
    boosts = guild.premium_subscription_count or 0
    level  = guild.premium_tier
    boosters = [m for m in guild.members if m.premium_since]

    embed = discord.Embed(title=f"🚀 {guild.name} Boost Stats", color=0xff73fa)
    embed.add_field(name="Boost Level", value=f"Level {level}", inline=True)
    embed.add_field(name="Total Boosts", value=str(boosts), inline=True)
    embed.add_field(name="Boosters", value=str(len(boosters)), inline=True)

    next_level_boosts = {0: 2, 1: 7, 2: 14}
    needed = next_level_boosts.get(level)
    if needed:
        remaining = max(0, needed - boosts)
        embed.add_field(
            name=f"Next Level (Level {level+1})",
            value=f"{remaining} more boost(s) needed",
            inline=False
        )
    embed.set_footer(text="SP4RK Boost System")
    await interaction.response.send_message(embed=embed)
