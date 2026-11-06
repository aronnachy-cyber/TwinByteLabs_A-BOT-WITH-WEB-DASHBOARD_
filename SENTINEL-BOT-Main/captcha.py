"""NVD Official CAPTCHA verification and new-member channel gating.

Uses a dedicated NVD Unverified role so existing members are not affected.
The selected Verified role remains entirely administrator-managed.
"""
from __future__ import annotations

import asyncio
import random
import time

import discord

_VIEW_CUSTOM_ID = "nvd:captcha:verify:v1"
_view_registered = False
_attempts: dict[tuple[int, int], tuple[int, float]] = {}
_LOCKOUT_SECONDS = 600
_MAX_ATTEMPTS = 5


def _guild_config(guild_id: int) -> dict:
    try:
        from dashboard import get_guild_config
        cfg = get_guild_config(str(guild_id))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        try:
            import main
            return main.get_config_for_guild(str(guild_id))
        except Exception:
            return {}


class CaptchaModal(discord.ui.Modal):
    def __init__(self, question: str, answer: int):
        super().__init__(title="Human verification")
        self.expected_answer = str(answer)
        self.answer = discord.ui.TextInput(
            label=f"Solve: {question}",
            placeholder="Enter the number",
            min_length=1,
            max_length=3,
            required=True,
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "This verification can only be completed inside the server.", ephemeral=True
            )

        key = (guild.id, member.id)
        count, locked_until = _attempts.get(key, (0, 0.0))
        now = time.monotonic()
        if locked_until > now:
            seconds = max(1, int(locked_until - now))
            return await interaction.response.send_message(
                f"Too many attempts. Try again in about {seconds} seconds.", ephemeral=True
            )

        if str(self.answer.value).strip() != self.expected_answer:
            count += 1
            if count >= _MAX_ATTEMPTS:
                _attempts[key] = (0, now + _LOCKOUT_SECONDS)
                return await interaction.response.send_message(
                    "That code was incorrect. Verification is paused for 10 minutes; try again later.",
                    ephemeral=True,
                )
            _attempts[key] = (count, 0.0)
            return await interaction.response.send_message(
                f"Incorrect code. Attempts remaining: {_MAX_ATTEMPTS - count}. Press Verify to try again.",
                ephemeral=True,
            )

        cfg = _guild_config(guild.id)
        if str(cfg.get("captcha_enabled", "OFF")).upper() != "ON":
            return await interaction.response.send_message(
                "Verification is currently disabled by the server staff.", ephemeral=True
            )

        try:
            verified_id = int(str(cfg.get("captcha_role", "")).strip())
            pending_id = int(str(cfg.get("captcha_pending_role", "")).strip())
            verified_role = guild.get_role(verified_id)
            pending_role = guild.get_role(pending_id)
            if verified_role is None or pending_role is None:
                raise RuntimeError("Configured verification roles are missing.")
            bot_member = guild.me
            if bot_member is None or verified_role >= bot_member.top_role or pending_role >= bot_member.top_role:
                raise RuntimeError("Move the NVD bot role above both verification roles.")
            await member.add_roles(verified_role, reason="NVD CAPTCHA passed")
            if pending_role in member.roles:
                await member.remove_roles(pending_role, reason="NVD CAPTCHA passed")
            _attempts.pop(key, None)
            await interaction.response.send_message(
                f"✅ Verification successful! You now have access to the server. Welcome, {member.mention}!",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I couldn't update your roles. Please ask a server administrator to check my Manage Roles permission and role position.",
                ephemeral=True,
            )
        except Exception as exc:
            print(f"[CAPTCHA] Verification failed in {guild.id} for {member.id}: {exc}")
            await interaction.response.send_message(
                "Verification could not be completed. Please contact a server administrator.",
                ephemeral=True,
            )


class CaptchaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        emoji="🔐",
        custom_id=_VIEW_CUSTOM_ID,
    )
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "Use this button inside the server.", ephemeral=True
            )
        cfg = _guild_config(interaction.guild.id)
        if str(cfg.get("captcha_enabled", "OFF")).upper() != "ON":
            return await interaction.response.send_message(
                "Verification is currently disabled.", ephemeral=True
            )
        if interaction.user.bot:
            return await interaction.response.send_message(
                "Bot accounts cannot complete human verification.", ephemeral=True
            )
        try:
            verified_id = int(str(cfg.get("captcha_role", "")).strip())
            pending_id = int(str(cfg.get("captcha_pending_role", "")).strip())
            if any(role.id == verified_id for role in interaction.user.roles):
                return await interaction.response.send_message(
                    "You're already verified. You're all set!", ephemeral=True
                )
            if not any(role.id == pending_id for role in interaction.user.roles):
                return await interaction.response.send_message(
                    "Your verification session isn't active. Please contact a server administrator.",
                    ephemeral=True,
                )
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                "Verification is not configured correctly. Please contact an administrator.",
                ephemeral=True,
            )

        key = (interaction.guild.id, interaction.user.id)
        count, locked_until = _attempts.get(key, (0, 0.0))
        if locked_until > time.monotonic():
            seconds = max(1, int(locked_until - time.monotonic()))
            return await interaction.response.send_message(
                f"Too many attempts. Try again in about {seconds} seconds.", ephemeral=True
            )

        # A short arithmetic challenge is self-contained and needs no external CAPTCHA key.
        first = random.SystemRandom().randint(12, 39)
        second = random.SystemRandom().randint(4, 19)
        if random.SystemRandom().choice((True, False)):
            question, answer = f"{first} + {second}", first + second
        else:
            larger, smaller = max(first, second), min(first, second)
            question, answer = f"{larger} - {smaller}", larger - smaller
        await interaction.response.send_modal(CaptchaModal(question, answer))


def register_persistent_view(bot):
    global _view_registered
    if not _view_registered:
        bot.add_view(CaptchaView())
        _view_registered = True


async def _trusted_bot_inviter(guild: discord.Guild, member: discord.Member) -> bool:
    """Only bypass verification for bots whose inviter is owner/admin."""
    try:
        await asyncio.sleep(1)
        cutoff = discord.utils.utcnow() - __import__("datetime").timedelta(seconds=30)
        for attempt in range(3):
            async for entry in guild.audit_logs(limit=20, action=discord.AuditLogAction.bot_add, after=cutoff):
                if getattr(entry.target, "id", None) != member.id or entry.user is None:
                    continue
                inviter = guild.get_member(entry.user.id)
                return entry.user.id == guild.owner_id or bool(
                    inviter and inviter.guild_permissions.administrator
                )
            if attempt < 2:
                await asyncio.sleep(1)
    except discord.Forbidden:
        print(f"[CAPTCHA] Cannot inspect bot inviter in {guild.id}; bot remains unverified.")
    except Exception as exc:
        print(f"[CAPTCHA] Bot inviter lookup failed in {guild.id}: {exc}")
    return False


async def handle_member_join(member: discord.Member, config: dict, bot=None) -> bool:
    """Return True when a human/untrusted bot is held in the verification role."""
    if str(config.get("captcha_enabled", "OFF")).upper() != "ON":
        return False
    if member.bot and await _trusted_bot_inviter(member.guild, member):
        print(f"[CAPTCHA] Trusted admin-added bot bypassed verification: {member} ({member.id})")
        return False

    try:
        pending_id = int(str(config.get("captcha_pending_role", "")).strip())
        verified_id = int(str(config.get("captcha_role", "")).strip())
        channel_id = int(str(config.get("captcha_channel", "")).strip())
        role = member.guild.get_role(pending_id)
        verified_role = member.guild.get_role(verified_id)
        channel = member.guild.get_channel(channel_id)
        if role is None or verified_role is None or channel is None:
            print(f"[CAPTCHA] Missing role/channel configuration in guild {member.guild.id}")
            return False
        bot_member = member.guild.me
        if bot_member is None or role >= bot_member.top_role or verified_role >= bot_member.top_role:
            print(f"[CAPTCHA] Bot role hierarchy prevents resetting verification in {member.guild.id}")
            return False

        # A returning member must verify again. Remove any restored/stale Verified
        # role and put the member into the pending role before the join flow continues.
        await member.add_roles(role, reason="NVD CAPTCHA: new join requires verification")
        if verified_role in member.roles:
            await member.remove_roles(
                verified_role, reason="NVD CAPTCHA: re-verification required after rejoining"
            )
        try:
            await member.send(
                f"Welcome to **{member.guild.name}**! Please open {channel.mention} and complete the CAPTCHA to access the server."
            )
        except discord.Forbidden:
            pass
        print(f"[CAPTCHA] Member held for verification: {member} ({member.id})")
        return True
    except discord.Forbidden:
        print(f"[CAPTCHA] Missing Manage Roles permission in {member.guild.id}")
    except Exception as exc:
        print(f"[CAPTCHA] Join handling failed in {member.guild.id}: {exc}")
    # Fail closed: do not let a join bypass the gate because of a transient error.
    return True


async def configure_guild(
    guild: discord.Guild,
    channel: discord.TextChannel,
    verified_role: discord.Role,
    pending_role_id: str = "",
) -> discord.Role:
    """Create/reuse the private pending role and gate all channels without changing @everyone."""
    bot_member = guild.me
    if bot_member is None:
        raise RuntimeError("I couldn't resolve my bot member in this server.")
    if not bot_member.guild_permissions.manage_roles or not bot_member.guild_permissions.manage_channels:
        raise RuntimeError("I need Manage Roles and Manage Channels permissions.")
    # View Audit Log is optional for setup. Without it, trusted-bot inviter
    # attribution is unavailable and bot accounts remain unverified (fail closed).
    if verified_role.is_default() or verified_role >= bot_member.top_role:
        raise RuntimeError("Move the NVD bot role above the selected Verified role first.")
    if not channel.permissions_for(bot_member).send_messages:
        raise RuntimeError("I need permission to send messages in the selected verification channel.")

    pending_role = guild.get_role(int(pending_role_id)) if str(pending_role_id).isdigit() else None
    created_role = False
    if pending_role is None:
        pending_role = await guild.create_role(
            name="NVD Unverified",
            permissions=discord.Permissions.none(),
            hoist=False,
            mentionable=False,
            reason="NVD CAPTCHA verification gate",
        )
        created_role = True
    if pending_role >= bot_member.top_role:
        raise RuntimeError("Move the NVD bot role above the NVD Unverified role.")

    changed = []
    try:
        # Apply denies to categories first. Synced child channels inherit the
        # category overwrite, avoiding one REST request per channel on large servers.
        # Unsynced children still receive an explicit deny. Only the selected
        # verification channel gets an explicit allow.
        categories = [ch for ch in guild.categories]
        for category in categories:
            overwrite = discord.PermissionOverwrite(view_channel=False)
            await category.set_permissions(
                pending_role, overwrite=overwrite,
                reason="NVD CAPTCHA: restrict unverified members",
            )
            changed.append(category)

        for guild_channel in guild.channels:
            if guild_channel.id == channel.id:
                overwrite = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
                await guild_channel.set_permissions(
                    pending_role, overwrite=overwrite,
                    reason="NVD CAPTCHA: allow verification channel",
                )
                changed.append(guild_channel)
                continue

            if getattr(guild_channel, "category", None) is not None and guild_channel.permissions_synced:
                continue

            if isinstance(guild_channel, discord.CategoryChannel):
                continue

            await guild_channel.set_permissions(
                pending_role,
                overwrite=discord.PermissionOverwrite(view_channel=False),
                reason="NVD CAPTCHA: restrict unsynced channel",
            )
            changed.append(guild_channel)

        embed = discord.Embed(
            title="🔐 Server Verification",
            description=(
                "Welcome! To unlock the rest of the server, complete the quick human-verification "
                "challenge below. New members can only see this channel until verification succeeds."
            ),
            color=0x5865F2,
        )
        embed.add_field(name="Step 1", value="Press **Verify** below.", inline=False)
        embed.add_field(name="Step 2", value="Solve the short maths question shown after pressing Verify.", inline=False)
        embed.set_footer(text="NVD Official • CAPTCHA verification")
        await channel.send(embed=embed, view=CaptchaView())
    except Exception:
        for guild_channel in changed:
            try:
                await guild_channel.set_permissions(pending_role, overwrite=None, reason="Rollback failed CAPTCHA setup")
            except Exception:
                pass
        if created_role:
            try:
                await pending_role.delete(reason="Rollback failed CAPTCHA setup")
            except Exception:
                pass
        raise

    return pending_role


async def disable_guild(guild: discord.Guild, pending_role_id: str = ""):
    """Remove the CAPTCHA role's channel restrictions without touching other roles."""
    if not str(pending_role_id).isdigit():
        return
    role = guild.get_role(int(pending_role_id))
    if role is None:
        return
    for channel in guild.channels:
        try:
            await channel.set_permissions(role, overwrite=None, reason="NVD CAPTCHA disabled")
        except Exception as exc:
            print(f"[CAPTCHA] Could not clear pending-role overwrite in {channel.id}: {exc}")
