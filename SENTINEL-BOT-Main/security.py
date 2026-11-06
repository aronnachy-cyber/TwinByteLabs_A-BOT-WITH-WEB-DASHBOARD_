import discord
import time

_recent_joins = {}
_ban_counts   = {}

RAID_THRESHOLD = 10
BAN_THRESHOLD  = 5

async def anti_raid_check(member, config, dm_callback=None, log_callback=None):
    if config.get("anti_raid") != "ON":
        return

    guild_id = member.guild.id
    now = time.time()

    if guild_id not in _recent_joins:
        _recent_joins[guild_id] = []

    _recent_joins[guild_id] = [t for t in _recent_joins[guild_id] if now - t < 60]
    _recent_joins[guild_id].append(now)

    # Account age check
    account_age_days = (discord.utils.utcnow() - member.created_at).days
    if account_age_days < 7:
        try:
            await member.send(
                f"⚠️ Your account is too new to join **{member.guild.name}**. "
                f"Please wait until your account is at least 7 days old."
            )
        except Exception:
            pass
        try:
            await member.kick(reason="Anti-Raid: Account too new (< 7 days)")
            msg = f"🛡️ Anti-Raid: Kicked {member} — account only {account_age_days} day(s) old."
            print(msg)
            if log_callback:
                log_callback("🛡️ ANTI-RAID", msg)
        except Exception as e:
            print(f"Anti-Raid kick error: {e}")
        return

    # Mass join check
    join_count = len(_recent_joins[guild_id])
    if join_count >= RAID_THRESHOLD:
        msg = f"🚨 RAID DETECTED in **{member.guild.name}**! {join_count} joins in 60 seconds."
        print(msg)
        if dm_callback:
            await dm_callback(
                f"🚨 **RAID ALERT — {member.guild.name}**\n"
                f"{join_count} members joined in the last 60 seconds!\n"
                f"Anti-raid is auto-kicking new joiners.\n"
                f"Consider enabling `!lockdown` to pause all joins."
            )
        if log_callback:
            log_callback("🚨 RAID", msg)
        try:
            await member.kick(reason="Anti-Raid: Mass join detected")
        except Exception as e:
            print(f"Anti-Raid kick error: {e}")

async def mass_action_prevent(ctx, action_type, config, log_callback=None):
    if config.get("mass_ban_prevent") != "ON":
        return True

    guild_id = ctx.guild.id
    now = time.time()

    if guild_id not in _ban_counts:
        _ban_counts[guild_id] = {"count": 0, "window_start": now}

    window = _ban_counts[guild_id]
    if now - window["window_start"] > 60:
        window["count"] = 0
        window["window_start"] = now

    window["count"] += 1

    if window["count"] > BAN_THRESHOLD:
        msg = f"🛑 Mass {action_type} prevention triggered in {ctx.guild.name}!"
        await ctx.send(f"🛑 Mass {action_type} prevention triggered! Too many actions in 60 seconds.")
        print(msg)
        if log_callback:
            log_callback("🛑 MASS-BAN-PREVENT", msg)
        return False

    return True

async def role_monitor_log(bot, config, member, before, after, log_callback=None):
    if config.get("role_monitor") != "ON":
        return

    added   = [r for r in after.roles  if r not in before.roles]
    removed = [r for r in before.roles if r not in after.roles]

    if not added and not removed:
        return

    log_channel_id = config.get("log_channel", "")
    if not log_channel_id:
        return

    desc = f"**Member:** {member.mention}\n"
    if added:
        desc += f"**Roles Added:** {', '.join(r.mention for r in added)}\n"
    if removed:
        desc += f"**Roles Removed:** {', '.join(r.mention for r in removed)}\n"

    try:
        channel = bot.get_channel(int(log_channel_id))
        if channel is None:
            try:
                channel = await bot.fetch_channel(int(log_channel_id))
            except Exception:
                channel = None
        if channel:
            embed = discord.Embed(title="🔔 Role Update Detected", description=desc, color=0xf39c12)
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Role monitor log error: {e}")

    if log_callback:
        log_callback("🔔 ROLE-UPDATE", f"{member} — roles changed")

async def check_security_2fa(bot, guild, config, log_callback=None):
    """When security_2fa is ON, verify the server requires 2FA for moderation actions.
    Logs a warning to the log channel and DMs the owner if 2FA is not enforced."""
    if config.get("security_2fa") != "ON":
        return

    # guild.mfa_level == 1 means 2FA is required for admin/mod actions.
    if getattr(guild, "mfa_level", 0) != 1:
        msg = (
            f"⚠️ **Security Alert — 2FA Not Enforced**\n"
            f"Your server **{guild.name}** has **Security 2FA** enabled in SP4RK, "
            f"but Discord's server-level 2FA requirement is currently **OFF**.\n"
            f"Enable it at: **Server Settings → Safety Setup → Require 2FA for moderator actions**."
        )
        log_channel_id = config.get("log_channel", "").strip()
        if log_channel_id:
            try:
                channel = bot.get_channel(int(log_channel_id))
                if channel is None:
                    try:
                        channel = await bot.fetch_channel(int(log_channel_id))
                    except Exception:
                        channel = None
                if channel:
                    await channel.send(msg)
            except Exception as e:
                print(f"[2FA] log send error: {e}")
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            if owner:
                await owner.send(msg)
        except Exception:
            pass
        if log_callback:
            log_callback("🔐 2FA-CHECK", f"{guild.name} — 2FA not enforced")

async def toggle_lockdown(guild, config, enable=True, log_callback=None):
    if config.get("temp_lockdown") != "ON" and enable:
        return

    for channel in guild.text_channels:
        try:
            overwrite = channel.overwrites_for(guild.default_role)
            overwrite.send_messages = False if enable else None
            await channel.set_permissions(guild.default_role, overwrite=overwrite)
        except Exception as e:
            print(f"Lockdown error on #{channel.name}: {e}")

    state = "LOCKED DOWN" if enable else "UNLOCKED"
    print(f"🔒 Server {guild.name} is now {state}")
    if log_callback:
        log_callback("🔒 LOCKDOWN", f"{guild.name} is now {state}")


# Anti-nuke audit-log monitor. This detects bursts of destructive server changes
# and alerts staff; it deliberately does not auto-ban/strip roles from admins.
_nuke_activity = {}
_nuke_alert_cooldown = {}
NUKE_WINDOW_SECONDS = 15
NUKE_THRESHOLD = 3

_NUKE_ACTIONS = {
    "channel_create": "channel_create",
    "channel_delete": "channel_delete",
    "channel_update": "channel_update",
    "role_create": "role_create",
    "role_delete": "role_delete",
    "role_update": "role_update",
    "ban": "ban",
    "kick": "kick",
    "webhook_create": "webhook_create",
    "webhook_delete": "webhook_delete",
    "guild_update": "guild_update",
}

async def check_anti_nuke(guild, event_type, config, bot=None, log_callback=None):
    """Alert when one actor performs several destructive/moderation changes quickly."""
    if not guild or config.get("anti_nuke") != "ON":
        return False
    action_name = _NUKE_ACTIONS.get(str(event_type))
    if not action_name:
        return False
    try:
        action = getattr(discord.AuditLogAction, action_name)
        now = discord.utils.utcnow()
        cutoff = now - __import__("datetime").timedelta(seconds=NUKE_WINDOW_SECONDS)
        entries = []
        async for entry in guild.audit_logs(limit=20, after=cutoff, action=action):
            if entry.user and entry.user.id != getattr(getattr(bot, "user", None), "id", None):
                entries.append(entry)
        if not entries:
            return False
        # Use the latest matching audit entry; Discord events may arrive before audit logs.
        entry = max(entries, key=lambda item: item.created_at)
        actor = entry.user
        key = (guild.id, actor.id)
        bucket = _nuke_activity.setdefault(key, [])
        bucket[:] = [stamp for stamp in bucket if (now - stamp).total_seconds() <= NUKE_WINDOW_SECONDS]
        bucket.append(now)
        if len(bucket) < NUKE_THRESHOLD:
            return False
        last_alert = _nuke_alert_cooldown.get(key)
        if last_alert and (now - last_alert).total_seconds() < 30:
            return True
        _nuke_alert_cooldown[key] = now
        detail = (
            f"🚨 **Possible server nuke activity detected** in **{guild.name}**.\\n"
            f"**Actor:** {actor} (ID: {actor.id})\\n"
            f"**Recent actions:** {len(bucket)} matching `{event_type}` audit events "
            f"in {NUKE_WINDOW_SECONDS} seconds.\\n"
            f"**Latest action:** `{event_type}`\\n"
            "Review the Audit Log and remove unexpected permissions manually."
        )
        log_channel_id = str(config.get("log_channel", "") or "").strip()
        channel = None
        if bot and log_channel_id.isdigit():
            channel = bot.get_channel(int(log_channel_id))
            if channel is None:
                try:
                    channel = await bot.fetch_channel(int(log_channel_id))
                except Exception:
                    channel = None
        if channel:
            await channel.send(detail)
        if log_callback:
            log_callback("🚨 ANTI-NUKE", f"{guild.name}: {actor} performed {len(bucket)} {event_type} actions in {NUKE_WINDOW_SECONDS}s", guild_id=guild.id)
        return True
    except discord.Forbidden:
        print(f"[ANTI-NUKE] Missing View Audit Log permission in {guild.name}")
        return False
    except Exception as e:
        print(f"[ANTI-NUKE] monitor error in {guild.name}: {e}")
        return False


async def check_join_protections(member, config, bot=None, log_callback=None):
    """Apply configured account-age and bot-invite protections to new members."""
    if not member or not member.guild:
        return
    guild = member.guild
    age_days = max(0, (discord.utils.utcnow() - member.created_at).days)

    # Custom Join Guard takes precedence over the Anti-Alt default threshold.
    try:
        min_age = int(config.get("joinguard_min_age", 0) or 0)
    except (TypeError, ValueError):
        min_age = 0
    if min_age <= 0 and config.get("anti_alt") == "ON":
        min_age = 30
    if min_age > 0 and age_days < min_age:
        try:
            await member.send(
                f"Your account must be at least {min_age} days old to join **{guild.name}**."
            )
        except Exception:
            pass
        try:
            await member.kick(reason=f"NVD Official Join Protection: account age {age_days}d; minimum {min_age}d")
            if log_callback:
                log_callback("🛡️ JOIN-GUARD", f"Kicked {member} — account age {age_days}d (minimum {min_age}d)", guild_id=guild.id)
            return True
        except Exception as e:
            print(f"[JOIN-GUARD] Could not remove {member}: {e}")
            return False
        return True

    if member.bot and config.get("anti_bot") == "ON":
        # Only allow bot additions performed by the server owner or an administrator.
        try:
            cutoff = discord.utils.utcnow() - __import__("datetime").timedelta(seconds=20)
            executor = None
            async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.bot_add, after=cutoff):
                if entry.target and getattr(entry.target, "id", None) == member.id:
                    executor = entry.user
                    break
            inviter = guild.get_member(executor.id) if executor else None
            trusted = bool(executor and (executor.id == guild.owner_id or (inviter and inviter.guild_permissions.administrator)))
            if not trusted:
                await member.kick(reason="NVD Official Anti-Bot: bot added by an untrusted inviter")
                if log_callback:
                    log_callback("🤖 ANTI-BOT", f"Removed bot {member} — inviter was not a server owner/admin", guild_id=guild.id)
                return True
        except discord.Forbidden:
            print(f"[ANTI-BOT] Missing permissions or View Audit Log in {guild.name}; bot was not removed.")
        except Exception as e:
            print(f"[ANTI-BOT] check error in {guild.name}: {e}")
    return False
