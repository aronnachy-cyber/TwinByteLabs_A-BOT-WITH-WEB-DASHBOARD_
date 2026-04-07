import discord
import asyncio

async def handle_auto_role(member, config):
    if config.get("auto_role"):
        try:
            role = member.guild.get_role(int(config["auto_role"]))
            if role:
                await member.add_roles(role)
        except Exception:
            print("Auto-role error: Invalid ID")

async def mute_member(ctx, member, config, duration_str=None):
    if not config.get("mute_role"):
        return await ctx.send("❌ Set a Mute Role ID in the dashboard first!")
    try:
        role = ctx.guild.get_role(int(config["mute_role"]))
        if not role:
            return await ctx.send("❌ Mute role not found. Check the ID in the dashboard.")
        await member.add_roles(role)
        if duration_str:
            from main import parse_duration, duration_label
            seconds = parse_duration(duration_str)
            if not seconds:
                return await ctx.send(f"🔇 {member.mention} muted. (Invalid duration format — mute is permanent until removed.)")
            label = duration_label(seconds)
            await ctx.send(f"🔇 {member.mention} has been muted for **{label}**.")

            async def auto_unmute():
                await asyncio.sleep(seconds)
                try:
                    await member.remove_roles(role, reason=f"Timed mute expired ({label})")
                    await ctx.send(f"🔊 {member.mention} has been automatically unmuted after {label}.")
                except Exception as e:
                    print(f"Auto-unmute error: {e}")
            asyncio.create_task(auto_unmute())
        else:
            await ctx.send(f"🔇 {member.mention} has been muted.")
    except Exception as e:
        await ctx.send(f"❌ Error muting: {e}")

async def unmute_member(ctx, member, config):
    if not config.get("mute_role"):
        return await ctx.send("❌ No mute role configured in the dashboard.")
    try:
        role = ctx.guild.get_role(int(config["mute_role"]))
        if role and role in member.roles:
            await member.remove_roles(role)
            await ctx.send(f"🔊 {member.mention} has been unmuted.")
        else:
            await ctx.send(f"❌ {member.mention} is not muted.")
    except Exception as e:
        await ctx.send(f"❌ Error unmuting: {e}")
