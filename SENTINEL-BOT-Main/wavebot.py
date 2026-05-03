"""
Wave📶 — Byte Labs Streaming & Watch Party Bot
Token env var: W_TOKEN

Discord acts as the controller, not the streamer.
  /watch [title] [url]  → create a watch-party room, post the link
  /sync                 → force-resync all viewers to current timestamp
  /status               → check if current room is live / paused
  /endstream            → end the watch session and tear down room
  /wave-help            → full command list
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("wave")
logging.basicConfig(level=logging.INFO)

TOKEN  = os.environ.get("W_TOKEN", "").strip()
_DIR   = Path(__file__).parent
_ROOMS = _DIR / "wave_rooms.json"
_lock     = threading.Lock()
_WAVE_CFG      = _DIR / "wave_config.json"
_WATCHLIST_FILE = _DIR / "wave_watchlist.json"
_wave_cfg: dict = {}

# ── Wave📶 config helpers ─────────────────────────────────────────────────────────
def _load_wave_cfg():
    global _wave_cfg
    try:
        if _WAVE_CFG.exists():
            _wave_cfg = json.loads(_WAVE_CFG.read_text(encoding="utf-8"))
    except Exception:
        _wave_cfg = {}

def _save_wave_cfg():
    tmp = _WAVE_CFG.with_suffix(".tmp")
    tmp.write_text(json.dumps(_wave_cfg, indent=2), encoding="utf-8")
    tmp.replace(_WAVE_CFG)

# ── Watchlist helpers ───────────────────────────────────────────────────────────
def _read_watchlist(guild_id: str) -> list:
    try:
        if _WATCHLIST_FILE.exists():
            data = json.loads(_WATCHLIST_FILE.read_text(encoding="utf-8"))
            return data.get(str(guild_id), [])
    except Exception:
        pass
    return []

def _write_watchlist(guild_id: str, items: list):
    try:
        data = {}
        if _WATCHLIST_FILE.exists():
            data = json.loads(_WATCHLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data[str(guild_id)] = items
    tmp = _WATCHLIST_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(_WATCHLIST_FILE)

def _wcfg(gid: str) -> dict:
    return _wave_cfg.setdefault(str(gid), {})

def _set_wcfg(gid: str, key: str, val):
    _wcfg(gid)[key] = val
    _save_wave_cfg()

# ── JSON helpers ───────────────────────────────────────────────────────────────
def _read_rooms() -> dict:
    try:
        if _ROOMS.exists():
            return json.loads(_ROOMS.read_text())
    except Exception:
        pass
    return {}

def _write_rooms(data: dict):
    tmp = _ROOMS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_ROOMS)

def _get_room_by_guild(guild_id: str) -> Optional[dict]:
    rooms = _read_rooms()
    for rid, room in rooms.items():
        if room.get("guild_id") == guild_id:
            return {"id": rid, **room}
    return None

# ── Theme ──────────────────────────────────────────────────────────────────────
ROSE  = 0xf43f5e
PINK  = 0xfb7185

# ── Bot setup ──────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members          = True

bot = commands.Bot(command_prefix="w!", intents=intents, help_command=None)

# ── Global slash-command gate: admin / owner only ──────────────────────────────
@bot.tree.interaction_check
async def _admin_only(interaction: discord.Interaction) -> bool:
    """All Wave📶 slash commands are restricted to server admins and the owner."""
    if interaction.guild is None:
        await interaction.response.send_message(
            "⚠️ Wave📶 commands can only be used inside a server.", ephemeral=True
        )
        return False
    perms = interaction.user.guild_permissions
    if perms.administrator or interaction.guild.owner_id == interaction.user.id:
        return True
    await interaction.response.send_message(
        "🔒 Only **server administrators** and the **server owner** can use Wave📶 commands.",
        ephemeral=True,
    )
    return False

@bot.event
async def on_ready():
    _load_wave_cfg()
    try:
        synced = await bot.tree.sync()
        logger.info(f"[Wave📶] Logged in as {bot.user} — synced {len(synced)} commands")
    except Exception as e:
        logger.error(f"[Wave📶] Sync failed: {e}")

# ── Helpers ────────────────────────────────────────────────────────────────────
def _base_url() -> str:
    domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
    if domain:
        return f"https://{domain}"
    return os.environ.get("SITE_URL", "https://bytelabs.dev")

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

# ─── /watch ───────────────────────────────────────────────────────────────────
@bot.tree.command(name="watch", description="Create a Wave📶 watch-party room and share the link", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(
    title="Session title (e.g. 'Movie Night')",
    url="Optional stream URL to pre-load (HLS .m3u8, YouTube, direct video, etc.)"
)
async def cmd_watch(interaction: discord.Interaction,
                    title: str = "Watch Party",
                    url: Optional[str] = None):
    await interaction.response.defer()

    guild_id = str(interaction.guild_id)
    room_id  = secrets.token_urlsafe(6)
    host_tok = secrets.token_hex(16)

    import time
    room = {
        "guild_id":    guild_id,
        "title":       title,
        "stream_url":  url or "",
        "playing":     False,
        "position":    0.0,
        "position_ts": time.time(),
        "host_token":  host_tok,
        "created_at":  datetime.now(timezone.utc).isoformat(),
        "chat":        [],
        "ffmpeg_pid":  None,
    }
    with _lock:
        rooms = _read_rooms()
        # Remove old room for this guild
        old = [k for k, v in rooms.items() if v.get("guild_id") == guild_id]
        for k in old:
            rooms.pop(k, None)
        rooms[room_id] = room
        _write_rooms(rooms)

    base  = _base_url()
    watch_url = f"{base}/watch/{room_id}"
    host_url  = f"{base}/watch/{room_id}?host={host_tok}"

    cfg = _wcfg(guild_id)
    vc_id = cfg.get("voice_channel_id")
    tc_id = cfg.get("text_channel_id")

    e = discord.Embed(
        title="📺 Watch Party Started!",
        description=f"**{title}**\n\nA watch-party room is ready. Click below to join!",
        color=ROSE,
        timestamp=datetime.now(timezone.utc)
    )
    e.add_field(name="🔗 Watch Link",   value=f"[Open Room]({watch_url})", inline=True)
    e.add_field(name="⚙️ Host Controls", value=f"[Host View]({host_url})", inline=True)
    if vc_id:
        vc = interaction.guild.get_channel(int(vc_id))
        if vc:
            e.add_field(name="🔊 Voice Channel", value=vc.mention, inline=True)
    if url:
        e.add_field(name="📡 Stream URL", value=f"`{url[:80]}{'…' if len(url)>80 else ''}`", inline=False)
    e.add_field(
        name="Commands",
        value="`/sync` — resync all viewers\n`/status` — check room status\n`/endstream` — close room",
        inline=False
    )
    e.set_footer(text="Wave📶 · Byte Labs · Streaming & Watch Parties")

    # If a text channel is configured, post the public embed there
    # and send the host link privately to the command user
    if tc_id:
        tc = interaction.guild.get_channel(int(tc_id))
        if tc:
            try:
                await tc.send(embed=e)
                host_e = discord.Embed(
                    title="⚙️ Your Host Link",
                    description=f"[Open Host View]({host_url})\n\nKeep this private — it gives full control over the room.",
                    color=PINK,
                )
                return await interaction.followup.send(embed=host_e, ephemeral=True)
            except Exception as _tc_err:
                logger.warning(f"[Wave📶] Could not post to text channel: {_tc_err}")

    # Fallback: respond in the current channel (no text channel configured)
    await interaction.followup.send(embed=e)


# ─── /sync ────────────────────────────────────────────────────────────────────
@bot.tree.command(name="sync", description="Force all viewers to sync to the host's current position", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_sync(interaction: discord.Interaction):
    await interaction.response.defer()
    guild_id = str(interaction.guild_id)
    room     = _get_room_by_guild(guild_id)
    if not room:
        return await interaction.followup.send("❌ No active Wave📶 room for this server. Use `/watch` to create one.")

    import time
    with _lock:
        rooms = _read_rooms()
        if room["id"] in rooms:
            rooms[room["id"]]["sync_pulse"] = time.time()
            _write_rooms(rooms)

    e = discord.Embed(
        title="🔄 Sync Triggered",
        description=f"All viewers in **{room['title']}** will snap to the host's position within 2 seconds.",
        color=ROSE
    )
    e.set_footer(text="Wave📶 · Byte Labs")
    await interaction.followup.send(embed=e)


# ─── /status ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="status", description="Check the current Wave📶 watch-party room status", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_status(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    room     = _get_room_by_guild(guild_id)
    if not room:
        return await interaction.response.send_message("No active Wave📶 room for this server.", ephemeral=True)

    import time
    state    = "▶️ Playing" if room.get("playing") else "⏸ Paused"
    position = room.get("position", 0)
    if room.get("playing"):
        position += time.time() - room.get("position_ts", time.time())
    mins, secs = divmod(int(position), 60)
    pos_str  = f"{mins}:{secs:02d}"
    url      = room.get("stream_url") or "Not set"

    base  = _base_url()
    watch_url = f"{base}/watch/{room['id']}"

    e = discord.Embed(title=f"📊 Wave📶 Room — {room['title']}", color=ROSE)
    e.add_field(name="Status",     value=state,   inline=True)
    e.add_field(name="Position",   value=pos_str, inline=True)
    e.add_field(name="Stream URL", value=f"`{url[:60]}{'…' if len(url)>60 else ''}`" if url != "Not set" else "Not set", inline=False)
    e.add_field(name="Watch Link", value=f"[Join Room]({watch_url})", inline=False)
    e.set_footer(text="Wave📶 · Byte Labs")
    await interaction.response.send_message(embed=e)


# ─── /endstream ───────────────────────────────────────────────────────────────
@bot.tree.command(name="endstream", description="End the current watch-party session", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_end(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
    await interaction.response.defer()
    guild_id = str(interaction.guild_id)
    with _lock:
        rooms = _read_rooms()
        old   = [k for k, v in rooms.items() if v.get("guild_id") == guild_id]
        for k in old:
            rooms.pop(k, None)
        _write_rooms(rooms)
    if old:
        await interaction.followup.send("⏹ Watch party ended and room closed.")
    else:
        await interaction.followup.send("No active room found.", ephemeral=True)


# ─── /wave-help ───────────────────────────────────────────────────────────────
@bot.tree.command(name="wave-help", description="Show all Wave📶 streaming commands", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_help(interaction: discord.Interaction):
    e = discord.Embed(
        title="📺 Wave📶 — Command Reference",
        description="Streaming & synchronized watch parties for Discord.",
        color=ROSE
    )
    e.add_field(name="🎬 Watch Parties", value=(
        "`/watch [title] [url]` — Create a watch-party room\n"
        "`/sync` — Force all viewers to sync\n"
        "`/status` — Check room status\n"
        "`/endstream` — Close the room"
    ), inline=False)
    e.add_field(name="💡 How it works", value=(
        "Wave📶 creates a web room all your members join.\n"
        "Paste any stream URL — YouTube, HLS, direct video.\n"
        "The host controls play/pause/seek for everyone.\n"
        "Chat overlay keeps the watch-party vibe alive."
    ), inline=False)
    e.add_field(name="🔗 Platform", value="[bytelabs.dev/wave](https://bytelabs.dev/wave)", inline=False)
    e.set_footer(text="Wave📶 · Byte Labs · Streaming & Watch Parties")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ─── /wave-config ─────────────────────────────────────────────────────────────
@bot.tree.command(
    name="wave-config",
    description="View or set Wave📶's stream voice channel and watch party text channel",
    default_member_permissions=discord.Permissions(administrator=True),
)
@app_commands.describe(
    voice_channel="Voice channel users stream in (shown in watch party embeds)",
    text_channel="Text channel where watch party links are automatically posted",
)
async def cmd_wave_config(
    interaction: discord.Interaction,
    voice_channel: Optional[discord.VoiceChannel] = None,
    text_channel: Optional[discord.TextChannel]   = None,
):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message(
            "❌ You need Manage Server permission.", ephemeral=True
        )
    gid = str(interaction.guild_id)
    changed = []
    if voice_channel is not None:
        _set_wcfg(gid, "voice_channel_id", str(voice_channel.id))
        changed.append(f"🔊 Stream voice channel → **{voice_channel.name}**")
    if text_channel is not None:
        _set_wcfg(gid, "text_channel_id", str(text_channel.id))
        changed.append(f"💬 Watch party channel → {text_channel.mention}")
    if changed:
        e = discord.Embed(title="✅ Wave📶 Config Updated", color=ROSE, description="\n".join(changed))
        return await interaction.response.send_message(embed=e, ephemeral=True)
    # No args — show current config
    cfg   = _wcfg(gid)
    vc_id = cfg.get("voice_channel_id")
    tc_id = cfg.get("text_channel_id")
    vc_ch = interaction.guild.get_channel(int(vc_id)) if vc_id else None
    tc_ch = interaction.guild.get_channel(int(tc_id)) if tc_id else None
    e = discord.Embed(title="⚙️ Wave📶 Config", color=ROSE)
    e.add_field(name="🔊 Stream Voice Channel", value=vc_ch.name if vc_ch else "Not set", inline=False)
    e.add_field(name="💬 Watch Party Channel",  value=tc_ch.mention if tc_ch else "Not set", inline=False)
    e.set_footer(text="Use /wave-config voice_channel: #vc text_channel: #tc to set channels")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ─── /watchlist ────────────────────────────────────────────────────────────────
watchlist_group = app_commands.Group(
    name="watchlist",
    description="Manage the server's watch party queue",
    default_member_permissions=discord.Permissions(administrator=True),
)
bot.tree.add_command(watchlist_group)

@watchlist_group.command(name="add", description="Add a title to the server's watchlist")
@app_commands.describe(title="Movie or show title", url="Optional stream URL to pre-load")
async def cmd_wl_add(interaction: discord.Interaction,
                     title: str,
                     url: Optional[str] = None):
    gid = str(interaction.guild_id)
    with _lock:
        items = _read_watchlist(gid)
        items.append({"title": title, "url": url or "", "added_by": str(interaction.user.id)})
        _write_watchlist(gid, items)
    e = discord.Embed(
        title="➕ Added to Watchlist",
        description=f"**{title}** is now #{len(items)} in the queue.",
        color=ROSE,
    )
    if url:
        e.add_field(name="📡 URL", value=f"`{url[:80]}{'…' if len(url)>80 else ''}`", inline=False)
    e.set_footer(text=f"Wave📶 · {len(items)} item(s) in queue")
    await interaction.response.send_message(embed=e)

@watchlist_group.command(name="show", description="View the server's watchlist")
async def cmd_wl_show(interaction: discord.Interaction):
    gid = str(interaction.guild_id)
    items = _read_watchlist(gid)
    if not items:
        return await interaction.response.send_message(
            "The watchlist is empty. Add something with `/watchlist add`.", ephemeral=True
        )
    lines = []
    for i, item in enumerate(items, 1):
        url_hint = " 🔗" if item.get("url") else ""
        lines.append(f"`{i}.` **{item['title']}**{url_hint}")
    e = discord.Embed(title="📋 Watchlist", description="\n".join(lines), color=ROSE)
    e.set_footer(text=f"Wave📶 · {len(items)} item(s) · Use /watchlist next to start the top one")
    await interaction.response.send_message(embed=e)

@watchlist_group.command(name="next", description="Pop the next item off the watchlist and start a watch party")
async def cmd_wl_next(interaction: discord.Interaction):
    gid = str(interaction.guild_id)
    await interaction.response.defer()
    with _lock:
        items = _read_watchlist(gid)
        if not items:
            return await interaction.followup.send(
                "The watchlist is empty. Add something with `/watchlist add`.", ephemeral=True
            )
        next_item = items.pop(0)
        _write_watchlist(gid, items)

    # Create the watch room
    room_id  = secrets.token_urlsafe(6)
    host_tok = secrets.token_hex(16)
    import time as _t
    room = {
        "guild_id":    gid,
        "title":       next_item["title"],
        "stream_url":  next_item.get("url", ""),
        "playing":     False,
        "position":    0.0,
        "position_ts": _t.time(),
        "host_token":  host_tok,
        "created_at":  datetime.now(timezone.utc).isoformat(),
        "chat":        [],
        "ffmpeg_pid":  None,
    }
    with _lock:
        rooms = _read_rooms()
        old = [k for k, v in rooms.items() if v.get("guild_id") == gid]
        for k in old:
            rooms.pop(k, None)
        rooms[room_id] = room
        _write_rooms(rooms)

    base      = _base_url()
    watch_url = f"{base}/watch/{room_id}"
    host_url  = f"{base}/watch/{room_id}?host={host_tok}"

    cfg   = _wcfg(gid)
    vc_id = cfg.get("voice_channel_id")

    e = discord.Embed(
        title="📺 Watch Party Started from Watchlist!",
        description=f"**{next_item['title']}**\n\nPopped from the watchlist and room is ready!",
        color=ROSE,
        timestamp=datetime.now(timezone.utc),
    )
    e.add_field(name="🔗 Watch Link",   value=f"[Open Room]({watch_url})", inline=True)
    e.add_field(name="⚙️ Host Controls", value=f"[Host View]({host_url})", inline=True)
    if vc_id:
        vc = interaction.guild.get_channel(int(vc_id))
        if vc:
            e.add_field(name="🔊 Voice Channel", value=vc.mention, inline=True)
    if next_item.get("url"):
        e.add_field(name="📡 Stream URL",
                    value=f"`{next_item['url'][:80]}{'…' if len(next_item['url'])>80 else ''}`",
                    inline=False)
    e.set_footer(text=f"Wave📶 · {len(items)} item(s) remaining in watchlist")

    tc_id = cfg.get("text_channel_id")
    if tc_id:
        tc = interaction.guild.get_channel(int(tc_id))
        if tc:
            try:
                await tc.send(embed=e)
                host_e = discord.Embed(
                    title="⚙️ Your Host Link",
                    description=f"[Open Host View]({host_url})\n\nKeep this private.",
                    color=PINK,
                )
                return await interaction.followup.send(embed=host_e, ephemeral=True)
            except Exception:
                pass
    await interaction.followup.send(embed=e)

@watchlist_group.command(name="remove", description="Remove a specific item from the watchlist by its number")
@app_commands.describe(position="Position number from /watchlist show (e.g. 2)")
async def cmd_wl_remove(interaction: discord.Interaction, position: int):
    gid = str(interaction.guild_id)
    with _lock:
        items = _read_watchlist(gid)
        if not items or position < 1 or position > len(items):
            return await interaction.response.send_message(
                f"❌ No item at position {position}. Use `/watchlist show` to see the list.", ephemeral=True
            )
        removed = items.pop(position - 1)
        _write_watchlist(gid, items)
    await interaction.response.send_message(
        f"🗑 Removed **{removed['title']}** from the watchlist. {len(items)} item(s) remaining."
    )

@watchlist_group.command(name="clear", description="Clear the entire server watchlist")
async def cmd_wl_clear(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
    gid = str(interaction.guild_id)
    with _lock:
        _write_watchlist(gid, [])
    await interaction.response.send_message("🗑 Watchlist cleared.")


# ─── /wave-announce ─────────────────────────────────────────────────────────────
@bot.tree.command(
    name="wave-announce",
    description="Announce an upcoming watch party to the server",
    default_member_permissions=discord.Permissions(administrator=True),
)
@app_commands.describe(
    title="What you're watching (e.g. 'Movie Night — Interstellar')",
    when="When it starts (e.g. 'Tonight at 9 PM EST')",
    url="Optional stream URL to include in the announcement",
)
async def cmd_wave_announce(
    interaction: discord.Interaction,
    title: str,
    when: str,
    url: Optional[str] = None,
):
    gid   = str(interaction.guild_id)
    cfg   = _wcfg(gid)
    vc_id = cfg.get("voice_channel_id")
    tc_id = cfg.get("text_channel_id")

    e = discord.Embed(
        title="📣 Upcoming Watch Party!",
        description=f"**{title}**",
        color=ROSE,
        timestamp=datetime.now(timezone.utc),
    )
    e.add_field(name="🕐 When", value=when, inline=True)
    if vc_id:
        vc = interaction.guild.get_channel(int(vc_id))
        if vc:
            e.add_field(name="🔊 Where", value=vc.mention, inline=True)
    if url:
        e.add_field(name="📡 Stream URL",
                    value=f"`{url[:80]}{'…' if len(url)>80 else ''}`",
                    inline=False)
    e.add_field(name="How to join", value="A watch link will be posted when the host runs `/watchlist next` or `/watch`.", inline=False)
    e.set_footer(text="Wave📶 · Byte Labs · Streaming & Watch Parties")

    sent_to = None
    if tc_id:
        tc = interaction.guild.get_channel(int(tc_id))
        if tc:
            try:
                await tc.send("@everyone", embed=e)
                sent_to = tc.mention
            except Exception:
                pass

    if sent_to:
        await interaction.response.send_message(f"✅ Announcement posted to {sent_to}.", ephemeral=True)
    else:
        await interaction.response.send_message(embed=e)


# ══════════════════════════════════════════════════════════════════════════════
# THREAD ENTRY
# ══════════════════════════════════════════════════════════════════════════════
_bot_thread: Optional[threading.Thread] = None

def start_bot_thread():
    global _bot_thread
    if not TOKEN:
        logger.warning("[Wave📶] W_TOKEN not set — Wave📶 bot will not start.")
        return

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.start(TOKEN))
        except Exception as e:
            logger.error(f"[Wave📶] Bot crashed: {e}")
        finally:
            loop.close()

    _bot_thread = threading.Thread(target=_run, name="wave-bot", daemon=True)
    _bot_thread.start()
    logger.info("[Wave📶] Bot thread started.")
