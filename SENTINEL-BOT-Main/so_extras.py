"""
Server Official extras — Dyno / Carl-bot signature features.

Adds two big features that complete the "modern community bot" feel:

    1. STARBOARD     — When a message gets ⭐ N times it's reposted to a
                       designated #starboard channel (Carl-bot/Dyno classic).
    2. EMBED BUILDER — `/embed` slash command for posting rich embeds
                       without needing the dashboard or webhooks.

Both are stored inside the existing per-guild `guild_configs` block in
`server_official_state.json`, so they survive restarts and ride the same
Supabase mirror as the rest of Server Official's data.

Wire-up:  call `setup(bot)` from `server_official.py` after the bot is
created (just below the slash-command definitions, near the end).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands

logger = logging.getLogger(__name__)


def setup(bot, load_state, save_state, get_guild_config, set_guild_config) -> None:
    """Attach Starboard + Embed Builder to the Server Official bot.

    The state-access helpers are passed in so we don't have to reach back
    into `server_official.py` (avoids any import cycle).
    """

    # ============================================================
    # ⭐ STARBOARD
    # ============================================================

    @bot.tree.command(
        name="setup-starboard",
        description="Configure the starboard: messages reaching N ⭐ are reposted here",
    )
    @app_commands.describe(
        channel="Channel to repost starred messages into",
        threshold="How many ⭐ reactions are needed (1-25)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_starboard(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        threshold: app_commands.Range[int, 1, 25] = 3,
    ):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Server only.", ephemeral=True)
        cfg = get_guild_config(str(interaction.guild.id))
        cfg["starboard_channel"] = str(channel.id)
        cfg["starboard_threshold"] = int(threshold)
        cfg.setdefault("starboard_posted", {})  # original_msg_id -> star_msg_id
        set_guild_config(str(interaction.guild.id), cfg)
        await interaction.response.send_message(
            f"⭐ Starboard configured.\n"
            f"• Channel: {channel.mention}\n"
            f"• Threshold: **{threshold}** ⭐ reaction(s)\n\n"
            f"_React to any message with ⭐ — once it hits the threshold I'll repost it here._"
        )

    @bot.tree.command(
        name="disable-starboard",
        description="Turn off the starboard for this server",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable_starboard(interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Server only.", ephemeral=True)
        cfg = get_guild_config(str(interaction.guild.id))
        cfg.pop("starboard_channel", None)
        cfg.pop("starboard_threshold", None)
        # Keep starboard_posted so a re-enable doesn't double-post old stars.
        set_guild_config(str(interaction.guild.id), cfg)
        await interaction.response.send_message("🛑 Starboard disabled for this server.")

    @bot.tree.command(
        name="starboard-stats",
        description="Show starboard configuration and stats",
    )
    async def starboard_stats(interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Server only.", ephemeral=True)
        cfg = get_guild_config(str(interaction.guild.id))
        ch_id = cfg.get("starboard_channel")
        threshold = cfg.get("starboard_threshold", 3)
        posted = cfg.get("starboard_posted", {}) or {}
        embed = discord.Embed(title="⭐ Starboard", color=0xfacc15)
        if not ch_id:
            embed.description = "_Not configured. Use `/setup-starboard` to enable._"
        else:
            embed.add_field(
                name="Channel",
                value=f"<#{ch_id}>",
                inline=True,
            )
            embed.add_field(name="Threshold", value=f"**{threshold}** ⭐", inline=True)
            embed.add_field(name="Messages Starred", value=f"**{len(posted):,}**", inline=True)
        await interaction.response.send_message(embed=embed)

    @bot.listen("on_raw_reaction_add")
    async def _starboard_listener(payload: discord.RawReactionActionEvent):
        # Filter quickly so we add zero overhead to non-star reactions.
        if str(payload.emoji) != "⭐":
            return
        if not payload.guild_id:
            return
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        cfg = get_guild_config(str(guild.id))
        ch_id = cfg.get("starboard_channel")
        if not ch_id:
            return
        threshold = int(cfg.get("starboard_threshold", 3))
        posted = cfg.setdefault("starboard_posted", {})
        # Avoid reposting if already starboarded.
        if str(payload.message_id) in posted:
            return
        try:
            chan = guild.get_channel(payload.channel_id)
            if not chan:
                return
            message = await chan.fetch_message(payload.message_id)
        except Exception as e:
            logger.warning(f"[starboard] fetch failed: {e}")
            return
        # Don't starboard the bot's own messages or messages from inside the
        # starboard channel itself (avoid infinite mirror loops).
        if message.author.bot or str(message.channel.id) == ch_id:
            return
        # Count current ⭐ reactions (humans only).
        star_reaction = None
        for r in message.reactions:
            if str(r.emoji) == "⭐":
                star_reaction = r
                break
        if not star_reaction or star_reaction.count < threshold:
            return
        # Build the starboard embed.
        star_chan = guild.get_channel(int(ch_id))
        if not star_chan:
            return
        import embeds as _emb
        embed = _emb.render(
            "so.starboard",
            guild_id=guild.id,
            content=(message.content[:2000] if message.content else ""),
            jump_url=message.jump_url,
            channel=message.channel,
            star_count=star_reaction.count,
            message_id=message.id,
        )
        # Author + timestamp + image are runtime-only (not template-overridable).
        embed.timestamp = message.created_at
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url,
        )
        if message.attachments:
            for att in message.attachments:
                ct = (att.content_type or "").lower()
                if ct.startswith("image/") or att.filename.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp")
                ):
                    embed.set_image(url=att.url)
                    break
        try:
            star_msg = await star_chan.send(
                content=f"⭐ **{star_reaction.count}** · {message.channel.mention}",
                embed=embed,
            )
            posted[str(payload.message_id)] = str(star_msg.id)
            set_guild_config(str(guild.id), cfg)
        except discord.Forbidden:
            logger.warning(f"[starboard] no perms to post in #{star_chan.name}")
        except Exception as e:
            logger.warning(f"[starboard] post failed: {e}")

    # ============================================================
    # 🎨 EMBED BUILDER  (/embed)
    # ============================================================

    def _parse_color(raw: Optional[str]) -> int:
        """Accept #RRGGBB / RRGGBB / int and named colors."""
        if not raw:
            return 0x4f8ef7
        raw = raw.strip().lstrip("#")
        named = {
            "red": 0xed4245, "green": 0x57f287, "blue": 0x5865f2,
            "yellow": 0xfee75c, "purple": 0xa970ff, "orange": 0xfb923c,
            "pink": 0xeb459e, "gold": 0xfacc15, "white": 0xffffff,
            "black": 0x000000, "blurple": 0x5865f2, "cyan": 0x06b6d4,
        }
        if raw.lower() in named:
            return named[raw.lower()]
        try:
            return int(raw, 16) if not raw.isdigit() else int(raw)
        except Exception:
            return 0x4f8ef7

    @bot.tree.command(
        name="embed",
        description="Build and post a rich embed (Carl-bot style — no dashboard needed)",
    )
    @app_commands.describe(
        channel="Where to post the embed (defaults to current channel)",
        title="Title text (top of the embed)",
        description="Main body — supports markdown and \\n for new lines",
        color="hex like #5865f2, an int, or a name (red, blue, gold, blurple…)",
        image_url="Big image at the bottom",
        thumbnail_url="Small image in the top-right corner",
        author_name="Small author line above the title",
        author_icon_url="Tiny icon next to the author line",
        footer="Footer text at the bottom",
        url="Make the title a clickable link",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def embed_builder(
        interaction: discord.Interaction,
        title: str = "",
        description: str = "",
        channel: Optional[discord.TextChannel] = None,
        color: Optional[str] = None,
        image_url: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        author_name: Optional[str] = None,
        author_icon_url: Optional[str] = None,
        footer: Optional[str] = None,
        url: Optional[str] = None,
    ):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Server only.", ephemeral=True)
        if not (title or description or image_url):
            return await interaction.response.send_message(
                "❌ Provide at least a title, description, or image.", ephemeral=True
            )
        target = channel or interaction.channel
        # `\n` literal in the slash arg should produce real newlines.
        clean_desc = description.replace("\\n", "\n") if description else ""
        embed = discord.Embed(
            title=title[:256] if title else None,
            description=clean_desc[:4000] if clean_desc else None,
            color=_parse_color(color),
            url=url if url else None,
        )
        if author_name:
            try:
                embed.set_author(
                    name=author_name[:256],
                    icon_url=author_icon_url if author_icon_url else None,
                )
            except Exception:
                embed.set_author(name=author_name[:256])
        if image_url:
            try:
                embed.set_image(url=image_url)
            except Exception:
                pass
        if thumbnail_url:
            try:
                embed.set_thumbnail(url=thumbnail_url)
            except Exception:
                pass
        if footer:
            embed.set_footer(text=footer[:2048])
        try:
            await target.send(embed=embed)
            await interaction.response.send_message(
                f"✅ Embed posted in {target.mention}.", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ I can't send messages in {target.mention}.", ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"❌ Discord rejected the embed (probably bad image URL or too long): `{e}`",
                ephemeral=True,
            )

    print("[so_extras] Loaded: starboard, embed builder")
