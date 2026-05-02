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
            embed = discord.Embed(
                title="📺 YouTube Video Shared!",
                description=desc,
                color=0xff0000
            )
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

    # Discord alerts (external invite links shared)
    if config.get("discord_alerts") == "ON":
        if "discord.gg/" in content or "discord.com/invite/" in content:
            embed = discord.Embed(
                title="💬 Discord Invite Shared!",
                description=f"**{message.author.display_name}** shared a Discord invite in {message.channel.mention}",
                color=0x5865f2
            )
            embed.add_field(name="Message", value=message.content[:500], inline=False)
            await _send_to_channel(bot, alert_channel_id, embed)

async def send_welcome_message(bot, member, config):
    welcome_msg     = config.get("welcome_msg", "").strip()
    # welcome_channel takes priority; fall back to log_channel for backwards compat
    welcome_ch_id   = config.get("welcome_channel", "").strip() or config.get("log_channel", "").strip()
    welcome_image   = config.get("welcome_image", "").strip()

    if not welcome_msg or not welcome_ch_id:
        return

    formatted = (
        welcome_msg
        .replace("{user}",     member.mention)
        .replace("{username}", member.display_name)
        .replace("{server}",   member.guild.name)
    )

    try:
        channel = bot.get_channel(int(welcome_ch_id))
        if not channel:
            channel = await bot.fetch_channel(int(welcome_ch_id))
        if not channel:
            return

        member_count = member.guild.member_count

        embed = discord.Embed(
            description=(
                f"## 👋 Hey {member.mention}, welcome to **{member.guild.name}**!\n\n"
                f"{formatted}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📜 **Read the rules** before you start chatting!\n"
                f"🎮 Introduce yourself and make some friends!\n"
                f"🚀 Enjoy your stay — we're happy you're here!\n"
            ),
            color=0x5b8cff,
        )

        # Large avatar as the main image so it looks vivid and personal
        embed.set_image(url=member.display_avatar.replace(size=512).url)

        # Small server icon in thumbnail corner
        if member.guild.icon:
            embed.set_thumbnail(url=member.guild.icon.url)

        embed.add_field(
            name="👤 Member",
            value=member.mention,
            inline=True,
        )
        embed.add_field(
            name="🎉 You are member",
            value=f"**#{member_count:,}**",
            inline=True,
        )

        # Optional custom banner below everything
        if welcome_image:
            embed.set_image(url=welcome_image)

        embed.set_footer(
            text=f"{member.guild.name}  ·  Powered by SP4RK",
            icon_url=member.guild.icon.url if member.guild.icon else None,
        )

        await channel.send(embed=embed)
    except Exception as e:
        print(f"Welcome message error: {e}")
