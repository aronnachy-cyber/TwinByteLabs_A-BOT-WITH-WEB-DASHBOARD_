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
