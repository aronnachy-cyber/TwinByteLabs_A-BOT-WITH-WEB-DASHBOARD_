import threading
import os
from dashboard import app, set_bot_restart_callback, set_bot_config_refresh_callback

def _start_bot():
    try:
        import main as bot_main
        set_bot_restart_callback(bot_main.start_bot_thread)
        set_bot_config_refresh_callback(bot_main.refresh_config)
        bot_main.start_bot_thread()
        print("[WSGI] Discord bot thread started.")
    except Exception as e:
        import traceback
        print(f"[WSGI] Bot start error: {e}")
        traceback.print_exc()

# Auto-start the Discord bot whenever a token is configured, unless explicitly disabled.
_token_present = bool(os.environ.get("DISCORD_TOKEN", "").strip())
_disabled = os.environ.get("START_BOT_IN_WEB", "").lower() in {"0", "false", "no", "off"}

if _token_present and not _disabled:
    threading.Thread(target=_start_bot, daemon=True).start()
elif not _token_present:
    print("[WSGI] DISCORD_TOKEN not set — Discord bot will not start. "
          "Giveaways, XP/leveling, autoresponder, reaction roles, and tickets need the bot running.")

# ---- Server Official (second bot, separate token) ----
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
