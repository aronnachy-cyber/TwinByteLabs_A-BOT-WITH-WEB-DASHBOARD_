"""
Centralized embed system for NVD Official + Server Official.

- Defines a registry of every user-facing embed both bots send.
- Lets admins customize each one (title / description / color / footer /
  thumbnail / image / author) from the dashboard. Customizations live in
  `embed_templates.json` (per guild + a global fallback).
- Substitutes simple {placeholder} tokens at render time.

Usage from a bot:
    import embeds
    e = embeds.render("sp4rk.welcome", guild_id=guild.id,
                      user=member, server=guild, member_count=guild.member_count)
    await channel.send(embed=e)

Usage from the dashboard:
    embeds.list_for_bot("sp4rk")        -> list of editable templates
    embeds.get_effective(key, guild_id) -> merged dict (default + override)
    embeds.save_override(key, data, guild_id=None)
    embeds.reset_override(key, guild_id=None)
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any

import discord

_DIR = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_DIR, "embed_templates.json")
_LOCK = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
#  REGISTRY  — every embed both bots can send
# ─────────────────────────────────────────────────────────────────────────────
#
# Each entry has:
#   bot          : "sp4rk" | "server_official"
#   category     : grouping label for the dashboard
#   label        : human title in the editor
#   description  : one-line explainer for admins
#   placeholders : list of {tokens} the admin can use
#   default      : fallback embed (title/description/color/footer/thumbnail/image/author)
#
# `color` is stored as an int (0xRRGGBB).
# `thumbnail`/`image`/`author_icon` accept full URLs OR placeholder tokens
# (`{user_avatar}`, `{server_icon}`).
#
EMBED_REGISTRY: dict[str, dict[str, Any]] = {
    # ── NVD Official ────────────────────────────────────────────────────────────
    "sp4rk.welcome": {
        "bot": "sp4rk", "category": "Welcome / Leave",
        "label": "👋 Member Joined",
        "description": "Posted in your log channel when a new member joins.",
        "placeholders": ["{user_mention}", "{user_name}", "{user_id}", "{user_avatar}",
                         "{server_name}", "{server_id}", "{member_count}", "{account_age}"],
        "default": {
            "title": "👋 Welcome to {server_name}!",
            "description": "A new member has arrived — say hello!\n\n"
                           "**{user_name}** has joined the server.\n"
                           "📅 Account created: {account_age}\n\n"
                           "You are member **#{member_count}**. Welcome aboard, {user_mention}!",
            "color": 0x4f8ef7,
            "footer": "🆔 User ID: {user_id}  •  NVD Official Sentinel",
            "thumbnail": "{user_avatar}",
            "image": "", "author_name": "New Member Joined", "author_icon": "",
        },
    },
    "sp4rk.leave": {
        "bot": "sp4rk", "category": "Welcome / Leave",
        "label": "🚪 Member Left",
        "description": "Posted when a member leaves the server.",
        "placeholders": ["{user_mention}", "{user_name}", "{user_id}", "{user_avatar}",
                         "{server_name}", "{member_count}"],
        "default": {
            "title": "🚪 Member Departed",
            "description": "**{user_name}** has left **{server_name}**.\n\n"
                           "The server now has **{member_count}** members.\n"
                           "We hope to see them again someday. 👋",
            "color": 0xe67e22,
            "footer": "🆔 User ID: {user_id}  •  NVD Official Sentinel",
            "thumbnail": "{user_avatar}",
            "image": "", "author_name": "Member Left", "author_icon": "",
        },
    },
    "sp4rk.member_banned": {
        "bot": "sp4rk", "category": "Welcome / Leave",
        "label": "⛔ Member Banned (event)",
        "description": "Fires when Discord reports a ban (any source, including manual).",
        "placeholders": ["{user_name}", "{user_id}", "{server_name}"],
        "default": {
            "title": "⛔ Member Banned",
            "description": "**{user_name}** was banned from **{server_name}**.\n\n"
                           "This action was detected by Discord's audit log.",
            "color": 0xe74c3c,
            "footer": "🆔 User ID: {user_id}  •  NVD Official Sentinel",
            "thumbnail": "", "image": "", "author_name": "Ban Detected", "author_icon": "",
        },
    },
    "sp4rk.warn": {
        "bot": "sp4rk", "category": "Moderation",
        "label": "⚠️ Warning Issued",
        "description": "Sent to your log channel when `!warn` is used.",
        "placeholders": ["{user_mention}", "{user_name}", "{moderator}", "{reason}", "{warn_count}"],
        "default": {
            "title": "⚠️ Warning Issued",
            "description": "A formal warning has been recorded against **{user_name}**.\n\n"
                           "👤 **Member:** {user_mention}\n"
                           "🛡️ **Moderator:** {moderator}\n"
                           "📝 **Reason:** {reason}\n"
                           "🔢 **Total Warnings:** {warn_count}",
            "color": 0xf39c12,
            "footer": "NVD Official Moderation  •  Repeated violations may result in a ban",
            "thumbnail": "", "image": "",
            "author_name": "Moderation Action — Warning", "author_icon": "",
        },
    },
    "sp4rk.kick": {
        "bot": "sp4rk", "category": "Moderation",
        "label": "🚪 Kick",
        "description": "Logged when a member is kicked.",
        "placeholders": ["{user_name}", "{moderator}", "{reason}", "{server_name}"],
        "default": {
            "title": "🚪 Member Kicked",
            "description": "**{user_name}** has been removed from **{server_name}**.\n\n"
                           "🛡️ **Moderator:** {moderator}\n"
                           "📝 **Reason:** {reason}\n\n"
                           "They may rejoin with an active invite link.",
            "color": 0xe67e22,
            "footer": "NVD Official Moderation  •  Kick logged",
            "thumbnail": "", "image": "",
            "author_name": "Moderation Action — Kick", "author_icon": "",
        },
    },
    "sp4rk.ban": {
        "bot": "sp4rk", "category": "Moderation",
        "label": "⛔ Ban",
        "description": "Logged when a member is banned via `!ban`.",
        "placeholders": ["{user_name}", "{moderator}", "{reason}"],
        "default": {
            "title": "⛔ Member Banned",
            "description": "**{user_name}** has been permanently banned.\n\n"
                           "🛡️ **Moderator:** {moderator}\n"
                           "📝 **Reason:** {reason}\n\n"
                           "Use `!unban <user_id>` to reverse this action.",
            "color": 0xe74c3c,
            "footer": "NVD Official Moderation  •  Ban logged",
            "thumbnail": "", "image": "",
            "author_name": "Moderation Action — Ban", "author_icon": "",
        },
    },
    "sp4rk.timeout": {
        "bot": "sp4rk", "category": "Moderation",
        "label": "⏱️ Timeout",
        "description": "Logged when a member is timed out.",
        "placeholders": ["{user_name}", "{moderator}", "{duration}", "{reason}"],
        "default": {
            "title": "⏱️ Member Timed Out",
            "description": "**{user_name}** has been placed in a temporary timeout.\n\n"
                           "🕐 **Duration:** {duration}\n"
                           "🛡️ **Moderator:** {moderator}\n"
                           "📝 **Reason:** {reason}\n\n"
                           "They will be able to participate again when the timeout expires.",
            "color": 0xf39c12,
            "footer": "NVD Official Moderation  •  Timeout logged",
            "thumbnail": "", "image": "",
            "author_name": "Moderation Action — Timeout", "author_icon": "",
        },
    },
    "sp4rk.unban": {
        "bot": "sp4rk", "category": "Moderation",
        "label": "✅ Unban",
        "description": "Logged when a banned user is restored.",
        "placeholders": ["{user_name}", "{moderator}", "{reason}"],
        "default": {
            "title": "✅ Member Unbanned",
            "description": "**{user_name}**'s ban has been lifted.\n\n"
                           "🛡️ **Moderator:** {moderator}\n"
                           "📝 **Reason:** {reason}\n\n"
                           "They may now rejoin the server with an invite link.",
            "color": 0x2ecc71,
            "footer": "NVD Official Moderation  •  Unban logged",
            "thumbnail": "", "image": "",
            "author_name": "Moderation Action — Unban", "author_icon": "",
        },
    },
    "sp4rk.message_edit": {
        "bot": "sp4rk", "category": "Logging",
        "label": "✏️ Message Edited",
        "description": "Posted in your log channel when a member edits a message.",
        "placeholders": ["{user_mention}", "{user_name}", "{user_id}", "{channel}",
                         "{before}", "{after}", "{jump_url}"],
        "default": {
            "title": "✏️ Message Edited",
            "description": "A message was modified in {channel}.\n\n"
                           "👤 **Author:** {user_mention} — `{user_name}`\n\n"
                           "📄 **Before:**\n```{before}```\n"
                           "📝 **After:**\n```{after}```\n\n"
                           "🔗 [Jump to message]({jump_url})",
            "color": 0x3498db,
            "footer": "🆔 User ID: {user_id}  •  NVD Official Logging",
            "thumbnail": "", "image": "", "author_name": "Message Edited", "author_icon": "",
        },
    },
    "sp4rk.message_delete": {
        "bot": "sp4rk", "category": "Logging",
        "label": "🗑️ Message Deleted",
        "description": "Posted in your log channel when a message is deleted.",
        "placeholders": ["{user_mention}", "{user_name}", "{user_id}", "{channel}", "{content}"],
        "default": {
            "title": "🗑️ Message Deleted",
            "description": "A message was permanently removed from {channel}.\n\n"
                           "👤 **Author:** {user_mention} — `{user_name}`\n\n"
                           "📄 **Content:**\n```{content}```",
            "color": 0xe74c3c,
            "footer": "🆔 User ID: {user_id}  •  NVD Official Logging",
            "thumbnail": "", "image": "", "author_name": "Message Deleted", "author_icon": "",
        },
    },
    "sp4rk.voice_join": {
        "bot": "sp4rk", "category": "Logging",
        "label": "🔊 Voice Join",
        "description": "Logged when a member joins a voice channel.",
        "placeholders": ["{user_mention}", "{user_name}", "{channel}"],
        "default": {
            "title": "🔊 Voice Channel Joined",
            "description": "**{user_name}** connected to a voice channel.\n\n"
                           "📡 **Channel:** {channel}\n"
                           "👤 **Member:** {user_mention}",
            "color": 0x2ecc71,
            "footer": "NVD Official Voice Logging",
            "thumbnail": "", "image": "",
            "author_name": "Voice Activity", "author_icon": "",
        },
    },
    "sp4rk.voice_leave": {
        "bot": "sp4rk", "category": "Logging",
        "label": "🔇 Voice Leave",
        "description": "Logged when a member leaves a voice channel.",
        "placeholders": ["{user_mention}", "{user_name}", "{channel}", "{duration}"],
        "default": {
            "title": "🔇 Voice Channel Left",
            "description": "**{user_name}** disconnected from a voice channel.\n\n"
                           "📡 **Channel:** {channel}\n"
                           "👤 **Member:** {user_mention}\n"
                           "⏱️ **Session:** {duration}",
            "color": 0x95a5a6,
            "footer": "NVD Official Voice Logging",
            "thumbnail": "", "image": "",
            "author_name": "Voice Activity", "author_icon": "",
        },
    },
    "sp4rk.ticket_open": {
        "bot": "sp4rk", "category": "Tickets",
        "label": "🎫 Ticket Opened",
        "description": "First message inside the new ticket channel.",
        "placeholders": ["{ticket_id}", "{reason}", "{user_mention}", "{user_name}"],
        "default": {
            "title": "🎫 Support Ticket  •  #{ticket_id}",
            "description": "Welcome, {user_mention}! Your ticket has been received.\n\n"
                           "📝 **Reason:** {reason}\n"
                           "👤 **Opened by:** {user_mention}\n\n"
                           "━━━━━━━━━━━━━━━━━━━━━━\n"
                           "A staff member will assist you shortly.\n"
                           "While you wait, please describe your issue in as much detail as possible.\n"
                           "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "🔒 Press **Close Ticket** or use `/ticket-close` when resolved.",
            "color": 0x3a8fff,
            "footer": "NVD Official Ticket System  •  Do not share sensitive information",
            "thumbnail": "", "image": "", "author_name": "NVD Official Support", "author_icon": "",
        },
    },
    "sp4rk.ticket_close": {
        "bot": "sp4rk", "category": "Tickets",
        "label": "🔒 Ticket Closing",
        "description": "Posted before the ticket channel is deleted.",
        "placeholders": ["{ticket_id}", "{user_mention}", "{moderator}"],
        "default": {
            "title": "🔒 Ticket Closed  •  #{ticket_id}",
            "description": "This ticket has been closed.\n\n"
                           "🛡️ **Closed by:** {moderator}\n\n"
                           "Thank you for reaching out to our support team.\n"
                           "This channel will be deleted in **5 seconds**.",
            "color": 0xe74c3c,
            "footer": "NVD Official Ticket System  •  Transcript may be saved",
            "thumbnail": "", "image": "",
            "author_name": "Ticket Resolved", "author_icon": "",
        },
    },
    "sp4rk.giveaway_active": {
        "bot": "sp4rk", "category": "Giveaways",
        "label": "🎉 Giveaway Active",
        "description": "Posted when `/giveaway` starts a new giveaway.",
        "placeholders": ["{prize}", "{winners}", "{host}", "{ends_at_relative}", "{ends_at_absolute}"],
        "default": {
            "title": "🎉  G I V E A W A Y  🎉",
            "description": "━━━━━━━━━━━━━━━━━━━━━━\n"
                           "🏆 **Prize:** {prize}\n"
                           "👑 **Winners:** {winners}\n"
                           "🎗️ **Hosted by:** {host}\n"
                           "⏰ **Ends:** {ends_at_relative}\n"
                           "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "React with 🎉 below to enter!\n"
                           "One entry per person. Good luck everyone! 🍀",
            "color": 0xf5b400,
            "footer": "⏰ Ends at {ends_at_absolute}  •  NVD Official Giveaways",
            "thumbnail": "", "image": "", "author_name": "NVD Official Giveaway", "author_icon": "",
        },
    },
    "sp4rk.giveaway_ended": {
        "bot": "sp4rk", "category": "Giveaways",
        "label": "🎉 Giveaway Ended",
        "description": "Posted when a giveaway ends with at least one entry.",
        "placeholders": ["{prize}", "{winners_mentions}", "{host}"],
        "default": {
            "title": "🎊  GIVEAWAY ENDED  🎊",
            "description": "The giveaway has concluded — congratulations to the winner(s)!\n\n"
                           "🏆 **Prize:** {prize}\n"
                           "🥇 **Winner(s):** {winners_mentions}\n"
                           "🎗️ **Hosted by:** {host}\n\n"
                           "Thank you to everyone who participated! 🎉",
            "color": 0x2ecc71,
            "footer": "NVD Official Giveaways  •  Congratulations!",
            "thumbnail": "", "image": "",
            "author_name": "Giveaway Complete", "author_icon": "",
        },
    },
    "sp4rk.giveaway_no_entries": {
        "bot": "sp4rk", "category": "Giveaways",
        "label": "🎉 Giveaway Ended (no entries)",
        "description": "Posted when a giveaway ends but nobody reacted.",
        "placeholders": ["{prize}"],
        "default": {
            "title": "😔  Giveaway Ended — No Winner",
            "description": "The giveaway has ended, but unfortunately no one entered.\n\n"
                           "🏆 **Prize:** {prize}\n\n"
                           "No valid reactions were detected before the deadline.\n"
                           "The host may choose to restart the giveaway.",
            "color": 0x95a5a6,
            "footer": "NVD Official Giveaways  •  Better luck next time!",
            "thumbnail": "", "image": "",
            "author_name": "Giveaway Concluded", "author_icon": "",
        },
    },

    # ── Server Official ──────────────────────────────────────────────────
    "so.welcome": {
        "bot": "server_official", "category": "Welcome / Leave",
        "label": "👋 Welcome Message",
        "description": "Posted in the welcome channel when someone joins.",
        "placeholders": ["{user_mention}", "{user_name}", "{user_avatar}",
                         "{server_name}", "{member_count}", "{account_age}"],
        "default": {
            "title": "👋 Welcome to {server_name}!",
            "description": "Hey {user_mention}, glad you're here!\n\n"
                           "You're our **#{member_count}** member.",
            "color": 0x10b981,
            "footer": "Have a great time here, {user_name}!",
            "thumbnail": "{user_avatar}",
            "image": "", "author_name": "", "author_icon": "",
        },
    },
    "so.leave": {
        "bot": "server_official", "category": "Welcome / Leave",
        "label": "👋 Goodbye Message",
        "description": "Posted in the welcome channel when someone leaves.",
        "placeholders": ["{user_mention}", "{user_name}", "{user_avatar}",
                         "{server_name}", "{member_count}"],
        "default": {
            "title": "👋 A member just left",
            "description": "**{user_name}** has left the server.",
            "color": 0x6b7280,
            "footer": "We'll miss you, {user_name}.",
            "thumbnail": "{user_avatar}",
            "image": "", "author_name": "", "author_icon": "",
        },
    },
    "so.log_event": {
        "bot": "server_official", "category": "Logging",
        "label": "📋 Server Log Entry",
        "description": "Generic log entry (member joins/leaves, channel/role changes, etc).",
        "placeholders": ["{log_type}", "{description}"],
        "default": {
            "title": "📋 {log_type}",
            "description": "{description}",
            "color": 0x6366f1,
            "footer": "", "thumbnail": "", "image": "",
            "author_name": "", "author_icon": "",
        },
    },
    "so.starboard": {
        "bot": "server_official", "category": "Engagement",
        "label": "⭐ Starboard Post",
        "description": "Posted in your starboard channel when a message hits the star threshold.",
        "placeholders": ["{user_mention}", "{user_name}", "{user_avatar}",
                         "{stars}", "{channel}", "{content}", "{jump_url}"],
        "default": {
            "title": "⭐ {stars} | {channel}",
            "description": "{content}\n\n[Jump to message]({jump_url})",
            "color": 0xfacc15,
            "footer": "", "thumbnail": "", "image": "",
            "author_name": "{user_name}", "author_icon": "{user_avatar}",
        },
    },
    "so.announcement": {
        "bot": "server_official", "category": "Announcements",
        "label": "📣 Scheduled Announcement",
        "description": "Wrapper used when a scheduled `/announce` fires.",
        "placeholders": ["{title}", "{body}", "{server_name}"],
        "default": {
            "title": "📣 {title}",
            "description": "{body}",
            "color": 0x5b8cff,
            "footer": "{server_name} • Scheduled announcement",
            "thumbnail": "", "image": "", "author_name": "", "author_icon": "",
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
#  Storage  — load / save with atomic write
# ─────────────────────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        import db_spark as _ds
        if _ds.is_available():
            val = _ds.get_blob("embed_templates")
            if val is not None:
                val.setdefault("global", {})
                val.setdefault("guilds", {})
                return val
    except Exception:
        pass
    if not os.path.exists(_FILE):
        return {"global": {}, "guilds": {}}
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("global", {})
        data.setdefault("guilds", {})
        return data
    except Exception as e:
        print(f"[embeds] load error, starting fresh: {e}")
        return {"global": {}, "guilds": {}}


def _save(data: dict) -> None:
    try:
        import db_spark as _ds
        if _ds.is_available():
            _ds.set_blob("embed_templates", data)
    except Exception:
        pass
    tmp = _FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _FILE)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def list_for_bot(bot_name: str) -> list[dict]:
    """Returns every editable template for the given bot, with effective values."""
    out = []
    for key, meta in EMBED_REGISTRY.items():
        if meta["bot"] != bot_name:
            continue
        eff = get_effective(key)
        out.append({
            "key": key,
            "bot": meta["bot"],
            "category": meta["category"],
            "label": meta["label"],
            "description": meta["description"],
            "placeholders": meta["placeholders"],
            "default": meta["default"],
            "current": eff,
            "is_customized": _has_override(key),
        })
    return out


def _has_override(key: str, guild_id: str | int | None = None) -> bool:
    data = _load()
    if guild_id is not None:
        return key in data.get("guilds", {}).get(str(guild_id), {})
    return key in data.get("global", {})


# Public alias so the dashboard doesn't reach into a "private" name.
def has_override(key: str, guild_id: str | int | None = None) -> bool:
    return _has_override(key, guild_id=guild_id)


def get_override(key: str, guild_id: str | int | None = None) -> dict | None:
    """Return the raw stored override (without merging defaults), or None."""
    data = _load()
    if guild_id is not None:
        return data.get("guilds", {}).get(str(guild_id), {}).get(key)
    return data.get("global", {}).get(key)


def get_effective(key: str, guild_id: str | int | None = None) -> dict:
    """Returns the merged template (default ← global override ← guild override)."""
    if key not in EMBED_REGISTRY:
        return {}
    base = dict(EMBED_REGISTRY[key]["default"])
    data = _load()
    g_over = data.get("global", {}).get(key)
    if isinstance(g_over, dict):
        base.update({k: v for k, v in g_over.items() if v not in (None, "")})
    if guild_id is not None:
        guild_over = data.get("guilds", {}).get(str(guild_id), {}).get(key)
        if isinstance(guild_over, dict):
            base.update({k: v for k, v in guild_over.items() if v not in (None, "")})
    return base


def save_override(key: str, payload: dict, guild_id: str | int | None = None) -> bool:
    if key not in EMBED_REGISTRY:
        return False
    allowed = {"title", "description", "color", "footer",
               "thumbnail", "image", "author_name", "author_icon"}
    clean = {}
    for k, v in (payload or {}).items():
        if k not in allowed:
            continue
        if k == "color":
            try:
                if isinstance(v, str):
                    v = v.strip().lstrip("#")
                    v = int(v, 16) if v else 0
                else:
                    v = int(v)
            except Exception:
                continue
            v = max(0, min(0xFFFFFF, v))
        else:
            v = "" if v is None else str(v)
        clean[k] = v
    with _LOCK:
        data = _load()
        if guild_id is not None:
            data.setdefault("guilds", {}).setdefault(str(guild_id), {})[key] = clean
        else:
            data.setdefault("global", {})[key] = clean
        _save(data)
    return True


def reset_override(key: str, guild_id: str | int | None = None) -> bool:
    if key not in EMBED_REGISTRY:
        return False
    with _LOCK:
        data = _load()
        if guild_id is not None:
            data.get("guilds", {}).get(str(guild_id), {}).pop(key, None)
        else:
            data.get("global", {}).pop(key, None)
        _save(data)
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Render — placeholder substitution + Embed construction
# ─────────────────────────────────────────────────────────────────────────────

def _build_vars(kwargs: dict) -> dict:
    """Expand convenience kwargs (user, server, channel, member) into flat tokens."""
    v: dict[str, str] = {}

    user = kwargs.pop("user", None)
    if user is not None:
        v.setdefault("user_mention", getattr(user, "mention", str(user)))
        v.setdefault("user_name",    str(user))
        v.setdefault("user_id",      str(getattr(user, "id", "")))
        avatar = getattr(user, "display_avatar", None)
        v.setdefault("user_avatar",  getattr(avatar, "url", "") if avatar else "")
        created = getattr(user, "created_at", None)
        if created:
            try:
                v.setdefault("account_age", f"<t:{int(created.timestamp())}:R>")
            except Exception:
                pass

    server = kwargs.pop("server", None) or kwargs.pop("guild", None)
    if server is not None:
        v.setdefault("server_name",  getattr(server, "name", ""))
        v.setdefault("server_id",    str(getattr(server, "id", "")))
        v.setdefault("member_count", str(getattr(server, "member_count", "") or ""))
        icon = getattr(server, "icon", None)
        v.setdefault("server_icon",  getattr(icon, "url", "") if icon else "")

    channel = kwargs.pop("channel", None)
    if channel is not None:
        v.setdefault("channel", getattr(channel, "mention", str(channel)))

    moderator = kwargs.pop("moderator", None)
    if moderator is not None:
        v["moderator"] = getattr(moderator, "mention", str(moderator))

    # Anything else: convert to str
    for k, val in kwargs.items():
        v[k] = "" if val is None else str(val)

    return v


def _sub(text: str, vars_: dict) -> str:
    if not text:
        return text
    out = text
    for k, val in vars_.items():
        out = out.replace("{" + k + "}", str(val))
    return out


def _resolve_url(field: str, vars_: dict) -> str:
    """Allow placeholders like {user_avatar} to flow into URL fields."""
    if not field:
        return ""
    field = _sub(field, vars_)
    if field.startswith(("http://", "https://")):
        return field
    return ""


def render(key: str, *, guild_id: str | int | None = None, **kwargs) -> discord.Embed:
    """Render an embed from the (merged) template + supplied placeholder vars."""
    if key not in EMBED_REGISTRY:
        return discord.Embed(description=f"⚠️ Unknown embed `{key}`")

    tpl = get_effective(key, guild_id=guild_id)
    vars_ = _build_vars(kwargs)

    title = _sub(tpl.get("title", "") or "", vars_)
    desc  = _sub(tpl.get("description", "") or "", vars_)
    try:
        color = int(tpl.get("color", 0x3a8fff) or 0)
    except Exception:
        color = 0x3a8fff

    e = discord.Embed(
        title=title[:256] if title else None,
        description=desc[:4000] if desc else None,
        color=color,
        timestamp=datetime.utcnow(),
    )

    footer = _sub(tpl.get("footer", "") or "", vars_)
    if footer:
        e.set_footer(text=footer[:2048])

    thumb = _resolve_url(tpl.get("thumbnail", ""), vars_)
    if thumb:
        try: e.set_thumbnail(url=thumb)
        except Exception: pass

    img = _resolve_url(tpl.get("image", ""), vars_)
    if img:
        try: e.set_image(url=img)
        except Exception: pass

    auth_name = _sub(tpl.get("author_name", "") or "", vars_)
    auth_icon = _resolve_url(tpl.get("author_icon", ""), vars_)
    if auth_name:
        try:
            if auth_icon:
                e.set_author(name=auth_name[:256], icon_url=auth_icon)
            else:
                e.set_author(name=auth_name[:256])
        except Exception:
            pass

    return e


def preview(key: str, payload: dict | None = None,
            guild_id: str | int | None = None) -> dict:
    """
    Render a JSON-serializable dummy preview for the dashboard editor.
    Uses fake placeholder values so the admin can see roughly what their template
    will look like without needing live event data.

    Layering: default ← global override ← guild override ← unsaved draft (`payload`).
    """
    if key not in EMBED_REGISTRY:
        return {"error": "unknown key"}
    # Start from the merged effective view, so guild/global overrides are visible
    # even when the user hasn't typed anything new yet.
    tpl = dict(get_effective(key, guild_id=guild_id))
    if isinstance(payload, dict):
        for k, v in payload.items():
            if v not in (None, ""):
                tpl[k] = v
    sample = {
        "user_mention": "@CoolUser",
        "user_name":    "CoolUser#1234",
        "user_id":      "123456789012345678",
        "user_avatar":  "https://cdn.discordapp.com/embed/avatars/0.png",
        "server_name":  "My Awesome Server",
        "server_id":    "987654321098765432",
        "member_count": "420",
        "account_age":  "2 years ago",
        "moderator":    "@StaffMember",
        "reason":       "spamming in #general",
        "warn_count":   "3",
        "duration":     "10 minutes",
        "channel":      "#general",
        "before":       "this was the original message",
        "after":        "this is the edited version",
        "content":      "this is the deleted message body",
        "jump_url":     "https://discord.com/channels/0/0/0",
        "ticket_id":    "0042",
        "prize":        "Nitro Classic (1 month)",
        "winners":      "1",
        "host":         "@HostUser",
        "winners_mentions": "@WinnerOne",
        "ends_at_relative": "in 2 hours",
        "ends_at_absolute": "2026-01-01 18:00 UTC",
        "log_type":     "MEMBER JOIN",
        "description":  "✅ @SomeUser joined the server.",
        "stars":        "5",
        "title":        "Server-wide announcement",
        "body":         "Hey @everyone, the new season starts tonight!",
    }
    rendered = {
        "title":       _sub(tpl.get("title", "") or "", sample),
        "description": _sub(tpl.get("description", "") or "", sample),
        "color":       int(tpl.get("color", 0x3a8fff) or 0),
        "footer":      _sub(tpl.get("footer", "") or "", sample),
        "thumbnail":   _resolve_url(tpl.get("thumbnail", ""), sample),
        "image":       _resolve_url(tpl.get("image", ""), sample),
        "author_name": _sub(tpl.get("author_name", "") or "", sample),
        "author_icon": _resolve_url(tpl.get("author_icon", ""), sample),
    }
    return rendered
