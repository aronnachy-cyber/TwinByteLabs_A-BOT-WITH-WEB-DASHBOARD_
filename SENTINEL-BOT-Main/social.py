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
    welcome_ch_id = (
        config.get("welcome_channel", "").strip()
        or config.get("log_channel", "").strip()
    )
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
