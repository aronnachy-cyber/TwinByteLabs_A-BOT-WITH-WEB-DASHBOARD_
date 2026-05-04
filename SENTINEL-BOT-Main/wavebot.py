import os
import json
import threading
import asyncio
from pathlib import Path

import discord
from discord import app_commands

W_CONFIG = Path(__file__).parent / 'wave_config.json'
_bot_thread: threading.Thread | None = None


def _read_cfg() -> dict:
    try:
        if W_CONFIG.exists():
            return json.loads(W_CONFIG.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}


def _write_cfg(data: dict):
    tmp = W_CONFIG.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    tmp.replace(W_CONFIG)


intents = discord.Intents.default()
_client = discord.Client(intents=intents)
_tree = app_commands.CommandTree(_client)


@_client.event
async def on_ready():
    try:
        await _tree.sync()
    except Exception as e:
        print(f"[Wave] Sync error: {e}")
    print(f"[Wave] Logged in as {_client.user} ({_client.user.id})")


@_tree.command(
    name="here",
    description="Set this channel as the Wave stream announcement channel (admin only)"
)
@app_commands.checks.has_permissions(administrator=True)
async def here_cmd(interaction: discord.Interaction):
    cfg = _read_cfg()
    gid = str(interaction.guild_id)
    cfg.setdefault(gid, {})['stream_channel'] = str(interaction.channel_id)
    _write_cfg(cfg)
    await interaction.response.send_message(
        "📡 **Wave** stream announcements will be posted in this channel.\n"
        "Use `/start` to launch a watch party.",
        ephemeral=True
    )


@_tree.command(
    name="start",
    description="Start a Wave streaming session (admin only)"
)
@app_commands.describe(
    title="Title shown to viewers",
    url="Optional starting URL (e.g. https://cricstreams.tv/)"
)
@app_commands.checks.has_permissions(administrator=True)
async def start_cmd(
    interaction: discord.Interaction,
    title: str = "Watch Party",
    url: str = ""
):
    await interaction.response.defer(ephemeral=True)
    cfg = _read_cfg()
    gid = str(interaction.guild_id)
    channel_id = cfg.get(gid, {}).get('stream_channel')
    if not channel_id:
        await interaction.followup.send(
            "❌ No stream channel set.\n"
            "Run `/here` in the channel where announcements should be posted first.",
            ephemeral=True
        )
        return

    import urllib.request
    try:
        base = (
            os.environ.get("DASHBOARD_URL", "").rstrip("/")
            or (f"https://{os.environ['REPLIT_DEV_DOMAIN']}"
                if "REPLIT_DEV_DOMAIN" in os.environ else "")
            or f"http://localhost:{os.environ.get('PORT', 5000)}"
        )
        payload = json.dumps({
            "title": title,
            "url": url.strip(),
            "guild_id": gid
        }).encode()
        req = urllib.request.Request(
            f"{base}/api/wave/create",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        if not result.get("ok"):
            raise Exception(result.get("error", "Unknown error"))

        watch_url = result["watch_url"]
        host_url = result["host_url"]

        channel = interaction.guild.get_channel(int(channel_id))
        if channel:
            embed = discord.Embed(
                title=f"📡  {title}",
                description=(
                    f"A live streaming session has started!\n\n"
                    f"**[🎬  Join the Stream]({watch_url})**\n\n"
                    f"Click the link to watch together in your browser."
                ),
                color=0xf43f5e
            )
            embed.set_footer(text="Wave by Byte Labs • Streaming & Watch Parties")
            await channel.send(embed=embed)

        await interaction.followup.send(
            f"✅ **Stream started!**\n\n"
            f"🎬 **Host link** (your private control panel):\n{host_url}\n\n"
            f"👥 **Viewer link** (share in your server):\n{watch_url}",
            ephemeral=True
        )

    except Exception as e:
        await interaction.followup.send(
            f"❌ Could not start stream: `{e}`", ephemeral=True
        )


@_tree.error
async def on_tree_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    msg = (
        "❌ Administrator permission required to use this command."
        if isinstance(error, app_commands.MissingPermissions)
        else f"❌ Error: {error}"
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


def start_bot_thread():
    global _bot_thread
    token = os.environ.get("W_TOKEN", "").strip()
    if not token:
        print("[Wave] W_TOKEN not set — Wave bot will not start.")
        return

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_client.start(token))
        except Exception as e:
            print(f"[Wave] Bot error: {e}")
        finally:
            loop.close()

    _bot_thread = threading.Thread(target=_run, daemon=True, name="wave-bot")
    _bot_thread.start()
    print("[Wave] Bot thread started.")
