import threading
import os
from dashboard import app

def _start_bot():
    try:
        import main
        main.set_bot_restart_callback(main.start_bot_thread)
        main.start_bot_thread()
    except Exception as e:
        print(f"[WSGI] Bot start error: {e}")

if os.environ.get("START_BOT_IN_WEB", "false").lower() in {"1", "true", "yes", "on"}:
    threading.Thread(target=_start_bot, daemon=True).start()
