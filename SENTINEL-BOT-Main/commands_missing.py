"""
commands_missing.py — All missing slash & prefix commands for SP4RK.
Registered via setup(bot, dlog) called from main.py.
Covers every command in the v2.5 command list that wasn't already present.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands

# ── edit-snipe cache (channel_id → {author, before, after, ts}) ──────────────
_edit_snipe_cache: dict = {}


# ── local guard helper (mirrors _slash_guard_guild in main.py) ───────────────
async def _guard(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ **Server Only** — Use this command inside a server.", ephemeral=True
        )
        return False
    return True


# ── lazy config helpers ───────────────────────────────────────────────────────
def _gcfg(guild_id):
    try:
        import main as _m
        return _m.get_config_for_guild(str(guild_id))
    except Exception:
        return {}


def _save_gcfg(guild_id, cfg):
    try:
        from dashboard import save_guild_config
        save_guild_config(str(guild_id), cfg)
    except Exception:
        pass


def _toggle(cfg, key, value=None):
    """Toggle ON/OFF or set a specific value. Returns new state string."""
    if value is not None:
        cfg[key] = value
        return value
    current = cfg.get(key, "OFF")
    new = "OFF" if current == "ON" else "ON"
    cfg[key] = new
    return new


# ─────────────────────────────────────────────────────────────────────────────
def setup(bot: commands.Bot, dlog=None):
    _dlog = dlog or (lambda *a, **kw: None)

    # ══════════════════════════════════════════════════════════════════════════
    # EDIT-SNIPE LISTENER
    # ══════════════════════════════════════════════════════════════════════════
    @bot.listen("on_message_edit")
    async def _track_edit(before: discord.Message, after: discord.Message):
        if before.author.bot:
            return
        if before.content == after.content:
            return
        _edit_snipe_cache[before.channel.id] = {
            "author": str(before.author),
            "avatar": before.author.display_avatar.url,
            "before": before.content[:1000],
            "after": after.content[:1000],
            "ts": datetime.utcnow(),
        }

    # ══════════════════════════════════════════════════════════════════════════
    # ── PREFIX COMMANDS ───────────────────────────────────────────────────────
    # ══════════════════════════════════════════════════════════════════════════

    # ── !ban ──
    @bot.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def prefix_ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
        import main as _m
        block = _m._hierarchy_block(ctx, member, "ban")
        if block:
            return await ctx.send(block)
        if not ctx.guild.me.guild_permissions.ban_members:
            return await ctx.send("❌ I need the **Ban Members** permission.")
        allowed = await _m.security.mass_action_prevent(ctx, "ban", _m.config, log_callback=_dlog)
        if not allowed:
            return
        try:
            await member.ban(reason=reason)
            await ctx.send(
                f"⛔ **Member Banned** — {member.mention}\n"
                f"> 🛡️ **By:** {ctx.author.mention}\n> 📝 **Reason:** {reason}"
            )
            _m.supabase_save_mod_log(ctx.guild.id, "ban", str(member), str(ctx.author), reason,
                                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            _m._increment_report_action()
            _dlog("⛔ BAN", f"{member} banned by {ctx.author} — {reason}")
        except discord.Forbidden:
            await ctx.send(f"❌ Cannot ban {member.mention} — check hierarchy.")
        except Exception as e:
            await ctx.send(f"❌ Ban failed: `{e}`")

    # ── !kick ──
    @bot.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def prefix_kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
        import main as _m
        block = _m._hierarchy_block(ctx, member, "kick")
        if block:
            return await ctx.send(block)
        try:
            await member.kick(reason=reason)
            await ctx.send(
                f"🚪 **Member Kicked** — {member.mention}\n"
                f"> 🛡️ **By:** {ctx.author.mention}\n> 📝 **Reason:** {reason}"
            )
            _m.supabase_save_mod_log(ctx.guild.id, "kick", str(member), str(ctx.author), reason,
                                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            _m._increment_report_action()
            _dlog("🚪 KICK", f"{member} kicked by {ctx.author} — {reason}")
        except discord.Forbidden:
            await ctx.send(f"❌ Cannot kick {member.mention} — check hierarchy.")
        except Exception as e:
            await ctx.send(f"❌ Kick failed: `{e}`")

    # ── !timeout ──
    @bot.command(name="timeout")
    @commands.has_permissions(moderate_members=True)
    async def prefix_timeout(ctx, member: discord.Member, duration: str = "10m", *, reason: str = "No reason provided"):
        import main as _m
        block = _m._hierarchy_block(ctx, member, "timeout")
        if block:
            return await ctx.send(block)
        seconds = _m.parse_duration(duration)
        if not seconds:
            return await ctx.send("❌ Invalid duration — use formats like `10m`, `1h`, `1d`.")
        until = discord.utils.utcnow() + timedelta(seconds=seconds)
        try:
            await member.timeout(until, reason=reason)
            await ctx.send(
                f"⏳ **Member Timed Out** — {member.mention}\n"
                f"> 🕐 **Duration:** {_m.duration_label(seconds)}\n"
                f"> 🛡️ **By:** {ctx.author.mention}\n> 📝 **Reason:** {reason}"
            )
            _dlog("⏳ TIMEOUT", f"{member} timed out for {duration} by {ctx.author}")
        except discord.Forbidden:
            await ctx.send(f"❌ Cannot timeout {member.mention} — check hierarchy.")
        except Exception as e:
            await ctx.send(f"❌ Timeout failed: `{e}`")

    # ── !untimeout ──
    @bot.command(name="untimeout")
    @commands.has_permissions(moderate_members=True)
    async def prefix_untimeout(ctx, member: discord.Member):
        import main as _m
        try:
            await member.timeout(None, reason=f"Timeout removed by {ctx.author}")
            await ctx.send(f"✅ **Timeout Removed** — {member.mention}\n> 🛡️ **By:** {ctx.author.mention}")
            _dlog("✅ UNTIMEOUT", f"Timeout removed for {member} by {ctx.author}")
        except discord.Forbidden:
            await ctx.send(f"❌ Cannot remove timeout for {member.mention} — check hierarchy.")
        except Exception as e:
            await ctx.send(f"❌ Untimeout failed: `{e}`")

    # ── !warn ──
    @bot.command(name="warn")
    @commands.has_permissions(manage_messages=True)
    async def prefix_warn(ctx, member: discord.Member, *, reason: str = "Violation"):
        import main as _m
        block = _m._hierarchy_block(ctx, member, "warn")
        if block:
            return await ctx.send(block)
        await _m.add_warning(member, reason)
        entry = _m._get_member_entry(str(ctx.guild.id), str(member.id))
        await ctx.send(
            f"⚠️ **Warning Issued** — {member.mention}\n"
            f"> 📊 **Total Warnings:** {entry['count']}\n"
            f"> 📝 **Reason:** {reason}\n"
            f"> 🛡️ **By:** {ctx.author.mention}"
        )
        _dlog("⚠️ WARN", f"{member} warned by {ctx.author}: {reason}")

    # ── !warnings ──
    @bot.command(name="warnings")
    @commands.has_permissions(manage_messages=True)
    async def prefix_warnings(ctx, member: discord.Member):
        import main as _m
        entry = _m._get_member_entry(str(ctx.guild.id), str(member.id))
        count = entry["count"]
        history = entry["history"]
        limit = int(_gcfg(ctx.guild.id).get("warning_limit", 30))
        embed = discord.Embed(
            title=f"⚠️ Warnings — {member.display_name}",
            description=f"**{member}** · `{member.id}`\nWarnings: **{count}** / {limit}",
            color=0xe74c3c if count >= limit else (0xf39c12 if count > 0 else 0x2ecc71),
            timestamp=datetime.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        if history:
            lines = "\n".join(f"`{i+1}.` {h['reason']} — *{h['time']}*" for i, h in enumerate(history[-5:][::-1]))
            embed.add_field(name="📋 Recent", value=lines, inline=False)
        else:
            embed.add_field(name="📋 History", value="*No warnings on record.*", inline=False)
        embed.set_footer(text=f"SP4RK Moderation")
        await ctx.send(embed=embed)

    # ── !clear ──
    @bot.command(name="clear")
    @commands.has_permissions(manage_messages=True)
    async def prefix_clear(ctx, amount: int = 10):
        if amount < 1 or amount > 200:
            return await ctx.send("❌ Amount must be between 1 and 200.", delete_after=5)
        deleted = await ctx.channel.purge(limit=amount + 1)
        msg = await ctx.send(f"🧹 Deleted **{max(0, len(deleted)-1)}** message(s).", )
        await asyncio.sleep(4)
        try:
            await msg.delete()
        except Exception:
            pass
        _dlog("🧹 CLEAR", f"{ctx.author} cleared {len(deleted)-1} msgs in #{ctx.channel.name}")

    # ── !purge group ──
    @bot.group(name="purge", invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def prefix_purge(ctx, amount: int = 10):
        if amount < 1 or amount > 200:
            return await ctx.send("❌ Amount must be between 1 and 200.", delete_after=5)
        deleted = await ctx.channel.purge(limit=amount + 1)
        msg = await ctx.send(f"🧹 Purged **{max(0, len(deleted)-1)}** message(s).")
        await asyncio.sleep(4)
        try:
            await msg.delete()
        except Exception:
            pass
        _dlog("🧹 PURGE", f"{ctx.author} purged {len(deleted)-1} in #{ctx.channel.name}")

    @prefix_purge.command(name="bots")
    @commands.has_permissions(manage_messages=True)
    async def prefix_purge_bots(ctx, amount: int = 50):
        deleted = await ctx.channel.purge(limit=amount, check=lambda m: m.author.bot)
        msg = await ctx.send(f"🤖 Deleted **{len(deleted)}** bot message(s).")
        await asyncio.sleep(4)
        try:
            await msg.delete()
        except Exception:
            pass

    @prefix_purge.command(name="links")
    @commands.has_permissions(manage_messages=True)
    async def prefix_purge_links(ctx, amount: int = 50):
        _url_re = re.compile(r"https?://\S+")
        deleted = await ctx.channel.purge(limit=amount, check=lambda m: bool(_url_re.search(m.content)))
        msg = await ctx.send(f"🔗 Deleted **{len(deleted)}** message(s) containing links.")
        await asyncio.sleep(4)
        try:
            await msg.delete()
        except Exception:
            pass

    @prefix_purge.command(name="images")
    @commands.has_permissions(manage_messages=True)
    async def prefix_purge_images(ctx, amount: int = 50):
        deleted = await ctx.channel.purge(limit=amount, check=lambda m: bool(m.attachments))
        msg = await ctx.send(f"🖼️ Deleted **{len(deleted)}** image message(s).")
        await asyncio.sleep(4)
        try:
            await msg.delete()
        except Exception:
            pass

    @prefix_purge.command(name="embeds")
    @commands.has_permissions(manage_messages=True)
    async def prefix_purge_embeds(ctx, amount: int = 50):
        deleted = await ctx.channel.purge(limit=amount, check=lambda m: bool(m.embeds))
        msg = await ctx.send(f"📋 Deleted **{len(deleted)}** embed message(s).")
        await asyncio.sleep(4)
        try:
            await msg.delete()
        except Exception:
            pass

    @prefix_purge.command(name="user")
    @commands.has_permissions(manage_messages=True)
    async def prefix_purge_user_sub(ctx, member: discord.Member, amount: int = 50):
        deleted = await ctx.channel.purge(limit=amount, check=lambda m: m.author.id == member.id)
        msg = await ctx.send(f"🧹 Deleted **{len(deleted)}** message(s) from {member.mention}.")
        await asyncio.sleep(4)
        try:
            await msg.delete()
        except Exception:
            pass

    # ── !softban ──
    @bot.command(name="softban")
    @commands.has_permissions(ban_members=True)
    async def prefix_softban(ctx, member: discord.Member, delete_days: int = 1, *, reason: str = "No reason provided"):
        import main as _m
        block = _m._hierarchy_block(ctx, member, "softban")
        if block:
            return await ctx.send(block)
        try:
            await member.ban(reason=f"[SOFTBAN] {reason}", delete_message_seconds=delete_days * 86400)
            await ctx.guild.unban(member, reason="Softban auto-unban")
            await ctx.send(
                f"🧹 **Member Softbanned** — {member.mention}\n"
                f"> 🗑️ Last {delete_days} day(s) of messages wiped\n"
                f"> 🛡️ **By:** {ctx.author.mention}\n> 📝 **Reason:** {reason}\n"
                f"> ℹ️ They may rejoin with an invite."
            )
            _dlog("🧹 SOFTBAN", f"{member} softbanned by {ctx.author} — {reason}")
        except discord.Forbidden:
            await ctx.send(f"❌ Cannot softban {member.mention} — check hierarchy.")
        except Exception as e:
            await ctx.send(f"❌ Softban failed: `{e}`")

    # ── !unban ──
    @bot.command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def prefix_unban(ctx, user_id: int, *, reason: str = "No reason provided"):
        try:
            user = discord.Object(id=user_id)
            await ctx.guild.unban(user, reason=reason)
            await ctx.send(f"✅ **User Unbanned** — `{user_id}`\n> 🛡️ **By:** {ctx.author.mention}\n> 📝 **Reason:** {reason}")
            _dlog("✅ UNBAN", f"User {user_id} unbanned by {ctx.author}")
        except discord.NotFound:
            await ctx.send(f"❌ User `{user_id}` is not banned in this server.")
        except discord.Forbidden:
            await ctx.send("❌ I need the **Ban Members** permission.")
        except Exception as e:
            await ctx.send(f"❌ Unban failed: `{e}`")

    # ── !nickname (alias for !nick) ──
    @bot.command(name="nickname")
    @commands.has_permissions(manage_nicknames=True)
    async def prefix_nickname(ctx, member: discord.Member, *, name: str = None):
        import main as _m
        block = _m._hierarchy_block(ctx, member, "rename")
        if block:
            return await ctx.send(block)
        try:
            await member.edit(nick=name, reason=f"Renamed by {ctx.author}")
            if name:
                await ctx.send(f"✅ **Nickname Updated** — {member.mention}\n> 🏷️ **New Name:** {name}\n> 🛡️ **By:** {ctx.author.mention}")
            else:
                await ctx.send(f"✅ **Nickname Reset** — {member.mention}\n> 🛡️ **By:** {ctx.author.mention}")
            _dlog("📝 NICK", f"{ctx.author} renamed {member} → {name or '(reset)'}")
        except discord.Forbidden:
            await ctx.send(f"❌ Cannot rename {member.mention} — check hierarchy.")
        except Exception as e:
            await ctx.send(f"❌ Nickname failed: `{e}`")

    # ── !modhistory (alias for !modlogs) ──
    @bot.command(name="modhistory")
    @commands.has_permissions(manage_messages=True)
    async def prefix_modhistory(ctx, member: discord.Member):
        modlogs_cmd = bot.get_command("modlogs")
        if modlogs_cmd:
            await ctx.invoke(modlogs_cmd, member=member)

    # ── !editsnipe ──
    @bot.command(name="editsnipe")
    @commands.has_permissions(manage_messages=True)
    async def prefix_editsnipe(ctx):
        data = _edit_snipe_cache.get(ctx.channel.id)
        if not data:
            return await ctx.send("ℹ️ No recently edited messages found in this channel.")
        embed = discord.Embed(title="✏️ Edit Sniped", color=0xf39c12, timestamp=data["ts"])
        embed.set_author(name=f"{data['author']} edited:")
        embed.add_field(name="Before", value=data["before"][:1024] or "*empty*", inline=False)
        embed.add_field(name="After",  value=data["after"][:1024]  or "*empty*", inline=False)
        embed.set_footer(text=f"In #{ctx.channel.name}  •  SP4RK")
        await ctx.send(embed=embed)

    # ── !case ──
    @bot.command(name="case")
    @commands.has_permissions(manage_messages=True)
    async def prefix_case(ctx, case_id: str = None):
        if not case_id:
            return await ctx.send("ℹ️ Usage: `!case <case_id>`")
        try:
            from dashboard import supabase
            if supabase:
                res = supabase.table("sentinel_mod_logs").select("*").eq("id", case_id).execute()
                rows = res.data or []
            else:
                rows = []
        except Exception:
            rows = []
        if not rows:
            return await ctx.send(f"❌ No case found with ID `{case_id}`.")
        row = rows[0]
        embed = discord.Embed(
            title=f"📋 Case #{case_id}",
            color=0x4f8ef7, timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Action",    value=row.get("action", "—"),      inline=True)
        embed.add_field(name="Target",    value=row.get("member", "—"),      inline=True)
        embed.add_field(name="Moderator", value=row.get("moderator", "—"),   inline=True)
        embed.add_field(name="Reason",    value=row.get("reason", "—"),      inline=False)
        embed.add_field(name="Date",      value=row.get("timestamp", "—"),   inline=False)
        embed.set_footer(text="SP4RK Case Log")
        await ctx.send(embed=embed)

    # ── !audit ──
    @bot.command(name="audit")
    @commands.has_permissions(view_audit_log=True)
    async def prefix_audit(ctx, limit: int = 10):
        if limit < 1 or limit > 50:
            limit = 10
        try:
            embed = discord.Embed(
                title=f"📋 Audit Log — {ctx.guild.name}",
                color=0x4f8ef7, timestamp=datetime.utcnow(),
            )
            entries = []
            async for entry in ctx.guild.audit_logs(limit=limit):
                entries.append(entry)
            if not entries:
                embed.description = "_No audit log entries found._"
            else:
                lines = []
                for e in entries:
                    action = str(e.action).replace("AuditLogAction.", "")
                    lines.append(f"`{action}` — **{e.user}** · <t:{int(e.created_at.timestamp())}:R>")
                embed.description = "\n".join(lines[:20])
            embed.set_footer(text=f"Last {len(entries)} entries  •  SP4RK")
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send("❌ I need the **View Audit Log** permission.")

    # ── !antiraid ──
    @bot.command(name="antiraid")
    @commands.has_permissions(administrator=True)
    async def prefix_antiraid(ctx):
        cfg = _gcfg(ctx.guild.id)
        new = _toggle(cfg, "anti_raid")
        _save_gcfg(ctx.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await ctx.send(f"{emoji} **Anti-Raid** is now **{new}** for this server.")
        _dlog("🛡️ ANTIRAID", f"Anti-raid {new} by {ctx.author}")

    # ── !antilink ──
    @bot.command(name="antilink")
    @commands.has_permissions(manage_messages=True)
    async def prefix_antilink(ctx):
        cfg = _gcfg(ctx.guild.id)
        new = _toggle(cfg, "auto_block_links")
        _save_gcfg(ctx.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await ctx.send(f"{emoji} **Anti-Link** is now **{new}** — links will {'be blocked' if new=='ON' else 'be allowed'}.")
        _dlog("🔗 ANTILINK", f"Anti-link {new} by {ctx.author}")

    # ── !antispam ──
    @bot.command(name="antispam")
    @commands.has_permissions(manage_messages=True)
    async def prefix_antispam(ctx):
        cfg = _gcfg(ctx.guild.id)
        new = _toggle(cfg, "spam_filter")
        _save_gcfg(ctx.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await ctx.send(f"{emoji} **Anti-Spam** is now **{new}**.")
        _dlog("📵 ANTISPAM", f"Anti-spam {new} by {ctx.author}")

    # ── !antimention ──
    @bot.command(name="antimention")
    @commands.has_permissions(manage_messages=True)
    async def prefix_antimention(ctx):
        cfg = _gcfg(ctx.guild.id)
        new = _toggle(cfg, "auto_block_mentions")
        _save_gcfg(ctx.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await ctx.send(f"{emoji} **Anti-Mention** (mass-ping blocker) is now **{new}**.")
        _dlog("📢 ANTIMENTION", f"Anti-mention {new} by {ctx.author}")

    # ── !antiinvite ──
    @bot.command(name="antiinvite")
    @commands.has_permissions(manage_messages=True)
    async def prefix_antiinvite(ctx):
        cfg = _gcfg(ctx.guild.id)
        new = _toggle(cfg, "auto_block_invites")
        _save_gcfg(ctx.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await ctx.send(f"{emoji} **Anti-Invite** is now **{new}** — Discord invites will {'be blocked' if new=='ON' else 'be allowed'}.")
        _dlog("📨 ANTIINVITE", f"Anti-invite {new} by {ctx.author}")

    # ── !blacklist group ──
    @bot.group(name="blacklist", invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def prefix_blacklist(ctx):
        await ctx.send("ℹ️ Usage: `!blacklist add <word>` or `!blacklist remove <word>`")

    @prefix_blacklist.command(name="add")
    @commands.has_permissions(manage_messages=True)
    async def prefix_blacklist_add(ctx, *, word: str):
        cfg = _gcfg(ctx.guild.id)
        current = [w.strip() for w in cfg.get("banned_words", "").split(",") if w.strip()]
        word = word.strip().lower()
        if word in current:
            return await ctx.send(f"ℹ️ `{word}` is already blacklisted.")
        current.append(word)
        cfg["banned_words"] = ",".join(current)
        _save_gcfg(ctx.guild.id, cfg)
        await ctx.send(f"✅ **Word Added** — `{word}` is now blacklisted.")
        _dlog("🚫 BLACKLIST+", f"{ctx.author} added '{word}' to blacklist")

    @prefix_blacklist.command(name="remove")
    @commands.has_permissions(manage_messages=True)
    async def prefix_blacklist_remove(ctx, *, word: str):
        cfg = _gcfg(ctx.guild.id)
        current = [w.strip() for w in cfg.get("banned_words", "").split(",") if w.strip()]
        word = word.strip().lower()
        if word not in current:
            return await ctx.send(f"❌ `{word}` is not in the blacklist.")
        current.remove(word)
        cfg["banned_words"] = ",".join(current)
        _save_gcfg(ctx.guild.id, cfg)
        await ctx.send(f"✅ **Word Removed** — `{word}` has been removed from the blacklist.")
        _dlog("🚫 BLACKLIST-", f"{ctx.author} removed '{word}' from blacklist")

    # ── !whitelist group ──
    @bot.group(name="whitelist", invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def prefix_whitelist(ctx):
        await ctx.send("ℹ️ Usage: `!whitelist add @user`")

    @prefix_whitelist.command(name="add")
    @commands.has_permissions(manage_messages=True)
    async def prefix_whitelist_add(ctx, member: discord.Member):
        cfg = _gcfg(ctx.guild.id)
        current = [w.strip() for w in cfg.get("whitelisted_users", "").split(",") if w.strip()]
        uid = str(member.id)
        if uid in current:
            return await ctx.send(f"ℹ️ {member.mention} is already whitelisted.")
        current.append(uid)
        cfg["whitelisted_users"] = ",".join(current)
        _save_gcfg(ctx.guild.id, cfg)
        await ctx.send(f"✅ **Whitelisted** — {member.mention} is exempt from automod.")
        _dlog("✅ WHITELIST+", f"{ctx.author} whitelisted {member}")

    # ── !antinuke ──
    @bot.command(name="antinuke")
    @commands.has_permissions(administrator=True)
    async def prefix_antinuke(ctx, action: str = "toggle"):
        cfg = _gcfg(ctx.guild.id)
        action = action.lower()
        if action in ("enable", "on"):
            cfg["anti_nuke"] = "ON"
        elif action in ("disable", "off"):
            cfg["anti_nuke"] = "OFF"
        else:
            cfg["anti_nuke"] = "OFF" if cfg.get("anti_nuke") == "ON" else "ON"
        _save_gcfg(ctx.guild.id, cfg)
        state = cfg["anti_nuke"]
        emoji = "✅" if state == "ON" else "🔴"
        await ctx.send(f"{emoji} **Anti-Nuke** protection is now **{state}**.")
        _dlog("🛡️ ANTINUKE", f"Anti-nuke {state} by {ctx.author}")

    # ── !antibot ──
    @bot.command(name="antibot")
    @commands.has_permissions(administrator=True)
    async def prefix_antibot(ctx):
        cfg = _gcfg(ctx.guild.id)
        new = _toggle(cfg, "anti_bot")
        _save_gcfg(ctx.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await ctx.send(f"{emoji} **Anti-Bot** is now **{new}** — unauthorized bots will {'be removed' if new=='ON' else 'be allowed'}.")
        _dlog("🤖 ANTIBOT", f"Anti-bot {new} by {ctx.author}")

    # ── !security group ──
    @bot.group(name="security", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def prefix_security(ctx):
        await ctx.send("ℹ️ Usage: `!security scan` or `!security status`")

    @prefix_security.command(name="scan")
    @commands.has_permissions(manage_guild=True)
    async def prefix_security_scan(ctx):
        cfg = _gcfg(ctx.guild.id)
        guild = ctx.guild
        embed = discord.Embed(
            title=f"🛡️ Security Scan — {guild.name}",
            color=0x3498db, timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Anti-Raid",    value="🟢 ON" if cfg.get("anti_raid")=="ON"          else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Link",    value="🟢 ON" if cfg.get("auto_block_links")=="ON"   else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Spam",    value="🟢 ON" if cfg.get("spam_filter")=="ON"        else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Mention", value="🟢 ON" if cfg.get("auto_block_mentions")=="ON"else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Invite",  value="🟢 ON" if cfg.get("auto_block_invites")=="ON" else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Nuke",    value="🟢 ON" if cfg.get("anti_nuke")=="ON"          else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Alt",     value="🟢 ON" if cfg.get("anti_alt")=="ON"           else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Bot",     value="🟢 ON" if cfg.get("anti_bot")=="ON"           else "🔴 OFF", inline=True)
        embed.add_field(name="Automod",      value="🟢 ON" if cfg.get("automod_on")=="ON"         else "🔴 OFF", inline=True)
        bots = sum(1 for m in guild.members if m.bot)
        embed.add_field(name="Members", value=f"{guild.member_count:,} ({bots} bots)", inline=True)
        embed.add_field(name="Verification", value=str(guild.verification_level).title(), inline=True)
        embed.set_footer(text=f"Scanned by {ctx.author}  •  SP4RK Security")
        await ctx.send(embed=embed)

    @prefix_security.command(name="status")
    @commands.has_permissions(manage_guild=True)
    async def prefix_security_status(ctx):
        await ctx.invoke(prefix_security_scan)

    # ── !joinguard ──
    @bot.command(name="joinguard")
    @commands.has_permissions(administrator=True)
    async def prefix_joinguard(ctx, min_age_days: int = None):
        cfg = _gcfg(ctx.guild.id)
        if min_age_days is None:
            current = cfg.get("joinguard_min_age", 0)
            if current:
                await ctx.send(f"🛡️ **Join Guard** active — accounts under **{current} day(s)** are kicked on join.\n> Use `!joinguard 0` to disable.")
            else:
                await ctx.send("ℹ️ **Join Guard** is currently OFF.\n> Use `!joinguard <days>` to set a minimum account age (e.g. `!joinguard 7`).")
            return
        if min_age_days < 0:
            return await ctx.send("❌ Days must be 0 or more. Use `0` to disable.")
        cfg["joinguard_min_age"] = min_age_days
        _save_gcfg(ctx.guild.id, cfg)
        if min_age_days == 0:
            await ctx.send("🔓 **Join Guard Disabled** — all accounts can now join freely.")
        else:
            await ctx.send(f"✅ **Join Guard Set** — accounts younger than **{min_age_days} day(s)** will be kicked on join.")
        _dlog("🛡️ JOINGUARD", f"Join guard set to {min_age_days}d by {ctx.author}")

    # ── !logs group ──
    @bot.group(name="logs", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def prefix_logs(ctx):
        await ctx.send("ℹ️ Usage: `!logs set #channel` · `!logs mod #ch` · `!logs message #ch` · `!logs join #ch` · `!logs voice #ch`")

    @prefix_logs.command(name="set")
    @commands.has_permissions(manage_guild=True)
    async def prefix_logs_set(ctx, channel: discord.TextChannel):
        cfg = _gcfg(ctx.guild.id)
        cfg["log_channel"] = str(channel.id)
        _save_gcfg(ctx.guild.id, cfg)
        await ctx.send(f"✅ **Log Channel Set** — All mod logs will be sent to {channel.mention}.")
        _dlog("📋 LOGS-SET", f"Log channel set to #{channel.name} by {ctx.author}")

    @prefix_logs.command(name="mod")
    @commands.has_permissions(manage_guild=True)
    async def prefix_logs_mod(ctx, channel: discord.TextChannel):
        cfg = _gcfg(ctx.guild.id)
        cfg["log_channel"] = str(channel.id)
        _save_gcfg(ctx.guild.id, cfg)
        await ctx.send(f"✅ **Mod Log Channel** → {channel.mention}")

    @prefix_logs.command(name="message")
    @commands.has_permissions(manage_guild=True)
    async def prefix_logs_message(ctx, channel: discord.TextChannel):
        cfg = _gcfg(ctx.guild.id)
        cfg["message_log_channel"] = str(channel.id)
        _save_gcfg(ctx.guild.id, cfg)
        await ctx.send(f"✅ **Message Log Channel** → {channel.mention}")

    @prefix_logs.command(name="join")
    @commands.has_permissions(manage_guild=True)
    async def prefix_logs_join(ctx, channel: discord.TextChannel):
        cfg = _gcfg(ctx.guild.id)
        cfg["join_log_channel"] = str(channel.id)
        _save_gcfg(ctx.guild.id, cfg)
        await ctx.send(f"✅ **Join Log Channel** → {channel.mention}")

    @prefix_logs.command(name="voice")
    @commands.has_permissions(manage_guild=True)
    async def prefix_logs_voice(ctx, channel: discord.TextChannel):
        cfg = _gcfg(ctx.guild.id)
        cfg["voice_log_channel"] = str(channel.id)
        _save_gcfg(ctx.guild.id, cfg)
        await ctx.send(f"✅ **Voice Log Channel** → {channel.mention}")

    # ── !reactionrole group ──
    @bot.group(name="reactionrole", invoke_without_command=True)
    @commands.has_permissions(manage_roles=True)
    async def prefix_rr(ctx):
        await ctx.send("ℹ️ Usage: `!reactionrole create <msg_id> <emoji> @role` · `!reactionrole delete <msg_id> <emoji>` · `!reactionrole edit <msg_id> <emoji> @role`")

    @prefix_rr.command(name="create")
    @commands.has_permissions(manage_roles=True)
    async def prefix_rr_create(ctx, message_id: int, emoji: str, role: discord.Role):
        import reaction_roles as rr_mod
        rr_mod.add_rule(ctx.guild.id, ctx.channel.id, message_id, emoji, role.id, role.name)
        await ctx.send(f"✅ **Reaction Role Created** — React {emoji} on message `{message_id}` to get **{role.name}**.")
        _dlog("🎭 RR-CREATE", f"{ctx.author} linked {emoji} → {role.name} on msg {message_id}")

    @prefix_rr.command(name="delete")
    @commands.has_permissions(manage_roles=True)
    async def prefix_rr_delete(ctx, message_id: int, emoji: str):
        import reaction_roles as rr_mod
        removed = rr_mod.remove_rule(ctx.guild.id, message_id, emoji)
        if removed:
            await ctx.send(f"✅ **Reaction Role Removed** — {emoji} unlinked from message `{message_id}`.")
        else:
            await ctx.send(f"❌ No reaction role found for {emoji} on message `{message_id}`.")

    @prefix_rr.command(name="edit")
    @commands.has_permissions(manage_roles=True)
    async def prefix_rr_edit(ctx, message_id: int, emoji: str, role: discord.Role):
        import reaction_roles as rr_mod
        rr_mod.remove_rule(ctx.guild.id, message_id, emoji)
        rr_mod.add_rule(ctx.guild.id, ctx.channel.id, message_id, emoji, role.id, role.name)
        await ctx.send(f"✅ **Reaction Role Updated** — {emoji} on `{message_id}` now gives **{role.name}**.")

    # ── !ticket group ──
    @bot.group(name="ticket", invoke_without_command=True)
    @commands.has_permissions(manage_channels=True)
    async def prefix_ticket(ctx):
        await ctx.send("ℹ️ Usage: `!ticket setup` · `!ticket close` · `!ticket add @user` · `!ticket remove @user` · `!ticket rename <name>` · `!ticket claim` · `!ticket unclaim` · `!ticket reopen` · `!ticket panel` · `!ticket stats`")

    @prefix_ticket.command(name="setup")
    @commands.has_permissions(manage_channels=True)
    async def prefix_ticket_setup(ctx, category: discord.CategoryChannel = None):
        cfg = _gcfg(ctx.guild.id)
        if category:
            cfg["ticket_category"] = str(category.id)
        elif not cfg.get("ticket_category"):
            new_cat = await ctx.guild.create_category("Tickets", reason="Ticket system setup by SP4RK")
            cfg["ticket_category"] = str(new_cat.id)
        _save_gcfg(ctx.guild.id, cfg)
        cat_name = category.name if category else "Tickets"
        await ctx.send(f"✅ **Ticket System Ready** — Tickets will be created in **{cat_name}**.")
        _dlog("🎫 TICKET-SETUP", f"Ticket system set up by {ctx.author}")

    @prefix_ticket.command(name="close")
    async def prefix_ticket_close(ctx):
        import tickets as tickets_mod
        ticket_data = next((t for t in tickets_mod.get_all_tickets() if t.get("channel_id") == ctx.channel.id), None)
        if not ticket_data:
            return await ctx.send("❌ This is not a ticket channel.")
        if not ctx.author.guild_permissions.manage_messages and ctx.author.id != ticket_data.get("user_id"):
            return await ctx.send("❌ Only staff or the ticket opener can close this ticket.")
        await ctx.send("🔒 Closing ticket...")
        await tickets_mod.close_ticket(ctx.channel, ctx.author)
        _dlog("🔒 TICKET-CLOSE", f"{ctx.author} closed ticket #{ticket_data.get('id','?')}")

    @prefix_ticket.command(name="add")
    @commands.has_permissions(manage_channels=True)
    async def prefix_ticket_add(ctx, member: discord.Member):
        ticket_data = None
        try:
            import tickets as tickets_mod
            ticket_data = next((t for t in tickets_mod.get_all_tickets() if t.get("channel_id") == ctx.channel.id), None)
        except Exception:
            pass
        if not ticket_data:
            return await ctx.send("❌ This is not a ticket channel.")
        await ctx.channel.set_permissions(member, read_messages=True, send_messages=True)
        await ctx.send(f"✅ **Added** {member.mention} to this ticket.")

    @prefix_ticket.command(name="remove")
    @commands.has_permissions(manage_channels=True)
    async def prefix_ticket_remove(ctx, member: discord.Member):
        ticket_data = None
        try:
            import tickets as tickets_mod
            ticket_data = next((t for t in tickets_mod.get_all_tickets() if t.get("channel_id") == ctx.channel.id), None)
        except Exception:
            pass
        if not ticket_data:
            return await ctx.send("❌ This is not a ticket channel.")
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.send(f"✅ **Removed** {member.mention} from this ticket.")

    @prefix_ticket.command(name="rename")
    @commands.has_permissions(manage_channels=True)
    async def prefix_ticket_rename(ctx, *, name: str):
        try:
            await ctx.channel.edit(name=name)
            await ctx.send(f"✅ **Ticket Renamed** → `{name}`")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to rename this channel.")

    @prefix_ticket.command(name="claim")
    @commands.has_permissions(manage_messages=True)
    async def prefix_ticket_claim(ctx):
        await ctx.send(f"🙋 **Ticket Claimed** — {ctx.author.mention} is now handling this ticket.")
        _dlog("🎫 TICKET-CLAIM", f"{ctx.author} claimed a ticket in #{ctx.channel.name}")

    @prefix_ticket.command(name="unclaim")
    @commands.has_permissions(manage_messages=True)
    async def prefix_ticket_unclaim(ctx):
        await ctx.send(f"🔓 **Ticket Unclaimed** — {ctx.author.mention} released this ticket.")

    @prefix_ticket.command(name="reopen")
    @commands.has_permissions(manage_channels=True)
    async def prefix_ticket_reopen(ctx):
        everyone = ctx.guild.default_role
        await ctx.channel.set_permissions(everyone, read_messages=None, send_messages=None)
        await ctx.send(f"🔓 **Ticket Reopened** — {ctx.channel.mention} is active again.")
        _dlog("🔓 TICKET-REOPEN", f"{ctx.author} reopened ticket in #{ctx.channel.name}")

    @prefix_ticket.command(name="panel")
    @commands.has_permissions(manage_channels=True)
    async def prefix_ticket_panel(ctx):
        embed = discord.Embed(
            title="🎫 Support Tickets",
            description="Click the button below to open a support ticket.\nOur staff will assist you shortly.",
            color=0x4f8ef7,
        )
        view = _TicketPanelView()
        await ctx.send(embed=embed, view=view)
        _dlog("🎫 TICKET-PANEL", f"{ctx.author} created a ticket panel in #{ctx.channel.name}")

    @prefix_ticket.command(name="stats")
    @commands.has_permissions(manage_messages=True)
    async def prefix_ticket_stats(ctx):
        try:
            import tickets as tickets_mod
            all_t = tickets_mod.get_all_tickets()
            open_t  = [t for t in all_t if t.get("status","open") == "open"]
            close_t = [t for t in all_t if t.get("status","open") == "closed"]
        except Exception:
            all_t, open_t, close_t = [], [], []
        embed = discord.Embed(
            title=f"🎫 Ticket Stats — {ctx.guild.name}",
            color=0x4f8ef7, timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Total",  value=str(len(all_t)),   inline=True)
        embed.add_field(name="Open",   value=str(len(open_t)),  inline=True)
        embed.add_field(name="Closed", value=str(len(close_t)), inline=True)
        embed.set_footer(text="SP4RK Ticket System")
        await ctx.send(embed=embed)

    # ── !serverinfo ──
    @bot.command(name="serverinfo")
    async def prefix_serverinfo(ctx):
        import main as _m
        embed = _m._build_serverinfo_embed(ctx.guild, ctx.author)
        await ctx.send(embed=embed)

    # ── !userinfo ──
    @bot.command(name="userinfo")
    async def prefix_userinfo(ctx, member: discord.Member = None):
        import main as _m
        target = member or ctx.author
        embed = _m._build_userinfo_embed(target, ctx.author)
        await ctx.send(embed=embed)

    # ── !avatar ──
    @bot.command(name="avatar")
    async def prefix_avatar(ctx, member: discord.Member = None):
        target = member or ctx.author
        avatar = target.display_avatar
        embed = discord.Embed(
            title=f"🖼️ {target.display_name}'s Avatar",
            color=getattr(target, "color", discord.Color(0x4f8ef7)) or discord.Color(0x4f8ef7),
            timestamp=datetime.utcnow(),
        )
        embed.set_image(url=avatar.replace(size=1024).url)
        await ctx.send(embed=embed)

    # ── !serverstats ──
    @bot.command(name="serverstats")
    async def prefix_serverstats(ctx):
        guild = ctx.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots   = sum(1 for m in guild.members if m.bot)
        embed = discord.Embed(
            title=f"📊 Server Stats — {guild.name}",
            color=0x4f8ef7, timestamp=datetime.utcnow(),
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
        embed.add_field(name="👥 Members",  value=f"**{guild.member_count:,}**",  inline=True)
        embed.add_field(name="👤 Humans",   value=f"**{humans:,}**",               inline=True)
        embed.add_field(name="🤖 Bots",     value=f"**{bots:,}**",                 inline=True)
        embed.add_field(name="💬 Text",     value=f"**{len(guild.text_channels)}**",  inline=True)
        embed.add_field(name="🔊 Voice",    value=f"**{len(guild.voice_channels)}**", inline=True)
        embed.add_field(name="🎭 Roles",    value=f"**{len(guild.roles)-1}**",        inline=True)
        embed.add_field(name="😀 Emojis",   value=f"**{len(guild.emojis)}**",         inline=True)
        embed.add_field(name="🚀 Boosts",   value=f"**{guild.premium_subscription_count or 0}**", inline=True)
        embed.add_field(name="🏅 Boost Tier", value=f"**{guild.premium_tier}**",     inline=True)
        embed.set_footer(text=f"ID: {guild.id}  •  SP4RK")
        await ctx.send(embed=embed)

    # ── !membercount ──
    @bot.command(name="membercount")
    async def prefix_membercount(ctx):
        guild = ctx.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots   = sum(1 for m in guild.members if m.bot)
        await ctx.send(
            f"👥 **{guild.name}** — `{guild.member_count:,}` total members\n"
            f"> 👤 **Humans:** {humans:,}  |  🤖 **Bots:** {bots:,}"
        )

    # ── !announce ──
    @bot.command(name="announce")
    @commands.has_permissions(manage_messages=True)
    async def prefix_announce(ctx, channel: discord.TextChannel = None, *, message: str):
        target = channel or ctx.channel
        embed = discord.Embed(
            description=message,
            color=0x4f8ef7,
            timestamp=datetime.utcnow(),
        )
        embed.set_author(name=f"📢 Announcement — {ctx.guild.name}", icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        embed.set_footer(text=f"By {ctx.author}")
        await target.send(embed=embed)
        if target != ctx.channel:
            await ctx.send(f"✅ Announcement sent to {target.mention}.")
        _dlog("📢 ANNOUNCE", f"{ctx.author} announced in #{target.name}")

    # ── !embed ──
    @bot.command(name="embed")
    @commands.has_permissions(manage_messages=True)
    async def prefix_embed(ctx, *, content: str):
        embed = discord.Embed(description=content, color=0x4f8ef7, timestamp=datetime.utcnow())
        embed.set_footer(text=f"By {ctx.author}")
        await ctx.message.delete()
        await ctx.send(embed=embed)

    # ── !suggest ──
    @bot.command(name="suggest")
    async def prefix_suggest(ctx, *, suggestion: str):
        embed = discord.Embed(
            title="💡 New Suggestion",
            description=suggestion,
            color=0xf1c40f,
            timestamp=datetime.utcnow(),
        )
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
        embed.set_footer(text=f"Suggested in #{ctx.channel.name}")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
        try:
            await ctx.message.delete()
        except Exception:
            pass
        _dlog("💡 SUGGEST", f"{ctx.author} suggested: {suggestion[:60]}")

    # ── !afk (prefix version) ──
    @bot.command(name="afk")
    async def prefix_afk(ctx, *, reason: str = "AFK"):
        try:
            import extras as _ext
            # Re-use the AFK storage from extras module
            _ext._guild_afk(ctx.guild.id)[str(ctx.author.id)] = {
                "reason": reason[:200],
                "since": datetime.now(timezone.utc).isoformat(),
            }
            _ext._save_afk()
        except Exception:
            pass
        await ctx.send(f"💤 **AFK Set** — {ctx.author.mention} is now away.\n> 📋 **Status:** *{reason}*")
        _dlog("💤 AFK", f"{ctx.author} → AFK: {reason}")

    # ══════════════════════════════════════════════════════════════════════════
    # ── SLASH COMMANDS ────────────────────────────────────────────────────────
    # ══════════════════════════════════════════════════════════════════════════

    # ── /clear ──
    @bot.tree.command(name="clear", description="Delete the last N messages in this channel (max 200).")
    @app_commands.describe(amount="Number of messages to delete (default 10)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_clear(interaction: discord.Interaction, amount: int = 10):
        if not await _guard(interaction):
            return
        if amount < 1 or amount > 200:
            return await interaction.response.send_message("❌ Amount must be 1–200.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"🧹 Deleted **{len(deleted)}** message(s).", ephemeral=True)
        _dlog("🧹 CLEAR", f"{interaction.user} cleared {len(deleted)} in #{interaction.channel.name}")

    # ── /purge-bots ──
    @bot.tree.command(name="purge-bots", description="Delete recent bot messages in this channel.")
    @app_commands.describe(amount="Max messages to scan (default 50)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_purge_bots(interaction: discord.Interaction, amount: int = 50):
        if not await _guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount, check=lambda m: m.author.bot)
        await interaction.followup.send(f"🤖 Deleted **{len(deleted)}** bot message(s).", ephemeral=True)

    # ── /purge-links ──
    @bot.tree.command(name="purge-links", description="Delete recent messages containing links.")
    @app_commands.describe(amount="Max messages to scan (default 50)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_purge_links(interaction: discord.Interaction, amount: int = 50):
        if not await _guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        _url = re.compile(r"https?://\S+")
        deleted = await interaction.channel.purge(limit=amount, check=lambda m: bool(_url.search(m.content)))
        await interaction.followup.send(f"🔗 Deleted **{len(deleted)}** message(s) with links.", ephemeral=True)

    # ── /purge-images ──
    @bot.tree.command(name="purge-images", description="Delete recent messages with image attachments.")
    @app_commands.describe(amount="Max messages to scan (default 50)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_purge_images(interaction: discord.Interaction, amount: int = 50):
        if not await _guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount, check=lambda m: bool(m.attachments))
        await interaction.followup.send(f"🖼️ Deleted **{len(deleted)}** image message(s).", ephemeral=True)

    # ── /purge-embeds ──
    @bot.tree.command(name="purge-embeds", description="Delete recent messages containing embeds.")
    @app_commands.describe(amount="Max messages to scan (default 50)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_purge_embeds(interaction: discord.Interaction, amount: int = 50):
        if not await _guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount, check=lambda m: bool(m.embeds))
        await interaction.followup.send(f"📋 Deleted **{len(deleted)}** embed message(s).", ephemeral=True)

    # ── /nickname ──
    @bot.tree.command(name="nickname", description="Change or reset a member's nickname.")
    @app_commands.describe(member="Target member", name="New nickname (leave blank to reset)")
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def slash_nickname(interaction: discord.Interaction, member: discord.Member, name: str = None):
        if not await _guard(interaction):
            return
        try:
            await member.edit(nick=name, reason=f"Renamed by {interaction.user}")
            msg = f"✅ Nickname set to `{name}` for {member.mention}." if name else f"✅ Nickname reset for {member.mention}."
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Cannot rename {member.mention} — check hierarchy.", ephemeral=True)

    # ── /editsnipe ──
    @bot.tree.command(name="editsnipe", description="Show the last edited message in this channel.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_editsnipe(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        data = _edit_snipe_cache.get(interaction.channel.id)
        if not data:
            return await interaction.response.send_message("ℹ️ No recently edited messages found.", ephemeral=True)
        embed = discord.Embed(title="✏️ Edit Sniped", color=0xf39c12, timestamp=data["ts"])
        embed.set_author(name=f"{data['author']} edited:")
        embed.add_field(name="Before", value=data["before"][:1024] or "*empty*", inline=False)
        embed.add_field(name="After",  value=data["after"][:1024]  or "*empty*", inline=False)
        embed.set_footer(text=f"In #{interaction.channel.name}  •  SP4RK")
        await interaction.response.send_message(embed=embed)

    # ── /modhistory ──
    @bot.tree.command(name="modhistory", description="View full mod history (warnings + notes) for a member.")
    @app_commands.describe(member="Member to inspect")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_modhistory(interaction: discord.Interaction, member: discord.Member):
        if not await _guard(interaction):
            return
        import main as _m
        guild_id  = str(interaction.guild.id)
        member_id = str(member.id)
        entry   = _m._get_member_entry(guild_id, member_id)
        count   = entry["count"]
        history = entry["history"]
        notes   = _m.supabase_get_notes(interaction.guild.id, member.id)
        _wlimit = int(_gcfg(guild_id).get("warning_limit", 30))
        pct = min(1.0, count / max(1, _wlimit))
        bar = "▰" * int(10 * pct) + "▱" * (10 - int(10 * pct))
        severity = "🔴 High Risk" if count >= _wlimit else ("🟠 Moderate" if count >= _wlimit // 2 else "🟢 Low Risk")
        embed = discord.Embed(
            title=f"📋 Mod History — {member.display_name}",
            description=f"**{member}** · `{member.id}`\n━━━━━━━━━━━━━━━━━━━━━━\nRisk: `{bar}` — **{severity}**",
            color=0xe74c3c if count >= _wlimit else (0xf39c12 if count >= 2 else 0x2ecc71),
            timestamp=datetime.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="⚠️ Warnings", value=f"**{count}** / {_wlimit}", inline=True)
        embed.add_field(name="📝 Notes",     value=f"**{len(notes)}**",        inline=True)
        embed.add_field(name="📊 Risk",      value=severity,                   inline=True)
        if history:
            warn_text = "\n".join(f"`{i+1}.` {h['reason']} — *{h['time']}*" for i, h in enumerate(history[-5:][::-1]))
            embed.add_field(name="📋 Recent Warnings", value=warn_text, inline=False)
        if notes:
            note_text = "\n".join(f"> *{n.get('note')}*\n> — {n.get('moderator')}" for n in notes[-3:])
            embed.add_field(name="📝 Recent Notes", value=note_text, inline=False)
        embed.set_footer(text=f"Requested by {interaction.user}  •  SP4RK")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /serverstats ──
    @bot.tree.command(name="serverstats", description="Show detailed server statistics.")
    async def slash_serverstats(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        guild = interaction.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots   = sum(1 for m in guild.members if m.bot)
        embed = discord.Embed(
            title=f"📊 Server Stats — {guild.name}",
            color=0x4f8ef7, timestamp=datetime.utcnow(),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="👥 Members",    value=f"**{guild.member_count:,}**",     inline=True)
        embed.add_field(name="👤 Humans",     value=f"**{humans:,}**",                  inline=True)
        embed.add_field(name="🤖 Bots",       value=f"**{bots:,}**",                    inline=True)
        embed.add_field(name="💬 Text Channels",  value=f"**{len(guild.text_channels)}**",  inline=True)
        embed.add_field(name="🔊 Voice Channels", value=f"**{len(guild.voice_channels)}**", inline=True)
        embed.add_field(name="🎭 Roles",      value=f"**{len(guild.roles)-1}**",        inline=True)
        embed.add_field(name="😀 Emojis",     value=f"**{len(guild.emojis)}**",          inline=True)
        embed.add_field(name="🚀 Boosts",     value=f"**{guild.premium_subscription_count or 0}**", inline=True)
        embed.add_field(name="🏅 Boost Tier", value=f"**{guild.premium_tier}**",        inline=True)
        embed.set_footer(text=f"ID: {guild.id}  •  SP4RK")
        await interaction.response.send_message(embed=embed)

    # ── /membercount ──
    @bot.tree.command(name="membercount", description="Show the server member count.")
    async def slash_membercount(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        guild = interaction.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots   = sum(1 for m in guild.members if m.bot)
        await interaction.response.send_message(
            f"👥 **{guild.name}** — `{guild.member_count:,}` total members\n"
            f"> 👤 **Humans:** {humans:,}  |  🤖 **Bots:** {bots:,}"
        )

    # ── /channelinfo ──
    @bot.tree.command(name="channelinfo", description="Show detailed info about a channel.")
    @app_commands.describe(channel="Channel to inspect (defaults to current)")
    async def slash_channelinfo(interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not await _guard(interaction):
            return
        target = channel or interaction.channel
        embed = discord.Embed(
            title=f"#️⃣ Channel Info — #{target.name}",
            description=target.topic or "_No topic set._",
            color=0x4f8ef7, timestamp=datetime.utcnow(),
        )
        embed.add_field(name="🆔 ID",       value=f"`{target.id}`",                                   inline=True)
        embed.add_field(name="📂 Category", value=target.category.name if target.category else "—",   inline=True)
        embed.add_field(name="📍 Position", value=f"#{target.position}",                              inline=True)
        embed.add_field(name="📅 Created",  value=f"<t:{int(target.created_at.timestamp())}:R>",      inline=True)
        embed.add_field(name="🐌 Slowmode", value=f"{target.slowmode_delay}s" if target.slowmode_delay else "Off", inline=True)
        embed.add_field(name="🔞 NSFW",     value="Yes" if target.is_nsfw() else "No",                inline=True)
        embed.set_footer(text="SP4RK Channel Info")
        await interaction.response.send_message(embed=embed)

    # ── /announce ──
    @bot.tree.command(name="announce", description="Send an announcement embed to a channel.")
    @app_commands.describe(message="Announcement content", channel="Channel to send to (defaults to current)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_announce(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
        if not await _guard(interaction):
            return
        target = channel or interaction.channel
        embed = discord.Embed(
            description=message,
            color=0x4f8ef7,
            timestamp=datetime.utcnow(),
        )
        embed.set_author(
            name=f"📢 Announcement — {interaction.guild.name}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None,
        )
        embed.set_footer(text=f"By {interaction.user}")
        try:
            await target.send(embed=embed)
            await interaction.response.send_message(f"✅ Announcement sent to {target.mention}.", ephemeral=True)
            _dlog("📢 ANNOUNCE", f"{interaction.user} announced in #{target.name}")
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ I can't send messages to {target.mention}.", ephemeral=True)

    # ── /uptime ──
    @bot.tree.command(name="uptime", description="Show how long the bot has been running.")
    async def slash_uptime(interaction: discord.Interaction):
        try:
            import main as _m
            started = _m.BOT_START_TIME
            delta = datetime.utcnow() - started
            d = delta.days
            h, rem = divmod(delta.seconds, 3600)
            m, s   = divmod(rem, 60)
            uptime_str = f"{d}d {h}h {m}m {s}s"
            ts = int(started.timestamp())
        except Exception:
            uptime_str = "Unknown"
            ts = None
        embed = discord.Embed(
            title="⏱️ SP4RK Uptime",
            description=f"**{uptime_str}**" + (f"\nOnline since <t:{ts}:R>" if ts else ""),
            color=0x2ecc71, timestamp=datetime.utcnow(),
        )
        embed.set_footer(text="SP4RK Bot")
        await interaction.response.send_message(embed=embed)

    # ── /suggest ──
    @bot.tree.command(name="suggest", description="Submit a suggestion for the server.")
    @app_commands.describe(suggestion="Your suggestion text")
    async def slash_suggest(interaction: discord.Interaction, suggestion: str):
        if not await _guard(interaction):
            return
        embed = discord.Embed(
            title="💡 New Suggestion",
            description=suggestion,
            color=0xf1c40f, timestamp=datetime.utcnow(),
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Suggested in #{interaction.channel.name}")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
        _dlog("💡 SUGGEST", f"{interaction.user} suggested: {suggestion[:60]}")

    # ── /automod-setup ──
    @bot.tree.command(name="automod-setup", description="Enable or disable the full SP4RK automod system.")
    @app_commands.describe(enabled="Turn automod on or off")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_automod_setup(interaction: discord.Interaction, enabled: bool):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["automod_on"] = "ON" if enabled else "OFF"
        _save_gcfg(interaction.guild.id, cfg)
        state = "ON" if enabled else "OFF"
        emoji = "✅" if enabled else "🔴"
        await interaction.response.send_message(
            f"{emoji} **Automod** is now **{state}** for this server.\n"
            f"> Configure filters and thresholds in the **Byte Labs dashboard**.", ephemeral=True
        )
        _dlog("🤖 AUTOMOD-SETUP", f"Automod {state} by {interaction.user}")

    # ── /antilink ──
    @bot.tree.command(name="antilink", description="Toggle the link filter — blocks non-whitelisted URLs.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_antilink(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        new = _toggle(cfg, "auto_block_links")
        _save_gcfg(interaction.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await interaction.response.send_message(f"{emoji} **Anti-Link** is now **{new}**.", ephemeral=True)
        _dlog("🔗 ANTILINK", f"Anti-link {new} by {interaction.user}")

    # ── /antispam ──
    @bot.tree.command(name="antispam", description="Toggle the spam filter.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_antispam(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        new = _toggle(cfg, "spam_filter")
        _save_gcfg(interaction.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await interaction.response.send_message(f"{emoji} **Anti-Spam** is now **{new}**.", ephemeral=True)
        _dlog("📵 ANTISPAM", f"Anti-spam {new} by {interaction.user}")

    # ── /antimention ──
    @bot.tree.command(name="antimention", description="Toggle the mass-mention blocker.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_antimention(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        new = _toggle(cfg, "auto_block_mentions")
        _save_gcfg(interaction.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await interaction.response.send_message(f"{emoji} **Anti-Mention** is now **{new}**.", ephemeral=True)
        _dlog("📢 ANTIMENTION", f"Anti-mention {new} by {interaction.user}")

    # ── /antiinvite ──
    @bot.tree.command(name="antiinvite", description="Toggle the Discord invite blocker.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_antiinvite(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        new = _toggle(cfg, "auto_block_invites")
        _save_gcfg(interaction.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await interaction.response.send_message(f"{emoji} **Anti-Invite** is now **{new}**.", ephemeral=True)
        _dlog("📨 ANTIINVITE", f"Anti-invite {new} by {interaction.user}")

    # ── /antibot ──
    @bot.tree.command(name="antibot", description="Toggle unauthorized bot blocking.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_antibot(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        new = _toggle(cfg, "anti_bot")
        _save_gcfg(interaction.guild.id, cfg)
        emoji = "✅" if new == "ON" else "🔴"
        await interaction.response.send_message(f"{emoji} **Anti-Bot** is now **{new}**.", ephemeral=True)
        _dlog("🤖 ANTIBOT", f"Anti-bot {new} by {interaction.user}")

    # ── /blacklist group ──
    _blacklist_group = app_commands.Group(name="blacklist", description="Manage the server word blacklist.")

    @_blacklist_group.command(name="add", description="Add a word to the blacklist.")
    @app_commands.describe(word="Word to block")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_blacklist_add(interaction: discord.Interaction, word: str):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        current = [w.strip() for w in cfg.get("banned_words", "").split(",") if w.strip()]
        word = word.strip().lower()
        if word in current:
            return await interaction.response.send_message(f"ℹ️ `{word}` is already blacklisted.", ephemeral=True)
        current.append(word)
        cfg["banned_words"] = ",".join(current)
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ `{word}` added to the blacklist.", ephemeral=True)
        _dlog("🚫 BLACKLIST+", f"{interaction.user} added '{word}'")

    @_blacklist_group.command(name="remove", description="Remove a word from the blacklist.")
    @app_commands.describe(word="Word to unblock")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_blacklist_remove(interaction: discord.Interaction, word: str):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        current = [w.strip() for w in cfg.get("banned_words", "").split(",") if w.strip()]
        word = word.strip().lower()
        if word not in current:
            return await interaction.response.send_message(f"❌ `{word}` is not in the blacklist.", ephemeral=True)
        current.remove(word)
        cfg["banned_words"] = ",".join(current)
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ `{word}` removed from the blacklist.", ephemeral=True)
        _dlog("🚫 BLACKLIST-", f"{interaction.user} removed '{word}'")

    bot.tree.add_command(_blacklist_group)

    # ── /whitelist group ──
    _whitelist_group = app_commands.Group(name="whitelist", description="Manage automod whitelist.")

    @_whitelist_group.command(name="add", description="Whitelist a user — exempt from automod filters.")
    @app_commands.describe(member="Member to whitelist")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_whitelist_add(interaction: discord.Interaction, member: discord.Member):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        current = [w.strip() for w in cfg.get("whitelisted_users", "").split(",") if w.strip()]
        uid = str(member.id)
        if uid in current:
            return await interaction.response.send_message(f"ℹ️ {member.mention} is already whitelisted.", ephemeral=True)
        current.append(uid)
        cfg["whitelisted_users"] = ",".join(current)
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ {member.mention} is now whitelisted.", ephemeral=True)
        _dlog("✅ WHITELIST+", f"{interaction.user} whitelisted {member}")

    bot.tree.add_command(_whitelist_group)

    # ── /antinuke group ──
    _antinuke_group = app_commands.Group(name="antinuke", description="Anti-nuke protection settings.")

    @_antinuke_group.command(name="enable", description="Enable anti-nuke protection.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_antinuke_enable(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["anti_nuke"] = "ON"
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message("✅ **Anti-Nuke** protection is now **ON**.", ephemeral=True)
        _dlog("🛡️ ANTINUKE", f"Anti-nuke enabled by {interaction.user}")

    @_antinuke_group.command(name="disable", description="Disable anti-nuke protection.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_antinuke_disable(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["anti_nuke"] = "OFF"
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message("🔴 **Anti-Nuke** protection is now **OFF**.", ephemeral=True)
        _dlog("🛡️ ANTINUKE", f"Anti-nuke disabled by {interaction.user}")

    bot.tree.add_command(_antinuke_group)

    # ── /antialt group ──
    _antialt_group = app_commands.Group(name="antialt", description="Alt-account protection settings.")

    @_antialt_group.command(name="enable", description="Enable alt-account protection.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_antialt_enable(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["anti_alt"] = "ON"
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message("✅ **Anti-Alt** protection is now **ON**.", ephemeral=True)
        _dlog("🛡️ ANTIALT", f"Anti-alt enabled by {interaction.user}")

    @_antialt_group.command(name="disable", description="Disable alt-account protection.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_antialt_disable(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["anti_alt"] = "OFF"
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message("🔴 **Anti-Alt** protection is now **OFF**.", ephemeral=True)
        _dlog("🛡️ ANTIALT", f"Anti-alt disabled by {interaction.user}")

    bot.tree.add_command(_antialt_group)

    # ── /security group ──
    _security_group = app_commands.Group(name="security", description="Server security tools.")

    @_security_group.command(name="scan", description="Run a full security scan of the server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def slash_security_scan(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        guild = interaction.guild
        embed = discord.Embed(
            title=f"🛡️ Security Scan — {guild.name}",
            color=0x3498db, timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Anti-Raid",    value="🟢 ON" if cfg.get("anti_raid")=="ON"           else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Link",    value="🟢 ON" if cfg.get("auto_block_links")=="ON"    else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Spam",    value="🟢 ON" if cfg.get("spam_filter")=="ON"         else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Mention", value="🟢 ON" if cfg.get("auto_block_mentions")=="ON" else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Invite",  value="🟢 ON" if cfg.get("auto_block_invites")=="ON"  else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Nuke",    value="🟢 ON" if cfg.get("anti_nuke")=="ON"           else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Alt",     value="🟢 ON" if cfg.get("anti_alt")=="ON"            else "🔴 OFF", inline=True)
        embed.add_field(name="Anti-Bot",     value="🟢 ON" if cfg.get("anti_bot")=="ON"            else "🔴 OFF", inline=True)
        embed.add_field(name="Automod",      value="🟢 ON" if cfg.get("automod_on")=="ON"          else "🔴 OFF", inline=True)
        bots = sum(1 for m in guild.members if m.bot)
        embed.add_field(name="Members",      value=f"{guild.member_count:,} ({bots} bots)",        inline=True)
        embed.add_field(name="Verification", value=str(guild.verification_level).title(),           inline=True)
        embed.set_footer(text=f"Scanned by {interaction.user}  •  SP4RK Security")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @_security_group.command(name="status", description="Show current security settings status.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def slash_security_status(interaction: discord.Interaction):
        await slash_security_scan.callback(interaction)

    bot.tree.add_command(_security_group)

    # ── /joinguard group ──
    _joinguard_group = app_commands.Group(name="joinguard", description="Join guard — restrict new accounts.")

    @_joinguard_group.command(name="set", description="Set minimum account age to join (days). Use 0 to disable.")
    @app_commands.describe(days="Minimum account age in days (0 = disabled)")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_joinguard_set(interaction: discord.Interaction, days: int):
        if not await _guard(interaction):
            return
        if days < 0:
            return await interaction.response.send_message("❌ Days must be 0 or more.", ephemeral=True)
        cfg = _gcfg(interaction.guild.id)
        cfg["joinguard_min_age"] = days
        _save_gcfg(interaction.guild.id, cfg)
        if days == 0:
            await interaction.response.send_message("🔓 **Join Guard Disabled** — all accounts can join freely.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"✅ **Join Guard Set** — accounts younger than **{days} day(s)** will be kicked on join.", ephemeral=True
            )
        _dlog("🛡️ JOINGUARD", f"Join guard set to {days}d by {interaction.user}")

    bot.tree.add_command(_joinguard_group)

    # ── /captcha group ──
    _captcha_group = app_commands.Group(name="captcha", description="Captcha verification settings.")

    @_captcha_group.command(name="setup", description="Enable captcha verification for new members.")
    @app_commands.describe(enabled="Enable or disable captcha verification")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_captcha_setup(interaction: discord.Interaction, enabled: bool):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["captcha_enabled"] = "ON" if enabled else "OFF"
        _save_gcfg(interaction.guild.id, cfg)
        state = "ON" if enabled else "OFF"
        emoji = "✅" if enabled else "🔴"
        await interaction.response.send_message(
            f"{emoji} **Captcha Verification** is now **{state}**.\n"
            f"> Configure the verification channel and role in the **Byte Labs dashboard**.", ephemeral=True
        )
        _dlog("🔐 CAPTCHA", f"Captcha {state} by {interaction.user}")

    bot.tree.add_command(_captcha_group)

    # ── /logs group ──
    _logs_group = app_commands.Group(name="logs", description="Configure logging channels.")

    @_logs_group.command(name="setup", description="Set the main mod log channel.")
    @app_commands.describe(channel="Channel to send all mod logs to")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def slash_logs_setup(interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["log_channel"] = str(channel.id)
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ **Log Channel Set** → {channel.mention}", ephemeral=True)
        _dlog("📋 LOGS-SETUP", f"Log channel → #{channel.name} by {interaction.user}")

    @_logs_group.command(name="mod", description="Set the moderation log channel.")
    @app_commands.describe(channel="Channel for mod action logs")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def slash_logs_mod(interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["log_channel"] = str(channel.id)
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ **Mod Log Channel** → {channel.mention}", ephemeral=True)

    @_logs_group.command(name="message", description="Set the message edit/delete log channel.")
    @app_commands.describe(channel="Channel for message logs")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def slash_logs_message(interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["message_log_channel"] = str(channel.id)
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ **Message Log Channel** → {channel.mention}", ephemeral=True)

    @_logs_group.command(name="join", description="Set the join/leave log channel.")
    @app_commands.describe(channel="Channel for join/leave logs")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def slash_logs_join(interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["join_log_channel"] = str(channel.id)
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ **Join Log Channel** → {channel.mention}", ephemeral=True)

    @_logs_group.command(name="voice", description="Set the voice activity log channel.")
    @app_commands.describe(channel="Channel for voice logs")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def slash_logs_voice(interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        cfg["voice_log_channel"] = str(channel.id)
        _save_gcfg(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ **Voice Log Channel** → {channel.mention}", ephemeral=True)

    bot.tree.add_command(_logs_group)

    # ── /case group ──
    _case_group = app_commands.Group(name="case", description="View and search moderation cases.")

    @_case_group.command(name="view", description="View a specific moderation case by ID.")
    @app_commands.describe(case_id="The case ID to look up")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_case_view(interaction: discord.Interaction, case_id: str):
        if not await _guard(interaction):
            return
        try:
            from dashboard import supabase
            rows = []
            if supabase:
                res = supabase.table("sentinel_mod_logs").select("*").eq("id", case_id).execute()
                rows = res.data or []
        except Exception:
            rows = []
        if not rows:
            return await interaction.response.send_message(f"❌ No case found with ID `{case_id}`.", ephemeral=True)
        row = rows[0]
        embed = discord.Embed(title=f"📋 Case #{case_id}", color=0x4f8ef7, timestamp=datetime.utcnow())
        embed.add_field(name="Action",    value=row.get("action", "—"),     inline=True)
        embed.add_field(name="Target",    value=row.get("member", "—"),     inline=True)
        embed.add_field(name="Moderator", value=row.get("moderator", "—"),  inline=True)
        embed.add_field(name="Reason",    value=row.get("reason", "—"),     inline=False)
        embed.add_field(name="Date",      value=row.get("timestamp", "—"),  inline=False)
        embed.set_footer(text="SP4RK Case Log")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @_case_group.command(name="search", description="Search mod cases for a member.")
    @app_commands.describe(member="Member to search cases for")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_case_search(interaction: discord.Interaction, member: discord.Member):
        if not await _guard(interaction):
            return
        try:
            from dashboard import supabase
            rows = []
            if supabase:
                res = supabase.table("sentinel_mod_logs").select("*").eq("guild_id", str(interaction.guild.id)).execute()
                all_rows = res.data or []
                name_str = str(member)
                rows = [r for r in all_rows if str(member.id) in str(r.get("member","")) or name_str in str(r.get("member",""))]
        except Exception:
            rows = []
        if not rows:
            return await interaction.response.send_message(f"❌ No cases found for {member.mention}.", ephemeral=True)
        embed = discord.Embed(
            title=f"🔍 Cases for {member.display_name}",
            description=f"Found **{len(rows)}** case(s).",
            color=0x4f8ef7, timestamp=datetime.utcnow(),
        )
        for r in rows[-10:]:
            embed.add_field(
                name=f"`{r.get('id','?')}` — {r.get('action','?')}",
                value=f"By {r.get('moderator','?')} · {r.get('reason','—')[:80]}",
                inline=False,
            )
        embed.set_footer(text="SP4RK Case Search")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    bot.tree.add_command(_case_group)

    # ── /audit group ──
    _audit_group = app_commands.Group(name="audit", description="View the Discord audit log.")

    @_audit_group.command(name="logs", description="Show recent audit log entries.")
    @app_commands.describe(limit="How many entries to show (max 25)")
    @app_commands.checks.has_permissions(view_audit_log=True)
    async def slash_audit_logs(interaction: discord.Interaction, limit: int = 10):
        if not await _guard(interaction):
            return
        if limit < 1 or limit > 25:
            limit = 10
        await interaction.response.defer(ephemeral=True)
        try:
            embed = discord.Embed(title=f"📋 Audit Log — {interaction.guild.name}", color=0x4f8ef7, timestamp=datetime.utcnow())
            entries = [e async for e in interaction.guild.audit_logs(limit=limit)]
            if not entries:
                embed.description = "_No entries found._"
            else:
                lines = []
                for e in entries:
                    action = str(e.action).replace("AuditLogAction.", "")
                    lines.append(f"`{action}` — **{e.user}** · <t:{int(e.created_at.timestamp())}:R>")
                embed.description = "\n".join(lines)
            embed.set_footer(text=f"Last {len(entries)} entries  •  SP4RK")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I need the **View Audit Log** permission.", ephemeral=True)

    @_audit_group.command(name="search", description="Search audit log for a specific action type.")
    @app_commands.describe(action="Action keyword to search for (e.g. ban, kick, delete)")
    @app_commands.checks.has_permissions(view_audit_log=True)
    async def slash_audit_search(interaction: discord.Interaction, action: str):
        if not await _guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            entries = [e async for e in interaction.guild.audit_logs(limit=100)]
            action_lower = action.lower()
            matches = [e for e in entries if action_lower in str(e.action).lower()]
            if not matches:
                return await interaction.followup.send(f"❌ No audit entries matching `{action}` found.", ephemeral=True)
            embed = discord.Embed(
                title=f"🔍 Audit Search — `{action}`",
                description=f"Found **{len(matches)}** match(es).",
                color=0x4f8ef7, timestamp=datetime.utcnow(),
            )
            for e in matches[:10]:
                act = str(e.action).replace("AuditLogAction.", "")
                embed.add_field(
                    name=f"`{act}` — {e.user}",
                    value=f"<t:{int(e.created_at.timestamp())}:R>" + (f" · target: {e.target}" if e.target else ""),
                    inline=False,
                )
            embed.set_footer(text="SP4RK Audit Search")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I need the **View Audit Log** permission.", ephemeral=True)

    bot.tree.add_command(_audit_group)

    # ── /reactionrole group ──
    _rr_group = app_commands.Group(name="reactionrole", description="Manage reaction roles.")

    @_rr_group.command(name="create", description="Link an emoji on a message to a role.")
    @app_commands.describe(message_id="Message ID", emoji="Emoji to react with", role="Role to assign")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def slash_rr_create(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
        if not await _guard(interaction):
            return
        try:
            mid = int(message_id)
        except ValueError:
            return await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)
        try:
            import reaction_roles as rr_mod
            rr_mod.add_rule(interaction.guild.id, interaction.channel.id, mid, emoji, role.id, role.name)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Failed: `{e}`", ephemeral=True)
        await interaction.response.send_message(
            f"✅ **Reaction Role Created** — React {emoji} on `{message_id}` to get **{role.name}**.", ephemeral=True
        )
        _dlog("🎭 RR-CREATE", f"{interaction.user} linked {emoji} → {role.name} on {message_id}")

    @_rr_group.command(name="edit", description="Update the role for an existing reaction-role.")
    @app_commands.describe(message_id="Message ID", emoji="Emoji", role="New role to assign")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def slash_rr_edit(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
        if not await _guard(interaction):
            return
        try:
            import reaction_roles as rr_mod
            rr_mod.remove_rule(interaction.guild.id, message_id, emoji)
            rr_mod.add_rule(interaction.guild.id, interaction.channel.id, int(message_id), emoji, role.id, role.name)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Failed: `{e}`", ephemeral=True)
        await interaction.response.send_message(
            f"✅ **Reaction Role Updated** — {emoji} on `{message_id}` now gives **{role.name}**.", ephemeral=True
        )

    @_rr_group.command(name="delete", description="Remove a reaction-role from a message.")
    @app_commands.describe(message_id="Message ID", emoji="Emoji to unlink")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def slash_rr_delete(interaction: discord.Interaction, message_id: str, emoji: str):
        if not await _guard(interaction):
            return
        try:
            import reaction_roles as rr_mod
            removed = rr_mod.remove_rule(interaction.guild.id, message_id, emoji)
        except Exception:
            removed = False
        if removed:
            await interaction.response.send_message(f"✅ Reaction role removed — {emoji} unlinked from `{message_id}`.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ No reaction role for {emoji} on `{message_id}`.", ephemeral=True)

    bot.tree.add_command(_rr_group)

    # ── /buttonrole group ──
    _buttonrole_group = app_commands.Group(name="buttonrole", description="Manage button-based roles.")

    @_buttonrole_group.command(name="create", description="Create a button that assigns a role when clicked.")
    @app_commands.describe(role="Role to assign", label="Button label text", channel="Channel to post in")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def slash_buttonrole_create(interaction: discord.Interaction, role: discord.Role, label: str = None, channel: discord.TextChannel = None):
        if not await _guard(interaction):
            return
        btn_label = label or role.name
        target = channel or interaction.channel
        embed = discord.Embed(
            title="🎭 Role Assignment",
            description=f"Click the button below to get the **{role.name}** role.",
            color=role.color if role.color.value else discord.Color(0x4f8ef7),
        )
        view = _ButtonRoleView(role, btn_label)
        await target.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ **Button Role Created** — posted in {target.mention}.", ephemeral=True)
        _dlog("🔘 BUTTONROLE", f"{interaction.user} created button role for @{role.name}")

    bot.tree.add_command(_buttonrole_group)

    # ── /selectrole group ──
    _selectrole_group = app_commands.Group(name="selectrole", description="Manage dropdown-based roles.")

    @_selectrole_group.command(name="create", description="Create a dropdown menu for role selection.")
    @app_commands.describe(roles="Comma-separated role names or mentions", channel="Channel to post in")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def slash_selectrole_create(interaction: discord.Interaction, roles: str, channel: discord.TextChannel = None):
        if not await _guard(interaction):
            return
        target = channel or interaction.channel
        role_list = []
        for part in roles.split(","):
            part = part.strip().strip("<@&>")
            try:
                role = interaction.guild.get_role(int(part))
                if not role:
                    role = discord.utils.get(interaction.guild.roles, name=part)
            except Exception:
                role = discord.utils.get(interaction.guild.roles, name=part)
            if role:
                role_list.append(role)
        if not role_list:
            return await interaction.response.send_message("❌ No valid roles found. Use role names or IDs.", ephemeral=True)
        embed = discord.Embed(
            title="🎭 Role Selection",
            description="Select a role from the dropdown below.",
            color=0x4f8ef7,
        )
        view = _SelectRoleView(role_list)
        await target.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ **Select Role Menu Created** — posted in {target.mention}.", ephemeral=True)
        _dlog("📋 SELECTROLE", f"{interaction.user} created select menu with {len(role_list)} roles")

    bot.tree.add_command(_selectrole_group)

    # ── /selfrole group ──
    _selfrole_group = app_commands.Group(name="selfrole", description="Self-assignable role panels.")

    @_selfrole_group.command(name="panel", description="Create a self-role panel with multiple roles.")
    @app_commands.describe(roles="Comma-separated role names", title="Panel title", channel="Channel to post in")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def slash_selfrole_panel(interaction: discord.Interaction, roles: str, title: str = "Self Roles", channel: discord.TextChannel = None):
        if not await _guard(interaction):
            return
        target = channel or interaction.channel
        role_list = []
        for part in roles.split(","):
            part = part.strip().strip("<@&>")
            try:
                role = interaction.guild.get_role(int(part))
                if not role:
                    role = discord.utils.get(interaction.guild.roles, name=part)
            except Exception:
                role = discord.utils.get(interaction.guild.roles, name=part)
            if role:
                role_list.append(role)
        if not role_list:
            return await interaction.response.send_message("❌ No valid roles found.", ephemeral=True)
        embed = discord.Embed(
            title=f"🎭 {title}",
            description="Click a button below to toggle your role.",
            color=0x4f8ef7,
        )
        for r in role_list:
            embed.add_field(name=r.name, value=r.mention, inline=True)
        view = _SelfRolePanelView(role_list)
        await target.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ **Self-Role Panel Created** — posted in {target.mention}.", ephemeral=True)

    bot.tree.add_command(_selfrole_group)

    # ── /autorole group ──
    _autorole_group = app_commands.Group(name="autorole", description="Auto-role settings.")

    @_autorole_group.command(name="set", description="Set a role to automatically assign to new members.")
    @app_commands.describe(role="Role to auto-assign (use none to disable)")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def slash_autorole_set(interaction: discord.Interaction, role: discord.Role = None):
        if not await _guard(interaction):
            return
        cfg = _gcfg(interaction.guild.id)
        if role:
            cfg["auto_role"] = str(role.id)
            _save_gcfg(interaction.guild.id, cfg)
            await interaction.response.send_message(f"✅ **Auto-Role Set** — New members will receive {role.mention}.", ephemeral=True)
        else:
            cfg["auto_role"] = ""
            _save_gcfg(interaction.guild.id, cfg)
            await interaction.response.send_message("✅ **Auto-Role Disabled** — New members won't be auto-assigned any role.", ephemeral=True)
        _dlog("🎭 AUTOROLE", f"Auto-role set to {role} by {interaction.user}")

    bot.tree.add_command(_autorole_group)

    # ── /embed group ──
    _embed_group = app_commands.Group(name="embed", description="Create and manage embed messages.")

    @_embed_group.command(name="create", description="Send a custom embed message to a channel.")
    @app_commands.describe(title="Embed title", description="Embed description", color="Hex color e.g. #3498db", channel="Channel to post in")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_embed_create(interaction: discord.Interaction, description: str, title: str = None, color: str = "#4f8ef7", channel: discord.TextChannel = None):
        if not await _guard(interaction):
            return
        try:
            color_int = int(color.strip("#"), 16)
        except Exception:
            color_int = 0x4f8ef7
        target = channel or interaction.channel
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color(color_int),
            timestamp=datetime.utcnow(),
        )
        embed.set_footer(text=f"By {interaction.user}")
        try:
            await target.send(embed=embed)
            await interaction.response.send_message(f"✅ Embed sent to {target.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Can't send to {target.mention}.", ephemeral=True)

    @_embed_group.command(name="edit", description="Edit an existing embed message sent by this bot.")
    @app_commands.describe(message_id="ID of the embed message to edit", description="New description text")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_embed_edit(interaction: discord.Interaction, message_id: str, description: str):
        if not await _guard(interaction):
            return
        try:
            msg = await interaction.channel.fetch_message(int(message_id))
            if not msg.author.id == interaction.guild.me.id:
                return await interaction.response.send_message("❌ I can only edit my own messages.", ephemeral=True)
            if not msg.embeds:
                return await interaction.response.send_message("❌ That message has no embed.", ephemeral=True)
            old = msg.embeds[0]
            new_embed = old.copy()
            new_embed.description = description
            await msg.edit(embed=new_embed)
            await interaction.response.send_message("✅ Embed updated.", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("❌ Message not found in this channel.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed: `{e}`", ephemeral=True)

    @_embed_group.command(name="delete", description="Delete an embed message sent by this bot.")
    @app_commands.describe(message_id="ID of the embed message to delete")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_embed_delete(interaction: discord.Interaction, message_id: str):
        if not await _guard(interaction):
            return
        try:
            msg = await interaction.channel.fetch_message(int(message_id))
            await msg.delete()
            await interaction.response.send_message("✅ Message deleted.", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("❌ Message not found.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to delete that message.", ephemeral=True)

    bot.tree.add_command(_embed_group)

    # ── /ticket-panel ──
    @bot.tree.command(name="ticket-panel", description="Post a ticket panel with an Open Ticket button.")
    @app_commands.describe(channel="Channel to post the panel in")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_ticket_panel(interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not await _guard(interaction):
            return
        target = channel or interaction.channel
        embed = discord.Embed(
            title="🎫 Support Tickets",
            description="Need help? Click the button below to open a private support ticket.\nOur staff will be with you shortly.",
            color=0x4f8ef7,
        )
        embed.set_footer(text=f"{interaction.guild.name}  •  Powered by SP4RK")
        view = _TicketPanelView()
        await target.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ Ticket panel posted in {target.mention}.", ephemeral=True)
        _dlog("🎫 TICKET-PANEL", f"{interaction.user} posted panel in #{target.name}")

    # ── /ticket-create ──
    @bot.tree.command(name="ticket-create", description="Open a new support ticket.")
    @app_commands.describe(reason="Reason for opening this ticket")
    async def slash_ticket_create(interaction: discord.Interaction, reason: str = "General Support"):
        if not await _guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            import tickets as tickets_mod
            import main as _m
            result = await tickets_mod.create_ticket(interaction.guild, interaction.user, _m.config, reason)
            if result:
                channel, ticket = result
                await interaction.followup.send(
                    f"✅ **Ticket Created** — {channel.mention}\n> 📝 **Reason:** {reason}", ephemeral=True
                )
                _dlog("🎫 TICKET", f"{interaction.user} opened #{ticket.get('id','?')}: {reason}")
            else:
                await interaction.followup.send(
                    "❌ Could not create ticket — check permissions and ticket category in the dashboard.", ephemeral=True
                )
        except Exception as e:
            await interaction.followup.send(f"❌ Ticket error: `{e}`", ephemeral=True)

    # ── /ticket-add ──
    @bot.tree.command(name="ticket-add", description="Add a user to the current ticket channel.")
    @app_commands.describe(member="Member to add")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_ticket_add(interaction: discord.Interaction, member: discord.Member):
        if not await _guard(interaction):
            return
        await interaction.channel.set_permissions(member, read_messages=True, send_messages=True)
        await interaction.response.send_message(f"✅ **Added** {member.mention} to this ticket.", ephemeral=True)

    # ── /ticket-remove ──
    @bot.tree.command(name="ticket-remove", description="Remove a user from the current ticket channel.")
    @app_commands.describe(member="Member to remove")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_ticket_remove(interaction: discord.Interaction, member: discord.Member):
        if not await _guard(interaction):
            return
        await interaction.channel.set_permissions(member, overwrite=None)
        await interaction.response.send_message(f"✅ **Removed** {member.mention} from this ticket.", ephemeral=True)

    # ── /ticket-rename ──
    @bot.tree.command(name="ticket-rename", description="Rename the current ticket channel.")
    @app_commands.describe(name="New channel name")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_ticket_rename(interaction: discord.Interaction, name: str):
        if not await _guard(interaction):
            return
        try:
            await interaction.channel.edit(name=name)
            await interaction.response.send_message(f"✅ **Ticket Renamed** → `{name}`", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to rename this channel.", ephemeral=True)

    # ── /ticket-claim ──
    @bot.tree.command(name="ticket-claim", description="Claim this ticket as the handling staff member.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_ticket_claim(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        await interaction.response.send_message(f"🙋 **Ticket Claimed** — {interaction.user.mention} is now handling this ticket.")
        _dlog("🎫 TICKET-CLAIM", f"{interaction.user} claimed ticket in #{interaction.channel.name}")

    # ── /ticket-unclaim ──
    @bot.tree.command(name="ticket-unclaim", description="Release your claim on this ticket.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_ticket_unclaim(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        await interaction.response.send_message(f"🔓 **Ticket Unclaimed** — {interaction.user.mention} released this ticket.")

    # ── /ticket-reopen ──
    @bot.tree.command(name="ticket-reopen", description="Reopen a closed ticket channel.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_ticket_reopen(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        everyone = interaction.guild.default_role
        await interaction.channel.set_permissions(everyone, read_messages=None, send_messages=None)
        await interaction.response.send_message(f"🔓 **Ticket Reopened** — This channel is active again.", ephemeral=True)
        _dlog("🔓 TICKET-REOPEN", f"{interaction.user} reopened #{interaction.channel.name}")

    # ── /ticket-stats ──
    @bot.tree.command(name="ticket-stats", description="Show ticket statistics for this server.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_ticket_stats(interaction: discord.Interaction):
        if not await _guard(interaction):
            return
        try:
            import tickets as tickets_mod
            all_t   = tickets_mod.get_all_tickets()
            open_t  = [t for t in all_t if t.get("status","open") == "open"]
            close_t = [t for t in all_t if t.get("status","open") == "closed"]
        except Exception:
            all_t, open_t, close_t = [], [], []
        embed = discord.Embed(
            title=f"🎫 Ticket Stats — {interaction.guild.name}",
            color=0x4f8ef7, timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Total",  value=str(len(all_t)),   inline=True)
        embed.add_field(name="Open",   value=str(len(open_t)),  inline=True)
        embed.add_field(name="Closed", value=str(len(close_t)), inline=True)
        embed.set_footer(text="SP4RK Ticket System")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# UI COMPONENTS (Views used by button/select/ticket commands)
# ══════════════════════════════════════════════════════════════════════════════

class _TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="sp4rk:ticket_open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            import tickets as tickets_mod
            import main as _m
            result = await tickets_mod.create_ticket(interaction.guild, interaction.user, _m.config, "Support Request")
            if result:
                channel, ticket = result
                await interaction.followup.send(f"✅ Your ticket is ready: {channel.mention}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Couldn't create ticket — contact a staff member.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: `{e}`", ephemeral=True)


class _ButtonRoleView(discord.ui.View):
    def __init__(self, role: discord.Role, label: str):
        super().__init__(timeout=None)
        self.role = role
        btn = discord.ui.Button(
            label=label, style=discord.ButtonStyle.secondary,
            emoji="🎭", custom_id=f"sp4rk:btnrole:{role.id}"
        )
        btn.callback = self._callback
        self.add_item(btn)

    async def _callback(self, interaction: discord.Interaction):
        member = interaction.user
        if self.role in member.roles:
            await member.remove_roles(self.role)
            await interaction.response.send_message(f"✅ **{self.role.name}** removed.", ephemeral=True)
        else:
            await member.add_roles(self.role)
            await interaction.response.send_message(f"✅ **{self.role.name}** assigned.", ephemeral=True)


class _SelectRoleView(discord.ui.View):
    def __init__(self, roles: list):
        super().__init__(timeout=None)
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles[:25]]
        select = discord.ui.Select(
            placeholder="Choose a role...",
            options=options,
            custom_id="sp4rk:selectrole",
        )
        select.callback = self._callback
        self.add_item(select)
        self._roles = {str(r.id): r for r in roles}

    async def _callback(self, interaction: discord.Interaction):
        role_id = interaction.data["values"][0]
        role = self._roles.get(role_id) or interaction.guild.get_role(int(role_id))
        if not role:
            return await interaction.response.send_message("❌ Role not found.", ephemeral=True)
        member = interaction.user
        if role in member.roles:
            await member.remove_roles(role)
            await interaction.response.send_message(f"✅ **{role.name}** removed.", ephemeral=True)
        else:
            await member.add_roles(role)
            await interaction.response.send_message(f"✅ **{role.name}** assigned.", ephemeral=True)


class _SelfRolePanelView(discord.ui.View):
    def __init__(self, roles: list):
        super().__init__(timeout=None)
        for role in roles[:5]:
            btn = discord.ui.Button(
                label=role.name, style=discord.ButtonStyle.secondary,
                custom_id=f"sp4rk:selfrole:{role.id}"
            )
            btn.callback = self._make_callback(role)
            self.add_item(btn)

    def _make_callback(self, role: discord.Role):
        async def _cb(interaction: discord.Interaction):
            member = interaction.user
            if role in member.roles:
                await member.remove_roles(role)
                await interaction.response.send_message(f"✅ **{role.name}** removed.", ephemeral=True)
            else:
                await member.add_roles(role)
                await interaction.response.send_message(f"✅ **{role.name}** assigned.", ephemeral=True)
        return _cb
