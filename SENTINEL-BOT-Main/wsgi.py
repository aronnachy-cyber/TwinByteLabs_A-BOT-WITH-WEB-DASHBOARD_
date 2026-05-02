import threading
import time
import os
import urllib.request
from dashboard import app, set_bot_restart_callback, set_bot_config_refresh_callback

_bot_main = None


def _start_bot():
    global _bot_main
    try:
        import main as bot_main
        _bot_main = bot_main
        set_bot_restart_callback(bot_main.start_bot_thread)
        set_bot_config_refresh_callback(bot_main.refresh_config)
        bot_main.start_bot_thread()
        print("[WSGI] Discord bot thread started.")
    except Exception as e:
        import traceback
        print(f"[WSGI] Bot start error: {e}")
        traceback.print_exc()


def _bot_watchdog():
    """Check every 60 s that the bot thread is alive; restart if it has silently died."""
    time.sleep(90)
    while True:
        try:
            if _bot_main is not None:
                thread = getattr(_bot_main, '_bot_thread', None)
                if thread is not None and not thread.is_alive():
                    print("[WATCHDOG] Bot thread is dead — restarting automatically.")
                    _bot_main.start_bot_thread()
                    time.sleep(30)
        except Exception as e:
            print(f"[WATCHDOG] Error: {e}")
        time.sleep(60)


def _self_ping():
    """Ping /health every 10 minutes to prevent the process from going idle.
    Works on Render (RENDER_EXTERNAL_URL), Replit (REPLIT_DEV_DOMAIN),
    and any custom DASHBOARD_URL, falling back to localhost.
    """
    time.sleep(120)
    while True:
        try:
            # Priority: explicit DASHBOARD_URL > Render > Replit > localhost
            base = (
                os.environ.get("DASHBOARD_URL", "").rstrip("/")
                or (f"https://{os.environ['RENDER_EXTERNAL_URL']}" if "RENDER_EXTERNAL_URL" in os.environ else "")
                or (f"https://{os.environ['REPLIT_DEV_DOMAIN']}" if "REPLIT_DEV_DOMAIN" in os.environ else "")
            )
            if not base:
                port = int(os.environ.get("PORT", 5000))
                base = f"http://localhost:{port}"
            url = f"{base}/health"
            with urllib.request.urlopen(url, timeout=10) as resp:
                if resp.status != 200:
                    print(f"[KEEPALIVE] /health returned {resp.status}")
        except Exception as e:
            print(f"[KEEPALIVE] Ping failed: {e}")
        time.sleep(600)


_token_present = bool(os.environ.get("DISCORD_TOKEN", "").strip())
_disabled = os.environ.get("START_BOT_IN_WEB", "").lower() in {"0", "false", "no", "off"}

if _token_present and not _disabled:
    threading.Thread(target=_start_bot, daemon=True).start()
    threading.Thread(target=_bot_watchdog, daemon=True, name="bot-watchdog").start()
elif not _token_present:
    print("[WSGI] DISCORD_TOKEN not set — Discord bot will not start. "
          "Giveaways, autoresponder, reaction roles, and tickets need the bot running.")


def _start_server_official():
    try:
        import server_official
        server_official.start_bot_thread()
        print("[WSGI] Server Official bot thread started.")
    except Exception as e:
        import traceback
        print(f"[WSGI] Server Official start error: {e}")
        traceback.print_exc()


_server_token_present = bool(os.environ.get("SERVER_TOKEN", "").strip())
if _server_token_present and not _disabled:
    threading.Thread(target=_start_server_official, daemon=True).start()
elif not _server_token_present:
    print("[WSGI] SERVER_TOKEN not set — Server Official bot will not start. "
          "Set SERVER_TOKEN to a Discord bot token to enable scheduled announcements, giveaways, "
          "invite tracking and stats.")


def _start_tempo():
    try:
        import tempo as tempo_bot
        tempo_bot.start_bot_thread()
        print("[WSGI] Tempo bot thread started.")
    except Exception as e:
        import traceback
        print(f"[WSGI] Tempo start error: {e}")
        traceback.print_exc()


_tempo_token_present = bool(os.environ.get("T_TOKEN", "").strip())
if _tempo_token_present and not _disabled:
    threading.Thread(target=_start_tempo, daemon=True).start()
elif not _tempo_token_present:
    print("[WSGI] T_TOKEN not set — Tempo music bot will not start. "
          "Set T_TOKEN to a Discord bot token to enable music playback.")


def _start_pulse():
    try:
        import pulse as pulse_bot
        pulse_bot.start_bot_thread()
        print("[WSGI] Pulse bot thread started.")
    except Exception as e:
        import traceback
        print(f"[WSGI] Pulse start error: {e}")
        traceback.print_exc()


_pulse_token_present = bool(os.environ.get("P_TOKEN", "").strip())
if _pulse_token_present and not _disabled:
    threading.Thread(target=_start_pulse, daemon=True).start()
elif not _pulse_token_present:
    print("[WSGI] P_TOKEN not set — Pulse analytics bot will not start. "
          "Set P_TOKEN to a Discord bot token to enable analytics tracking.")

threading.Thread(target=_self_ping, daemon=True, name="self-keepalive").start()
