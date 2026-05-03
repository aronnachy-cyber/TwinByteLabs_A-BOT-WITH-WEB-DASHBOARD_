"""
ROCKSTAR🎸 — Byte Labs Music Bot
Token env var: T_TOKEN

Features:
  • Multi-source playback (YouTube, SoundCloud, direct URLs via yt-dlp + FFmpeg)
  • Smart per-guild queue with loop / shuffle
  • Saved playlists (per-guild, per-user) persisted to JSON
  • Audio filters: bassboost, nightcore, vaporwave, 8D, soft, karaoke
  • Volume control (0–200%)
  • 24/7 mode (bot stays in VC when everyone leaves)
  • Listener XP leaderboard (minutes listened)
  • DJ role permission layer
  • Slash commands for everything
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ── yt-dlp ────────────────────────────────────────────────────────────────────
try:
    import yt_dlp
    YT_DLP_OK = True
except ImportError:
    YT_DLP_OK = False
    logging.warning("[ROCKSTAR🎸] yt_dlp not found — music playback disabled")

logger = logging.getLogger("tempo")
logging.basicConfig(level=logging.INFO)

# ── Storage paths ──────────────────────────────────────────────────────────────
_DIR = Path(__file__).parent
_PLAYLISTS_FILE = _DIR / "tempo_playlists.json"
_LISTENERS_FILE = _DIR / "tempo_listeners.json"
_CONFIG_FILE    = _DIR / "tempo_config.json"
_lock = threading.Lock()

TOKEN = os.environ.get("T_TOKEN", "").strip()

# ══════════════════════════════════════════════════════════════════════════════
# JSON HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}

def _write_json(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)

# ══════════════════════════════════════════════════════════════════════════════
# LISTENER XP
# ══════════════════════════════════════════════════════════════════════════════
_listeners: Dict[str, Dict[str, int]] = {}   # guild_id → {user_id: seconds}
_session_starts: Dict[str, Dict[str, float]] = {}  # guild_id → {user_id: start_ts}

def _load_listeners():
    global _listeners
    _listeners = _read_json(_LISTENERS_FILE)

def _save_listeners():
    _write_json(_LISTENERS_FILE, _listeners)

def _tick_listener(guild_id: str, user_id: str):
    g = _session_starts.setdefault(str(guild_id), {})
    g[str(user_id)] = time.time()

def _stop_listener(guild_id: str, user_id: str):
    gid, uid = str(guild_id), str(user_id)
    start = _session_starts.get(gid, {}).pop(uid, None)
    if start is None:
        return
    elapsed = int(time.time() - start)
    g = _listeners.setdefault(gid, {})
    g[uid] = g.get(uid, 0) + elapsed
    _save_listeners()

def _get_leaderboard(guild_id: str, top: int = 10):
    gid = str(guild_id)
    data = _listeners.get(gid, {})
    sorted_entries = sorted(data.items(), key=lambda x: x[1], reverse=True)
    return sorted_entries[:top]

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG  (DJ role, 24/7 mode per guild)
# ══════════════════════════════════════════════════════════════════════════════
_cfg: Dict[str, dict] = {}

def _load_cfg():
    global _cfg
    _cfg = _read_json(_CONFIG_FILE)

def _save_cfg():
    _write_json(_CONFIG_FILE, _cfg)

def _guild_cfg(gid: str) -> dict:
    return _cfg.setdefault(str(gid), {})

def _set_guild_cfg(gid: str, key: str, val):
    _guild_cfg(gid)[key] = val
    _save_cfg()

# ══════════════════════════════════════════════════════════════════════════════
# PLAYLISTS
# ══════════════════════════════════════════════════════════════════════════════
_playlists: Dict[str, Dict[str, list]] = {}  # guild_id → {name: [track, ...]}

def _load_playlists():
    global _playlists
    _playlists = _read_json(_PLAYLISTS_FILE)

def _save_playlists():
    _write_json(_PLAYLISTS_FILE, _playlists)

# ══════════════════════════════════════════════════════════════════════════════
# AUDIO FILTERS
# ══════════════════════════════════════════════════════════════════════════════
FILTERS: Dict[str, Optional[str]] = {
    "none":       None,
    "bassboost":  "bass=g=15,dynaudnorm=f=200",
    "nightcore":  "atempo=1.06,asetrate=48000*1.25",
    "vaporwave":  "atempo=0.8,asetrate=48000*0.8",
    "8d":         "apulsator=hz=0.125",
    "soft":       "lowpass=f=5000",
    "karaoke":    "stereotools=mlev=0.1",
}

# ══════════════════════════════════════════════════════════════════════════════
# YT-DLP HELPERS
# ══════════════════════════════════════════════════════════════════════════════
YTDL_OPTS = {
    "format":            "bestaudio/best",
    "noplaylist":        True,
    "quiet":             True,
    "no_warnings":       True,
    "default_search":    "ytsearch",
    "source_address":    "0.0.0.0",
    "extract_flat":      False,
    "skip_download":     True,
}

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options":        "-vn",
}

def _ffmpeg_opts(af: Optional[str] = None) -> dict:
    opts = dict(FFMPEG_OPTS)
    if af:
        opts["options"] = f"-vn -af \"{af}\""
    return opts

async def _extract(query: str, loop: asyncio.AbstractEventLoop) -> Optional[dict]:
    if not YT_DLP_OK:
        return None
    try:
        ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        if not data:
            return None
        if "entries" in data:
            data = data["entries"][0]
        return data
    except Exception as e:
        logger.warning(f"[ROCKSTAR🎸] extract error: {e}")
        return None

def _track_from_data(data: dict, requester: discord.Member) -> dict:
    return {
        "url":        data.get("url") or data.get("webpage_url", ""),
        "webpage_url": data.get("webpage_url", ""),
        "title":      data.get("title", "Unknown"),
        "duration":   data.get("duration", 0),
        "thumbnail":  data.get("thumbnail", ""),
        "requester":  str(requester),
        "requester_id": requester.id,
    }

def _fmt_dur(seconds: int) -> str:
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

# ══════════════════════════════════════════════════════════════════════════════
# GUILD MUSIC STATE
# ══════════════════════════════════════════════════════════════════════════════
class GuildPlayer:
    def __init__(self, guild_id: int):
        self.guild_id   = guild_id
        self.queue:     List[dict] = []
        self.current:   Optional[dict] = None
        self.loop_mode: str = "none"   # "none" | "track" | "queue"
        self.volume:    float = 1.0    # 0.0–2.0
        self.af:        Optional[str] = None  # current audio filter string
        self.filter_name: str = "none"
        self.track_start: float = 0.0
        self._vc:       Optional[discord.VoiceClient] = None
        self._playing = False

    @property
    def vc(self) -> Optional[discord.VoiceClient]:
        return self._vc

    @vc.setter
    def vc(self, v: Optional[discord.VoiceClient]):
        self._vc = v

    def elapsed(self) -> int:
        if self._playing and self.track_start:
            return int(time.time() - self.track_start)
        return 0

_players: Dict[int, GuildPlayer] = {}

def _get_player(guild_id: int) -> GuildPlayer:
    if guild_id not in _players:
        _players[guild_id] = GuildPlayer(guild_id)
    return _players[guild_id]

# ══════════════════════════════════════════════════════════════════════════════
# BOT SETUP
# ══════════════════════════════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="t!", intents=intents, help_command=None)

# ── Global slash-command gate: admin / owner only ──────────────────────────────
@bot.tree.interaction_check
async def _admin_only(interaction: discord.Interaction) -> bool:
    """All ROCKSTAR🎸 slash commands are restricted to server admins and the owner."""
    if interaction.guild is None:
        await interaction.response.send_message(
            "⚠️ ROCKSTAR🎸 commands can only be used inside a server.", ephemeral=True
        )
        return False
    perms = interaction.user.guild_permissions
    if perms.administrator or interaction.guild.owner_id == interaction.user.id:
        return True
    await interaction.response.send_message(
        "🔒 Only **server administrators** and the **server owner** can use ROCKSTAR🎸 commands.",
        ephemeral=True,
    )
    return False

# ── Permission helper ──────────────────────────────────────────────────────────
def _is_dj(interaction: discord.Interaction) -> bool:
    gid = str(interaction.guild_id)
    dj_role_id = _guild_cfg(gid).get("dj_role")
    if not dj_role_id:
        return True   # no DJ role set → everyone can control
    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        return False
    if member.guild_permissions.manage_guild:
        return True
    return any(str(r.id) == str(dj_role_id) for r in member.roles)

async def _require_dj(interaction: discord.Interaction) -> bool:
    if not _is_dj(interaction):
        await interaction.response.send_message(
            "❌ You need the DJ role to use this command.", ephemeral=True
        )
        return False
    return True

async def _require_vc(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    # 1. User is already in a voice channel — use it
    if interaction.user.voice and interaction.user.voice.channel:
        return interaction.user.voice.channel
    # 2. Fall back to the server's configured default voice channel
    gid = str(interaction.guild_id)
    vc_id = _guild_cfg(gid).get("voice_channel_id")
    if vc_id:
        ch = interaction.guild.get_channel(int(vc_id))
        if isinstance(ch, discord.VoiceChannel):
            return ch
    await interaction.response.send_message(
        "❌ You're not in a voice channel — join one, or set a default with `/tempo-config`.",
        ephemeral=True,
    )
    return None

# ── Colour ──────────────────────────────────────────────────────────────────
PURPLE = 0x8b5cf6
PINK   = 0xe879f9

# ══════════════════════════════════════════════════════════════════════════════
# PLAYBACK ENGINE
# ══════════════════════════════════════════════════════════════════════════════
async def _play_next(guild: discord.Guild, player: GuildPlayer):
    """Advance to the next track or stop if queue is empty."""
    if not player.vc or not player.vc.is_connected():
        return

    # Loop-track: re-queue current at front
    if player.loop_mode == "track" and player.current:
        player.queue.insert(0, player.current)

    if not player.queue:
        player.current = None
        player._playing = False
        # check 24/7 mode
        if not _guild_cfg(str(guild.id)).get("mode_247"):
            await asyncio.sleep(300)   # 5 min idle then disconnect
            if player.vc and player.vc.is_connected() and not player._playing:
                await player.vc.disconnect()
        return

    track = player.queue.pop(0)

    # Loop-queue: push current to back
    if player.loop_mode == "queue" and player.current:
        player.queue.append(player.current)

    player.current = track
    player.track_start = time.time()
    player._playing = True

    try:
        # Re-extract the stream URL (they expire after a while)
        ytdl = yt_dlp.YoutubeDL({**YTDL_OPTS, "noplaylist": True})
        data = await guild._state.loop.run_in_executor(
            None, lambda: ytdl.extract_info(track["webpage_url"] or track["url"], download=False)
        )
        stream_url = data.get("url", track["url"])
    except Exception as e:
        logger.warning(f"[ROCKSTAR🎸] re-extract failed: {e}")
        stream_url = track["url"]

    source = discord.FFmpegPCMAudio(stream_url, **_ffmpeg_opts(player.af))
    source = discord.PCMVolumeTransformer(source, volume=player.volume)

    def after_play(err):
        if err:
            logger.warning(f"[ROCKSTAR🎸] playback error: {err}")
        player._playing = False
        asyncio.run_coroutine_threadsafe(_play_next(guild, player), guild._state.loop)

    if player.vc.is_playing():
        player.vc.stop()

    player.vc.play(source, after=after_play)

    # Post "Now Playing" card to the configured text channel (if set)
    gid = str(guild.id)
    tc_id = _guild_cfg(gid).get("text_channel_id")
    if tc_id:
        tc = guild.get_channel(int(tc_id))
        if tc:
            try:
                thumb = track.get("thumbnail")
                dur   = track.get("duration", 0)
                m, s  = divmod(int(dur), 60) if dur else (0, 0)
                e = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**{track.get('title', 'Unknown')}**",
                    color=PURPLE,
                )
                if thumb:
                    e.set_thumbnail(url=thumb)
                if dur:
                    e.add_field(name="Duration", value=f"{m}:{s:02d}", inline=True)
                e.add_field(name="Volume",    value=f"{int(player.volume*100)}%", inline=True)
                e.add_field(name="Loop",      value=player.loop_mode or "none",   inline=True)
                if player.queue:
                    next_track = player.queue[0]
                    e.add_field(name="Up Next", value=next_track.get("title", "?"), inline=False)
                e.set_footer(text=f"ROCKSTAR🎸 · {len(player.queue)} track(s) in queue")
                await tc.send(embed=e)
            except Exception as _np_err:
                logger.warning(f"[ROCKSTAR🎸] NP card post failed: {_np_err}")

    # Tick listener XP for all members in VC
    if player.vc.channel:
        for m in player.vc.channel.members:
            if not m.bot:
                _tick_listener(str(guild.id), str(m.id))

async def _ensure_vc(interaction: discord.Interaction, channel: discord.VoiceChannel) -> Optional[discord.VoiceClient]:
    player = _get_player(interaction.guild_id)
    if player.vc and player.vc.is_connected():
        if player.vc.channel != channel:
            await player.vc.move_to(channel)
        return player.vc
    vc = await channel.connect()
    player.vc = vc
    return vc

# ══════════════════════════════════════════════════════════════════════════════
# BOT EVENTS
# ══════════════════════════════════════════════════════════════════════════════
@bot.event
async def on_ready():
    _load_listeners()
    _load_playlists()
    _load_cfg()
    try:
        synced = await bot.tree.sync()
        logger.info(f"[ROCKSTAR🎸] Logged in as {bot.user} — synced {len(synced)} commands")
    except Exception as e:
        logger.error(f"[ROCKSTAR🎸] Sync failed: {e}")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    gid = str(member.guild.id)

    # User left VC or switched away from bot's VC
    if before.channel and (after.channel is None or after.channel != before.channel):
        _stop_listener(gid, str(member.id))

    # User joined a VC that has the bot playing
    if after.channel and not before.channel:
        player = _players.get(member.guild.id)
        if player and player._playing and player.vc and player.vc.channel == after.channel:
            _tick_listener(gid, str(member.id))

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

# ─── /play ────────────────────────────────────────────────────────────────────
@bot.tree.command(name="play", description="Play a song by name or URL", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(query="Song name, YouTube URL, playlist link, etc.")
async def cmd_play(interaction: discord.Interaction, query: str):
    vc_channel = await _require_vc(interaction)
    if not vc_channel:
        return
    if not YT_DLP_OK:
        return await interaction.response.send_message("❌ yt-dlp is not installed.", ephemeral=True)

    await interaction.response.defer(thinking=True)

    data = await _extract(query, interaction.client.loop)
    if not data:
        return await interaction.followup.send("❌ Could not find anything for that query.")

    track = _track_from_data(data, interaction.user)
    player = _get_player(interaction.guild_id)
    await _ensure_vc(interaction, vc_channel)

    player.queue.append(track)

    if not player._playing:
        await _play_next(interaction.guild, player)
        e = discord.Embed(
            title="▶️ Now Playing",
            description=f"**[{track['title']}]({track['webpage_url']})**",
            color=PURPLE
        )
    else:
        pos = len(player.queue)
        e = discord.Embed(
            title="➕ Added to Queue",
            description=f"**[{track['title']}]({track['webpage_url']})**",
            color=PINK
        )
        e.add_field(name="Position", value=f"#{pos}", inline=True)

    if track["thumbnail"]:
        e.set_thumbnail(url=track["thumbnail"])
    e.add_field(name="Duration", value=_fmt_dur(track["duration"]), inline=True)
    e.add_field(name="Requested by", value=track["requester"], inline=True)
    e.set_footer(text="ROCKSTAR🎸 · Byte Labs")
    await interaction.followup.send(embed=e)


# ─── /pause ───────────────────────────────────────────────────────────────────
@bot.tree.command(name="pause", description="Pause playback", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_pause(interaction: discord.Interaction):
    if not await _require_dj(interaction): return
    player = _get_player(interaction.guild_id)
    if player.vc and player.vc.is_playing():
        player.vc.pause()
        await interaction.response.send_message("⏸ Paused.", ephemeral=True)
    else:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)


# ─── /resume ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="resume", description="Resume playback", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_resume(interaction: discord.Interaction):
    if not await _require_dj(interaction): return
    player = _get_player(interaction.guild_id)
    if player.vc and player.vc.is_paused():
        player.vc.resume()
        await interaction.response.send_message("▶️ Resumed.", ephemeral=True)
    else:
        await interaction.response.send_message("Nothing is paused.", ephemeral=True)


# ─── /skip ────────────────────────────────────────────────────────────────────
@bot.tree.command(name="skip", description="Skip the current track", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_skip(interaction: discord.Interaction):
    if not await _require_dj(interaction): return
    player = _get_player(interaction.guild_id)
    if player.vc and (player.vc.is_playing() or player.vc.is_paused()):
        player.vc.stop()
        await interaction.response.send_message("⏭ Skipped.", ephemeral=True)
    else:
        await interaction.response.send_message("Nothing to skip.", ephemeral=True)


# ─── /stop ────────────────────────────────────────────────────────────────────
@bot.tree.command(name="stop", description="Stop playback and clear the queue", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_stop(interaction: discord.Interaction):
    if not await _require_dj(interaction): return
    player = _get_player(interaction.guild_id)
    player.queue.clear()
    player.current = None
    player.loop_mode = "none"
    if player.vc:
        if player.vc.is_playing() or player.vc.is_paused():
            player.vc.stop()
    await interaction.response.send_message("⏹ Stopped and queue cleared.")


# ─── /nowplaying ──────────────────────────────────────────────────────────────
@bot.tree.command(name="nowplaying", description="Show the currently playing track", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_np(interaction: discord.Interaction):
    player = _get_player(interaction.guild_id)
    if not player.current:
        return await interaction.response.send_message("Nothing is playing right now.", ephemeral=True)
    t = player.current
    elapsed = player.elapsed()
    total = t.get("duration", 0)

    bar_len = 20
    filled = int(bar_len * elapsed / total) if total else 0
    bar = "█" * filled + "░" * (bar_len - filled)

    e = discord.Embed(
        title="🎵 Now Playing",
        description=f"**[{t['title']}]({t['webpage_url']})**",
        color=PURPLE
    )
    if t.get("thumbnail"):
        e.set_thumbnail(url=t["thumbnail"])
    e.add_field(name="Progress", value=f"`{_fmt_dur(elapsed)}` {bar} `{_fmt_dur(total)}`", inline=False)
    e.add_field(name="Volume",   value=f"{int(player.volume*100)}%", inline=True)
    e.add_field(name="Loop",     value=player.loop_mode.title(), inline=True)
    e.add_field(name="Filter",   value=player.filter_name.title(), inline=True)
    e.add_field(name="Requested by", value=t["requester"], inline=True)
    e.add_field(name="Queue",    value=f"{len(player.queue)} track(s) up next", inline=True)
    e.set_footer(text="ROCKSTAR🎸 · Byte Labs")
    await interaction.response.send_message(embed=e)


# ─── /queue ───────────────────────────────────────────────────────────────────
@bot.tree.command(name="queue", description="View the current queue", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(page="Page number (10 tracks per page)")
async def cmd_queue(interaction: discord.Interaction, page: int = 1):
    player = _get_player(interaction.guild_id)
    per_page = 10
    q = player.queue
    total_pages = max(1, math.ceil(len(q) / per_page))
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    chunk = q[start:start + per_page]

    e = discord.Embed(title="📋 Queue", color=PURPLE)
    if player.current:
        e.add_field(
            name="▶️ Now Playing",
            value=f"**{player.current['title']}** — {_fmt_dur(player.current.get('duration', 0))}",
            inline=False
        )
    if chunk:
        lines = []
        for i, t in enumerate(chunk, start=start + 1):
            lines.append(f"`{i}.` **{t['title']}** — {_fmt_dur(t.get('duration', 0))}")
        e.add_field(name=f"Up Next (page {page}/{total_pages})", value="\n".join(lines), inline=False)
    else:
        e.add_field(name="Up Next", value="Queue is empty.", inline=False)

    total_dur = sum(t.get("duration", 0) for t in q)
    e.set_footer(text=f"{len(q)} tracks · Total {_fmt_dur(total_dur)} · Loop: {player.loop_mode} · ROCKSTAR🎸 · Byte Labs")
    await interaction.response.send_message(embed=e)


# ─── /volume ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="volume", description="Set playback volume (0–200)", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(level="Volume level from 0 to 200")
async def cmd_volume(interaction: discord.Interaction, level: int):
    if not await _require_dj(interaction): return
    level = max(0, min(200, level))
    player = _get_player(interaction.guild_id)
    player.volume = level / 100.0
    if player.vc and player.vc.source:
        player.vc.source.volume = player.volume
    bar = "█" * int(level / 10) + "░" * (20 - int(level / 10))
    await interaction.response.send_message(f"🔊 Volume set to **{level}%**\n`{bar}`")


# ─── /loop ────────────────────────────────────────────────────────────────────
@bot.tree.command(name="loop", description="Set loop mode: none / track / queue", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(mode="Loop mode to set")
@app_commands.choices(mode=[
    app_commands.Choice(name="none",  value="none"),
    app_commands.Choice(name="track", value="track"),
    app_commands.Choice(name="queue", value="queue"),
])
async def cmd_loop(interaction: discord.Interaction, mode: str):
    if not await _require_dj(interaction): return
    player = _get_player(interaction.guild_id)
    player.loop_mode = mode
    icons = {"none": "➡️", "track": "🔂", "queue": "🔁"}
    await interaction.response.send_message(f"{icons[mode]} Loop set to **{mode}**.")


# ─── /shuffle ─────────────────────────────────────────────────────────────────
@bot.tree.command(name="shuffle", description="Shuffle the current queue", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_shuffle(interaction: discord.Interaction):
    if not await _require_dj(interaction): return
    player = _get_player(interaction.guild_id)
    if not player.queue:
        return await interaction.response.send_message("Queue is empty.", ephemeral=True)
    random.shuffle(player.queue)
    await interaction.response.send_message(f"🔀 Shuffled **{len(player.queue)}** tracks.")


# ─── /remove ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="remove", description="Remove a track from the queue by position", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(position="Position number from /queue")
async def cmd_remove(interaction: discord.Interaction, position: int):
    if not await _require_dj(interaction): return
    player = _get_player(interaction.guild_id)
    if position < 1 or position > len(player.queue):
        return await interaction.response.send_message("❌ Invalid position.", ephemeral=True)
    removed = player.queue.pop(position - 1)
    await interaction.response.send_message(f"🗑 Removed **{removed['title']}** from queue.")


# ─── /move ────────────────────────────────────────────────────────────────────
@bot.tree.command(name="move", description="Move a track to a new position in the queue", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(from_pos="Current position", to_pos="Target position")
async def cmd_move(interaction: discord.Interaction, from_pos: int, to_pos: int):
    if not await _require_dj(interaction): return
    player = _get_player(interaction.guild_id)
    q = player.queue
    if not (1 <= from_pos <= len(q)) or not (1 <= to_pos <= len(q)):
        return await interaction.response.send_message("❌ Invalid position(s).", ephemeral=True)
    track = q.pop(from_pos - 1)
    q.insert(to_pos - 1, track)
    await interaction.response.send_message(f"↔️ Moved **{track['title']}** to position **{to_pos}**.")


# ─── /filter ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="filter", description="Apply an audio filter to playback", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(name="Filter to apply")
@app_commands.choices(name=[app_commands.Choice(name=k, value=k) for k in FILTERS])
async def cmd_filter(interaction: discord.Interaction, name: str):
    if not await _require_dj(interaction): return
    player = _get_player(interaction.guild_id)
    player.af = FILTERS.get(name)
    player.filter_name = name

    # Restart current track with new filter
    if player.vc and (player.vc.is_playing() or player.vc.is_paused()) and player.current:
        player.queue.insert(0, player.current)
        player.vc.stop()   # after_play callback will call _play_next
        await interaction.response.send_message(f"🎛 Filter set to **{name}** — restarting track.")
    else:
        await interaction.response.send_message(f"🎛 Filter set to **{name}**. Will apply to next track.")


# ─── /247 ─────────────────────────────────────────────────────────────────────
@bot.tree.command(name="247", description="Toggle 24/7 mode — ROCKSTAR🎸 stays in VC permanently", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_247(interaction: discord.Interaction):
    if not await _require_dj(interaction): return
    gid = str(interaction.guild_id)
    current = _guild_cfg(gid).get("mode_247", False)
    _set_guild_cfg(gid, "mode_247", not current)
    state = "enabled ✅" if not current else "disabled ⛔"
    await interaction.response.send_message(f"🕐 24/7 mode {state}.")


# ─── /djrole ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="djrole", description="Set the DJ role (only this role can control ROCKSTAR🎸)", default_member_permissions=discord.Permissions(administrator=True))
@app_commands.describe(role="The role to assign as DJ")
async def cmd_djrole(interaction: discord.Interaction, role: Optional[discord.Role] = None):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
    gid = str(interaction.guild_id)
    if role is None:
        _set_guild_cfg(gid, "dj_role", None)
        await interaction.response.send_message("🎧 DJ role cleared — everyone can now control ROCKSTAR🎸.")
    else:
        _set_guild_cfg(gid, "dj_role", str(role.id))
        await interaction.response.send_message(f"🎧 DJ role set to **{role.name}**.")


# ─── /tempo-config ────────────────────────────────────────────────────────────
@bot.tree.command(
    name="tempo-config",
    description="View or set ROCKSTAR🎸's default voice & Now Playing text channel for this server",
    default_member_permissions=discord.Permissions(administrator=True),
)
@app_commands.describe(
    voice_channel="Default voice channel ROCKSTAR🎸 joins when you're not already in one",
    text_channel="Text channel where ROCKSTAR🎸 posts Now Playing cards automatically",
)
async def cmd_tempo_config(
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
        _set_guild_cfg(gid, "voice_channel_id", str(voice_channel.id))
        changed.append(f"🔊 Default voice channel → **{voice_channel.name}**")
    if text_channel is not None:
        _set_guild_cfg(gid, "text_channel_id", str(text_channel.id))
        changed.append(f"💬 Now Playing channel → {text_channel.mention}")
    if changed:
        e = discord.Embed(title="✅ ROCKSTAR🎸 Config Updated", color=PURPLE, description="\n".join(changed))
        return await interaction.response.send_message(embed=e, ephemeral=True)
    # No args — show current config
    cfg  = _guild_cfg(gid)
    vc_id = cfg.get("voice_channel_id")
    tc_id = cfg.get("text_channel_id")
    vc_ch = interaction.guild.get_channel(int(vc_id)) if vc_id else None
    tc_ch = interaction.guild.get_channel(int(tc_id)) if tc_id else None
    e = discord.Embed(title="⚙️ ROCKSTAR🎸 Config", color=PURPLE)
    e.add_field(name="🔊 Default Voice Channel", value=vc_ch.name if vc_ch else "Not set", inline=False)
    e.add_field(name="💬 Now Playing Channel",   value=tc_ch.mention if tc_ch else "Not set", inline=False)
    e.add_field(name="🎧 DJ Role",  value=f"<@&{cfg['dj_role']}>" if cfg.get("dj_role") else "Not set", inline=True)
    e.add_field(name="🕐 24/7 Mode", value="Enabled ✅" if cfg.get("mode_247") else "Disabled ⛔", inline=True)
    await interaction.response.send_message(embed=e, ephemeral=True)


# ─── /music-top ───────────────────────────────────────────────────────────────
@bot.tree.command(name="music-top", description="Show the listener leaderboard", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_music_top(interaction: discord.Interaction):
    await interaction.response.defer()
    board = _get_leaderboard(str(interaction.guild_id))
    if not board:
        return await interaction.followup.send("No listener data yet. Start playing some music!")

    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, secs) in enumerate(board):
        mins = secs // 60
        medal = medals[i] if i < 3 else f"`{i+1}.`"
        try:
            member = interaction.guild.get_member(int(uid)) or await interaction.guild.fetch_member(int(uid))
            name = member.display_name
        except Exception:
            name = f"User {uid}"
        lines.append(f"{medal} **{name}** — {mins:,} min listened")

    e = discord.Embed(
        title="🏆 Listener Leaderboard",
        description="\n".join(lines),
        color=PURPLE
    )
    e.set_footer(text="ROCKSTAR🎸 · Byte Labs")
    await interaction.followup.send(embed=e)


# ─── /playlist ────────────────────────────────────────────────────────────────
playlist_group = app_commands.Group(
    name="playlist",
    description="Manage saved playlists",
    default_member_permissions=discord.Permissions(administrator=True),
)
bot.tree.add_command(playlist_group)

@playlist_group.command(name="save", description="Save the current queue as a named playlist")
@app_commands.describe(name="Name for this playlist")
async def cmd_pl_save(interaction: discord.Interaction, name: str):
    player = _get_player(interaction.guild_id)
    tracks = []
    if player.current:
        tracks.append(player.current)
    tracks.extend(player.queue)
    if not tracks:
        return await interaction.response.send_message("Queue is empty — nothing to save.", ephemeral=True)
    gid = str(interaction.guild_id)
    g = _playlists.setdefault(gid, {})
    g[name] = tracks
    _save_playlists()
    await interaction.response.send_message(f"💾 Playlist **{name}** saved with **{len(tracks)}** tracks.")

@playlist_group.command(name="load", description="Load a saved playlist into the queue")
@app_commands.describe(name="Playlist name to load")
async def cmd_pl_load(interaction: discord.Interaction, name: str):
    vc_channel = await _require_vc(interaction)
    if not vc_channel: return
    gid = str(interaction.guild_id)
    tracks = _playlists.get(gid, {}).get(name)
    if not tracks:
        return await interaction.response.send_message(f"❌ No playlist named **{name}**.", ephemeral=True)
    player = _get_player(interaction.guild_id)
    await _ensure_vc(interaction, vc_channel)
    player.queue.extend(tracks)
    if not player._playing:
        await _play_next(interaction.guild, player)
    await interaction.response.send_message(f"📂 Loaded **{name}** — **{len(tracks)}** tracks added to queue.")

@playlist_group.command(name="list", description="List all saved playlists in this server")
async def cmd_pl_list(interaction: discord.Interaction):
    gid = str(interaction.guild_id)
    plists = _playlists.get(gid, {})
    if not plists:
        return await interaction.response.send_message("No saved playlists yet. Use `/playlist save`.", ephemeral=True)
    lines = [f"• **{n}** — {len(t)} tracks" for n, t in plists.items()]
    e = discord.Embed(title="📚 Saved Playlists", description="\n".join(lines), color=PURPLE)
    e.set_footer(text="ROCKSTAR🎸 · Byte Labs")
    await interaction.response.send_message(embed=e)

@playlist_group.command(name="delete", description="Delete a saved playlist")
@app_commands.describe(name="Playlist name to delete")
async def cmd_pl_del(interaction: discord.Interaction, name: str):
    if not await _require_dj(interaction): return
    gid = str(interaction.guild_id)
    if name not in _playlists.get(gid, {}):
        return await interaction.response.send_message(f"❌ No playlist named **{name}**.", ephemeral=True)
    del _playlists[gid][name]
    _save_playlists()
    await interaction.response.send_message(f"🗑 Playlist **{name}** deleted.")

@playlist_group.command(name="show", description="Show all tracks in a saved playlist")
@app_commands.describe(name="Playlist name to inspect")
async def cmd_pl_show(interaction: discord.Interaction, name: str):
    gid = str(interaction.guild_id)
    tracks = _playlists.get(gid, {}).get(name)
    if not tracks:
        return await interaction.response.send_message(f"❌ No playlist named **{name}**.", ephemeral=True)
    lines = []
    for i, t in enumerate(tracks[:20], 1):
        dur = t.get("duration", 0)
        m, s = divmod(int(dur), 60) if dur else (0, 0)
        dur_str = f"`{m}:{s:02d}`" if dur else ""
        lines.append(f"`{i}.` **{t.get('title','Unknown')}** {dur_str}")
    remaining = len(tracks) - 20
    desc = "\n".join(lines)
    if remaining > 0:
        desc += f"\n*…and {remaining} more*"
    e = discord.Embed(title=f"🎵 Playlist — {name}", description=desc, color=PURPLE)
    e.set_footer(text=f"ROCKSTAR🎸 · {len(tracks)} tracks total")
    await interaction.response.send_message(embed=e, ephemeral=True)

@playlist_group.command(name="add", description="Search for a song and add it directly to a saved playlist")
@app_commands.describe(name="Playlist name", query="Song name or URL to add")
async def cmd_pl_add(interaction: discord.Interaction, name: str, query: str):
    if not await _require_dj(interaction): return
    gid = str(interaction.guild_id)
    if name not in _playlists.get(gid, {}):
        return await interaction.response.send_message(f"❌ No playlist named **{name}**. Create it with `/playlist save`.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        ytdl = yt_dlp.YoutubeDL({**YTDL_OPTS, "noplaylist": True})
        data = await interaction.guild._state.loop.run_in_executor(
            None, lambda: ytdl.extract_info(query, download=False)
        )
        entries = data.get("entries", [data])
        added = []
        for entry in entries[:10]:
            if not entry:
                continue
            track = {
                "title":       entry.get("title", query),
                "url":         entry.get("url", ""),
                "webpage_url": entry.get("webpage_url", query),
                "duration":    entry.get("duration", 0),
                "thumbnail":   entry.get("thumbnail", ""),
                "uploader":    entry.get("uploader", ""),
            }
            _playlists[gid][name].append(track)
            added.append(track["title"])
        _save_playlists()
        desc = "\n".join(f"• **{t}**" for t in added) if added else "No tracks found."
        e = discord.Embed(title=f"✅ Added to **{name}**", description=desc, color=PURPLE)
        e.set_footer(text=f"ROCKSTAR🎸 · Playlist now has {len(_playlists[gid][name])} tracks")
        await interaction.followup.send(embed=e, ephemeral=True)
    except Exception as ex:
        await interaction.followup.send(f"❌ Could not find that track: `{ex}`", ephemeral=True)

@playlist_group.command(name="rename", description="Rename an existing playlist")
@app_commands.describe(old_name="Current playlist name", new_name="New name for the playlist")
async def cmd_pl_rename(interaction: discord.Interaction, old_name: str, new_name: str):
    if not await _require_dj(interaction): return
    gid = str(interaction.guild_id)
    guild_pls = _playlists.get(gid, {})
    if old_name not in guild_pls:
        return await interaction.response.send_message(f"❌ No playlist named **{old_name}**.", ephemeral=True)
    if new_name in guild_pls:
        return await interaction.response.send_message(f"❌ A playlist named **{new_name}** already exists.", ephemeral=True)
    guild_pls[new_name] = guild_pls.pop(old_name)
    _save_playlists()
    await interaction.response.send_message(f"✏️ Renamed **{old_name}** → **{new_name}**.", ephemeral=True)


# ─── /disconnect ──────────────────────────────────────────────────────────────
@bot.tree.command(name="disconnect", description="Disconnect ROCKSTAR🎸 from the voice channel", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_dc(interaction: discord.Interaction):
    if not await _require_dj(interaction): return
    player = _get_player(interaction.guild_id)
    if player.vc and player.vc.is_connected():
        player.queue.clear()
        player.current = None
        player._playing = False
        await player.vc.disconnect()
        player.vc = None
        await interaction.response.send_message("👋 Disconnected.")
    else:
        await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)


# ─── /tempo-help ──────────────────────────────────────────────────────────────
@bot.tree.command(name="tempo-help", description="Show all ROCKSTAR🎸 commands", default_member_permissions=discord.Permissions(administrator=True))
async def cmd_help(interaction: discord.Interaction):
    e = discord.Embed(
        title="🎵 ROCKSTAR🎸 — Command Reference",
        description="High-quality music for your Discord server.",
        color=PURPLE
    )
    sections = {
        "▶️ Playback": [
            "`/play <query>` — Play a song or URL",
            "`/pause` · `/resume` · `/skip` · `/stop`",
            "`/nowplaying` — Detailed now-playing card",
            "`/volume <0–200>` — Set volume",
        ],
        "📋 Queue": [
            "`/queue [page]` — View the queue",
            "`/shuffle` — Randomise queue order",
            "`/remove <pos>` — Remove a track",
            "`/move <from> <to>` — Reorder queue",
            "`/loop <none|track|queue>` — Loop modes",
        ],
        "🎛 Effects": [
            "`/filter <name>` — bassboost, nightcore, vaporwave, 8D, soft, karaoke, none",
        ],
        "💾 Playlists": [
            "`/playlist save <name>` — Save current queue",
            "`/playlist load <name>` — Load playlist",
            "`/playlist list` — See all playlists",
            "`/playlist delete <name>` — Remove playlist",
        ],
        "🏆 Leaderboard": ["`/music-top` — Listener leaderboard"],
        "⚙️ Settings": [
            "`/djrole [role]` — Set/clear DJ role",
            "`/247` — Toggle 24/7 VC mode",
            "`/disconnect` — Leave voice channel",
        ],
    }
    for title, lines in sections.items():
        e.add_field(name=title, value="\n".join(lines), inline=False)
    e.set_footer(text="ROCKSTAR🎸 · Byte Labs · Music & Voice Engagement")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# THREAD ENTRY POINT  (called from wsgi.py)
# ══════════════════════════════════════════════════════════════════════════════
_bot_thread: Optional[threading.Thread] = None

def start_bot_thread():
    global _bot_thread
    if not TOKEN:
        logger.warning("[ROCKSTAR🎸] T_TOKEN not set — ROCKSTAR🎸 bot will not start.")
        return

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.start(TOKEN))
        except Exception as e:
            logger.error(f"[ROCKSTAR🎸] Bot crashed: {e}")
        finally:
            loop.close()

    _bot_thread = threading.Thread(target=_run, name="tempo-bot", daemon=True)
    _bot_thread.start()
    logger.info("[ROCKSTAR🎸] Bot thread started.")
