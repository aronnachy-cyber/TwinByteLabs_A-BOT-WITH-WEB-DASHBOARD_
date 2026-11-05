"""
Startup configuration checker for SP4RK OS.
Imported by wsgi.py before anything else starts.

Prints a clear summary of which env vars are set/missing so operators
can diagnose configuration problems immediately on boot.
"""
import os
import sys

_REQUIRED = [
    ("DISCORD_TOKEN",        "Bot token from Discord Developer Portal"),
    ("FLASK_SECRET_KEY",     "Random 32-byte hex string for session signing (or SESSION_SECRET)"),
]

_OPTIONAL = [
    ("DISCORD_CLIENT_ID",    "OAuth2 Client ID — required for /login (Discord OAuth)"),
    ("DISCORD_CLIENT_SECRET","OAuth2 Client Secret — required for /login (Discord OAuth)"),
    ("DISCORD_REDIRECT_URI", "OAuth2 redirect URL — must match Discord app settings"),
    ("ADMIN_DISCORD_ID",     "Your Discord user ID — grants access to /owner panel"),
    ("DATABASE_URL",         "PostgreSQL URL — for persistent storage across restarts"),
    ("SUPABASE_URL",         "Supabase project URL — alternative to raw PostgreSQL"),
    ("SUPABASE_KEY",         "Supabase anon/service key"),
    ("GEMINI_KEY",           "Google Gemini API key — for AI auto-moderation"),
    ("SERVER_TOKEN",         "Server Official bot token — for the second bot"),
    ("DEV_PIN",              "Dev panel PIN — defaults to 'change-me-in-production'"),
    ("DASHBOARD_URL",        "Public URL — used by keepalive ping"),
]

_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"

def check_config(print_optional: bool = False) -> bool:
    """
    Check required and optional environment variables.
    Returns True if all required vars are present.
    """
    # Accept either FLASK_SECRET_KEY or SESSION_SECRET
    has_secret = bool(
        os.environ.get("FLASK_SECRET_KEY", "").strip()
        or os.environ.get("SESSION_SECRET", "").strip()
    )

    missing_required = []
    print(f"\n{_BOLD}{'='*60}")
    print("  SP4RK OS — Environment Configuration Check")
    print(f"{'='*60}{_RESET}")

    for key, desc in _REQUIRED:
        if key == "FLASK_SECRET_KEY":
            present = has_secret
        else:
            present = bool(os.environ.get(key, "").strip())

        status = f"{_GREEN}✓ SET{_RESET}" if present else f"{_RED}✗ MISSING{_RESET}"
        print(f"  {status}  {key}")
        if not present:
            missing_required.append((key, desc))

    if print_optional:
        print(f"\n  {_BOLD}Optional:{_RESET}")
        for key, desc in _OPTIONAL:
            if key == "SUPABASE_URL":
                present = bool(os.environ.get("SUPABASE_URL", "").strip())
            else:
                present = bool(os.environ.get(key, "").strip())
            status = f"{_GREEN}✓{_RESET}" if present else f"{_YELLOW}–{_RESET}"
            label  = key if present else f"{key} ({desc})"
            print(f"  {status} {label}")

    print(f"{_BOLD}{'='*60}{_RESET}\n")

    if missing_required:
        for key, desc in missing_required:
            print(f"{_RED}[config] MISSING required env var: {key}{_RESET}")
            print(f"         → {desc}")
        print(f"\n{_YELLOW}[config] Copy .env.example → .env, fill in the missing values, "
              f"then restart.{_RESET}\n")
        return False
    return True


def warn_dev_pin():
    """Warn if DEV_PIN is still the default value."""
    pin = os.environ.get("DEV_PIN", "")
    if pin in ("sp4rk2024", "change-me-in-production", ""):
        print(f"{_YELLOW}[config] WARNING: DEV_PIN is not set or uses the default value. "
              f"Set a strong DEV_PIN in production to protect the /dev panel.{_RESET}")
