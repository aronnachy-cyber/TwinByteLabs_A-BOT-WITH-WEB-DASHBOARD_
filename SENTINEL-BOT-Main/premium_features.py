"""
premium_features.py — SP4RK Premium Admin Feature Pack

Adds the following slash command groups (skips anything already registered):
  /rr        — Advanced Reaction Role Panels (buttons + dropdowns, analytics)
  /logs      — registered by logging_system.py (full configurable logging)
  /automod   — Smart AutoMod slash controls
  /tag       — Advanced Custom Commands / Tag System
  /softban   — Ban + instant unban to clear message history
  /case      — Case Management System
  /stats     — Server Analytics Dashboard
  /rule      — Automation Rules Engine
  /perm      — Role & Permission Manager
  /godpanel  — SP4RK God Panel (premium vibe overview)
"""

from __future__ import annotations

import io
import json
import os
import random
import string
import uuid
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# ---------------------------------------------------------------------------
# Data helpers — local JSON fallback, db_spark blob for persistence
# ---------------------------------------------------------------------------
_DATA_FILE = os.path.join(os.path.dirname(__file__), "premium_data.json")


def _load() -> dict:
    try:
        import db_spark as _dbs
        blob = _dbs.get_blob("premium_features", "global")
        if blob:
            return blob
    except Exception:
        pass
    try:
        with open(_DATA_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        import db_spark as _dbs
        _dbs.set_blob("premium_features", data, "global")
    except Exception:
        pass
    try:
        with open(_DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception:
        pass


def _gd(data: dict, section: str, guild_id: str) -> dict:
    data.setdefault(section, {})
    data[section].setdefault(str(guild_id), {})
    return data[section][str(guild_id)]


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


# ---------------------------------------------------------------------------
# Tag variable substitution
# ---------------------------------------------------------------------------
def _substitute(text: str, interaction: discord.Interaction, target: Optional[discord.Member] = None) -> str:
    user = target or interaction.user
    text = text.replace("{user}", user.mention)
    text = text.replace("{username}", str(user))
    text = text.replace("{server}", interaction.guild.name if interaction.guild else "DMs")
    text = text.replace("{membercount}", str(interaction.guild.member_count) if interaction.guild else "?")
    text = text.replace("{count}", str(interaction.guild.member_count) if interaction.guild else "?")
    text = text.replace("{channel}", interaction.channel.mention if interaction.channel else "#?")
    return text


# ---------------------------------------------------------------------------
# RR Panel UI — Buttons
# ---------------------------------------------------------------------------
class RRButton(discord.ui.Button):
    def __init__(self, emoji_str: str, role_name: str, role_id: str, guild_id: str, panel_id: str):
        super().__init__(
            label=role_name[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"rrb_{guild_id}_{panel_id}_{role_id}",
        )
        try:
            self.emoji = discord.PartialEmoji.from_str(emoji_str)
        except Exception:
            pass
        self.role_id = role_id
        self.guild_id = guild_id
        self.panel_id = panel_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(int(self.role_id))
        if not role:
            await interaction.response.send_message("❌ Role not found — it may have been deleted.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason="RR Panel")
            await interaction.response.send_message(f"✅ Removed **{role.name}**.", ephemeral=True)
            action = "removed"
        else:
            await interaction.user.add_roles(role, reason="RR Panel")
            await interaction.response.send_message(f"✅ You now have **{role.name}**.", ephemeral=True)
            action = "added"
        # Update stats
        try:
            d = _load()
            panels = _gd(d, "rr_panels", self.guild_id)
            p = panels.get(self.panel_id, {})
            p.setdefault("stats", {})
            p["stats"][self.role_id] = p["stats"].get(self.role_id, 0) + (1 if action == "added" else 0)
            panels[self.panel_id] = p
            _save(d)
        except Exception:
            pass


class RRButtonView(discord.ui.View):
    def __init__(self, guild_id: str, panel_id: str, roles: list):
        super().__init__(timeout=None)
        for r in roles[:25]:
            self.add_item(RRButton(r.get("emoji", "⭐"), r.get("role_name", "Role"),
                                   r["role_id"], guild_id, panel_id))


# ---------------------------------------------------------------------------
# RR Panel UI — Dropdown
# ---------------------------------------------------------------------------
class RRSelect(discord.ui.Select):
    def __init__(self, guild_id: str, panel_id: str, roles: list, max_roles: int):
        options = []
        for r in roles[:25]:
            opt = discord.SelectOption(label=r.get("role_name", "Role")[:100], value=r["role_id"])
            try:
                opt.emoji = discord.PartialEmoji.from_str(r.get("emoji", "⭐"))
            except Exception:
                pass
            options.append(opt)
        mv = max(1, min(max_roles or 1, len(options)))
        super().__init__(
            placeholder="Choose your role(s)…",
            options=options,
            max_values=mv,
            min_values=0,
            custom_id=f"rrs_{guild_id}_{panel_id}",
        )
        self.guild_id = guild_id
        self.panel_id = panel_id
        self.all_role_ids = [r["role_id"] for r in roles[:25]]

    async def callback(self, interaction: discord.Interaction):
        wanted = set(self.values)
        panel_roles = set(self.all_role_ids)
        to_add, to_remove = [], []
        for rid in panel_roles:
            role = interaction.guild.get_role(int(rid))
            if not role:
                continue
            if rid in wanted and role not in interaction.user.roles:
                to_add.append(role)
            elif rid not in wanted and role in interaction.user.roles:
                to_remove.append(role)
        if to_add:
            await interaction.user.add_roles(*to_add, reason="RR Dropdown Panel")
        if to_remove:
            await interaction.user.remove_roles(*to_remove, reason="RR Dropdown Panel")
        added = [r.name for r in to_add]
        removed = [r.name for r in to_remove]
        parts = []
        if added:
            parts.append(f"✅ Added: **{', '.join(added)}**")
        if removed:
            parts.append(f"➖ Removed: **{', '.join(removed)}**")
        msg = "\n".join(parts) if parts else "No changes made."
        await interaction.response.send_message(msg, ephemeral=True)
        try:
            d = _load()
            panels = _gd(d, "rr_panels", self.guild_id)
            p = panels.get(self.panel_id, {})
            p.setdefault("stats", {})
            for r in to_add:
                rid = str(r.id)
                p["stats"][rid] = p["stats"].get(rid, 0) + 1
            panels[self.panel_id] = p
            _save(d)
        except Exception:
            pass


class RRSelectView(discord.ui.View):
    def __init__(self, guild_id: str, panel_id: str, roles: list, max_roles: int):
        super().__init__(timeout=None)
        self.add_item(RRSelect(guild_id, panel_id, roles, max_roles))


# ---------------------------------------------------------------------------
# Helper: admin check
# ---------------------------------------------------------------------------
def _is_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    member = interaction.guild.get_member(interaction.user.id) or interaction.user
    if hasattr(member, "guild_permissions"):
        return member.guild_permissions.administrator or member.guild_permissions.manage_guild
    return False


def _admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not _is_admin(interaction):
            await interaction.response.send_message(
                "❌ **Admin only** — You need Administrator or Manage Server permission.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)


# ---------------------------------------------------------------------------
# /rr — Advanced Reaction Roles
# ---------------------------------------------------------------------------
class RRGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="rr", description="👑 Advanced Reaction Role Panels")

    @app_commands.command(name="create", description="Create a new Reaction Role panel")
    @app_commands.describe(title="Panel title", description="Optional description shown on the panel")
    async def rr_create(self, interaction: discord.Interaction, title: str, description: str = ""):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        d = _load()
        panels = _gd(d, "rr_panels", gid)
        pid = _uid()
        while pid in panels:
            pid = _uid()
        panels[pid] = {
            "title": title, "description": description, "channel_id": None,
            "message_id": None, "roles": [], "type": "button", "max_roles": 0,
            "stats": {}, "created_at": _ts(),
        }
        _save(d)
        embed = discord.Embed(title="✅ Panel Created", color=0x5b8cff)
        embed.add_field(name="ID", value=f"`{pid}`", inline=True)
        embed.add_field(name="Title", value=title, inline=True)
        embed.set_footer(text=f"Use /rr add {pid} <emoji> <role>  to add roles, then /rr build {pid} to post it.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="add", description="Add an emoji → role mapping to a panel")
    @app_commands.describe(panel_id="Panel ID from /rr create", emoji="Emoji to display", role="Role to assign")
    async def rr_add(self, interaction: discord.Interaction, panel_id: str, emoji: str, role: discord.Role):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        d = _load()
        panels = _gd(d, "rr_panels", gid)
        p = panels.get(panel_id)
        if not p:
            return await interaction.response.send_message(f"❌ Panel `{panel_id}` not found.", ephemeral=True)
        if len(p["roles"]) >= 25:
            return await interaction.response.send_message("❌ Maximum 25 roles per panel.", ephemeral=True)
        for r in p["roles"]:
            if r["role_id"] == str(role.id):
                return await interaction.response.send_message("❌ That role is already on this panel.", ephemeral=True)
        p["roles"].append({"emoji": emoji, "role_id": str(role.id), "role_name": role.name})
        _save(d)
        await interaction.response.send_message(
            f"✅ Added {emoji} → {role.mention} to panel `{panel_id}` ({len(p['roles'])}/25 roles).", ephemeral=True)

    @app_commands.command(name="remove", description="Remove an emoji → role mapping from a panel")
    @app_commands.describe(panel_id="Panel ID", emoji="Emoji to remove")
    async def rr_remove(self, interaction: discord.Interaction, panel_id: str, emoji: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        d = _load()
        panels = _gd(d, "rr_panels", gid)
        p = panels.get(panel_id)
        if not p:
            return await interaction.response.send_message(f"❌ Panel `{panel_id}` not found.", ephemeral=True)
        before = len(p["roles"])
        p["roles"] = [r for r in p["roles"] if r["emoji"] != emoji]
        _save(d)
        removed = before - len(p["roles"])
        await interaction.response.send_message(
            f"✅ Removed {removed} mapping(s) for {emoji} from panel `{panel_id}`.", ephemeral=True)

    @app_commands.command(name="build", description="Post a Reaction Role panel (buttons) in a channel")
    @app_commands.describe(panel_id="Panel ID", channel="Channel to post in (defaults to current)")
    async def rr_build(self, interaction: discord.Interaction, panel_id: str, channel: Optional[discord.TextChannel] = None):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        d = _load()
        panels = _gd(d, "rr_panels", gid)
        p = panels.get(panel_id)
        if not p:
            return await interaction.response.send_message(f"❌ Panel `{panel_id}` not found.", ephemeral=True)
        if not p["roles"]:
            return await interaction.response.send_message("❌ Add at least one role first with `/rr add`.", ephemeral=True)
        ch = channel or interaction.channel
        embed = discord.Embed(title=p["title"], description=p["description"] or None, color=0x5b8cff)
        roles_preview = "\n".join(f"{r['emoji']} → <@&{r['role_id']}>" for r in p["roles"])
        embed.add_field(name="Roles", value=roles_preview, inline=False)
        embed.set_footer(text=f"Click a button to toggle your role  •  Panel ID: {panel_id}")
        view = RRButtonView(gid, panel_id, p["roles"])
        msg = await ch.send(embed=embed, view=view)
        p["channel_id"] = str(ch.id)
        p["message_id"] = str(msg.id)
        p["type"] = "button"
        _save(d)
        await interaction.response.send_message(f"✅ Panel posted in {ch.mention}.", ephemeral=True)

    @app_commands.command(name="dropdown", description="Post a Reaction Role panel as a dropdown/select menu")
    @app_commands.describe(panel_id="Panel ID", channel="Channel to post in (defaults to current)")
    async def rr_dropdown(self, interaction: discord.Interaction, panel_id: str, channel: Optional[discord.TextChannel] = None):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        d = _load()
        panels = _gd(d, "rr_panels", gid)
        p = panels.get(panel_id)
        if not p:
            return await interaction.response.send_message(f"❌ Panel `{panel_id}` not found.", ephemeral=True)
        if not p["roles"]:
            return await interaction.response.send_message("❌ Add at least one role first with `/rr add`.", ephemeral=True)
        ch = channel or interaction.channel
        embed = discord.Embed(title=p["title"], description=p["description"] or "Select your roles below.", color=0x5b8cff)
        embed.set_footer(text=f"Select roles from the dropdown  •  Panel ID: {panel_id}")
        view = RRSelectView(gid, panel_id, p["roles"], p.get("max_roles", 1))
        msg = await ch.send(embed=embed, view=view)
        p["channel_id"] = str(ch.id)
        p["message_id"] = str(msg.id)
        p["type"] = "dropdown"
        _save(d)
        await interaction.response.send_message(f"✅ Dropdown panel posted in {ch.mention}.", ephemeral=True)

    @app_commands.command(name="limit", description="Set max number of roles a user can pick from a panel")
    @app_commands.describe(panel_id="Panel ID", count="Max roles (0 = unlimited)")
    async def rr_limit(self, interaction: discord.Interaction, panel_id: str, count: int):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        d = _load()
        panels = _gd(d, "rr_panels", gid)
        p = panels.get(panel_id)
        if not p:
            return await interaction.response.send_message(f"❌ Panel `{panel_id}` not found.", ephemeral=True)
        p["max_roles"] = max(0, count)
        _save(d)
        label = "unlimited" if count == 0 else str(count)
        await interaction.response.send_message(f"✅ Panel `{panel_id}` now allows **{label}** role(s). Re-post with `/rr dropdown` to apply.", ephemeral=True)

    @app_commands.command(name="stats", description="View role pick analytics for a panel")
    @app_commands.describe(panel_id="Panel ID")
    async def rr_stats(self, interaction: discord.Interaction, panel_id: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        d = _load()
        panels = _gd(d, "rr_panels", gid)
        p = panels.get(panel_id)
        if not p:
            return await interaction.response.send_message(f"❌ Panel `{panel_id}` not found.", ephemeral=True)
        stats = p.get("stats", {})
        roles = p.get("roles", [])
        embed = discord.Embed(title=f"📊 Panel Analytics — {p['title']}", color=0x5b8cff)
        if not stats:
            embed.description = "*No role picks recorded yet.*"
        else:
            lines = []
            for r in sorted(roles, key=lambda r: -stats.get(r["role_id"], 0)):
                picks = stats.get(r["role_id"], 0)
                bar = "█" * min(10, picks) + "░" * max(0, 10 - picks)
                lines.append(f"{r['emoji']} **{r['role_name']}** — {picks} pick(s)\n`{bar}`")
            embed.description = "\n\n".join(lines)
        embed.set_footer(text=f"Panel ID: {panel_id}  •  Type: {p.get('type','button')}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /logs — Ultra Logging System config
# ---------------------------------------------------------------------------
_LOG_EVENT_PRESETS = {
    "all":      ["messages", "members", "roles", "voice", "channels", "nicknames"],
    "messages": ["messages"],
    "members":  ["members"],
    "roles":    ["roles"],
    "voice":    ["voice"],
    "channels": ["channels"],
}

class LogsGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="logs", description="📜 Ultra Logging System configuration")

    def _cfg(self, gid: str) -> dict:
        d = _load()
        cfg = _gd(d, "logs_config", gid)
        cfg.setdefault("enabled", False)
        cfg.setdefault("channel_id", None)
        cfg.setdefault("events", list(_LOG_EVENT_PRESETS["all"]))
        return cfg, d

    @app_commands.command(name="enable", description="Toggle the Ultra Logging System on or off")
    async def logs_enable(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, d = self._cfg(gid)
        cfg["enabled"] = not cfg["enabled"]
        d["logs_config"][gid] = cfg
        _save(d)
        state = "✅ Enabled" if cfg["enabled"] else "⛔ Disabled"
        ch_hint = f" — set a channel with `/logs channel #channel`" if not cfg["channel_id"] and cfg["enabled"] else ""
        await interaction.response.send_message(f"{state} Ultra Logging{ch_hint}.", ephemeral=True)

    @app_commands.command(name="channel", description="Set the channel where logs are sent")
    @app_commands.describe(channel="The log channel")
    async def logs_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, d = self._cfg(gid)
        cfg["channel_id"] = str(channel.id)
        d["logs_config"][gid] = cfg
        _save(d)
        await interaction.response.send_message(f"✅ Logs will be sent to {channel.mention}.", ephemeral=True)

    @app_commands.command(name="events", description="Set which events to log using a preset")
    @app_commands.describe(preset="Event preset: all, messages, members, roles, voice, channels")
    @app_commands.choices(preset=[
        app_commands.Choice(name="All events", value="all"),
        app_commands.Choice(name="Messages (edits/deletes)", value="messages"),
        app_commands.Choice(name="Members (joins/leaves)", value="members"),
        app_commands.Choice(name="Roles (changes)", value="roles"),
        app_commands.Choice(name="Voice (joins/leaves)", value="voice"),
        app_commands.Choice(name="Channels (create/delete/edit)", value="channels"),
    ])
    async def logs_events(self, interaction: discord.Interaction, preset: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, d = self._cfg(gid)
        cfg["events"] = list(_LOG_EVENT_PRESETS.get(preset, _LOG_EVENT_PRESETS["all"]))
        d["logs_config"][gid] = cfg
        _save(d)
        await interaction.response.send_message(
            f"✅ Logging preset set to **{preset}** — tracking: `{', '.join(cfg['events'])}`", ephemeral=True)

    @app_commands.command(name="filter", description="Toggle a specific event on or off")
    @app_commands.describe(event="Event name: messages, members, roles, voice, channels, nicknames")
    @app_commands.choices(event=[
        app_commands.Choice(name="Messages", value="messages"),
        app_commands.Choice(name="Members", value="members"),
        app_commands.Choice(name="Roles", value="roles"),
        app_commands.Choice(name="Voice", value="voice"),
        app_commands.Choice(name="Channels", value="channels"),
        app_commands.Choice(name="Nicknames", value="nicknames"),
    ])
    async def logs_filter(self, interaction: discord.Interaction, event: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, d = self._cfg(gid)
        if event in cfg["events"]:
            cfg["events"].remove(event)
            action = "⛔ Disabled"
        else:
            cfg["events"].append(event)
            action = "✅ Enabled"
        d["logs_config"][gid] = cfg
        _save(d)
        await interaction.response.send_message(
            f"{action} logging for **{event}**. Active: `{', '.join(cfg['events']) or 'none'}`", ephemeral=True)

    @app_commands.command(name="status", description="View current logging configuration")
    async def logs_status(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, _ = self._cfg(gid)
        ch = f"<#{cfg['channel_id']}>" if cfg["channel_id"] else "*(not set)*"
        embed = discord.Embed(title="📜 Logging Configuration", color=0x5b8cff)
        embed.add_field(name="Status", value="✅ Enabled" if cfg["enabled"] else "⛔ Disabled", inline=True)
        embed.add_field(name="Log Channel", value=ch, inline=True)
        embed.add_field(name="Tracked Events", value=f"`{', '.join(cfg['events']) or 'none'}`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="export", description="Export recent action logs as a file")
    async def logs_export(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        try:
            import db_spark as _dbs
            rows = _dbs.get_action_logs(gid, limit=200) if hasattr(_dbs, "get_action_logs") else []
        except Exception:
            rows = []
        if not rows:
            return await interaction.followup.send("❌ No action logs found for this server.", ephemeral=True)
        lines = [f"[{r.get('timestamp','?')}] {r.get('action','?')} — {r.get('moderator','?')} → {r.get('member','?')} | {r.get('reason','—')}" for r in rows]
        content = "\n".join(lines)
        buf = io.BytesIO(content.encode())
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        await interaction.followup.send(
            f"📥 **Action Log Export** — {len(rows)} entries",
            file=discord.File(buf, filename=f"action_logs_{gid}_{ts_str}.txt"),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# /automod — Smart AutoMod slash controls
# ---------------------------------------------------------------------------
class AutoModGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="automod", description="🤖 Smart AutoMod configuration")

    def _cfg(self, gid: str):
        d = _load()
        cfg = _gd(d, "automod_config", gid)
        cfg.setdefault("enabled", False)
        cfg.setdefault("antispam", False)
        cfg.setdefault("antilink", False)
        cfg.setdefault("antiinvite", False)
        cfg.setdefault("punish", "warn")
        cfg.setdefault("severity_scoring", False)
        cfg.setdefault("auto_escalation", False)
        return cfg, d

    def _save_cfg(self, gid: str, cfg: dict, d: dict):
        d["automod_config"][gid] = cfg
        _save(d)

    @app_commands.command(name="enable", description="Toggle the AutoMod system on or off")
    async def am_enable(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, d = self._cfg(gid)
        cfg["enabled"] = not cfg["enabled"]
        self._save_cfg(gid, cfg, d)
        state = "✅ AutoMod Enabled" if cfg["enabled"] else "⛔ AutoMod Disabled"
        await interaction.response.send_message(state, ephemeral=True)

    @app_commands.command(name="antispam", description="Toggle anti-spam detection")
    @app_commands.describe(enabled="Turn anti-spam on or off")
    @app_commands.choices(enabled=[app_commands.Choice(name="On", value="on"), app_commands.Choice(name="Off", value="off")])
    async def am_antispam(self, interaction: discord.Interaction, enabled: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, d = self._cfg(gid)
        cfg["antispam"] = (enabled == "on")
        self._save_cfg(gid, cfg, d)
        await interaction.response.send_message(f"✅ Anti-spam: **{'ON' if cfg['antispam'] else 'OFF'}**", ephemeral=True)

    @app_commands.command(name="antilink", description="Toggle link filtering")
    @app_commands.choices(enabled=[app_commands.Choice(name="On", value="on"), app_commands.Choice(name="Off", value="off")])
    async def am_antilink(self, interaction: discord.Interaction, enabled: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, d = self._cfg(gid)
        cfg["antilink"] = (enabled == "on")
        self._save_cfg(gid, cfg, d)
        await interaction.response.send_message(f"✅ Anti-link: **{'ON' if cfg['antilink'] else 'OFF'}**", ephemeral=True)

    @app_commands.command(name="antiinvite", description="Block Discord server invites in chat")
    @app_commands.choices(enabled=[app_commands.Choice(name="On", value="on"), app_commands.Choice(name="Off", value="off")])
    async def am_antiinvite(self, interaction: discord.Interaction, enabled: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, d = self._cfg(gid)
        cfg["antiinvite"] = (enabled == "on")
        self._save_cfg(gid, cfg, d)
        await interaction.response.send_message(f"✅ Anti-invite: **{'ON' if cfg['antiinvite'] else 'OFF'}**", ephemeral=True)

    @app_commands.command(name="punish", description="Set the default punishment action for AutoMod violations")
    @app_commands.describe(action="What to do when AutoMod triggers")
    @app_commands.choices(action=[
        app_commands.Choice(name="Warn", value="warn"),
        app_commands.Choice(name="Mute (10 min)", value="mute"),
        app_commands.Choice(name="Kick", value="kick"),
        app_commands.Choice(name="Ban", value="ban"),
    ])
    async def am_punish(self, interaction: discord.Interaction, action: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, d = self._cfg(gid)
        cfg["punish"] = action
        self._save_cfg(gid, cfg, d)
        await interaction.response.send_message(f"✅ AutoMod punishment set to **{action.upper()}**.", ephemeral=True)

    @app_commands.command(name="escalation", description="Toggle auto-escalation (warn → mute → ban on repeat violations)")
    async def am_escalation(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, d = self._cfg(gid)
        cfg["auto_escalation"] = not cfg.get("auto_escalation", False)
        self._save_cfg(gid, cfg, d)
        state = "✅ Enabled" if cfg["auto_escalation"] else "⛔ Disabled"
        await interaction.response.send_message(f"{state} auto-escalation (warn → mute → ban).", ephemeral=True)

    @app_commands.command(name="status", description="View current AutoMod configuration")
    async def am_status(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        cfg, _ = self._cfg(gid)
        e = discord.Embed(title="🤖 AutoMod Configuration", color=0x5b8cff)
        e.add_field(name="Status",        value="✅ ON" if cfg["enabled"]       else "⛔ OFF", inline=True)
        e.add_field(name="Anti-Spam",     value="✅ ON" if cfg["antispam"]      else "⛔ OFF", inline=True)
        e.add_field(name="Anti-Link",     value="✅ ON" if cfg["antilink"]      else "⛔ OFF", inline=True)
        e.add_field(name="Anti-Invite",   value="✅ ON" if cfg["antiinvite"]    else "⛔ OFF", inline=True)
        e.add_field(name="Punishment",    value=cfg["punish"].upper(),                          inline=True)
        e.add_field(name="Auto-Escalate", value="✅ ON" if cfg.get("auto_escalation") else "⛔ OFF", inline=True)
        await interaction.response.send_message(embed=e, ephemeral=True)


# ---------------------------------------------------------------------------
# /tag — Advanced Custom Commands / Tag System
# ---------------------------------------------------------------------------
class TagGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="tag", description="🧾 Server Tag System — custom responses with variables")

    def _tags(self, gid: str):
        d = _load()
        tags = _gd(d, "tags", gid)
        return tags, d

    @app_commands.command(name="create", description="Create a new tag. Supports {user}, {server}, {membercount}, {channel}")
    @app_commands.describe(name="Tag name (no spaces)", content="Tag content — use {user}, {server}, {membercount}")
    async def tag_create(self, interaction: discord.Interaction, name: str, content: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        name = name.lower().strip().replace(" ", "_")
        if not name or len(name) > 32:
            return await interaction.response.send_message("❌ Tag name must be 1–32 characters.", ephemeral=True)
        gid = str(interaction.guild_id)
        tags, d = self._tags(gid)
        if name in tags:
            return await interaction.response.send_message(f"❌ Tag `{name}` already exists. Use `/tag edit` to update it.", ephemeral=True)
        tags[name] = {
            "content": content, "embed": None,
            "created_by": str(interaction.user.id),
            "created_at": _ts(), "uses": 0,
        }
        _save(d)
        await interaction.response.send_message(f"✅ Tag `{name}` created. Use `/tag use {name}` to post it.", ephemeral=True)

    @app_commands.command(name="edit", description="Edit an existing tag's content")
    @app_commands.describe(name="Tag name", content="New content")
    async def tag_edit(self, interaction: discord.Interaction, name: str, content: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        name = name.lower().strip()
        gid = str(interaction.guild_id)
        tags, d = self._tags(gid)
        if name not in tags:
            return await interaction.response.send_message(f"❌ Tag `{name}` not found.", ephemeral=True)
        tags[name]["content"] = content
        tags[name]["edited_at"] = _ts()
        _save(d)
        await interaction.response.send_message(f"✅ Tag `{name}` updated.", ephemeral=True)

    @app_commands.command(name="delete", description="Delete a tag")
    @app_commands.describe(name="Tag name to delete")
    async def tag_delete(self, interaction: discord.Interaction, name: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        name = name.lower().strip()
        gid = str(interaction.guild_id)
        tags, d = self._tags(gid)
        if name not in tags:
            return await interaction.response.send_message(f"❌ Tag `{name}` not found.", ephemeral=True)
        del tags[name]
        _save(d)
        await interaction.response.send_message(f"✅ Tag `{name}` deleted.", ephemeral=True)

    @app_commands.command(name="list", description="List all tags in this server")
    async def tag_list(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        tags, _ = self._tags(gid)
        if not tags:
            return await interaction.response.send_message("📭 No tags yet. Create one with `/tag create`.", ephemeral=True)
        lines = []
        for n, t in sorted(tags.items()):
            uses = t.get("uses", 0)
            preview = t.get("content", "")[:40].replace("\n", " ")
            lines.append(f"`{n}` — {uses} use(s) — *{preview}{'…' if len(t.get('content',''))>40 else ''}*")
        embed = discord.Embed(title=f"🧾 Tags — {interaction.guild.name} ({len(tags)})", color=0x5b8cff)
        embed.description = "\n".join(lines[:30])
        if len(lines) > 30:
            embed.set_footer(text=f"Showing 30/{len(tags)} tags")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="use", description="Post a tag in the current channel")
    @app_commands.describe(name="Tag name", target="Optional: @mention to substitute {user}")
    async def tag_use(self, interaction: discord.Interaction, name: str, target: Optional[discord.Member] = None):
        name = name.lower().strip()
        gid = str(interaction.guild_id)
        tags, d = self._tags(gid)
        t = tags.get(name)
        if not t:
            return await interaction.response.send_message(f"❌ Tag `{name}` not found.", ephemeral=True)
        content = _substitute(t["content"], interaction, target)
        if t.get("embed"):
            emb_data = t["embed"]
            emb = discord.Embed(
                title=_substitute(emb_data.get("title", ""), interaction, target),
                description=_substitute(emb_data.get("description", ""), interaction, target),
                color=0x5b8cff,
            )
            await interaction.response.send_message(embed=emb)
        else:
            await interaction.response.send_message(content)
        tags[name]["uses"] = t.get("uses", 0) + 1
        _save(d)

    @app_commands.command(name="embed", description="Create a tag that posts as a rich embed")
    @app_commands.describe(name="Tag name", title="Embed title", description="Embed description. Supports {user}, {server}")
    async def tag_embed(self, interaction: discord.Interaction, name: str, title: str, description: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        name = name.lower().strip().replace(" ", "_")
        gid = str(interaction.guild_id)
        tags, d = self._tags(gid)
        tags[name] = {
            "content": f"{title}: {description}",
            "embed": {"title": title, "description": description},
            "created_by": str(interaction.user.id),
            "created_at": _ts(), "uses": 0,
        }
        _save(d)
        preview = discord.Embed(title=title, description=description[:200], color=0x5b8cff)
        preview.set_footer(text=f"Tag: {name}  •  Use /tag use {name} to post")
        await interaction.response.send_message("✅ Embed tag created. Preview:", embed=preview, ephemeral=True)


# ---------------------------------------------------------------------------
# /softban — slash version
# ---------------------------------------------------------------------------
async def softban_command(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason", delete_days: int = 1):
    if not interaction.guild.me.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ I need Ban Members permission.", ephemeral=True)
    if not _is_admin(interaction):
        return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
    if delete_days < 0 or delete_days > 7:
        delete_days = 1
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.guild.ban(user, reason=f"Softban by {interaction.user}: {reason}", delete_message_days=delete_days)
        await interaction.guild.unban(user, reason="Softban — automatic unban")
        embed = discord.Embed(
            title="🔨 Softban Applied",
            description=f"**{user}** was softbanned and their last {delete_days}d of messages were deleted.",
            color=0xe67e22,
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to ban that member.", ephemeral=True)
    except Exception as err:
        await interaction.followup.send(f"❌ Error: {err}", ephemeral=True)


# ---------------------------------------------------------------------------
# /case — Case Management System
# ---------------------------------------------------------------------------
class CaseGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="case", description="📁 Case Management System")

    def _cases(self, gid: str):
        d = _load()
        g = _gd(d, "cases", gid)
        g.setdefault("next_id", 1)
        g.setdefault("cases", {})
        return g, d

    @app_commands.command(name="create", description="Manually create a moderation case")
    @app_commands.describe(user="Target member", case_type="Case type", reason="Reason for the case")
    @app_commands.choices(case_type=[
        app_commands.Choice(name="Warning",  value="warn"),
        app_commands.Choice(name="Mute",     value="mute"),
        app_commands.Choice(name="Kick",     value="kick"),
        app_commands.Choice(name="Ban",      value="ban"),
        app_commands.Choice(name="Note",     value="note"),
        app_commands.Choice(name="Softban",  value="softban"),
    ])
    async def case_create(self, interaction: discord.Interaction, user: discord.Member, case_type: str, reason: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        g, d = self._cases(gid)
        cid = str(g["next_id"])
        g["next_id"] += 1
        g["cases"][cid] = {
            "id": cid, "type": case_type,
            "user_id": str(user.id), "user_tag": str(user),
            "mod_id": str(interaction.user.id), "mod_tag": str(interaction.user),
            "reason": reason, "ts": _ts(), "status": "open", "notes": "",
        }
        _save(d)
        embed = discord.Embed(title=f"📁 Case #{cid} Created", color=0x5b8cff)
        embed.add_field(name="Type",      value=case_type.upper(), inline=True)
        embed.add_field(name="User",      value=str(user),          inline=True)
        embed.add_field(name="Moderator", value=str(interaction.user), inline=True)
        embed.add_field(name="Reason",    value=reason,              inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="view", description="View details of a specific case by ID")
    @app_commands.describe(case_id="The case number")
    async def case_view(self, interaction: discord.Interaction, case_id: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        g, _ = self._cases(gid)
        case = g["cases"].get(str(case_id))
        if not case:
            return await interaction.response.send_message(f"❌ Case `#{case_id}` not found.", ephemeral=True)
        status_icon = "🟢" if case["status"] == "closed" else "🔴"
        embed = discord.Embed(title=f"📁 Case #{case_id}", color=0x5b8cff if case["status"] == "open" else 0x2ecc71)
        embed.add_field(name="Status",    value=f"{status_icon} {case['status'].upper()}", inline=True)
        embed.add_field(name="Type",      value=case["type"].upper(),   inline=True)
        embed.add_field(name="User",      value=case["user_tag"],       inline=True)
        embed.add_field(name="Moderator", value=case["mod_tag"],        inline=True)
        embed.add_field(name="Reason",    value=case["reason"],         inline=False)
        if case.get("notes"):
            embed.add_field(name="Notes", value=case["notes"],          inline=False)
        embed.set_footer(text=f"Opened: {case['ts'][:10]}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="list", description="List cases, optionally filtered by user")
    @app_commands.describe(user="Filter by member (leave blank for all)")
    async def case_list(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        g, _ = self._cases(gid)
        all_cases = list(g["cases"].values())
        if user:
            all_cases = [c for c in all_cases if c["user_id"] == str(user.id)]
        if not all_cases:
            return await interaction.response.send_message("📭 No cases found.", ephemeral=True)
        all_cases = sorted(all_cases, key=lambda c: int(c["id"]), reverse=True)[:20]
        lines = []
        for c in all_cases:
            icon = "🟢" if c["status"] == "closed" else "🔴"
            lines.append(f"{icon} **#{c['id']}** `{c['type'].upper()}` — {c['user_tag']} — *{c['reason'][:40]}*")
        embed = discord.Embed(title=f"📁 Cases{f' for {user}' if user else ''}", color=0x5b8cff)
        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Showing {len(all_cases)} most recent cases")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="close", description="Close/resolve a case")
    @app_commands.describe(case_id="Case number to close", note="Optional resolution note")
    async def case_close(self, interaction: discord.Interaction, case_id: str, note: str = ""):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        g, d = self._cases(gid)
        case = g["cases"].get(str(case_id))
        if not case:
            return await interaction.response.send_message(f"❌ Case `#{case_id}` not found.", ephemeral=True)
        case["status"] = "closed"
        case["closed_at"] = _ts()
        case["closed_by"] = str(interaction.user)
        if note:
            case["notes"] = note
        _save(d)
        await interaction.response.send_message(f"✅ Case **#{case_id}** closed{f' — *{note}*' if note else ''}.", ephemeral=True)


# ---------------------------------------------------------------------------
# /stats — Server Analytics Dashboard
# ---------------------------------------------------------------------------
class StatsGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="stats", description="📊 Server Analytics Dashboard")

    @app_commands.command(name="server", description="Show a full server overview and stats")
    async def stats_server(self, interaction: discord.Interaction):
        g = interaction.guild
        if not g:
            return await interaction.response.send_message("❌ Server only.", ephemeral=True)
        await interaction.response.defer()
        bots    = sum(1 for m in g.members if m.bot)
        humans  = g.member_count - bots
        online  = sum(1 for m in g.members if m.status != discord.Status.offline)
        text_ch = sum(1 for c in g.channels if isinstance(c, discord.TextChannel))
        voice_ch= sum(1 for c in g.channels if isinstance(c, discord.VoiceChannel))
        embed = discord.Embed(title=f"📊 {g.name} — Server Overview", color=0x5b8cff, timestamp=datetime.now(timezone.utc))
        embed.set_thumbnail(url=g.icon.url if g.icon else None)
        embed.add_field(name="👥 Members",    value=f"**{g.member_count}** total\n{humans} humans / {bots} bots",  inline=True)
        embed.add_field(name="🟢 Online",     value=f"{online} members",                                            inline=True)
        embed.add_field(name="📅 Created",    value=f"<t:{int(g.created_at.timestamp())}:D>",                       inline=True)
        embed.add_field(name="💬 Channels",   value=f"{text_ch} text / {voice_ch} voice",                           inline=True)
        embed.add_field(name="🎭 Roles",      value=str(len(g.roles) - 1),                                          inline=True)
        embed.add_field(name="💎 Boosts",     value=f"Tier {g.premium_tier} — {g.premium_subscription_count} boosts", inline=True)
        embed.add_field(name="😀 Emojis",     value=f"{len(g.emojis)}/{g.emoji_limit}",                             inline=True)
        embed.add_field(name="🔒 Verification", value=str(g.verification_level).title(),                            inline=True)
        embed.add_field(name="👑 Owner",      value=f"<@{g.owner_id}>",                                             inline=True)
        embed.set_footer(text="SP4RK Analytics  •  /stats activity for message data")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="activity", description="Show server message activity from analytics data")
    async def stats_activity(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Server only.", ephemeral=True)
        await interaction.response.defer()
        gid = str(interaction.guild_id)
        try:
            import db_spark as _dbs
            data = _dbs.analytics_get_data(gid, days=30)
            daily = data.get("daily", [])
        except Exception:
            daily = []
        total_msgs = sum(d.get("messages", 0) for d in daily)
        today_msgs = daily[-1].get("messages", 0) if daily else 0
        week_msgs  = sum(d.get("messages", 0) for d in daily[-7:])
        peak = max(daily, key=lambda d: d.get("messages", 0), default={})
        embed = discord.Embed(title="📊 Message Activity", color=0x5b8cff, timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Today",       value=f"**{today_msgs}** messages", inline=True)
        embed.add_field(name="This Week",   value=f"**{week_msgs}** messages",  inline=True)
        embed.add_field(name="Last 30 Days",value=f"**{total_msgs}** messages", inline=True)
        if peak:
            embed.add_field(name="Peak Day", value=f"{peak.get('date','?')} — {peak.get('messages',0)} msgs", inline=True)
        embed.set_footer(text="View full charts on the Byte Labs Analytics dashboard")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="users", description="Show top chatters and voice activity leaders")
    async def stats_users(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Server only.", ephemeral=True)
        await interaction.response.defer()
        gid = str(interaction.guild_id)
        try:
            import db_spark as _dbs
            data = _dbs.analytics_get_data(gid, days=30)
            chatters = data.get("top_chatters", [])
            voice    = data.get("top_voice", [])
        except Exception:
            chatters, voice = [], []
        embed = discord.Embed(title="📊 Top Users", color=0x5b8cff)
        if chatters:
            lines = [f"`{i+1}.` **{c['username']}** — {c['messages']} msgs" for i, c in enumerate(chatters[:5])]
            embed.add_field(name="💬 Top Chatters", value="\n".join(lines), inline=True)
        else:
            embed.add_field(name="💬 Top Chatters", value="*No data yet*", inline=True)
        if voice:
            lines = [f"`{i+1}.` **{v['username']}** — {v['minutes']} min" for i, v in enumerate(voice[:5])]
            embed.add_field(name="🔊 Top Voice", value="\n".join(lines), inline=True)
        else:
            embed.add_field(name="🔊 Top Voice", value="*No data yet*", inline=True)
        embed.set_footer(text="All-time voice, last 30d messages")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="growth", description="Show member growth summary for the past 30 days")
    async def stats_growth(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Server only.", ephemeral=True)
        await interaction.response.defer()
        gid = str(interaction.guild_id)
        try:
            import db_spark as _dbs
            data = _dbs.analytics_get_data(gid, days=30)
            daily = data.get("daily", [])
        except Exception:
            daily = []
        total_joins  = sum(d.get("joins", 0)  for d in daily)
        total_leaves = sum(d.get("leaves", 0) for d in daily)
        net = total_joins - total_leaves
        peak = max(daily, key=lambda d: d.get("joins", 0), default={})
        embed = discord.Embed(title="📈 Member Growth — Last 30 Days", color=0x2ecc71 if net >= 0 else 0xe74c3c)
        embed.add_field(name="Total Joins",  value=f"+{total_joins}", inline=True)
        embed.add_field(name="Total Leaves", value=f"-{total_leaves}", inline=True)
        embed.add_field(name="Net Change",   value=f"{'+'if net>=0 else ''}{net}", inline=True)
        if peak:
            embed.add_field(name="Peak Join Day", value=f"{peak.get('date','?')} ({peak.get('joins',0)} joins)", inline=False)
        retention = round(max(0, 1 - total_leaves / max(total_joins, 1)) * 100, 1)
        embed.add_field(name="Retention Rate", value=f"{retention}%", inline=True)
        embed.set_footer(text="SP4RK Analytics  •  For charts open Byte Labs > Analytics tab")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="export", description="Export analytics data as a JSON file")
    async def stats_export(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        try:
            import db_spark as _dbs
            data = _dbs.analytics_get_data(gid, days=60)
            mood = _dbs.mood_get_current(gid)
        except Exception as e:
            data, mood = {}, {}
        export = {
            "guild_id": gid, "guild_name": interaction.guild.name,
            "exported_at": _ts(), "analytics": data, "mood": mood,
        }
        buf = io.BytesIO(json.dumps(export, indent=2, default=str).encode())
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        await interaction.followup.send(
            "📥 **Analytics Export** — 60-day data + mood snapshot",
            file=discord.File(buf, filename=f"spark_analytics_{gid}_{ts_str}.json"),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# /rule — Automation Rules Engine
# ---------------------------------------------------------------------------
_RULE_CONDITIONS = ["spam", "word", "newaccount", "joinrate", "contains", "capslock"]
_RULE_ACTIONS    = ["warn", "mute", "kick", "ban", "delete", "slowmode"]

class RuleGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="rule", description="⚙️ Automation Rules Engine")

    def _rules(self, gid: str):
        d = _load()
        r = _gd(d, "rules", gid)
        return r, d

    @app_commands.command(name="create", description="Create an automation rule (e.g. if spam → mute)")
    @app_commands.describe(
        name="Rule name (unique per server)",
        condition="Condition type",
        condition_value="Condition value (e.g. 5 for spam count, 'discord.gg' for word)",
        action="Action to take",
        action_value="Action value (e.g. mute duration '10m', channel ID for msg)",
    )
    @app_commands.choices(condition=[
        app_commands.Choice(name="Spam (messages in short time)", value="spam"),
        app_commands.Choice(name="Contains word/phrase",          value="word"),
        app_commands.Choice(name="New account (days old)",        value="newaccount"),
        app_commands.Choice(name="Join rate (joins per minute)",  value="joinrate"),
        app_commands.Choice(name="Caps lock heavy (% threshold)", value="capslock"),
    ])
    @app_commands.choices(action=[
        app_commands.Choice(name="Warn",      value="warn"),
        app_commands.Choice(name="Mute",      value="mute"),
        app_commands.Choice(name="Kick",      value="kick"),
        app_commands.Choice(name="Ban",       value="ban"),
        app_commands.Choice(name="Delete message", value="delete"),
        app_commands.Choice(name="Set slowmode",   value="slowmode"),
    ])
    async def rule_create(self, interaction: discord.Interaction, name: str, condition: str,
                          condition_value: str, action: str, action_value: str = ""):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        name = name.lower().strip().replace(" ", "_")
        gid = str(interaction.guild_id)
        rules, d = self._rules(gid)
        if name in rules:
            return await interaction.response.send_message(f"❌ Rule `{name}` already exists. Delete it first.", ephemeral=True)
        rules[name] = {
            "name": name, "condition": condition, "condition_value": condition_value,
            "action": action, "action_value": action_value,
            "enabled": True, "created_at": _ts(), "triggers": 0,
        }
        _save(d)
        embed = discord.Embed(title="⚙️ Rule Created", color=0x5b8cff)
        embed.add_field(name="Name",      value=f"`{name}`",                              inline=True)
        embed.add_field(name="IF",        value=f"`{condition}` = `{condition_value}`",   inline=True)
        embed.add_field(name="THEN",      value=f"`{action}` `{action_value or '—'}`",    inline=True)
        embed.set_footer(text="Rule is ENABLED — use /rule toggle to pause it")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="list", description="List all automation rules in this server")
    async def rule_list(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        rules, _ = self._rules(gid)
        if not rules:
            return await interaction.response.send_message("📭 No rules yet. Create one with `/rule create`.", ephemeral=True)
        embed = discord.Embed(title=f"⚙️ Automation Rules ({len(rules)})", color=0x5b8cff)
        lines = []
        for n, r in rules.items():
            icon = "✅" if r["enabled"] else "⛔"
            lines.append(f"{icon} `{n}` — IF `{r['condition']}={r['condition_value']}` → `{r['action']}` (triggered {r.get('triggers',0)}x)")
        embed.description = "\n".join(lines[:20])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="delete", description="Delete an automation rule")
    @app_commands.describe(name="Rule name to delete")
    async def rule_delete(self, interaction: discord.Interaction, name: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        name = name.lower().strip()
        gid = str(interaction.guild_id)
        rules, d = self._rules(gid)
        if name not in rules:
            return await interaction.response.send_message(f"❌ Rule `{name}` not found.", ephemeral=True)
        del rules[name]
        _save(d)
        await interaction.response.send_message(f"✅ Rule `{name}` deleted.", ephemeral=True)

    @app_commands.command(name="toggle", description="Enable or disable an automation rule")
    @app_commands.describe(name="Rule name to toggle")
    async def rule_toggle(self, interaction: discord.Interaction, name: str):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        name = name.lower().strip()
        gid = str(interaction.guild_id)
        rules, d = self._rules(gid)
        r = rules.get(name)
        if not r:
            return await interaction.response.send_message(f"❌ Rule `{name}` not found.", ephemeral=True)
        r["enabled"] = not r["enabled"]
        _save(d)
        state = "✅ Enabled" if r["enabled"] else "⛔ Disabled"
        await interaction.response.send_message(f"{state} rule `{name}`.", ephemeral=True)


# ---------------------------------------------------------------------------
# /perm — Role & Permission Manager
# ---------------------------------------------------------------------------
class PermGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="perm", description="🔐 Role & Permission Manager")

    @app_commands.command(name="view", description="Show permission overview for a channel or role")
    @app_commands.describe(channel="Channel to inspect (defaults to current)")
    async def perm_view(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        ch = channel or interaction.channel
        embed = discord.Embed(title=f"🔐 Permissions — #{ch.name}", color=0x5b8cff)
        lines = []
        for target, overwrite in ch.overwrites.items():
            name = target.name if hasattr(target, "name") else str(target)
            allow, deny = overwrite.pair()
            granted = [p for p, v in allow if v] if allow else []
            denied  = [p for p, v in deny  if v] if deny  else []
            if granted or denied:
                parts = []
                if granted: parts.append(f"✅ {', '.join(granted[:4])}")
                if denied:  parts.append(f"❌ {', '.join(denied[:4])}")
                lines.append(f"**{name}**: {' | '.join(parts)}")
        embed.description = "\n".join(lines[:20]) if lines else "*No custom overwrites — using role defaults.*"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="lock", description="Deny send permission for a role in a channel")
    @app_commands.describe(channel="Channel to lock", role="Role to deny (defaults to @everyone)")
    async def perm_lock(self, interaction: discord.Interaction, channel: discord.TextChannel, role: Optional[discord.Role] = None):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        r = role or interaction.guild.default_role
        await channel.set_permissions(r, send_messages=False, reason=f"Locked by {interaction.user}")
        await interaction.response.send_message(f"🔒 Locked {channel.mention} for **{r.name}**.", ephemeral=True)

    @app_commands.command(name="unlock", description="Restore send permission for a role in a channel")
    @app_commands.describe(channel="Channel to unlock", role="Role to restore (defaults to @everyone)")
    async def perm_unlock(self, interaction: discord.Interaction, channel: discord.TextChannel, role: Optional[discord.Role] = None):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        r = role or interaction.guild.default_role
        await channel.set_permissions(r, send_messages=None, reason=f"Unlocked by {interaction.user}")
        await interaction.response.send_message(f"🔓 Unlocked {channel.mention} for **{r.name}**.", ephemeral=True)

    @app_commands.command(name="clone", description="Copy one role's channel permissions to another role")
    @app_commands.describe(source_role="Copy permissions FROM this role", target_role="Apply permissions TO this role")
    async def perm_clone(self, interaction: discord.Interaction, source_role: discord.Role, target_role: discord.Role):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        count = 0
        for ch in interaction.guild.text_channels:
            ow = ch.overwrites_for(source_role)
            if not ow.is_empty():
                await ch.set_permissions(target_role, overwrite=ow, reason=f"Cloned from {source_role.name} by {interaction.user}")
                count += 1
        await interaction.followup.send(f"✅ Cloned **{source_role.name}**'s overwrites to **{target_role.name}** across {count} channel(s).", ephemeral=True)

    @app_commands.command(name="audit", description="Show what permissions a member or role has in this server")
    @app_commands.describe(user="Member to audit (leave blank for yourself)")
    async def perm_audit(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        member = user or interaction.guild.get_member(interaction.user.id)
        if not member:
            return await interaction.response.send_message("❌ Member not found.", ephemeral=True)
        perms = member.guild_permissions
        embed = discord.Embed(title=f"🔐 Permission Audit — {member}", color=0x5b8cff)
        key_perms = [
            ("administrator",       "👑 Administrator"),
            ("manage_guild",        "⚙️ Manage Server"),
            ("manage_roles",        "🎭 Manage Roles"),
            ("manage_channels",     "📂 Manage Channels"),
            ("manage_messages",     "🗑️ Manage Messages"),
            ("kick_members",        "👢 Kick Members"),
            ("ban_members",         "🔨 Ban Members"),
            ("moderate_members",    "⏳ Timeout Members"),
            ("manage_webhooks",     "🔗 Manage Webhooks"),
            ("view_audit_log",      "📋 View Audit Log"),
            ("mention_everyone",    "📣 Mention Everyone"),
        ]
        granted = [label for perm, label in key_perms if getattr(perms, perm, False)]
        denied  = [label for perm, label in key_perms if not getattr(perms, perm, False)]
        embed.add_field(name="✅ Has",        value="\n".join(granted) or "*None*",   inline=True)
        embed.add_field(name="❌ Doesn't Have", value="\n".join(denied[:8]) or "*All granted*", inline=True)
        roles_str = ", ".join(r.mention for r in member.roles[1:] if r.name != "@everyone")
        embed.add_field(name="🎭 Roles", value=roles_str or "*No roles*", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /godpanel — SP4RK God Panel
# ---------------------------------------------------------------------------
async def godpanel_command(interaction: discord.Interaction):
    if not _is_admin(interaction):
        return await interaction.response.send_message("❌ **God Panel is admin-only.** — You need Administrator permission.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    g = interaction.guild
    gid = str(g.id)

    # Gather data
    bots   = sum(1 for m in g.members if m.bot)
    humans = g.member_count - bots
    online = sum(1 for m in g.members if m.status != discord.Status.offline)
    banned = 0
    try:
        bans = [_ async for _ in g.bans(limit=200)]
        banned = len(bans)
    except Exception:
        pass

    mood_label, mood_emoji = "Normal Activity", "😐"
    try:
        import db_spark as _dbs
        snap = _dbs.mood_get_current(gid)
        mood_label = snap.get("mood_label", "Normal Activity")
        mood_emoji = snap.get("mood_emoji", "😐")
        mood_score = snap.get("mood_score", 0)
    except Exception:
        mood_score = 0

    d = _load()
    cases_g = _gd(d, "cases", gid)
    open_cases = sum(1 for c in cases_g.get("cases", {}).values() if c.get("status") == "open")
    rules_g = _gd(d, "rules", gid)
    active_rules = sum(1 for r in rules_g.values() if isinstance(r, dict) and r.get("enabled"))
    tags_g = _gd(d, "tags", gid)
    am_cfg = _gd(d, "automod_config", gid)

    embed = discord.Embed(
        title=f"⚡ SP4RK God Panel — {g.name}",
        description="*Full server oversight at a glance.*",
        color=0xf1c40f,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=g.icon.url if g.icon else None)

    embed.add_field(name="👥 Members",       value=f"{humans} humans / {bots} bots\n🟢 {online} online", inline=True)
    embed.add_field(name="🛡️ Bans on Record", value=str(banned),                                          inline=True)
    embed.add_field(name="📁 Open Cases",     value=str(open_cases),                                       inline=True)
    embed.add_field(name=f"{mood_emoji} Server Mood", value=f"**{mood_label}** ({'+' if mood_score>=0 else ''}{mood_score})", inline=True)
    embed.add_field(name="⚙️ Active Rules",  value=str(active_rules),                                     inline=True)
    embed.add_field(name="🧾 Tags",          value=str(len(tags_g)),                                       inline=True)
    embed.add_field(name="🤖 AutoMod",       value="✅ ON" if am_cfg.get("enabled") else "⛔ OFF",          inline=True)
    embed.add_field(name="💎 Boosts",        value=f"Tier {g.premium_tier} ({g.premium_subscription_count})", inline=True)
    embed.add_field(name="🎭 Roles",         value=str(len(g.roles) - 1),                                  inline=True)

    rr_panels = _gd(d, "rr_panels", gid)
    embed.add_field(name="🎨 RR Panels", value=str(len(rr_panels)), inline=True)
    try:
        import logging_system as _nvd_logs
        logging_enabled = bool(_nvd_logs._cfg(gid).get("logging_enabled", False))
    except Exception:
        logging_enabled = False
    embed.add_field(name="📜 Logging", value="✅ ON" if logging_enabled else "⛔ OFF", inline=True)
    embed.add_field(name="📊 Analytics", value="[Byte Labs Dashboard]", inline=True)

    embed.set_footer(text=f"SP4RK God Panel  •  Requested by {interaction.user}  •  {g.member_count} total members")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /sparkelite — Premium flex commands
# ---------------------------------------------------------------------------
class SparkEliteGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="sparkelite", description="💎 SP4RK Elite / Premium Mode")

    def _pm(self, gid: str):
        d = _load()
        pm = _gd(d, "premium_mode", gid)
        pm.setdefault("enabled", False)
        pm.setdefault("elite_mode", False)
        pm.setdefault("activated_at", None)
        return pm, d

    @app_commands.command(name="enable", description="Enable SP4RK Premium mode for this server")
    async def elite_enable(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        pm, d = self._pm(gid)
        pm["enabled"] = True
        pm["activated_at"] = _ts()
        d["premium_mode"][gid] = pm
        _save(d)
        embed = discord.Embed(
            title="💎 SP4RK Premium — ACTIVATED",
            description="This server has been elevated to **Premium tier**.\n\nAll premium features are now enabled: advanced logs, smart automod AI, full analytics, automation rules engine, and more.",
            color=0xf1c40f,
        )
        embed.set_footer(text="SP4RK Premium  •  You're in the elite tier now 😏")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="elitemode", description="Activate SP4RK Elite Mode — maximum power")
    async def elite_mode(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        gid = str(interaction.guild_id)
        pm, d = self._pm(gid)
        pm["elite_mode"] = not pm.get("elite_mode", False)
        d["premium_mode"][gid] = pm
        _save(d)
        if pm["elite_mode"]:
            embed = discord.Embed(
                title="⚡ ELITE MODE — ONLINE",
                description="SP4RK is now operating at **maximum capacity**.\n\n> *Context-based detection active. Severity scoring live. Auto-escalation armed.*\n\nHandle with care.",
                color=0xe74c3c,
            )
        else:
            embed = discord.Embed(title="💎 Elite Mode — Standby", description="Elite mode deactivated. Normal Premium tier active.", color=0x5b8cff)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="status", description="Show SP4RK Elite/Premium status for this server")
    async def elite_status(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        pm, _ = self._pm(gid)
        embed = discord.Embed(title="💎 SP4RK Elite Status", color=0xf1c40f if pm["enabled"] else 0x5b8cff)
        embed.add_field(name="Premium",    value="✅ Active" if pm["enabled"]     else "⛔ Inactive", inline=True)
        embed.add_field(name="Elite Mode", value="⚡ ON"    if pm["elite_mode"]   else "💤 OFF",      inline=True)
        if pm.get("activated_at"):
            embed.add_field(name="Activated", value=pm["activated_at"][:10], inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# setup — register everything with the bot
# ---------------------------------------------------------------------------
def setup(bot: commands.Bot, dlog=None) -> None:
    groups = [
        RRGroup(),
        AutoModGroup(),
        TagGroup(),
        CaseGroup(),
        StatsGroup(),
        RuleGroup(),
        PermGroup(),
        SparkEliteGroup(),
    ]

    for group in groups:
        try:
            bot.tree.add_command(group)
        except Exception as e:
            if "already" in str(e).lower():
                print(f"[premium] /{group.name} already registered — skipping")
            else:
                print(f"[premium] /{group.name} error: {e}")

    # Standalone /softban slash
    try:
        @bot.tree.command(name="softban", description="Ban a member then immediately unban them to clear their messages")
        @app_commands.describe(user="Member to softban", reason="Reason", delete_days="Days of messages to delete (1-7)")
        async def _softban(interaction: discord.Interaction, user: discord.Member,
                           reason: str = "No reason provided", delete_days: int = 1):
            await softban_command(interaction, user, reason, delete_days)
    except Exception as e:
        if "already" not in str(e).lower():
            print(f"[premium] /softban error: {e}")

    # Standalone /godpanel
    try:
        @bot.tree.command(name="godpanel", description="⚡ SP4RK God Panel — full server oversight")
        async def _godpanel(interaction: discord.Interaction):
            await godpanel_command(interaction)
    except Exception as e:
        if "already" not in str(e).lower():
            print(f"[premium] /godpanel error: {e}")

    registered = [g.name for g in groups] + ["softban", "godpanel"]
    print(f"[premium] Loaded: /{', /'.join(registered)}")
