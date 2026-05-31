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
    """Ping /health every 4 minutes to prevent the process from going idle."""
    time.sleep(30)
    while True:
        try:
            _replit_domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
            base = (
                os.environ.get("DASHBOARD_URL", "").rstrip("/")
                or (f"https://{_replit_domain}" if _replit_domain else "")
            )
            if not base:
                port = int(os.environ.get("PORT", 5000))
                base = f"http://localhost:{port}"
            url = f"{base}/health"
            with urllib.request.urlopen(url, timeout=10) as resp:
                if resp.status == 200:
                    print(f"[KEEPALIVE] OK — {url}")
                else:
                    print(f"[KEEPALIVE] /health returned {resp.status}")
        except Exception as e:
            print(f"[KEEPALIVE] Ping failed: {e}")
        time.sleep(240)


_token_present = bool(os.environ.get("DISCORD_TOKEN", "").strip())
_disabled = os.environ.get("START_BOT_IN_WEB", "").lower() in {"0", "false", "no", "off"}

if _token_present and not _disabled:
    threading.Thread(target=_start_bot, daemon=True).start()
    threading.Thread(target=_bot_watchdog, daemon=True, name="bot-watchdog").start()
elif not _token_present:
    print("[WSGI] DISCORD_TOKEN not set — Discord bot will not start. "
          "Set DISCORD_TOKEN in Secrets to enable the bot.")


threading.Thread(target=_self_ping, daemon=True, name="self-keepalive").start()
