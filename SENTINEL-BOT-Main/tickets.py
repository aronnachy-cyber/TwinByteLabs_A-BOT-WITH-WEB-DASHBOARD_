import discord
import json
import os
import re
from datetime import datetime

TICKETS_FILE = "tickets.json"
_TABLE = "sentinel_tickets"

# Callback set by main.py on startup so TicketPanelView can fetch guild config
# without creating a circular import.
_get_config_callback = None


def set_config_callback(fn):
    global _get_config_callback
    _get_config_callback = fn


def _get_supabase():
    try:
        from dashboard import supabase
        return supabase
    except Exception:
        return None


# ── Postgres + Local JSON fallback ──────────────────────────────────────────

def _local_load():
    try:
        import db_spark as _ds
        if _ds.is_available():
            val = _ds.get_blob("tickets")
            if val is not None:
                return val
    except Exception:
        pass
    if os.path.exists(TICKETS_FILE):
        try:
            with open(TICKETS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tickets": [], "counter": 0}


def _local_save(data):
    try:
        import db_spark as _ds
        if _ds.is_available():
            _ds.set_blob("tickets", data)
    except Exception:
        pass
    tmp = TICKETS_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, TICKETS_FILE)
    except Exception:
        pass


def _row_to_dict(row):
    if not row:
        return None
    out = dict(row)
    if "ticket_num" in out and out["ticket_num"] is not None:
        out["id"] = out["ticket_num"]
    for k in ("channel_id", "guild_id", "user_id"):
        if out.get(k) is not None:
            try:
                out[k] = int(out[k])
            except (TypeError, ValueError):
                pass
    return out


# ── Public read API ──────────────────────────────────────────────────────────

def load_tickets():
    sb = _get_supabase()
    if sb:
        try:
            res = sb.table(_TABLE).select("*").order("created_at", desc=False).execute()
            tickets = [_row_to_dict(r) for r in (res.data or [])]
            counter = max([t.get("ticket_num", 0) for t in tickets] or [0])
            return {"tickets": tickets, "counter": counter}
        except Exception as e:
            print(f"[TICKET] Supabase load error: {e}")
    return _local_load()


def save_tickets(data):
    """Local-only JSON save (used as fallback)."""
    _local_save(data)


def get_all_tickets():
    return load_tickets().get("tickets", [])


def get_tickets_for_guild(guild_id):
    """Return only tickets belonging to the given guild_id."""
    gid = str(guild_id)
    sb = _get_supabase()
    if sb:
        try:
            res = (
                sb.table(_TABLE)
                .select("*")
                .eq("guild_id", gid)
                .order("created_at", desc=False)
                .execute()
            )
            return [_row_to_dict(r) for r in (res.data or [])]
        except Exception as e:
            print(f"[TICKET] Supabase guild filter error: {e}")
    # fallback: filter local JSON
    return [t for t in _local_load().get("tickets", []) if str(t.get("guild_id")) == gid]


def _next_counter(guild_id):
    """Return the next ticket number for a guild."""
    sb = _get_supabase()
    if sb:
        try:
            res = (
                sb.table(_TABLE)
                .select("ticket_num")
                .eq("guild_id", str(guild_id))
                .order("ticket_num", desc=True)
                .limit(1)
                .execute()
            )
            highest = (res.data[0]["ticket_num"] if res.data else 0) or 0
            return int(highest) + 1
        except Exception as e:
            print(f"[TICKET] Supabase counter error: {e}")
    data = _local_load()
    data["counter"] = data.get("counter", 0) + 1
    _local_save(data)
    return data["counter"]


def _insert_ticket(record):
    sb = _get_supabase()
    payload = {
        "ticket_num": int(record["id"]),
        "channel_id": str(record["channel_id"]),
        "guild_id":   str(record["guild_id"]),
        "user_id":    str(record["user_id"]),
        "user_name":  record.get("user_name", ""),
        "reason":     record.get("reason", ""),
        "status":     record.get("status", "open"),
        "created_at": record.get("created_at"),
        "closed_at":  record.get("closed_at"),
        "closed_by":  record.get("closed_by"),
    }
    if sb:
        try:
            sb.table(_TABLE).insert(payload).execute()
            return
        except Exception as e:
            print(f"[TICKET] Supabase insert error: {e}")
    data = _local_load()
    data.setdefault("tickets", []).append(record)
    _local_save(data)


def _update_ticket_by_channel(channel_id, fields):
    sb = _get_supabase()
    if sb:
        try:
            sb.table(_TABLE).update(fields).eq("channel_id", str(channel_id)).execute()
            return True
        except Exception as e:
            print(f"[TICKET] Supabase update error: {e}")
    data = _local_load()
    updated = False
    for t in data.get("tickets", []):
        if str(t.get("channel_id")) == str(channel_id):
            t.update(fields)
            updated = True
    if updated:
        _local_save(data)
    return updated


def _find_by_channel(channel_id):
    sb = _get_supabase()
    if sb:
        try:
            res = sb.table(_TABLE).select("*").eq("channel_id", str(channel_id)).execute()
            if res.data:
                return _row_to_dict(res.data[0])
        except Exception as e:
            print(f"[TICKET] Supabase find error: {e}")
    for t in _local_load().get("tickets", []):
        if str(t.get("channel_id")) == str(channel_id):
            return t
    return None


# ── Public write API ─────────────────────────────────────────────────────────

async def create_ticket(guild, member, config, reason="General Support"):
    ticket_num = _next_counter(guild.id)

    category_id = config.get("ticket_category", "").strip()
    category = None
    if category_id and category_id.isdigit():
        ch = guild.get_channel(int(category_id))
        if isinstance(ch, discord.CategoryChannel):
            category = ch

    reason_word = re.sub(r'[^a-z0-9]', '', (reason.split()[0] if reason else 'general').lower())[:16] or "general"
    channel_name = f"ticket-{ticket_num:04d}-{reason_word}"

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

    import embeds
    embed = embeds.render(
        "sp4rk.ticket_open",
        guild_id=guild.id,
        user=member,
        ticket_id=f"{ticket_num:04d}",
        reason=reason,
    )
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
        "closed_at": None,
    }
    _insert_ticket(ticket)
    return channel, ticket


async def close_ticket(channel, closer, transcript=True):
    ticket = _find_by_channel(channel.id)
    if not ticket:
        return False

    import embeds
    embed = embeds.render(
        "sp4rk.ticket_close",
        guild_id=getattr(channel.guild, "id", None),
        moderator=closer,
        ticket_id=f"{ticket.get('id', 0):04d}",
    )
    await channel.send(embed=embed)

    _update_ticket_by_channel(channel.id, {
        "status":    "closed",
        "closed_at": datetime.utcnow().isoformat(),
        "closed_by": str(closer),
    })

    import asyncio
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {closer}")
    except Exception as e:
        print(f"Ticket delete error: {e}")
    return True


class TicketPanelView(discord.ui.View):
    """Persistent view posted by /ticket-panel. Survives bot restarts."""

    def __init__(self, button_label: str = "Open Ticket"):
        super().__init__(timeout=None)
        # Update the button label dynamically when re-creating from on_ready
        self.btn_open.label = button_label

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.success,
                       emoji="🎫", custom_id="ticket_panel_open")
    async def btn_open(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user

        config = {}
        if _get_config_callback:
            try:
                config = _get_config_callback(str(guild.id))
            except Exception:
                config = {}

        try:
            channel, ticket = await create_ticket(guild, member, config, "General Support")
            await interaction.followup.send(
                f"✅ Your ticket has been created: {channel.mention}", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Could not create your ticket: {e}", ephemeral=True
            )


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
                await interaction.response.send_message("❌ **Access Denied** — Only staff or the original ticket opener can close this ticket.", ephemeral=True)
                return
        await interaction.response.send_message("🔒 **Closing Ticket** — Please wait while the ticket is being closed...")
        await close_ticket(interaction.channel, interaction.user)
