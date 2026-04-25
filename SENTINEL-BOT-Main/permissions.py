"""
Centralised permission helpers for SP4RK.

The goal: prefer the bot's SERVER-level (guild) permissions over per-channel
overrides. If SP4RK has a high role (Administrator, or Manage Messages at the
guild level), it should be able to act anywhere in the server, not just in the
specific channels where the perm is granted.

Discord still enforces channel overrides for non-Administrator roles, so for
truly server-wide deletion the bot's role must have **Administrator** (which
bypasses every channel override). This helper makes that intent explicit and
logs clearly when a channel override is blocking action.
"""

import discord


def bot_member(message):
    """Return the bot's Member object in this guild, or None."""
    if not message or not message.guild:
        return None
    return message.guild.me


def has_guild_delete_power(message):
    """True if the bot has server-level Admin OR Manage Messages."""
    me = bot_member(message)
    if not me:
        return False
    p = me.guild_permissions
    return p.administrator or p.manage_messages


async def safe_delete(message):
    """
    Try to delete `message` using server-level permission intent.

    Returns True on success, False otherwise. Never raises — failures are
    logged so the rest of the moderation pipeline keeps running.
    """
    if message is None:
        return False

    me = bot_member(message)
    server_power = has_guild_delete_power(message)

    try:
        await message.delete()
        if server_power and me and me.guild_permissions.administrator:
            print(f"[perm] deleted in #{message.channel} via Admin (server-wide).")
        return True
    except discord.Forbidden:
        if server_power:
            print(
                f"[perm] BLOCKED in #{message.channel}: bot has server-level "
                f"Manage Messages but this channel overrides it. "
                f"Give SP4RK Administrator (or remove the channel override) "
                f"for true server-wide deletion."
            )
        else:
            print(
                f"[perm] BLOCKED in #{message.channel}: bot has no Manage "
                f"Messages permission at the server level. Promote SP4RK's role."
            )
        return False
    except discord.NotFound:
        return True  # already gone
    except Exception as e:
        print(f"[perm] safe_delete error: {e}")
        return False
