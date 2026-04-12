import discord
import json
import os
import re
from datetime import datetime

TICKETS_FILE = "tickets.json"

def load_tickets():
    if os.path.exists(TICKETS_FILE):
        with open(TICKETS_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {"tickets": [], "counter": 0}
    return {"tickets": [], "counter": 0}

def save_tickets(data):
    with open(TICKETS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_all_tickets():
    return load_tickets().get("tickets", [])

async def create_ticket(guild, member, config, reason="General Support"):
    data = load_tickets()
    data["counter"] = data.get("counter", 0) + 1
    ticket_num = data["counter"]

    category_id = config.get("ticket_category", "").strip()
    category = None
    if category_id and category_id.isdigit():
        ch = guild.get_channel(int(category_id))
        if isinstance(ch, discord.CategoryChannel):
            category = ch

    # Clean channel name — Discord allows only lowercase letters, numbers, hyphens
    safe_name = re.sub(r'[^a-z0-9]', '', member.name.lower())[:12] or "user"
    channel_name = f"ticket-{ticket_num:04d}-{safe_name}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
    }
    for role in guild.roles:
        if role.permissions.manage_messages and role != guild.default_role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    try:
        channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Support ticket for {member} | Reason: {reason}"
        )
    except discord.Forbidden:
        print(f"Ticket error: Missing Manage Channels permission in {guild.name}")
        raise Exception("I don't have permission to create channels. Grant me **Manage Channels**.")
    except Exception as e:
        print(f"Ticket channel creation error: {e}")
        raise

    embed = discord.Embed(
        title=f"🎫 Ticket #{ticket_num:04d}",
        description=(
            f"Welcome {member.mention}! Staff will be with you shortly.\n\n"
            f"**Reason:** {reason}\n"
            f"**Opened by:** {member.mention}\n\n"
            f"React with 🔒 or use `/ticket-close` to close this ticket."
        ),
        color=0x3a8fff,
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text="Sentinel Ticket System")
    close_btn_view = TicketCloseView()
    await channel.send(embed=embed, view=close_btn_view)

    ticket = {
        "id": ticket_num,
        "channel_id": channel.id,
        "guild_id": guild.id,
        "user_id": member.id,
        "user_name": str(member),
        "reason": reason,
        "status": "open",
        "created_at": datetime.utcnow().isoformat(),
        "closed_at": None
    }
    data["tickets"].append(ticket)
    save_tickets(data)
    return channel, ticket

async def close_ticket(channel, closer, transcript=True):
    data = load_tickets()
    ticket = next((t for t in data["tickets"] if t["channel_id"] == channel.id), None)
    if not ticket:
        return False

    embed = discord.Embed(
        title="🔒 Ticket Closing",
        description=f"This ticket is being closed by {closer.mention}.\nChannel will be deleted in 5 seconds.",
        color=0xe74c3c
    )
    await channel.send(embed=embed)

    ticket["status"] = "closed"
    ticket["closed_at"] = datetime.utcnow().isoformat()
    ticket["closed_by"] = str(closer)
    save_tickets(data)

    import asyncio
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {closer}")
    except Exception as e:
        print(f"Ticket delete error: {e}")
    return True

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_messages and interaction.channel.topic and str(interaction.user.id) not in interaction.channel.topic:
            opener_id = None
            for t in get_all_tickets():
                if t["channel_id"] == interaction.channel.id:
                    opener_id = t["user_id"]
                    break
            if opener_id and interaction.user.id != opener_id:
                await interaction.response.send_message("❌ Only staff or the ticket opener can close this.", ephemeral=True)
                return
        await interaction.response.send_message("🔒 Closing ticket...")
        await close_ticket(interaction.channel, interaction.user)
