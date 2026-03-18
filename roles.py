import discord

async def check_hierarchy(member, before, after, config):
    # Role Hierarchy Monitoring লজিক
    if config.get("role_monitor") == "ON":
        if len(before.roles) != len(after.roles):
            # এখানে তূমি লজিক দিতে পারো যাতে কেউ হায়ারার্কি ভাঙলে অ্যালার্ট দেয়
            print(f"🔔 Role update detected for {member.name}")

async def assign_auto_role(member, config):
    # ড্যাশবোর্ডের 'auto_role' ইনপুট থেকে আইডি নিয়ে রোল দেওয়া
    role_id = config.get("auto_role")
    if role_id and role_id.isdigit():
        role = member.guild.get_role(int(role_id))
        if role:
            try:
                await member.add_roles(role)
            except:
                pass
