"""
Wave — Byte Labs Streaming & Watch Party Bot
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
_lock  = threading.Lock()

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

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        logger.info(f"[Wave] Logged in as {bot.user} — synced {len(synced)} commands")
    except Exception as e:
        logger.error(f"[Wave] Sync failed: {e}")

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
@bot.tree.command(name="watch", description="Create a Wave watch-party room and share the link")
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

    e = discord.Embed(
        title="📺 Watch Party Started!",
        description=f"**{title}**\n\nA watch-party room is ready. Click below to join!",
        color=ROSE,
        timestamp=datetime.now(timezone.utc)
    )
    e.add_field(name="🔗 Watch Link",   value=f"[Open Room]({watch_url})", inline=True)
    e.add_field(name="⚙️ Host Controls", value=f"[Host View]({host_url})", inline=True)
    if url:
        e.add_field(name="📡 Stream URL", value=f"`{url[:80]}{'…' if len(url)>80 else ''}`", inline=False)
    e.add_field(
        name="Commands",
        value="`/sync` — resync all viewers\n`/status` — check room status\n`/endstream` — close room",
        inline=False
    )
    e.set_footer(text="Wave · Byte Labs · Streaming & Watch Parties")
    await interaction.followup.send(embed=e)


# ─── /sync ────────────────────────────────────────────────────────────────────
@bot.tree.command(name="sync", description="Force all viewers to sync to the host's current position")
async def cmd_sync(interaction: discord.Interaction):
    await interaction.response.defer()
    guild_id = str(interaction.guild_id)
    room     = _get_room_by_guild(guild_id)
    if not room:
        return await interaction.followup.send("❌ No active Wave room for this server. Use `/watch` to create one.")

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
    e.set_footer(text="Wave · Byte Labs")
    await interaction.followup.send(embed=e)


# ─── /status ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="status", description="Check the current Wave watch-party room status")
async def cmd_status(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    room     = _get_room_by_guild(guild_id)
    if not room:
        return await interaction.response.send_message("No active Wave room for this server.", ephemeral=True)

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

    e = discord.Embed(title=f"📊 Wave Room — {room['title']}", color=ROSE)
    e.add_field(name="Status",     value=state,   inline=True)
    e.add_field(name="Position",   value=pos_str, inline=True)
    e.add_field(name="Stream URL", value=f"`{url[:60]}{'…' if len(url)>60 else ''}`" if url != "Not set" else "Not set", inline=False)
    e.add_field(name="Watch Link", value=f"[Join Room]({watch_url})", inline=False)
    e.set_footer(text="Wave · Byte Labs")
    await interaction.response.send_message(embed=e)


# ─── /endstream ───────────────────────────────────────────────────────────────
@bot.tree.command(name="endstream", description="End the current watch-party session")
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
@bot.tree.command(name="wave-help", description="Show all Wave streaming commands")
async def cmd_help(interaction: discord.Interaction):
    e = discord.Embed(
        title="📺 Wave — Command Reference",
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
        "Wave creates a web room all your members join.\n"
        "Paste any stream URL — YouTube, HLS, direct video.\n"
        "The host controls play/pause/seek for everyone.\n"
        "Chat overlay keeps the watch-party vibe alive."
    ), inline=False)
    e.add_field(name="🔗 Platform", value="[bytelabs.dev/wave](https://bytelabs.dev/wave)", inline=False)
    e.set_footer(text="Wave · Byte Labs · Streaming & Watch Parties")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# THREAD ENTRY
# ══════════════════════════════════════════════════════════════════════════════
_bot_thread: Optional[threading.Thread] = None

def start_bot_thread():
    global _bot_thread
    if not TOKEN:
        logger.warning("[Wave] W_TOKEN not set — Wave bot will not start.")
        return

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.start(TOKEN))
        except Exception as e:
            logger.error(f"[Wave] Bot crashed: {e}")
        finally:
            loop.close()

    _bot_thread = threading.Thread(target=_run, name="wave-bot", daemon=True)
    _bot_thread.start()
    logger.info("[Wave] Bot thread started.")
