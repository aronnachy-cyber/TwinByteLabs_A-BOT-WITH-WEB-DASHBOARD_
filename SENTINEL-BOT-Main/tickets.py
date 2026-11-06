"""Ticket channel management for NVD Official.

Stores ticket metadata in tickets.json next to this module. Channel permissions
are private by default: the opener and server staff can view the channel.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import discord

_DATA_FILE = Path(__file__).with_name("tickets.json")
_LOCK = threading.RLock()


def _read_data() -> dict:
    try:
        with _LOCK:
            if not _DATA_FILE.exists():
                return {"tickets": []}
            with _DATA_FILE.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or not isinstance(data.get("tickets"), list):
                return {"tickets": []}
            return data
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[TICKETS] Could not read ticket store: {exc}")
        return {"tickets": []}


def _write_data(data: dict) -> None:
    tmp = _DATA_FILE.with_suffix(".json.tmp")
    try:
        with _LOCK:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, _DATA_FILE)
    except OSError as exc:
        print(f"[TICKETS] Could not save ticket store: {exc}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def get_all_tickets() -> list[dict]:
    """Return all stored ticket records."""
    return _read_data()["tickets"]


def get_tickets_for_guild(guild_id) -> list[dict]:
    gid = str(guild_id)
    return [ticket for ticket in get_all_tickets()
            if str(ticket.get("guild_id")) == gid]


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return (slug[:35] or "member")


async def _get_ticket_category(guild: discord.Guild, config: dict):
    raw_id = str((config or {}).get("ticket_category", "")).strip()
    if raw_id.isdigit():
        category = guild.get_channel(int(raw_id))
        if isinstance(category, discord.CategoryChannel):
            return category
    # The configured category may be missing or not set. Create a usable one.
    category = discord.utils.get(guild.categories, name="Tickets")
    if category is None:
        category = await guild.create_category(
            "Tickets", reason="NVD Official ticket system"
        )
    return category


async def create_ticket(guild: discord.Guild, user: discord.Member,
                        config: dict | None = None, reason: str = "General Support"):
    """Create a private ticket channel. Returns (channel, ticket_record) or None."""
    if guild is None or user is None:
        return None

    # Prevent accidental duplicate open tickets by the same member in this server.
    existing = next((t for t in get_tickets_for_guild(guild.id)
                     if int(t.get("user_id", 0) or 0) == user.id
                     and t.get("status", "open") == "open"
                     and guild.get_channel(int(t.get("channel_id", 0) or 0))), None)
    if existing:
        existing_channel = guild.get_channel(int(existing["channel_id"]))
        if existing_channel:
            return existing_channel, existing

    category = await _get_ticket_category(guild, config or {})
    bot_member = guild.me or guild.get_member(guild._state.self_id)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True
        ),
    }
    if bot_member:
        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            manage_channels=True, manage_messages=True
        )

    # Let server staff roles access tickets without exposing them to everyone.
    for role in guild.roles:
        perms = role.permissions
        if role != guild.default_role and (
            perms.administrator or perms.manage_messages or perms.manage_channels
        ):
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

    # Ticket IDs are monotonic within this JSON store.
    records = get_all_tickets()
    next_id = max([int(t.get("id", 0) or 0) for t in records] + [0]) + 1
    channel_name = f"ticket-{_safe_slug(user.display_name)}-{next_id:04d}"
    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"NVD ticket #{next_id} | opener={user.id} | {str(reason)[:150]}",
        reason=f"Ticket opened by {user} ({user.id})"
    )

    ticket = {
        "id": next_id,
        "guild_id": guild.id,
        "channel_id": channel.id,
        "user_id": user.id,
        "username": str(user),
        "reason": str(reason)[:1000],
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None,
        "closed_by": None,
    }
    records.append(ticket)
    _write_data({"tickets": records})

    embed = discord.Embed(
        title=f"🎫 Support Ticket #{next_id}",
        description=(
            f"Welcome {user.mention}! Staff will be with you shortly.\n\n"
            f"**Reason:** {discord.utils.escape_markdown(str(reason)[:1000])}\n"
            "Please provide any details that will help us assist you."
        ),
        color=0x4f8ef7,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"{guild.name} • NVD Official")
    await channel.send(content=user.mention, embed=embed)
    return channel, ticket


async def close_ticket(channel: discord.TextChannel, closed_by) -> bool:
    """Mark a ticket closed and hide it from the opener while retaining staff access."""
    records = get_all_tickets()
    ticket = next((t for t in records
                   if int(t.get("channel_id", 0) or 0) == channel.id
                   and str(t.get("guild_id")) == str(channel.guild.id)), None)
    if not ticket:
        return False

    opener = channel.guild.get_member(int(ticket.get("user_id", 0) or 0))
    if opener:
        await channel.set_permissions(opener, overwrite=discord.PermissionOverwrite(
            view_channel=False
        ), reason=f"Ticket closed by {closed_by}")
    ticket["status"] = "closed"
    ticket["closed_at"] = datetime.now(timezone.utc).isoformat()
    ticket["closed_by"] = str(getattr(closed_by, "id", closed_by))
    _write_data({"tickets": records})
    try:
        await channel.edit(name=f"closed-{channel.name}"[:100],
                           reason=f"Ticket closed by {closed_by}")
    except discord.HTTPException as exc:
        print(f"[TICKETS] Could not rename closed ticket {channel.id}: {exc}")
    await channel.send(f"🔒 Ticket closed by {getattr(closed_by, 'mention', str(closed_by))}.")
    return True
