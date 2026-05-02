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
        return await ctx.send(
            "❌ **Setup Required** — No mute role has been configured.\n"
            "> Set a Mute Role ID in the dashboard to use this command."
        )
    try:
        role = ctx.guild.get_role(int(config["mute_role"]))
        if not role:
            return await ctx.send(
                "❌ **Role Not Found** — The configured mute role ID is invalid.\n"
                "> Please verify the Mute Role ID in the dashboard."
            )
        await member.add_roles(role)
        if duration_str:
            from main import parse_duration, duration_label
            seconds = parse_duration(duration_str)
            if not seconds:
                return await ctx.send(
                    f"🔇 **Member Muted** — {member.mention}\n"
                    f"> ⚠️ **Duration:** Invalid format — mute is permanent until manually removed.\n"
                    f"> 🛡️ **By:** {ctx.author.mention}"
                )
            label = duration_label(seconds)
            await ctx.send(
                f"🔇 **Member Muted** — {member.mention}\n"
                f"> 🕐 **Duration:** {label}\n"
                f"> 🛡️ **By:** {ctx.author.mention}"
            )

            async def auto_unmute():
                await asyncio.sleep(seconds)
                try:
                    await member.remove_roles(role, reason=f"Timed mute expired ({label})")
                    await ctx.send(
                        f"🔊 **Mute Expired** — {member.mention} has been automatically unmuted.\n"
                        f"> 🕐 **Duration was:** {label}"
                    )
                except Exception as e:
                    print(f"Auto-unmute error: {e}")
            asyncio.create_task(auto_unmute())
        else:
            await ctx.send(
                f"🔇 **Member Muted** — {member.mention}\n"
                f"> ⏳ **Duration:** Permanent (until manually removed)\n"
                f"> 🛡️ **By:** {ctx.author.mention}"
            )
    except Exception as e:
        await ctx.send(f"❌ **Mute Failed** — `{e}`")

async def unmute_member(ctx, member, config):
    if not config.get("mute_role"):
        return await ctx.send(
            "❌ **Setup Required** — No mute role has been configured.\n"
            "> Set a Mute Role ID in the dashboard to use this command."
        )
    try:
        role = ctx.guild.get_role(int(config["mute_role"]))
        if role and role in member.roles:
            await member.remove_roles(role)
            await ctx.send(
                f"🔊 **Member Unmuted** — {member.mention}\n"
                f"> 🛡️ **By:** {ctx.author.mention}"
            )
        else:
            await ctx.send(
                f"ℹ️ **Not Muted** — {member.mention} does not currently have the mute role."
            )
    except Exception as e:
        await ctx.send(f"❌ **Unmute Failed** — `{e}`")
