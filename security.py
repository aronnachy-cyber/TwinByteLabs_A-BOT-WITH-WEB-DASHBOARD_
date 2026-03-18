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
        if channel:
            embed = discord.Embed(title="🔔 Role Update Detected", description=desc, color=0xf39c12)
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Role monitor log error: {e}")

    if log_callback:
        log_callback("🔔 ROLE-UPDATE", f"{member} — roles changed")

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
