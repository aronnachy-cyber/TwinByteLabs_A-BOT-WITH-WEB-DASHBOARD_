import threading
import time
import os
import urllib.request

# Load .env file for local development — must happen before any app imports
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

# Print startup config summary and warn on missing required vars
try:
    from config_check import check_config, warn_dev_pin
    check_config(print_optional=True)
    warn_dev_pin()
except Exception as _cc_err:
    print(f"[WSGI] config_check skipped: {_cc_err}")

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
    """Ping /health every 2 minutes to prevent Render/Replit from spinning down.

    Uses the public DASHBOARD_URL when set so the ping counts as real inbound
    traffic on Render (internal localhost pings don't reset the idle timer).
    Falls back to localhost only when no public URL is known.
    """
    time.sleep(30)
    while True:
        try:
            _render_url    = os.environ.get("RENDER_EXTERNAL_URL", "")
            _railway_url   = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
            _replit_domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
            base = (
                os.environ.get("DASHBOARD_URL", "").rstrip("/")
                or (_render_url.rstrip("/") if _render_url else "")
                or (f"https://{_railway_url}" if _railway_url else "")
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
        time.sleep(120)  # every 2 minutes — well within Render's 15-min idle window


_token_present = bool(os.environ.get("DISCORD_TOKEN", "").strip())
_disabled = os.environ.get("START_BOT_IN_WEB", "").lower() in {"0", "false", "no", "off"}

if _token_present and not _disabled:
    threading.Thread(target=_start_bot, daemon=True).start()
    threading.Thread(target=_bot_watchdog, daemon=True, name="bot-watchdog").start()
elif not _token_present:
    print("[WSGI] DISCORD_TOKEN not set — Discord bot will not start. "
          "Set DISCORD_TOKEN in your .env file (local) or host secrets (cloud) to enable the bot.")


def _start_server_official():
    """Start the Server Official bot if SERVER_TOKEN is configured."""
    so_token = os.environ.get("SERVER_TOKEN", "").strip()
    if not so_token:
        print("[WSGI] SERVER_TOKEN not set — Server Official bot will not start.")
        return
    try:
        import server_official as so
        so.start_bot_thread()
        print("[WSGI] Server Official bot thread started.")
    except Exception as e:
        import traceback
        print(f"[WSGI] Server Official start error: {e}")
        traceback.print_exc()


threading.Thread(target=_start_server_official, daemon=True, name="so-bot-starter").start()
threading.Thread(target=_self_ping, daemon=True, name="self-keepalive").start()
