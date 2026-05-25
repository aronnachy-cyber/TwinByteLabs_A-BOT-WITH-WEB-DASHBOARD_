"""
testcord.py — Testcord: A dedicated bot for testing all SP4RK / Byte Labs commands.

Run with:
    python testcord.py

Requires:
    TESTCORD_TOKEN  — Discord bot token for the Testcord test bot
    TEST_GUILD_ID   — (optional) Guild ID to sync slash commands instantly

Every command here mirrors a real bot command but runs in isolation so you
can test without touching your live servers.
"""

import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
import threading
from datetime import datetime, timedelta, timezone

TESTCORD_TOKEN = os.environ.get("TESTCORD_TOKEN", "").strip()
TEST_GUILD_ID  = os.environ.get("TEST_GUILD_ID",  "").strip()

if not TESTCORD_TOKEN:
    print("❌ TESTCORD_TOKEN not set — set it in your environment secrets.")
    raise SystemExit(1)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="t!", intents=intents, help_command=None)

# ─── Ready ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    guild_obj = discord.Object(id=int(TEST_GUILD_ID)) if TEST_GUILD_ID else None
    if guild_obj:
        bot.tree.copy_global_to(guild=guild_obj)
        await bot.tree.sync(guild=guild_obj)
        print(f"✅ Testcord ready as {bot.user} — slash commands synced to guild {TEST_GUILD_ID}")
    else:
        await bot.tree.sync()
        print(f"✅ Testcord ready as {bot.user} — slash commands synced globally (may take up to 1 hour)")

# ─── Helper ───────────────────────────────────────────────────────────────────

def _embed(title: str, description: str = "", color: int = 0x3a8fff) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.utcnow())
    e.set_footer(text="Testcord — SP4RK command tester")
    return e

# ─── PREFIX COMMANDS ──────────────────────────────────────────────────────────

@bot.command(name="ping")
async def prefix_ping(ctx):
    """Check if Testcord is alive."""
    latency = round(bot.latency * 1000)
    await ctx.send(embed=_embed("🏓 Pong!", f"Latency: **{latency}ms**", 0x2ecc71))


@bot.command(name="echo")
async def prefix_echo(ctx, *, text: str = "Hello from Testcord!"):
    """Echo a message back."""
    await ctx.send(embed=_embed("📢 Echo", discord.utils.escape_markdown(text[:500])))


@bot.command(name="userinfo")
async def prefix_userinfo(ctx, member: discord.Member = None):
    """Show info about a user."""
    member = member or ctx.author
    e = _embed(f"👤 {member}", color=0x3498db)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="ID",      value=str(member.id),              inline=True)
    e.add_field(name="Joined",  value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "—", inline=True)
    e.add_field(name="Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    e.add_field(name="Top Role", value=member.top_role.mention,    inline=True)
    e.add_field(name="Roles",   value=str(len(member.roles) - 1),  inline=True)
    e.add_field(name="Bot?",    value="✅" if member.bot else "❌",  inline=True)
    await ctx.send(embed=e)


@bot.command(name="serverinfo")
async def prefix_serverinfo(ctx):
    """Show info about the server."""
    g = ctx.guild
    e = _embed(f"🏠 {g.name}", color=0x9b59b6)
    e.set_thumbnail(url=g.icon.url if g.icon else discord.Embed.Empty)
    e.add_field(name="Members",  value=str(g.member_count),         inline=True)
    e.add_field(name="Channels", value=str(len(g.channels)),        inline=True)
    e.add_field(name="Roles",    value=str(len(g.roles)),           inline=True)
    e.add_field(name="Owner",    value=str(g.owner),                inline=True)
    e.add_field(name="Created",  value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
    e.add_field(name="Boost Tier", value=str(g.premium_tier),       inline=True)
    await ctx.send(embed=e)


@bot.command(name="avatar")
async def prefix_avatar(ctx, member: discord.Member = None):
    """Show a user's avatar."""
    member = member or ctx.author
    e = _embed(f"🖼️ Avatar — {member}", color=0x1abc9c)
    e.set_image(url=member.display_avatar.url)
    await ctx.send(embed=e)


@bot.command(name="roles")
async def prefix_roles(ctx, member: discord.Member = None):
    """List all roles of a member."""
    member = member or ctx.author
    role_list = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    desc = " ".join(role_list[:20]) or "_No roles_"
    if len(role_list) > 20:
        desc += f"\n…and {len(role_list) - 20} more"
    await ctx.send(embed=_embed(f"🎖️ Roles — {member}", desc, 0xe67e22))


@bot.command(name="warn")
@commands.has_permissions(manage_messages=True)
async def prefix_warn_test(ctx, member: discord.Member, *, reason: str = "Test warning"):
    """Simulate a warning (test only — not saved to real database)."""
    e = _embed("⚠️ Warning Issued (TEST)",
               f"**Target:** {member.mention}\n**Reason:** {reason}\n**By:** {ctx.author.mention}\n\n"
               "⚠️ *This is a test — no data was saved.*", 0xf39c12)
    await ctx.send(embed=e)


@bot.command(name="timeout")
@commands.has_permissions(moderate_members=True)
async def prefix_timeout_test(ctx, member: discord.Member, duration: str = "1m", *, reason: str = "Test timeout"):
    """Simulate a timeout (test only — not applied)."""
    e = _embed("⏱️ Timeout (TEST)",
               f"**Target:** {member.mention}\n**Duration:** {duration}\n"
               f"**Reason:** {reason}\n**By:** {ctx.author.mention}\n\n"
               "⚠️ *This is a test — no timeout was applied.*", 0xe74c3c)
    await ctx.send(embed=e)


@bot.command(name="embed")
async def prefix_embed_test(ctx, color: str = "#3498db", *, text: str = "Test embed content"):
    """Test embed rendering with a custom color."""
    try:
        col = int(color.lstrip("#"), 16)
    except ValueError:
        col = 0x3498db
    e = discord.Embed(description=text[:2000], color=col, timestamp=datetime.utcnow())
    e.set_footer(text="Testcord embed test")
    await ctx.send(embed=e)


@bot.command(name="help")
async def prefix_help(ctx):
    """Show all Testcord commands."""
    e = _embed("📖 Testcord Commands", color=0x3a8fff)
    e.add_field(name="📌 Prefix (t!)", value=(
        "`t!ping` — latency check\n"
        "`t!echo [text]` — echo a message\n"
        "`t!userinfo [@user]` — user details\n"
        "`t!serverinfo` — server details\n"
        "`t!avatar [@user]` — show avatar\n"
        "`t!roles [@user]` — list roles\n"
        "`t!warn @user [reason]` — simulate warn\n"
        "`t!timeout @user [dur] [reason]` — simulate timeout\n"
        "`t!embed [#color] [text]` — test embed\n"
        "`t!help` — this menu"
    ), inline=False)
    e.add_field(name="🔷 Slash (/tc-*)", value=(
        "`/tc-ping` — latency check\n"
        "`/tc-userinfo` — user info\n"
        "`/tc-warn` — simulate a warn\n"
        "`/tc-ban` — simulate a ban\n"
        "`/tc-kick` — simulate a kick\n"
        "`/tc-timeout` — simulate timeout\n"
        "`/tc-embed` — test embed builder\n"
        "`/tc-logs` — test log channel output\n"
        "`/tc-captcha` — captcha config test\n"
        "`/tc-automod` — automod status test"
    ), inline=False)
    await ctx.send(embed=e)

# ─── SLASH COMMANDS ───────────────────────────────────────────────────────────

@bot.tree.command(name="tc-ping", description="Check Testcord latency.")
async def slash_ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(
        embed=_embed("🏓 Pong!", f"Latency: **{latency}ms**", 0x2ecc71), ephemeral=True
    )


@bot.tree.command(name="tc-userinfo", description="Show information about a user.")
@app_commands.describe(member="The user to look up (defaults to you)")
async def slash_userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    e = _embed(f"👤 {member}", color=0x3498db)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="ID",       value=str(member.id),              inline=True)
    e.add_field(name="Joined",   value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "—", inline=True)
    e.add_field(name="Created",  value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    e.add_field(name="Top Role", value=member.top_role.mention,     inline=True)
    e.add_field(name="Roles",    value=str(len(member.roles) - 1),  inline=True)
    e.add_field(name="Bot?",     value="✅" if member.bot else "❌",  inline=True)
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="tc-warn", description="Simulate issuing a warning (test only, nothing is saved).")
@app_commands.describe(member="Target member", reason="Reason for the warning")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "Test violation"):
    e = _embed("⚠️ Warning Issued (TEST)",
               f"**Target:** {member.mention}\n**Reason:** {reason}\n"
               f"**By:** {interaction.user.mention}\n\n⚠️ *Test only — not saved.*", 0xf39c12)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="tc-ban", description="Simulate a ban (test only — member is NOT actually banned).")
@app_commands.describe(member="Target member", reason="Reason for ban")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "Test ban"):
    e = _embed("⛔ Ban (TEST)",
               f"**Target:** {member.mention}\n**Reason:** {reason}\n"
               f"**By:** {interaction.user.mention}\n\n⚠️ *Test only — no ban was applied.*", 0xe74c3c)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="tc-kick", description="Simulate a kick (test only — member is NOT actually kicked).")
@app_commands.describe(member="Target member", reason="Reason for kick")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "Test kick"):
    e = _embed("🚪 Kick (TEST)",
               f"**Target:** {member.mention}\n**Reason:** {reason}\n"
               f"**By:** {interaction.user.mention}\n\n⚠️ *Test only — no kick was applied.*", 0xe67e22)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="tc-timeout", description="Simulate a timeout (test only — NOT applied).")
@app_commands.describe(member="Target member", duration="Duration (e.g. 10m, 1h, 1d)", reason="Reason")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_timeout(interaction: discord.Interaction, member: discord.Member,
                        duration: str = "10m", reason: str = "Test timeout"):
    e = _embed("⏱️ Timeout (TEST)",
               f"**Target:** {member.mention}\n**Duration:** `{duration}`\n"
               f"**Reason:** {reason}\n**By:** {interaction.user.mention}\n\n"
               "⚠️ *Test only — no timeout was applied.*", 0xe74c3c)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="tc-embed", description="Test embed builder — preview how an embed will look.")
@app_commands.describe(
    title="Embed title",
    description="Embed description",
    color="Hex color code (e.g. #3498db)",
)
async def slash_embed(interaction: discord.Interaction,
                      title: str = "Test Embed",
                      description: str = "This is a test embed from Testcord.",
                      color: str = "#3498db"):
    try:
        col = int(color.lstrip("#"), 16)
    except ValueError:
        col = 0x3498db
    e = discord.Embed(title=title, description=description, color=col, timestamp=datetime.utcnow())
    e.set_footer(text="Testcord embed preview")
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="tc-logs", description="Test that a log channel works — sends a sample log embed to the channel.")
@app_commands.describe(channel="Channel to send the test log to")
@app_commands.checks.has_permissions(manage_guild=True)
async def slash_logs_test(interaction: discord.Interaction, channel: discord.TextChannel):
    test_embed = discord.Embed(
        title="📋 Test Log Entry",
        description=(
            "This is a **test log** sent by Testcord.\n"
            "If you see this in your log channel, logging is working correctly! ✅\n\n"
            f"Triggered by: {interaction.user.mention}"
        ),
        color=0x2ecc71,
        timestamp=datetime.utcnow(),
    )
    test_embed.set_footer(text="Testcord — log channel test")
    try:
        await channel.send(embed=test_embed)
        await interaction.response.send_message(
            f"✅ Test log sent to {channel.mention} — check that channel to confirm it's working.", ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ I don't have permission to send messages in {channel.mention}. "
            "Make sure I have **Send Messages** and **Embed Links** permissions there.", ephemeral=True
        )


@bot.tree.command(name="tc-captcha", description="Preview the captcha setup command response.")
@app_commands.describe(
    enabled="Enable or disable captcha",
    channel="Verification channel",
    role="Verified role",
)
@app_commands.checks.has_permissions(administrator=True)
async def slash_captcha_test(interaction: discord.Interaction,
                              enabled: bool = True,
                              channel: discord.TextChannel = None,
                              role: discord.Role = None):
    state = "ON" if enabled else "OFF"
    emoji = "✅" if enabled else "🔴"
    lines = [f"{emoji} **Captcha Verification** would be set to **{state}** on the main bot."]
    if channel:
        lines.append(f"> 📢 **Verification Channel:** {channel.mention}")
    else:
        lines.append("> ⚠️ No verification channel provided")
    if role:
        lines.append(f"> 🎖️ **Verified Role:** {role.mention}")
    else:
        lines.append("> ⚠️ No verified role provided")
    lines.append("\n*This is a test — nothing was saved. Use `/captcha setup` on the main SP4RK bot.*")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="tc-automod", description="Preview automod status for this server.")
@app_commands.checks.has_permissions(manage_guild=True)
async def slash_automod_test(interaction: discord.Interaction):
    e = _embed("🛡️ Automod Status Preview (TEST)", color=0x3498db)
    e.add_field(name="Spam Filter",    value="OFF (test)", inline=True)
    e.add_field(name="Link Blocker",   value="OFF (test)", inline=True)
    e.add_field(name="Invite Blocker", value="OFF (test)", inline=True)
    e.add_field(name="Caps Filter",    value="OFF (test)", inline=True)
    e.add_field(name="Profanity",      value="low (test)", inline=True)
    e.add_field(name="Anti-Raid",      value="OFF (test)", inline=True)
    e.description = (
        "This shows what automod fields look like on the main bot.\n"
        "Use `/automod-setup` on SP4RK to configure the real settings."
    )
    await interaction.response.send_message(embed=e, ephemeral=True)


# ─── Error handler ─────────────────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ An error occurred: `{error}`", ephemeral=True
        )


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found. Mention them or use their ID.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(f"❌ Error: `{error}`")


# ─── Thread management (used by dashboard /dev/testcord/start|stop) ────────

_bot_thread: threading.Thread | None = None
_bot_log: list[str] = []          # ring buffer — last 100 lines
_MAX_LOG = 100


def _log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    _bot_log.append(line)
    if len(_bot_log) > _MAX_LOG:
        _bot_log.pop(0)


def _run_bot():
    """Target for the bot thread — runs the discord.py event loop."""
    if not TESTCORD_TOKEN:
        _log("❌ TESTCORD_TOKEN not set — cannot start.")
        return
    _log("🚀 Testcord starting…")
    try:
        bot.run(TESTCORD_TOKEN, reconnect=True, log_handler=None)
    except Exception as e:
        _log(f"❌ Bot crashed: {e}")
    _log("⏹ Testcord stopped.")


def start_bot_thread() -> bool:
    """Start the Testcord bot in a daemon thread. Returns True if started."""
    global _bot_thread
    if _bot_thread and _bot_thread.is_alive():
        return False  # already running
    if not TESTCORD_TOKEN:
        return False
    _bot_thread = threading.Thread(target=_run_bot, name="TestcordBot", daemon=True)
    _bot_thread.start()
    return True


def stop_bot_thread() -> bool:
    """Ask the bot to shut down. Returns True if a shutdown was requested."""
    global _bot_thread
    if not (_bot_thread and _bot_thread.is_alive()):
        return False
    try:
        loop = bot.loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(bot.close(), loop)
            return True
    except Exception:
        pass
    return False


def is_running() -> bool:
    return bool(_bot_thread and _bot_thread.is_alive())


def get_log() -> list[str]:
    return list(_bot_log)


# ─── Entry ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🚀 Starting Testcord...")
    bot.run(TESTCORD_TOKEN)
