"""
SP4RK extras — the Dyno / Carl-bot "signature" feature pack.

This module bundles four high-impact features users expect from a modern
moderation/utility bot:

    1. TAGS        — user-created text snippets (`/tag <name>`)
    2. SOFTBAN     — ban + immediately unban to wipe a member's recent
                     messages without permanent removal
    3. AFK         — `/afk [reason]`; bot replies when you're mentioned
                     and clears your AFK status on your next message
    4. HIGHLIGHTS  — `/highlight add|remove|list`; DM alert when your
                     keyword appears in any visible channel

Everything in here is registered through a single `setup(bot, dlog)` call
from `main.py`. No code in `main.py` needs to be aware of the internals,
keeping that already-large file from growing further.

Storage:
    Three small JSON files written atomically (`.tmp` + `os.replace`) so
    a crash mid-write can never corrupt the data. Files live next to
    `warnings.json` in the bot's working directory.
"""

from __future__ import annotations

import json
import os
import re
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands


# ============================================================================
# 🗃️  ATOMIC JSON HELPERS
# ============================================================================

_EXTRAS_SCOPES = {
    "tags.json":       "tags",
    "afk.json":        "extras_afk",
    "highlights.json": "highlights",
}


def _atomic_write(path: str, data) -> None:
    scope = _EXTRAS_SCOPES.get(os.path.basename(path))
    if scope:
        try:
            import db_spark as _ds
            if _ds.is_available():
                _ds.set_blob(scope, data)
        except Exception:
            pass
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[extras] atomic write failed for {path}: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _load_json(path: str) -> Dict:
    scope = _EXTRAS_SCOPES.get(os.path.basename(path))
    if scope:
        try:
            import db_spark as _ds
            if _ds.is_available():
                val = _ds.get_blob(scope)
                if val is not None:
                    return val
        except Exception:
            pass
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[extras] load failed for {path}: {e}")
        return {}


# ============================================================================
# 1️⃣  TAGS — user-created text snippets
# ============================================================================
TAGS_FILE = "tags.json"
# Schema: { guild_id: { tag_name_lower: {content, author_id, created_at, uses, edited_at?} } }
_tags_db: Dict[str, Dict[str, dict]] = _load_json(TAGS_FILE)


def _save_tags() -> None:
    _atomic_write(TAGS_FILE, _tags_db)


def _guild_tags(guild_id: int) -> Dict[str, dict]:
    return _tags_db.setdefault(str(guild_id), {})


_VALID_TAG = re.compile(r"^[a-z0-9_\-]{1,32}$")


def _normalize_tag(name: str) -> str:
    return name.strip().lower()


# ============================================================================
# 2️⃣  AFK — auto-reply when you're mentioned while away
# ============================================================================
AFK_FILE = "afk.json"
# Schema: { guild_id: { user_id: {reason, since_iso} } }
_afk_db: Dict[str, Dict[str, dict]] = _load_json(AFK_FILE)


def _save_afk() -> None:
    _atomic_write(AFK_FILE, _afk_db)


def _guild_afk(guild_id: int) -> Dict[str, dict]:
    return _afk_db.setdefault(str(guild_id), {})


# ============================================================================
# 3️⃣  HIGHLIGHTS — DM alerts on keywords
# ============================================================================
HIGHLIGHTS_FILE = "highlights.json"
# Schema: { guild_id: { user_id: [keyword_lower, ...] } }
_hl_db: Dict[str, Dict[str, List[str]]] = _load_json(HIGHLIGHTS_FILE)
# Per-(guild,user) cooldown so we don't spam DMs.
_hl_cooldown: Dict[str, datetime] = {}
_HL_COOLDOWN_SECONDS = 60
_HL_MAX_PER_USER = 10


def _save_hl() -> None:
    _atomic_write(HIGHLIGHTS_FILE, _hl_db)


def _guild_hl(guild_id: int) -> Dict[str, List[str]]:
    return _hl_db.setdefault(str(guild_id), {})


# ============================================================================
# 🔧  PUBLIC ENTRY — wire everything into the bot
# ============================================================================

def setup(bot: commands.Bot, dlog=None) -> None:
    """Register all extras commands and event listeners on `bot`.

    `dlog(event_type, description)` is the dashboard-log callback from
    main.py — purely optional; we no-op if it raises.
    """

    def _log(event: str, msg: str) -> None:
        if dlog:
            try:
                dlog(event, msg)
            except Exception:
                pass

    # ----------------------------------------------------------------
    # TAGS — slash + prefix
    # ----------------------------------------------------------------
    tag_group = app_commands.Group(
        name="tag",
        description="User-created text snippets (like Dyno/Carl-bot tags)",
    )

    @tag_group.command(name="show", description="Show a tag's content")
    @app_commands.describe(name="The tag name")
    async def tag_show(interaction: discord.Interaction, name: str):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ **Server Only** — Tags can only be used inside a server.", ephemeral=True
            )
        key = _normalize_tag(name)
        tag = _guild_tags(interaction.guild.id).get(key)
        if not tag:
            return await interaction.response.send_message(
                f"❌ **Tag Not Found** — No tag named **{key}** exists.\n> Use `/tag list` to see all available tags.",
                ephemeral=True,
            )
        tag["uses"] = int(tag.get("uses", 0)) + 1
        _save_tags()
        await interaction.response.send_message(tag["content"][:2000])

    @tag_group.command(name="create", description="Create a new tag")
    @app_commands.describe(name="Short name (a-z, 0-9, _ or -)", content="What the tag should say")
    async def tag_create(interaction: discord.Interaction, name: str, content: str):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ **Server Only** — Tags can only be used inside a server.", ephemeral=True
            )
        key = _normalize_tag(name)
        if not _VALID_TAG.match(key):
            return await interaction.response.send_message(
                "❌ **Invalid Name** — Tag names must be 1–32 characters: lowercase letters, digits, `_` or `-`.",
                ephemeral=True,
            )
        gtags = _guild_tags(interaction.guild.id)
        if key in gtags:
            return await interaction.response.send_message(
                f"❌ **Already Exists** — A tag named **{key}** already exists.\n> Use `/tag edit` to update it.",
                ephemeral=True,
            )
        gtags[key] = {
            "content": content[:2000],
            "author_id": str(interaction.user.id),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "uses": 0,
        }
        _save_tags()
        _log("🏷️ TAG", f"{interaction.user} created tag '{key}'")
        await interaction.response.send_message(
            f"✅ **Tag Created** — **{key}** is now live.\n> Use `/tag show {key}` or `!tag {key}` to display it."
        )

    @tag_group.command(name="edit", description="Edit a tag you created (mods can edit any)")
    @app_commands.describe(name="Tag to edit", content="New content")
    async def tag_edit(interaction: discord.Interaction, name: str, content: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Server only.", ephemeral=True)
        key = _normalize_tag(name)
        gtags = _guild_tags(interaction.guild.id)
        tag = gtags.get(key)
        if not tag:
            return await interaction.response.send_message(
                f"❌ **Tag Not Found** — No tag named **{key}** exists.", ephemeral=True
            )
        is_mod = interaction.user.guild_permissions.manage_messages
        if str(interaction.user.id) != tag.get("author_id") and not is_mod:
            return await interaction.response.send_message(
                "❌ **Access Denied** — Only the tag's author or a moderator can edit it.", ephemeral=True
            )
        tag["content"] = content[:2000]
        tag["edited_at"] = datetime.now(timezone.utc).isoformat()
        _save_tags()
        _log("🏷️ TAG", f"{interaction.user} edited tag '{key}'")
        await interaction.response.send_message(f"✅ **Tag Updated** — **{key}** has been edited successfully.")

    @tag_group.command(name="delete", description="Delete a tag you created (mods can delete any)")
    @app_commands.describe(name="Tag to delete")
    async def tag_delete(interaction: discord.Interaction, name: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Server only.", ephemeral=True)
        key = _normalize_tag(name)
        gtags = _guild_tags(interaction.guild.id)
        tag = gtags.get(key)
        if not tag:
            return await interaction.response.send_message(
                f"❌ **Tag Not Found** — No tag named **{key}** exists.", ephemeral=True
            )
        is_mod = interaction.user.guild_permissions.manage_messages
        if str(interaction.user.id) != tag.get("author_id") and not is_mod:
            return await interaction.response.send_message(
                "❌ **Access Denied** — Only the tag's author or a moderator can delete it.", ephemeral=True
            )
        gtags.pop(key, None)
        _save_tags()
        _log("🏷️ TAG", f"{interaction.user} deleted tag '{key}'")
        await interaction.response.send_message(f"🗑️ **Tag Deleted** — **{key}** has been permanently removed.")

    @tag_group.command(name="list", description="List every tag in this server")
    async def tag_list(interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ **Server Only** — This command can only be used in a server.", ephemeral=True)
        gtags = _guild_tags(interaction.guild.id)
        if not gtags:
            return await interaction.response.send_message(
                "📭 No tags yet. Create one with `/tag create`.", ephemeral=True
            )
        # Sort by usage descending so the popular ones surface.
        rows = sorted(gtags.items(), key=lambda kv: -int(kv[1].get("uses", 0)))
        lines = [
            f"`{i+1}.` **{k}** — {v.get('uses', 0)} use(s)"
            for i, (k, v) in enumerate(rows[:30])
        ]
        embed = discord.Embed(
            title=f"🏷️ Server Tags — {interaction.guild.name}",
            description=(
                f"**{len(gtags)}** tag(s) available. Sorted by popularity.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(lines)
            ),
            color=0x4f8ef7,
            timestamp=datetime.now(timezone.utc),
        )
        if len(gtags) > 30:
            embed.set_footer(text=f"Showing top 30 of {len(gtags)} tags by usage  •  SP4RK Tags")
        else:
            embed.set_footer(text=f"Use /tag show <name> to display a tag  •  SP4RK Tags")
        await interaction.response.send_message(embed=embed)

    @tag_group.command(name="info", description="Show metadata about a tag")
    @app_commands.describe(name="Tag to inspect")
    async def tag_info(interaction: discord.Interaction, name: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ **Server Only** — This command can only be used in a server.", ephemeral=True)
        key = _normalize_tag(name)
        tag = _guild_tags(interaction.guild.id).get(key)
        if not tag:
            return await interaction.response.send_message(
                f"❌ **Tag Not Found** — No tag named **{key}** exists.", ephemeral=True
            )
        try:
            author = await bot.fetch_user(int(tag["author_id"]))
            author_str = f"{author} (`{author.id}`)"
        except Exception:
            author_str = f"Unknown (`{tag.get('author_id')}`)"
        created = tag.get("created_at", "")
        edited = tag.get("edited_at", "")
        try:
            created_disp = f"<t:{int(datetime.fromisoformat(created).timestamp())}:R>"
        except Exception:
            created_disp = created or "—"
        embed = discord.Embed(
            title=f"🏷️ Tag Info — {key}",
            description=(
                f"Detailed metadata for the **{key}** tag.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=0x4f8ef7,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="✍️ Author",   value=author_str,               inline=False)
        embed.add_field(name="📅 Created",  value=created_disp,             inline=True)
        embed.add_field(name="📊 Uses",     value=f"**{tag.get('uses', 0)}**", inline=True)
        if edited:
            try:
                edited_disp = f"<t:{int(datetime.fromisoformat(edited).timestamp())}:R>"
            except Exception:
                edited_disp = edited
            embed.add_field(name="📝 Last Edited", value=edited_disp, inline=True)
        content_preview = tag["content"][:500] + ("…" if len(tag["content"]) > 500 else "")
        embed.add_field(
            name="📜 Content Preview",
            value=f"```{content_preview}```",
            inline=False,
        )
        embed.set_footer(text=f"Use /tag show {key} to display this tag  •  SP4RK Tags")
        await interaction.response.send_message(embed=embed)

    bot.tree.add_command(tag_group)

    # Prefix shortcut: `!tag <name>` — handy mobile/legacy fallback
    @bot.command(name="tag")
    async def tag_prefix(ctx: commands.Context, name: str = None):
        if not ctx.guild:
            return
        if not name:
            return await ctx.send("ℹ️ **Usage:** `!tag <name>` — use `/tag list` to see all available tags.")
        key = _normalize_tag(name)
        tag = _guild_tags(ctx.guild.id).get(key)
        if not tag:
            return await ctx.send(f"❌ **Tag Not Found** — No tag named **{key}** exists.")
        tag["uses"] = int(tag.get("uses", 0)) + 1
        _save_tags()
        await ctx.send(tag["content"][:2000])

    # ----------------------------------------------------------------
    # SOFTBAN — wipe recent messages without keeping them banned
    # ----------------------------------------------------------------
    @bot.tree.command(
        name="softban",
        description="Ban then immediately unban a member to wipe their recent messages",
    )
    @app_commands.describe(
        member="Who to softban",
        reason="Reason shown in audit log",
        delete_days="How many days of messages to wipe (1-7)",
    )
    @app_commands.checks.has_permissions(ban_members=True)
    async def softban(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "Cleanup",
        delete_days: app_commands.Range[int, 1, 7] = 1,
    ):
        if not interaction.guild:
            return await interaction.response.send_message("❌ **Server Only** — This command can only be used in a server.", ephemeral=True)
        bot_top = interaction.guild.me.top_role
        if member.top_role >= bot_top:
            return await interaction.response.send_message(
                f"❌ **Action Blocked** — Cannot softban {member.mention}.\n> Their role is equal to or above mine.",
                ephemeral=True,
            )
        if (
            interaction.user.id != interaction.guild.owner_id
            and member.top_role >= interaction.user.top_role
        ):
            return await interaction.response.send_message(
                f"❌ **Action Blocked** — Cannot softban {member.mention}.\n> Their role is equal to or higher than yours.",
                ephemeral=True,
            )
        await interaction.response.defer()
        try:
            await member.ban(
                reason=f"[SOFTBAN] {reason} (by {interaction.user})",
                delete_message_seconds=int(delete_days) * 86400,
            )
            await interaction.guild.unban(
                member, reason=f"Softban auto-unban (by {interaction.user})"
            )
            await interaction.followup.send(
                f"🧹 **Member Softbanned** — {member.mention}\n"
                f"> 🗑️ **Messages Wiped:** Last {delete_days} day(s)\n"
                f"> 🛡️ **By:** {interaction.user.mention}\n"
                f"> 📝 **Reason:** {reason}\n"
                f"> ℹ️ They may rejoin immediately with an invite."
            )
            _log("🧹 SOFTBAN", f"{member} softbanned by {interaction.user} — {reason}")
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ **Permission Denied** — I can't ban {member.mention}.\n> Check role hierarchy and my permissions.", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ **Softban Failed** — `{e}`", ephemeral=True)

    # ----------------------------------------------------------------
    # AFK — slash + on_message handlers
    # ----------------------------------------------------------------
    @bot.tree.command(name="afk", description="Mark yourself away — bot will reply when you're mentioned")
    @app_commands.describe(reason="Optional message shown when others ping you")
    async def afk_cmd(interaction: discord.Interaction, reason: str = "AFK"):
        if not interaction.guild:
            return await interaction.response.send_message("❌ **Server Only** — This command can only be used in a server.", ephemeral=True)
        _guild_afk(interaction.guild.id)[str(interaction.user.id)] = {
            "reason": reason[:200],
            "since": datetime.now(timezone.utc).isoformat(),
        }
        _save_afk()
        _log("💤 AFK", f"{interaction.user} → AFK: {reason}")
        await interaction.response.send_message(
            f"💤 **AFK Set** — {interaction.user.mention} is now away.\n> 📋 **Status:** *{reason}*"
        )

    # ----------------------------------------------------------------
    # HIGHLIGHTS — slash group
    # ----------------------------------------------------------------
    hl_group = app_commands.Group(
        name="highlight",
        description="Get DM'd when a keyword you choose is mentioned (Carl-bot style)",
    )

    @hl_group.command(name="add", description="Add a keyword to your highlights for this server")
    @app_commands.describe(keyword="The word or phrase that triggers a DM (case-insensitive)")
    async def hl_add(interaction: discord.Interaction, keyword: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ **Server Only** — This command can only be used in a server.", ephemeral=True)
        kw = keyword.strip().lower()
        if not (2 <= len(kw) <= 50):
            return await interaction.response.send_message(
                "❌ **Invalid Keyword** — Highlights must be between 2 and 50 characters.", ephemeral=True
            )
        gh = _guild_hl(interaction.guild.id)
        user_list = gh.setdefault(str(interaction.user.id), [])
        if kw in user_list:
            return await interaction.response.send_message(
                f"ℹ️ **Already Added** — **{kw}** is already in your highlights for this server.", ephemeral=True
            )
        if len(user_list) >= _HL_MAX_PER_USER:
            return await interaction.response.send_message(
                f"❌ **Limit Reached** — You've used all **{_HL_MAX_PER_USER}** highlight slots for this server.\n"
                "> Remove one first with `/highlight remove`.",
                ephemeral=True,
            )
        user_list.append(kw)
        _save_hl()
        await interaction.response.send_message(
            f"🔔 **Highlight Added** — **{kw}** is now tracked.\n> I'll DM you whenever it's mentioned in this server.",
            ephemeral=True,
        )

    @hl_group.command(name="remove", description="Remove a highlight keyword")
    @app_commands.describe(keyword="The keyword to remove")
    async def hl_remove(interaction: discord.Interaction, keyword: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ **Server Only** — This command can only be used in a server.", ephemeral=True)
        kw = keyword.strip().lower()
        gh = _guild_hl(interaction.guild.id)
        user_list = gh.get(str(interaction.user.id), [])
        if kw not in user_list:
            return await interaction.response.send_message(
                f"❌ **Not Found** — **{kw}** is not in your highlights for this server.", ephemeral=True
            )
        user_list.remove(kw)
        _save_hl()
        await interaction.response.send_message(
            f"🔕 **Highlight Removed** — **{kw}** has been removed. You will no longer be notified.", ephemeral=True
        )

    @hl_group.command(name="list", description="See your highlight keywords for this server")
    async def hl_list(interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ **Server Only** — This command can only be used in a server.", ephemeral=True)
        user_list = _guild_hl(interaction.guild.id).get(str(interaction.user.id), [])
        if not user_list:
            return await interaction.response.send_message(
                "📭 **No Highlights** — You haven't added any highlights for this server yet.\n> Use `/highlight add` to get started.",
                ephemeral=True,
            )
        embed = discord.Embed(
            title=f"🔔 Your Highlights — {interaction.guild.name}",
            description=(
                f"You'll receive a DM whenever any of these keywords are mentioned.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(f"`{i+1}.` **{kw}**" for i, kw in enumerate(user_list))
            ),
            color=0x4f8ef7,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"{len(user_list)}/{_HL_MAX_PER_USER} highlight slots used  •  SP4RK Highlights")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @hl_group.command(name="clear", description="Remove all your highlights in this server")
    async def hl_clear(interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ **Server Only** — This command can only be used in a server.", ephemeral=True)
        gh = _guild_hl(interaction.guild.id)
        had = gh.pop(str(interaction.user.id), None)
        _save_hl()
        if had:
            await interaction.response.send_message(
                f"🧹 **Highlights Cleared** — All **{len(had)}** highlight(s) for this server have been removed.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "📭 **Nothing to Clear** — You had no highlights set for this server.", ephemeral=True
            )

    bot.tree.add_command(hl_group)

    # ----------------------------------------------------------------
    # on_message LISTENER — drives both AFK and Highlights
    # ----------------------------------------------------------------
    async def _process_extras_message(message: discord.Message) -> None:
        # Guards: ignore bots, DMs, and messages with no usable guild context.
        if message.author.bot or not message.guild:
            return

        # ---- AFK: clear the author's AFK on activity ----
        try:
            gafk = _guild_afk(message.guild.id)
            uid = str(message.author.id)
            if uid in gafk:
                gafk.pop(uid, None)
                _save_afk()
                try:
                    await message.channel.send(
                        f"👋 **Welcome Back** — {message.author.mention}, your AFK status has been cleared.",
                        delete_after=8,
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"[extras] AFK clear error: {e}")

        # ---- AFK: notify when an AFK user gets pinged ----
        try:
            if message.mentions:
                gafk = _guild_afk(message.guild.id)
                replies = []
                for u in message.mentions:
                    info = gafk.get(str(u.id))
                    if not info:
                        continue
                    try:
                        ts = int(datetime.fromisoformat(info["since"]).timestamp())
                        rel = f" (since <t:{ts}:R>)"
                    except Exception:
                        rel = ""
                    replies.append(f"💤 **{u.display_name}** is AFK{rel} — *{info['reason']}*")
                if replies:
                    try:
                        await message.channel.send("\n".join(replies[:3]))
                    except Exception:
                        pass
        except Exception as e:
            print(f"[extras] AFK ping error: {e}")

        # ---- HIGHLIGHTS: DM matching users ----
        try:
            gh = _guild_hl(message.guild.id)
            if not gh:
                return
            content_lower = (message.content or "").lower()
            if not content_lower:
                return
            for uid, kws in gh.items():
                if uid == str(message.author.id):
                    continue            # don't DM yourself
                if not kws:
                    continue
                # Quick substring filter first, then word-boundary check.
                if not any(kw in content_lower for kw in kws):
                    continue
                # Resolve member; skip if they can't see this channel.
                try:
                    member = message.guild.get_member(int(uid)) or \
                             await message.guild.fetch_member(int(uid))
                except Exception:
                    continue
                perms = message.channel.permissions_for(member)
                if not perms.read_messages:
                    continue
                # Cooldown per (guild,user) so we don't DM-spam.
                cd_key = f"{message.guild.id}:{uid}"
                now = datetime.now(timezone.utc)
                last = _hl_cooldown.get(cd_key)
                if last and (now - last).total_seconds() < _HL_COOLDOWN_SECONDS:
                    continue
                # Find which keyword matched (word boundary preferred).
                matched = None
                for kw in kws:
                    if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", content_lower):
                        matched = kw
                        break
                if not matched:
                    continue
                _hl_cooldown[cd_key] = now
                try:
                    chan_ref = message.channel.mention if hasattr(message.channel, "mention") else "#unknown"
                    embed = discord.Embed(
                        title=f"🔔 Highlight Triggered — \"{matched}\"",
                        description=(
                            f"Your keyword **\"{matched}\"** was mentioned in **{message.guild.name}**.\n\n"
                            f"📡 **Channel:** {chan_ref}\n"
                            f"👤 **Author:** {message.author.display_name} (`{message.author.id}`)\n\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"{message.content[:900]}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━"
                        ),
                        color=0xf5b400,
                        timestamp=message.created_at,
                    )
                    if message.author.display_avatar:
                        embed.set_thumbnail(url=message.author.display_avatar.url)
                    embed.add_field(
                        name="🔗 Jump to Message",
                        value=f"[Click here to view context]({message.jump_url})",
                        inline=False,
                    )
                    embed.set_footer(text=f"SP4RK Highlights  •  Matched: {matched}")
                    await member.send(embed=embed)
                except discord.Forbidden:
                    pass    # user has DMs closed — don't keep trying
                except Exception as e:
                    print(f"[extras] highlight DM error: {e}")
        except Exception as e:
            print(f"[extras] highlight scan error: {e}")

    @bot.listen("on_message")
    async def _extras_on_message(message: discord.Message):
        # Schedule as a background task so command processing isn't blocked.
        asyncio.create_task(_process_extras_message(message))

    print("[extras] Loaded: tags, softban, afk, highlights")
