import discord

async def handle_auto_role(member, config):
    # Join করার সময় অটো রোল দেওয়া
    if config.get("auto_role"):
        try:
            role = member.guild.get_role(int(config["auto_role"]))
            if role:
                await member.add_roles(role)
        except:
            print("❌ Auto-role error: Invalid ID")

async def mute_member(ctx, member, config):
    # ড্যাশবোর্ড থেকে Mute Role নিয়ে কাজ করা
    if not config.get("mute_role"):
        return await ctx.send("❌ Dashboard-e Mute Role set koro!")
    
    try:
        role = ctx.guild.get_role(int(config["mute_role"]))
        if role:
            await member.add_roles(role)
            await ctx.send(f"🔇 {member.mention} has been muted.")
    except:
        await ctx.send("❌ Error: Invalid Mute Role ID")
