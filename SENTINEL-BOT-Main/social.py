import discord
import re

async def _send_to_channel(bot, channel_id, embed):
    if not channel_id:
        return
    try:
        channel = bot.get_channel(int(channel_id))
        if channel:
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Social alert send error: {e}")

async def check_social_alerts(message, config, bot):
    content = message.content.lower()
    alert_channel_id = config.get("multi_channel", "").strip()

    # YouTube alerts
    if config.get("youtube_alerts") == "ON":
        yt_pattern = r'(youtube\.com/watch|youtu\.be/|youtube\.com/shorts)'
        if re.search(yt_pattern, content):
            notify_msg = config.get("youtube_notify_msg", "📺 A new YouTube video was shared! Check it out:").strip()
            yt_channel_link = config.get("youtube_channel_link", "").strip()
            desc = f"{notify_msg}\n\n**Shared by:** {message.author.mention} in {message.channel.mention}"
            if yt_channel_link:
                desc += f"\n**Channel:** {yt_channel_link}"
            embed = discord.Embed(title="📺 YouTube Video Shared!", description=desc, color=0xff0000)
            embed.add_field(name="Link", value=message.content[:500], inline=False)
            embed.set_footer(text=f"#{message.channel.name}")
            await _send_to_channel(bot, alert_channel_id, embed)

    # Twitter / X alerts
    if config.get("twitter_alerts") == "ON":
        if "twitter.com" in content or "x.com" in content:
            embed = discord.Embed(
                title="🐦 Twitter/X Post Shared!",
                description=f"**{message.author.display_name}** shared a Twitter/X link in {message.channel.mention}",
                color=0x1da1f2
            )
            embed.add_field(name="Message", value=message.content[:500], inline=False)
            await _send_to_channel(bot, alert_channel_id, embed)

    # Discord alerts
    if config.get("discord_alerts") == "ON":
        if "discord.gg/" in content or "discord.com/invite/" in content:
            embed = discord.Embed(
                title="💬 Discord Invite Shared!",
                description=f"**{message.author.display_name}** shared a Discord invite in {message.channel.mention}",
                color=0x5865f2
            )
            embed.add_field(name="Message", value=message.content[:500], inline=False)
            await _send_to_channel(bot, alert_channel_id, embed)


def _ch(channel_id: str) -> str:
    """Return a clickable channel mention, or the raw text if not a numeric ID."""
    cid = channel_id.strip()
    if cid.lstrip("<#>").isdigit():
        numeric = cid.lstrip("<#>")
        return f"<#{numeric}>"
    return cid


async def send_welcome_message(bot, member, config):
    # ── Channel routing ──────────────────────────────────────────────────────
    welcome_ch_id = config.get("welcome_channel", "").strip()
    if not welcome_ch_id:
        return

    try:
        channel = bot.get_channel(int(welcome_ch_id))
        if not channel:
            channel = await bot.fetch_channel(int(welcome_ch_id))
        if not channel:
            return
    except Exception as e:
        print(f"Welcome channel error: {e}")
        return

    # ── Pull config values ────────────────────────────────────────────────────
    dot        = config.get("welcome_dot_emoji",   "🔴").strip()
    arrow      = config.get("welcome_arrow_emoji", "➡️").strip()
    footer_em  = config.get("welcome_footer_emoji","❤️").strip()
    staff_em   = config.get("welcome_staff_emoji", "👦").strip()
    welcome_image = config.get("welcome_image", "").strip()

    # 4 configurable action lines
    lines_cfg = [
        (config.get("welcome_line1_label",   "PLEASE READ").strip(),
         config.get("welcome_line1_channel", "").strip()),
        (config.get("welcome_line2_label",   "PLEASE CHECK").strip(),
         config.get("welcome_line2_channel", "").strip()),
        (config.get("welcome_line3_label",   "ALSO REACT IN").strip(),
         config.get("welcome_line3_channel", "").strip()),
        (config.get("welcome_line4_label",   "CHAT IN").strip(),
         config.get("welcome_line4_channel", "").strip()),
    ]

    footer_msg = config.get("welcome_footer_msg",  "THANKS FOR JOINING US").strip()
    staff_msg  = config.get("welcome_staff_msg",   "FOR ANY HELP CONTACT WITH OUR STAFFS").strip()

    # ── Build description ─────────────────────────────────────────────────────
    desc_lines = [
        f"**Welcome {member.mention} to {member.guild.name}!**",
        "",
    ]

    for label, ch_id in lines_cfg:
        if not label:
            continue
        ch_part = f" {_ch(ch_id)}" if ch_id else ""
        desc_lines.append(f"{dot}  **{label}** {arrow}{ch_part}")

    desc_lines += [
        "",
        f"{footer_em}  **{footer_msg}**  {footer_em}",
        f"**{staff_msg}** {staff_em}",
    ]

    description = "\n".join(desc_lines)

    # ── Build embed ───────────────────────────────────────────────────────────
    embed = discord.Embed(
        description=description,
        color=0xcc1a1a,          # deep red to match the theme
    )

    # Member avatar as thumbnail (corner) so their face is always visible
    embed.set_thumbnail(url=member.display_avatar.replace(size=256).url)

    # Optional custom banner image
    if welcome_image:
        embed.set_image(url=welcome_image)
    elif member.guild.banner:
        embed.set_image(url=member.guild.banner.replace(size=1024).url)

    embed.set_footer(
        text=f"{member.guild.name}  ·  Member #{member.guild.member_count:,}",
        icon_url=member.guild.icon.url if member.guild.icon else None,
    )

    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"Welcome message send error: {e}")


# ---------------------------------------------------------------------------
# Twitch Live Notifications (MEE6-style)
# Polls the Twitch API every 5 minutes via the background task.
#
# Setup env vars:  TWITCH_CLIENT_ID  +  TWITCH_CLIENT_SECRET
# Storage: db_spark.get_blob / set_blob  (scope "twitch_cfg", key=guild_id)
#          db_spark.get_blob / set_blob  (scope "twitch_live", key=guild_id)
#          — tracks which streams are already announced to avoid duplicates.
# ---------------------------------------------------------------------------

import os as _os
import aiohttp as _aiohttp

_TWITCH_CLIENT_ID     = _os.environ.get("TWITCH_CLIENT_ID", "")
_TWITCH_CLIENT_SECRET = _os.environ.get("TWITCH_CLIENT_SECRET", "")
_twitch_token: dict = {}   # {"access_token": ..., "expires_at": float}

try:
    import db_spark as _twdb
    _TWDB = True
except ImportError:
    _twdb = None
    _TWDB = False

_TWITCH_LOCAL: dict = {}


def _tw_load(scope: str, guild_id: str) -> dict:
    if _TWDB and _twdb.is_available():
        d = _twdb.get_blob(scope, guild_id)
        return d if isinstance(d, dict) else {}
    return _TWITCH_LOCAL.get(f"{scope}:{guild_id}", {})


def _tw_save(scope: str, guild_id: str, data: dict) -> None:
    if _TWDB and _twdb.is_available():
        _twdb.set_blob(scope, data, guild_id)
    else:
        _TWITCH_LOCAL[f"{scope}:{guild_id}"] = data


async def _get_twitch_token() -> str:
    """Return a valid Twitch app-access token, refreshing if needed."""
    import time as _t
    global _twitch_token
    if _twitch_token.get("access_token") and _t.time() < _twitch_token.get("expires_at", 0) - 60:
        return _twitch_token["access_token"]
    if not _TWITCH_CLIENT_ID or not _TWITCH_CLIENT_SECRET:
        return ""
    try:
        async with _aiohttp.ClientSession() as s:
            async with s.post(
                "https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": _TWITCH_CLIENT_ID,
                    "client_secret": _TWITCH_CLIENT_SECRET,
                    "grant_type": "client_credentials",
                },
                timeout=_aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status != 200:
                    return ""
                data = await r.json()
        _twitch_token = {
            "access_token": data["access_token"],
            "expires_at": _t.time() + data.get("expires_in", 3600),
        }
        return _twitch_token["access_token"]
    except Exception as e:
        print(f"[twitch] token error: {e}")
        return ""


async def _fetch_live_streams(usernames: list) -> list:
    """Return a list of live-stream dicts for the given Twitch usernames."""
    if not usernames:
        return []
    token = await _get_twitch_token()
    if not token:
        return []
    try:
        params = [("user_login", u.lower().strip()) for u in usernames[:10]]
        headers = {"Client-ID": _TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}
        async with _aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.twitch.tv/helix/streams",
                params=params,
                headers=headers,
                timeout=_aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return []
                body = await r.json()
        return body.get("data", [])
    except Exception as e:
        print(f"[twitch] streams fetch error: {e}")
        return []


async def check_twitch_alerts(bot) -> None:
    """Poll Twitch for each guild's tracked streamers; announce new live streams."""
    for guild in bot.guilds:
        gid = str(guild.id)
        cfg = _tw_load("twitch_cfg", gid)
        if not cfg:
            continue
        ch_id = cfg.get("channel_id")
        streamers = cfg.get("streamers", [])
        if not ch_id or not streamers:
            continue

        live_data = await _fetch_live_streams(streamers)
        already_live = _tw_load("twitch_live", gid)
        new_live = {}

        for stream in live_data:
            login = stream.get("user_login", "").lower()
            stream_id = stream.get("id", "")
            new_live[login] = stream_id

            if already_live.get(login) == stream_id:
                continue  # already announced this stream session

            channel = guild.get_channel(int(ch_id))
            if not channel:
                continue

            game = stream.get("game_name") or "Unknown game"
            title = stream.get("title") or "No title"
            viewers = stream.get("viewer_count", 0)
            thumb = (stream.get("thumbnail_url") or "")
            if thumb:
                thumb = thumb.replace("{width}", "440").replace("{height}", "248")

            embed = discord.Embed(
                title=f"🔴 {stream['user_name']} is now LIVE!",
                url=f"https://twitch.tv/{login}",
                description=f"**{title}**",
                color=0x9146ff,
            )
            embed.add_field(name="🎮 Game", value=game, inline=True)
            embed.add_field(name="👀 Viewers", value=str(viewers), inline=True)
            if thumb:
                embed.set_image(url=thumb)
            embed.set_footer(text="SP4RK Twitch Alerts • twitch.tv/" + login)
            try:
                await channel.send(embed=embed)
            except Exception as e:
                print(f"[twitch] announce error: {e}")

        _tw_save("twitch_live", gid, new_live)


# Slash commands for Twitch configuration
import discord as _dc
from discord import app_commands as _apc
from typing import Optional as _Opt

twitch_group = _apc.Group(name="twitch", description="🟣 Twitch live notifications")


@twitch_group.command(name="add", description="Add a Twitch streamer to track")
@_apc.describe(username="Twitch username to track")
async def twitch_add(interaction: _dc.Interaction, username: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    gid = str(interaction.guild_id)
    cfg = _tw_load("twitch_cfg", gid)
    streamers = cfg.get("streamers", [])
    uname = username.lower().strip()
    if uname in streamers:
        await interaction.response.send_message(f"⚠️ `{uname}` is already being tracked.", ephemeral=True)
        return
    if len(streamers) >= 10:
        await interaction.response.send_message("❌ Max 10 streamers per server.", ephemeral=True)
        return
    streamers.append(uname)
    cfg["streamers"] = streamers
    _tw_save("twitch_cfg", gid, cfg)
    await interaction.response.send_message(f"✅ Now tracking **{uname}** on Twitch.", ephemeral=True)


@twitch_group.command(name="remove", description="Stop tracking a Twitch streamer")
@_apc.describe(username="Twitch username to remove")
async def twitch_remove(interaction: _dc.Interaction, username: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    gid = str(interaction.guild_id)
    cfg = _tw_load("twitch_cfg", gid)
    uname = username.lower().strip()
    streamers = [s for s in cfg.get("streamers", []) if s != uname]
    cfg["streamers"] = streamers
    _tw_save("twitch_cfg", gid, cfg)
    await interaction.response.send_message(f"✅ Removed **{uname}** from Twitch tracking.", ephemeral=True)


@twitch_group.command(name="channel", description="[Admin] Set the channel for Twitch alerts")
@_apc.describe(channel="Channel to post Twitch live alerts in")
async def twitch_channel(interaction: _dc.Interaction, channel: _dc.TextChannel):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    gid = str(interaction.guild_id)
    cfg = _tw_load("twitch_cfg", gid)
    cfg["channel_id"] = str(channel.id)
    _tw_save("twitch_cfg", gid, cfg)
    await interaction.response.send_message(f"✅ Twitch alerts will post in {channel.mention}.", ephemeral=True)


@twitch_group.command(name="list", description="Show tracked Twitch streamers")
async def twitch_list(interaction: _dc.Interaction):
    gid = str(interaction.guild_id)
    cfg = _tw_load("twitch_cfg", gid)
    streamers = cfg.get("streamers", [])
    ch_id = cfg.get("channel_id")
    ch_txt = f"<#{ch_id}>" if ch_id else "not set"
    if not streamers:
        await interaction.response.send_message(
            f"🟣 No streamers tracked. Use `/twitch add <username>` to add one.\nAlert channel: {ch_txt}",
            ephemeral=True
        )
        return
    lines = "\n".join(f"• [{s}](https://twitch.tv/{s})" for s in streamers)
    embed = _dc.Embed(title="🟣 Tracked Twitch Streamers", description=lines, color=0x9146ff)
    embed.add_field(name="Alert Channel", value=ch_txt, inline=True)
    embed.set_footer(text="SP4RK Twitch Alerts")
    await interaction.response.send_message(embed=embed)
