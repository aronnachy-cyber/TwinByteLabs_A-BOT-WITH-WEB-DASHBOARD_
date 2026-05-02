from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
import os
import json
import secrets
import requests as http_requests
import traceback
from html import escape as html_escape
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from supabase import create_client, Client as SupabaseClient
from werkzeug.middleware.proxy_fix import ProxyFix
import license_manager as _lm

DISCORD_CLIENT_ID     = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI  = os.environ.get("DISCORD_REDIRECT_URI", "")
DISCORD_API_BASE      = "https://discord.com/api/v10"

# ---- SUPABASE ----
_sb_url = os.environ.get("SUPABASE_URL", "")
_sb_key = os.environ.get("SUPABASE_KEY", "")
supabase: SupabaseClient = create_client(_sb_url, _sb_key) if _sb_url and _sb_key else None
if supabase:
    print("✅ Supabase connected")
else:
    print("⚠️  Supabase not configured — falling back to local JSON files")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SESSION_SECRET") or os.urandom(24).hex()
# Trust the reverse proxy (Render/Replit) so request.scheme/host_url reflect HTTPS
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
# Persistent, cross-redirect-safe session cookies for the Discord OAuth round-trip
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)


@app.before_request
def _make_session_permanent():
    session.permanent = True

DEFAULT_CONFIG = {
    "prefix": "!",
    "bot_status": "online",
    "theme_color": "#3498db",
    "welcome_msg": "",
    "welcome_image": "",
    "welcome_channel": "",
    "welcome_dot_emoji":    "🔴",
    "welcome_arrow_emoji":  "➡️",
    "welcome_line1_label":   "PLEASE READ",
    "welcome_line1_channel": "",
    "welcome_line2_label":   "PLEASE CHECK",
    "welcome_line2_channel": "",
    "welcome_line3_label":   "ALSO REACT IN",
    "welcome_line3_channel": "",
    "welcome_line4_label":   "CHAT IN",
    "welcome_line4_channel": "",
    "welcome_footer_emoji":  "❤️",
    "welcome_footer_msg":    "THANKS FOR JOINING US",
    "welcome_staff_msg":     "FOR ANY HELP CONTACT WITH OUR STAFFS",
    "welcome_staff_emoji":   "👦",
    "bot_avatar": "",
    "log_channel": "",
    "mod_block_links": "OFF",
    "mod_block_mentions": "OFF",
    "profanity_level": "low",
    "mute_role": "",
    "auto_role": "",
    "auto_block_links": "OFF",
    "auto_block_invites": "OFF",
    "auto_block_mentions": "OFF",
    "auto_custom_blacklist": "",
    "auto_dm_warning": "OFF",
    "webhook_log": "",
    "warning_limit": 30,
    "owner_approval": "OFF",
    "anti_raid": "OFF",
    "banned_words": "",
    "security_2fa": "OFF",
    "role_monitor": "OFF",
    "mass_ban_prevent": "OFF",
    "temp_lockdown": "OFF",
    "discord_alerts": "OFF",
    "twitter_alerts": "OFF",
    "youtube_alerts": "OFF",
    "youtube_channel_link": "",
    "youtube_notify_msg": "📺 A new YouTube video was shared! Check it out:",
    "multi_channel": "",
    "owner_dm_id": "",
    "daily_report": "OFF",
    "ticket_category": "",
    # --- Advanced automod ---
    "spam_filter": "OFF",
    "spam_threshold": 5,
    "spam_window": 5,
    "caps_filter": "OFF",
    "caps_percent": 70,
    "emoji_filter": "OFF",
    "emoji_limit": 8,
    "duplicate_filter": "OFF",
    "wall_filter": "OFF",
    "wall_newlines": 10,
    "wall_length": 1000,
    "anti_zalgo": "OFF",
    "grad_punish": "OFF",
    # --- Master "Automod" auto-action toggle ---
    # When ON, SP4RK auto-mutes / tempbans / kicks based on warning count
    # using the chart below (5 → 5min mute, 10 → 10min mute,
    # 20 → 30min tempban, 30 → kick). When OFF, behaviour is unchanged.
    "automod_on": "OFF",
    # --- SP4RK Server Knowledge (used when the bot is @mentioned) ---
    "server_description": "",
    "server_rules": "",
}

config = {**DEFAULT_CONFIG}

def load_config_from_supabase():
    global config
    if supabase:
        try:
            res = supabase.table("sentinel_config").select("*").eq("id", 1).execute()
            if res.data:
                stored = res.data[0].get("data", {})
                if isinstance(stored, str):
                    try:
                        stored = json.loads(stored)
                    except Exception:
                        stored = {}
                if isinstance(stored, dict):
                    config.update(stored)
                print("📁 Settings loaded from Supabase!")
                return
        except Exception as e:
            print(f"⚠️ Supabase config load error: {e}")
    if os.path.exists('settings.json'):
        with open('settings.json', 'r') as f:
            try:
                stored = json.load(f)
                if isinstance(stored, dict):
                    config.update(stored)
                print("📁 Settings loaded from settings.json")
            except Exception:
                pass

load_config_from_supabase()

# ---- PER-GUILD CONFIG STORE ----
import threading as _threading
_GUILD_CONFIGS_FILE = os.path.join(os.path.dirname(__file__), "sp4rk_guild_configs.json")
_guild_cfg_lock = _threading.RLock()

def _load_guild_configs() -> dict:
    if os.path.exists(_GUILD_CONFIGS_FILE):
        try:
            with open(_GUILD_CONFIGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_guild_configs(data: dict) -> None:
    with _guild_cfg_lock:
        tmp = _GUILD_CONFIGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _GUILD_CONFIGS_FILE)

def get_guild_config(guild_id: str) -> dict:
    """Return the effective config for a guild: per-guild overrides merged over the global defaults."""
    guild_id = str(guild_id).strip()
    data = _load_guild_configs()
    return {**config, **data.get(guild_id, {})}

def save_guild_config(guild_id: str, updates: dict) -> None:
    """Persist per-guild config overrides (only keys that differ from the global config).

    If ``log_channel`` is included, it is also mirrored into the SO bot's
    per-guild config (as ``log_channel_id``) so both bots share one value.
    """
    guild_id = str(guild_id).strip()
    with _guild_cfg_lock:
        data = _load_guild_configs()
        existing = data.get(guild_id, {})
        existing.update(updates)
        data[guild_id] = existing
        _save_guild_configs(data)

    if "log_channel" in updates:
        try:
            import server_official as _so
            _so.update_guild_config(guild_id, {"log_channel_id": updates["log_channel"]})
        except Exception as _e:
            print(f"[dashboard] SO log_channel sync skipped: {_e}")

def _sp4rk_live_state(user_guild_ids=None) -> dict:
    """Return SP4RK bot guild list filtered to the user's admin guilds."""
    out = {"guilds": [], "guilds_count": 0}
    try:
        import main as _main_mod
        b = getattr(_main_mod, "bot", None)
        if b and b.is_ready():
            filter_ids = None
            if user_guild_ids is not None:
                filter_ids = {str(x) for x in user_guild_ids if x}
            guilds = []
            for g in b.guilds:
                gid_str = str(g.id)
                if filter_ids is not None and gid_str not in filter_ids:
                    continue
                guilds.append({"id": gid_str, "name": g.name, "member_count": g.member_count or 0})
            out["guilds"] = sorted(guilds, key=lambda x: x["member_count"], reverse=True)
            out["guilds_count"] = len(guilds)
    except Exception as e:
        app.logger.warning(f"[sp4rk_live_state] {e}")
    return out

warnings_db = []

# ---- WARNINGS (Supabase-backed, JSON fallback) ----
def supabase_save_warning(guild_id, member_id, username, count, reason, timestamp):
    if not supabase:
        return
    try:
        supabase.table("sentinel_warnings").insert({
            "guild_id":  str(guild_id),
            "member_id": str(member_id),
            "username":  username,
            "count":     count,
            "reason":    reason,
            "timestamp": timestamp,
        }).execute()
    except Exception as e:
        print(f"⚠️ Supabase save_warning error: {e}")

def supabase_clear_warnings(guild_id, member_id):
    if not supabase:
        return
    try:
        supabase.table("sentinel_warnings").delete()\
            .eq("guild_id", str(guild_id))\
            .eq("member_id", str(member_id))\
            .execute()
    except Exception as e:
        print(f"⚠️ Supabase clear_warnings error: {e}")

def supabase_get_warnings(guild_id, member_id):
    if not supabase:
        return []
    try:
        res = supabase.table("sentinel_warnings")\
            .select("*")\
            .eq("guild_id", str(guild_id))\
            .eq("member_id", str(member_id))\
            .order("timestamp", desc=False)\
            .execute()
        return res.data or []
    except Exception as e:
        print(f"⚠️ Supabase get_warnings error: {e}")
        return []

def supabase_load_all_warnings():
    """
    Load every warning row from Supabase and group them into the
    in-memory shape used by the bot:
        { guild_id: { member_id: { "count": int, "history": [ {reason, time} ] } } }
    Returns {} if Supabase is unavailable so callers can fall back to the
    local JSON cache.
    """
    if not supabase:
        return {}
    try:
        res = (
            supabase.table("sentinel_warnings")
            .select("*")
            .order("timestamp", desc=False)
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        print(f"⚠️ Supabase load_all_warnings error: {e}")
        return {}

    out = {}
    for r in rows:
        gid = str(r.get("guild_id") or "")
        mid = str(r.get("member_id") or "")
        if not gid or not mid:
            continue
        guild = out.setdefault(gid, {})
        entry = guild.setdefault(mid, {"count": 0, "history": []})
        # Each row is one warning event; count the events and keep history.
        entry["count"] = max(int(r.get("count") or 0), entry["count"] + 1)
        entry["history"].append({
            "reason": r.get("reason") or "",
            "time":   r.get("timestamp") or "",
        })
    # Re-derive count as the length of history so it can never disagree
    # with the stored history list.
    for guild in out.values():
        for entry in guild.values():
            entry["count"] = len(entry["history"])
    return out

def supabase_save_mod_log(guild_id, action, target, moderator, reason, timestamp):
    if not supabase:
        return
    try:
        supabase.table("sentinel_mod_logs").insert({
            "guild_id":  str(guild_id),
            "action":    action,
            "target":    target,
            "moderator": moderator,
            "reason":    reason,
            "timestamp": timestamp,
        }).execute()
    except Exception as e:
        print(f"⚠️ Supabase save_mod_log error: {e}")

def supabase_save_note(guild_id, member_id, username, note, moderator, timestamp):
    if not supabase:
        return
    try:
        supabase.table("sentinel_notes").insert({
            "guild_id":  str(guild_id),
            "member_id": str(member_id),
            "username":  username,
            "note":      note,
            "moderator": moderator,
            "timestamp": timestamp,
        }).execute()
    except Exception as e:
        print(f"⚠️ Supabase save_note error: {e}")

def supabase_get_notes(guild_id, member_id):
    if not supabase:
        return []
    try:
        res = supabase.table("sentinel_notes")\
            .select("*")\
            .eq("guild_id", str(guild_id))\
            .eq("member_id", str(member_id))\
            .order("timestamp", desc=False)\
            .execute()
        return res.data or []
    except Exception as e:
        print(f"⚠️ Supabase get_notes error: {e}")
        return []

# ---- USER FEEDBACK (star ratings, Supabase + JSON fallback) ----
FEEDBACK_FILE = 'feedback.json'


def _load_feedback_file():
    """Local fallback store when Supabase is unavailable."""
    try:
        if os.path.exists(FEEDBACK_FILE):
            with open(FEEDBACK_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception as e:
        print(f"⚠️ load feedback file error: {e}")
    return []


def _save_feedback_file(items):
    try:
        with open(FEEDBACK_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, indent=2, default=str)
    except Exception as e:
        print(f"⚠️ save feedback file error: {e}")


def supabase_save_feedback(entry):
    """Insert one feedback row in Supabase. Returns True on success."""
    if not supabase:
        return False
    try:
        supabase.table("sentinel_feedback").insert({
            "discord_id": entry.get("discord_id"),
            "user_name":  entry.get("user_name"),
            "rating":     entry.get("rating"),
            "comment":    entry.get("comment", ""),
            "source":     entry.get("source", "main"),
        }).execute()
        return True
    except Exception as e:
        print(f"⚠️ Supabase save_feedback error: {e}")
        return False


def supabase_list_feedback(limit=20):
    """Fetch the latest feedback rows from Supabase, newest first."""
    if not supabase:
        return None
    try:
        res = supabase.table("sentinel_feedback")\
            .select("*")\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        return res.data or []
    except Exception as e:
        print(f"⚠️ Supabase list_feedback error: {e}")
        return None


# ---- USERS MANAGEMENT (Discord-only, Supabase-backed with JSON fallback) ----
USERS_FILE = 'users.json'

# The Supabase `sentinel_users` table schema is:
#   discord_id text primary key, username text, avatar_url text,
#   email text, created_at timestamptz, last_login timestamptz
# The application code internally uses `name` (display name) and
# `discord_username` (raw Discord username). The helpers below translate
# between the two representations so we can persist everything in Supabase.

def _normalize_user(u):
    if not isinstance(u, dict) or not u.get('discord_id'):
        return None
    u['discord_id'] = str(u['discord_id'])
    if not u.get('name'):
        u['name'] = u.get('discord_username') or u.get('username') or 'User'
    if not u.get('discord_username'):
        u['discord_username'] = u.get('username') or u['name']
    if not u.get('created_at'):
        u['created_at'] = datetime.now().strftime('%Y-%m-%d')
    return u

def _to_db_row(u):
    """Convert an in-memory user dict to a Supabase `sentinel_users` row."""
    row = {
        'discord_id': str(u['discord_id']),
        'username':   u.get('name') or u.get('discord_username') or 'User',
        'last_login': datetime.utcnow().isoformat(),
    }
    if u.get('avatar_url'):
        row['avatar_url'] = u['avatar_url']
    if u.get('email'):
        row['email'] = u['email']
    return row

def _from_db_row(r):
    """Convert a Supabase `sentinel_users` row back to the app's user dict."""
    if not isinstance(r, dict) or not r.get('discord_id'):
        return None
    username = r.get('username') or 'User'
    return _normalize_user({
        'discord_id':       str(r['discord_id']),
        'discord_username': username,
        'name':             username,
        'avatar_url':       r.get('avatar_url') or '',
        'email':            r.get('email') or '',
        'created_at':       r.get('created_at') or datetime.now().strftime('%Y-%m-%d'),
        'last_login':       r.get('last_login') or '',
    })

# ---- Postgres-backed user store (preferred when DATABASE_URL is set) ----
import db_users as _db_users

# One-shot migration of any pre-existing users.json into Postgres.
# Safe: uses upsert, so re-running this on every boot is a no-op once empty.
if _db_users.is_available() and os.path.exists(USERS_FILE):
    try:
        _db_users.migrate_from_json(USERS_FILE)
    except Exception as _e:
        print(f"[users] one-shot JSON→PG migration skipped: {_e}")


def load_users():
    # 1) Postgres (DATABASE_URL) — preferred persistent store.
    if _db_users.is_available():
        return _db_users.load_all()
    # 2) Supabase REST — legacy path, still supported when env vars are set.
    if supabase:
        try:
            res = supabase.table("sentinel_users").select("*").execute()
            return [u for u in (_from_db_row(r) for r in (res.data or [])) if u]
        except Exception as e:
            print(f"⚠️ Supabase load_users error: {e}")
    # 3) JSON file fallback.
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            try:
                stored = json.load(f)
                users = stored.get('users', []) if isinstance(stored, dict) else stored
                return [u for u in users if _normalize_user(u)]
            except Exception:
                return []
    return []

def save_single_user(u):
    if not _normalize_user(u):
        return
    # 1) Postgres.
    if _db_users.is_available():
        if _db_users.upsert(u):
            return
    # 2) Supabase REST.
    if supabase:
        try:
            row = _to_db_row(u)
            supabase.table("sentinel_users").upsert(
                row, on_conflict="discord_id"
            ).execute()
            return
        except Exception as e:
            err_str = str(e)
            # PGRST204: column doesn't exist in Supabase schema cache yet.
            # Retry without the missing column so the rest of the row still saves.
            if "PGRST204" in err_str or "avatar_url" in err_str:
                try:
                    row.pop("avatar_url", None)
                    supabase.table("sentinel_users").upsert(
                        row, on_conflict="discord_id"
                    ).execute()
                    print("⚠️ Supabase: saved user without avatar_url (column missing — run migration)")
                    return
                except Exception as e2:
                    print(f"⚠️ Supabase save_single_user error (retry): {e2}")
            else:
                print(f"⚠️ Supabase save_single_user error: {e}")
    # 3) JSON file fallback.
    users = load_users()
    for i, existing in enumerate(users):
        if str(existing.get("discord_id")) == u["discord_id"]:
            users[i] = u
            break
    else:
        users.append(u)
    with open(USERS_FILE, 'w') as f:
        json.dump({'users': users}, f, indent=4)

def find_user_by_discord_id(discord_id):
    discord_id = str(discord_id)
    # 1) Postgres.
    if _db_users.is_available():
        u = _db_users.find_by_discord_id(discord_id)
        if u:
            return u
    # 2) Supabase REST.
    if supabase:
        try:
            res = (
                supabase.table("sentinel_users")
                .select("*")
                .eq("discord_id", discord_id)
                .limit(1)
                .execute()
            )
            if res.data:
                return _from_db_row(res.data[0])
        except Exception as e:
            print(f"⚠️ Supabase find_user_by_discord_id error: {e}")
    # 3) JSON scan fallback.
    for u in load_users():
        if str(u.get('discord_id')) == discord_id:
            return u
    return None

@app.errorhandler(500)
def handle_internal_error(error):
    print("❌ Dashboard internal server error")
    original = getattr(error, "original_exception", None)
    if original:
        traceback.print_exception(type(original), original, original.__traceback__)
    else:
        traceback.print_exc()
    return render_template(
        'login.html',
        error="The dashboard hit a server error. Please try logging in again.",
        discord_configured=bool(DISCORD_CLIENT_ID),
    ), 200

# ---- BOT RESTART CALLBACK ----
_bot_restart_callback = None

def set_bot_restart_callback(fn):
    global _bot_restart_callback
    _bot_restart_callback = fn

# ---- BOT CONFIG REFRESH CALLBACK ----
# Called every time settings are saved so the bot picks up changes instantly
_bot_config_refresh_callback = None

def set_bot_config_refresh_callback(fn):
    global _bot_config_refresh_callback
    _bot_config_refresh_callback = fn

def _trigger_bot_config_refresh():
    if _bot_config_refresh_callback:
        try:
            _bot_config_refresh_callback()
        except Exception as e:
            print(f"Config refresh callback error: {e}")

# ---- ACTION LOGS ----
action_logs = []

def add_dashboard_log(event_type, description):
    action_logs.insert(0, {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": event_type,
        "description": description
    })
    if len(action_logs) > 300:
        action_logs.pop()

TOGGLE_KEYS = [
    "mod_block_links", "mod_block_mentions",
    "auto_block_links", "auto_block_invites", "auto_block_mentions",
    "auto_dm_warning", "owner_approval",
    "anti_raid", "security_2fa", "role_monitor", "mass_ban_prevent",
    "temp_lockdown", "discord_alerts", "twitter_alerts", "youtube_alerts",
    "daily_report",
    "spam_filter", "caps_filter", "emoji_filter", "duplicate_filter",
    "wall_filter", "anti_zalgo", "grad_punish", "automod_on",
]

TEXT_KEYS = [
    "prefix", "bot_status", "theme_color", "welcome_msg", "welcome_image", "welcome_channel", "bot_avatar",
    "welcome_dot_emoji", "welcome_arrow_emoji",
    "welcome_line1_label", "welcome_line1_channel",
    "welcome_line2_label", "welcome_line2_channel",
    "welcome_line3_label", "welcome_line3_channel",
    "welcome_line4_label", "welcome_line4_channel",
    "welcome_footer_emoji", "welcome_footer_msg",
    "welcome_staff_msg", "welcome_staff_emoji",
    "log_channel", "profanity_level", "mute_role", "auto_role",
    "auto_custom_blacklist", "auto_allowlist", "webhook_log", "banned_words",
    "youtube_channel_link", "youtube_notify_msg", "multi_channel",
    "owner_dm_id", "ticket_category",
    "spam_threshold", "spam_window", "caps_percent",
    "emoji_limit", "wall_newlines", "wall_length",
    "server_description", "server_rules",
]

PUBLIC_BOT_CONFIG_KEYS = set(TOGGLE_KEYS + TEXT_KEYS + ["warning_limit"])

def save_config():
    if supabase:
        try:
            res = supabase.table("sentinel_config").select("id").eq("id", 1).execute()
            if res.data:
                supabase.table("sentinel_config").update({"data": config}).eq("id", 1).execute()
            else:
                supabase.table("sentinel_config").insert({"id": 1, "data": config}).execute()
            print("💾 Settings saved to Supabase!")
            return
        except Exception as e:
            print(f"⚠️ Supabase save_config error: {e}")
    with open('settings.json', 'w') as f:
        json.dump(config, f, indent=4)
    print("💾 Settings saved to settings.json")

# ---- DEV / MAINTENANCE STATE ----
DEV_PIN = os.environ.get("DEV_PIN", "sp4rk2024")
_DEV_STATE_FILE = "dev_state.json"

def _load_dev_state():
    try:
        if os.path.exists(_DEV_STATE_FILE):
            with open(_DEV_STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"maintenance": False, "maintenance_eta": ""}

def _save_dev_state(state):
    try:
        tmp = _DEV_STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, _DEV_STATE_FILE)
    except Exception as e:
        print(f"[dev_state] save error: {e}")

def is_maintenance_mode():
    return bool(_load_dev_state().get("maintenance", False))

def is_dev_session():
    return bool(session.get("dev_unlocked"))

# ---- SERVER-OFFICIAL BOT (admin-customizable card on /bots) ----
SERVER_OFFICIAL_DEFAULTS = {
    "name": "Server Official",
    "tagline": "Your server's signature bot",
    "image": "",  # URL — empty falls back to icon
    "description": (
        "A custom bot crafted for your community. Rename it, set a custom "
        "profile picture, and make it truly yours."
    ),
}

def get_server_official():
    state = _load_dev_state()
    cfg = state.get("server_official") or {}
    merged = {**SERVER_OFFICIAL_DEFAULTS, **{k: v for k, v in cfg.items() if v is not None}}
    return merged

def save_server_official(updates: dict):
    state = _load_dev_state()
    current = state.get("server_official") or {}
    for key in ("name", "tagline", "image", "description"):
        if key in updates and updates[key] is not None:
            current[key] = str(updates[key]).strip()
    state["server_official"] = current
    _save_dev_state(state)
    return get_server_official()

# ---- SITE SETTINGS (theme + hero text — live "redesign in minutes") ----
SITE_SETTINGS_DEFAULTS = {
    # Theme colors (any valid CSS color)
    "color_bg":      "#050b18",
    "color_accent":  "#5b8cff",
    "color_accent2": "#8b5cf6",
    "color_accent3": "#22d3ee",
    "color_text":    "#e8f0ff",
    # Hero / brand text
    "brand_name":      "Byte Labs",
    "hero_badge":      "Building the future of community automation",
    "hero_title_pre":  "We build",
    "hero_title_grad": "intelligent bots",
    "hero_title_post": "that power modern communities.",
    "hero_lede": (
        "Byte Labs designs and ships AI-driven bots, dashboards, and automation tools "
        "that help Discord servers, communities, and creators run smoother — without "
        "the busywork."
    ),
    # Section title under "What We Build"
    "section_title": "Every Byte Labs bot follows the same principles.",
    "section_lede":  "Beautiful dashboards, intelligent automation, rock-solid uptime, and full control in your hands.",
    # CTA strip
    "cta_title": "Ready to add a Byte Labs bot to your server?",
    "cta_lede":  "Browse our growing lineup of bots and find the one that fits your community.",
    # Footer tag
    "footer_tag": "Crafted with care for communities.",
}

def get_site_settings():
    state = _load_dev_state()
    cfg = state.get("site_settings") or {}
    merged = {**SITE_SETTINGS_DEFAULTS, **{k: v for k, v in cfg.items() if v not in (None, "")}}
    return merged

def save_site_settings(updates: dict):
    state = _load_dev_state()
    current = state.get("site_settings") or {}
    for key in SITE_SETTINGS_DEFAULTS.keys():
        if key in updates and updates[key] is not None:
            current[key] = str(updates[key]).strip()
    state["site_settings"] = current
    _save_dev_state(state)
    return get_site_settings()

# ---- BOT REQUESTS ("Ask for a Bot" inbox) ----
# Storage strategy: prefer the Postgres `sentinel_form_submissions` table
# (via db_forms) when DATABASE_URL is set. Fall back to the legacy JSON
# file so existing data still works if the DB is ever unavailable.
BOT_REQUESTS_FILE = "bot_requests.json"

import db_forms as _db_forms  # noqa: E402

# One-shot: copy any pre-existing JSON into the DB on first boot. Idempotent.
if _db_forms.is_available() and os.path.exists(BOT_REQUESTS_FILE):
    try:
        _db_forms.migrate_from_json(BOT_REQUESTS_FILE)
    except Exception as _e:
        print(f"[forms] one-shot JSON→PG migration skipped: {_e}")


def _load_bot_requests():
    # 1) Postgres — preferred, persistent across redeploys.
    if _db_forms.is_available():
        try:
            return _db_forms.list_all(form_type=_db_forms.FORM_TYPE_BOT_REQUEST, limit=1000)
        except Exception as e:
            print(f"⚠️ db_forms list_all error: {e}")
    # 2) Legacy JSON file fallback.
    try:
        if os.path.exists(BOT_REQUESTS_FILE):
            with open(BOT_REQUESTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception as e:
        print(f"⚠️ load bot_requests error: {e}")
    return []


def _save_bot_request(entry):
    """Persist a single submission. Used by the public POST route."""
    if _db_forms.is_available():
        if _db_forms.insert(entry, form_type=_db_forms.FORM_TYPE_BOT_REQUEST):
            return
    # JSON fallback: append + atomic replace.
    items = []
    try:
        if os.path.exists(BOT_REQUESTS_FILE):
            with open(BOT_REQUESTS_FILE, "r", encoding="utf-8") as f:
                items = json.load(f) or []
    except Exception:
        items = []
    items.insert(0, entry)
    items = items[:1000]
    try:
        tmp = BOT_REQUESTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, default=str)
        os.replace(tmp, BOT_REQUESTS_FILE)
    except Exception as e:
        print(f"⚠️ save bot_requests error: {e}")


def _delete_bot_request(rid):
    """Delete a single submission. Returns True when something was removed."""
    if _db_forms.is_available():
        if _db_forms.delete_by_id(rid):
            return True
    # JSON fallback.
    try:
        if not os.path.exists(BOT_REQUESTS_FILE):
            return False
        with open(BOT_REQUESTS_FILE, "r", encoding="utf-8") as f:
            items = json.load(f) or []
        new_items = [r for r in items if r.get("id") != rid]
        if len(new_items) == len(items):
            return False
        tmp = BOT_REQUESTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new_items, f, indent=2, default=str)
        os.replace(tmp, BOT_REQUESTS_FILE)
        return True
    except Exception as e:
        print(f"⚠️ delete bot_request error: {e}")
        return False


# Kept for backwards compatibility with anything still calling the old API.
def _save_bot_requests(items):
    try:
        tmp = BOT_REQUESTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, default=str)
        os.replace(tmp, BOT_REQUESTS_FILE)
    except Exception as e:
        print(f"⚠️ save bot_requests error: {e}")

# ---- LANDING PAGES ----
def _current_user_summary():
    """Return basic identity info for the navbar (login pill / logout)."""
    if not session.get('logged_in'):
        return {"logged_in": False}
    discord_id = session.get('discord_id', '')
    user = find_user_by_discord_id(discord_id) if discord_id else None
    name = (user or {}).get('name') or session.get('user_name', 'User')
    avatar_url = (user or {}).get('avatar_url', '')
    return {
        "logged_in":  True,
        "user_name":  name,
        "avatar_url": avatar_url,
        "discord_id": discord_id,
    }


@app.route('/')
def landing():
    """Byte Labs parent landing page (the new public homepage).

    Maintenance mode: show a "coming soon" page to everyone EXCEPT the dev.
    Returns HTTP 200 so external uptime/cron pingers keep the service healthy.
    """
    if is_maintenance_mode() and not is_dev_session():
        return render_template('under_maintenance.html', maintenance_eta=_load_dev_state().get('maintenance_eta','')), 200

    return render_template(
        'bytelabs_landing.html',
        dev_unlocked=is_dev_session(),
        maintenance_on=is_maintenance_mode(),
        discord_configured=bool(DISCORD_CLIENT_ID),
        site=get_site_settings(),
        **_current_user_summary(),
    )


@app.route('/bots')
def bots_showcase():
    """Card-style interface listing all Byte Labs bots."""
    if is_maintenance_mode() and not is_dev_session():
        return render_template('under_maintenance.html', maintenance_eta=_load_dev_state().get('maintenance_eta','')), 200

    return render_template(
        'bots.html',
        dev_unlocked=is_dev_session(),
        maintenance_on=is_maintenance_mode(),
        server_official=get_server_official(),
        discord_configured=bool(DISCORD_CLIENT_ID),
        **_current_user_summary(),
    )


@app.route('/dev/server-official', methods=['GET'])
def dev_server_official_get():
    """Read the current Server Official bot card config (public)."""
    return jsonify({"ok": True, "data": get_server_official()})


@app.route('/dev/server-official', methods=['POST'])
def dev_server_official_set():
    """Update the Server Official bot card. Requires unlocked dev session."""
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Locked"}), 403
    payload = request.get_json(silent=True) or {}
    # Basic length sanity
    name = (payload.get("name") or "").strip()
    if name and len(name) > 60:
        return jsonify({"ok": False, "error": "Name is too long (max 60 chars)"}), 400
    image = (payload.get("image") or "").strip()
    if image and len(image) > 1000:
        return jsonify({"ok": False, "error": "Image URL is too long"}), 400
    if image and not (image.startswith("http://") or image.startswith("https://") or image.startswith("/")):
        return jsonify({"ok": False, "error": "Image must be an http(s) URL or a /static path"}), 400
    tagline = (payload.get("tagline") or "").strip()
    description = (payload.get("description") or "").strip()
    saved = save_server_official({
        "name": name if name else None,
        "image": image if image is not None else None,
        "tagline": tagline if tagline else None,
        "description": description if description else None,
    })
    return jsonify({"ok": True, "data": saved})


# ============================================================
# SERVER OFFICIAL — Landing, Dashboard, API
# ============================================================
def _so_module():
    """Lazy-import the server_official module so dashboard.py doesn't crash if discord libs missing."""
    try:
        import server_official as so
        return so
    except Exception as e:
        app.logger.warning(f"[server-official] import failed: {e}")
        return None


def _user_admin_guild_ids():
    """Return the user's admin guild ids as a set, or None if not yet fetched.

    Populated by the OAuth callback (`identify guilds` scope).

    Returns:
      - `None`  — key absent; the SO dashboard will force a re-auth so the
                  guild list gets fetched fresh (prevents showing all servers).
      - `set[str]` — possibly empty; user manages no servers the bot is in.
    """
    if 'user_admin_guild_ids' not in session:
        return None
    raw = session.get('user_admin_guild_ids') or []
    return {str(x) for x in raw if x}


def _so_live_state(guild_id: str = "", user_guild_ids=None):
    """Pull live guild list / counts straight from the running bot (if any).

    When `guild_id` is supplied, `selected_name` and `selected_member_count`
    are populated so the dashboard's overview cards can show per-server
    numbers instead of bot-wide totals.

    When `user_guild_ids` is a set/iterable of guild ids, the returned list
    is filtered to ONLY those servers (intersection of "bot is in" AND
    "user can administer"). Pass `None` to skip filtering (public/landing
    contexts where we just want bot-wide counts).
    """
    so = _so_module()
    out = {
        "guilds_count": 0, "members_count": 0, "guilds": [],
        "selected_id": str(guild_id or ""),
        "selected_name": "",
        "selected_member_count": 0,
    }
    if not so:
        return out

    # Normalize the filter (None → no filtering; otherwise coerce to set[str]).
    filter_ids = None
    if user_guild_ids is not None:
        filter_ids = {str(x) for x in user_guild_ids if x}

    try:
        b = so.bot
        if b and b.is_ready():
            guilds = []
            total_members = 0
            for g in b.guilds:
                gid_str = str(g.id)
                # Skip guilds the user has no admin access to.
                if filter_ids is not None and gid_str not in filter_ids:
                    continue
                gm = g.member_count or 0
                guilds.append({
                    "id": gid_str,
                    "name": g.name,
                    "member_count": gm,
                })
                total_members += gm
                if guild_id and gid_str == str(guild_id):
                    out["selected_name"] = g.name
                    out["selected_member_count"] = gm
            out["guilds_count"] = len(guilds)
            out["members_count"] = total_members
            out["guilds"] = sorted(guilds, key=lambda x: x["member_count"], reverse=True)
    except Exception as e:
        app.logger.warning(f"[server-official] live_state error: {e}")
    return out


@app.route('/server-official')
def server_official_landing():
    """Public landing page for the Server Official bot.

    Pulls live state + a feature overview so the landing page can show
    real numbers ("managing N servers, M members") and feel alive
    instead of being a static brochure.
    """
    if is_maintenance_mode() and not is_dev_session():
        return render_template('under_maintenance.html', maintenance_eta=_load_dev_state().get('maintenance_eta','')), 200
    so = _so_module()

    # Build the "Add to Discord" invite URL. Prefer a Server-Official
    # specific OAuth app if one is configured; fall back to the shared
    # DISCORD_CLIENT_ID so a single-OAuth-app deployment still works.
    so_client_id = os.environ.get("SERVER_OFFICIAL_CLIENT_ID") or DISCORD_CLIENT_ID
    bot_perms = os.environ.get("SERVER_OFFICIAL_PERMISSIONS", "8")
    invite_url = ""
    if so_client_id:
        invite_url = (
            f"https://discord.com/oauth2/authorize"
            f"?client_id={so_client_id}"
            f"&scope=bot+applications.commands"
            f"&permissions={bot_perms}"
        )

    # Live counts (best-effort — these fall back to zeros if the bot
    # isn't running yet).
    live = _so_live_state()
    overview = {}
    if so:
        try:
            overview = so.get_full_overview()
        except Exception as e:
            app.logger.warning(f"[server-official] overview failed: {e}")

    return render_template(
        'server_official_landing.html',
        so=get_server_official(),
        bot_running=bool(so and so.is_running()),
        live=live,
        overview=overview,
        invite_url=invite_url,
    )


@app.route('/server-official/dashboard')
def server_official_dashboard():
    """Web dashboard for managing scheduled announcements, giveaways and stats."""
    if is_maintenance_mode() and not is_dev_session():
        return render_template('under_maintenance.html', maintenance_eta=_load_dev_state().get('maintenance_eta','')), 200
    if not session.get('logged_in'):
        # Send unauthenticated visitors through Discord login, then back here.
        session['post_login_redirect'] = url_for('server_official_dashboard')
        return redirect(url_for('login'))

    # If the session is missing guild info (old session or failed OAuth guilds
    # fetch), force a fresh Discord re-auth so the guild list is populated.
    # Without this, _user_admin_guild_ids() returns None → no filtering →
    # every server the bot is in becomes visible to any logged-in user.
    if 'user_admin_guild_ids' not in session:
        redirect_target = url_for('server_official_dashboard')
        session.clear()
        session['post_login_redirect'] = redirect_target
        return redirect(url_for('login'))

    so = _so_module()
    # Scope the server picker to ONLY guilds the logged-in user can administer.
    live = _so_live_state(user_guild_ids=_user_admin_guild_ids())
    overview = so.get_full_overview() if so else {
        "scheduled_count": 0, "active_giveaways": 0, "templates_count": 0,
        "polls_active": 0, "reminders_pending": 0, "stickies": 0,
        "goals_count": 0, "backups_count": 0, "custom_commands_count": 0,
        "external_hooks": 0, "incoming_webhooks": 0, "logs_count": 0,
        "bot_actions_count": 0,
    }
    return render_template(
        'server_official_dashboard.html',
        bot_running=bool(so and so.is_running()),
        live=live,
        overview=overview,
        dev_unlocked=is_dev_session(),
    )


@app.route('/server-official/api/announcements', methods=['GET'])
def server_official_api_list_announcements():
    if not session.get('logged_in'):
        return jsonify({"ok": False, "error": "Login required"}), 401
    so = _so_module()
    if not so:
        return jsonify({"ok": False, "error": "Bot module unavailable"}), 503
    return jsonify({"ok": True, "data": so.list_scheduled_announcements(include_posted=False)})


@app.route('/server-official/api/announcements', methods=['POST'])
def server_official_api_create_announcement():
    if not session.get('logged_in'):
        return jsonify({"ok": False, "error": "Login required"}), 401
    so = _so_module()
    if not so:
        return jsonify({"ok": False, "error": "Bot module unavailable"}), 503

    payload = request.get_json(silent=True) or {}
    channel_id = (payload.get("channel_id") or "").strip()
    message = (payload.get("message") or "").strip()
    scheduled_for = (payload.get("scheduled_for") or "").strip()
    title = (payload.get("title") or "").strip() or None
    guild_id = (payload.get("guild_id") or "") or None
    color = payload.get("color")
    image_url = (payload.get("image_url") or "").strip() or None

    if not channel_id or not channel_id.isdigit():
        return jsonify({"ok": False, "error": "channel_id must be a numeric Discord channel ID"}), 400
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400
    if len(message) > 2000:
        return jsonify({"ok": False, "error": "message too long (max 2000 chars)"}), 400
    if not scheduled_for:
        return jsonify({"ok": False, "error": "scheduled_for (ISO datetime) is required"}), 400
    when = so._parse_iso(scheduled_for)
    if not when:
        return jsonify({"ok": False, "error": "scheduled_for must be ISO 8601 (e.g. 2026-04-28T10:00:00Z)"}), 400
    from datetime import datetime, timezone
    if when <= datetime.now(timezone.utc):
        return jsonify({"ok": False, "error": "scheduled_for must be in the future"}), 400

    # If the user uploaded a relative path (e.g. "/static/announcement_images/abc.png"),
    # promote it to an absolute URL so Discord's CDN can fetch it at post time.
    if image_url and image_url.startswith("/"):
        image_url = request.host_url.rstrip("/") + image_url

    item = so.add_scheduled_announcement(
        guild_id=str(guild_id) if guild_id else None,
        channel_id=channel_id,
        title=title or "📣 Announcement",
        message=message,
        scheduled_for_iso=when.isoformat(),
        color=int(color) if color is not None else None,
        created_by="dashboard",
        image_url=image_url,
    )
    return jsonify({"ok": True, "data": item})


@app.route('/server-official/api/announcements/upload_image', methods=['POST'])
def server_official_api_upload_announcement_image():
    """Accept a single image upload for use as the picture on a scheduled
    announcement. Returns a public URL (absolute) the caller stores and then
    sends back inside the schedule POST."""
    so, err = _so_require_login()
    if err: return err
    if 'image' not in request.files:
        return jsonify({"ok": False, "error": "No image uploaded"}), 400
    f = request.files['image']
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Empty file"}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'png'
    data = f.read()
    result = so.save_announcement_image(data, ext)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "Save failed")}), 400
    rel = result["path"]
    absolute = request.host_url.rstrip("/") + rel
    return jsonify({"ok": True, "data": {"url": absolute, "relative_url": rel, "filename": result["filename"]}})


@app.route('/server-official/api/announcements/<announce_id>', methods=['DELETE'])
def server_official_api_delete_announcement(announce_id):
    if not session.get('logged_in'):
        return jsonify({"ok": False, "error": "Login required"}), 401
    so = _so_module()
    if not so:
        return jsonify({"ok": False, "error": "Bot module unavailable"}), 503
    if so.delete_scheduled_announcement(announce_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Not found"}), 404


@app.route('/server-official/api/state', methods=['GET'])
def server_official_api_state():
    """Public read-only status for the landing page badge."""
    so = _so_module()
    return jsonify({
        "ok": True,
        "bot_running": bool(so and so.is_running()),
        "guilds_count": _so_live_state()["guilds_count"],
    })


# ============================================================
# SERVER OFFICIAL — Extended Dashboard APIs
# ============================================================
def _so_require_login():
    """Helper: returns (so, error_response) tuple."""
    if not session.get('logged_in'):
        return None, (jsonify({"ok": False, "error": "Login required"}), 401)
    so = _so_module()
    if not so:
        return None, (jsonify({"ok": False, "error": "Bot module unavailable"}), 503)
    return so, None


def _current_actor() -> str:
    u = session.get('user') or {}
    return u.get('username') or u.get('id') or 'dashboard'


# --- Analytics / Overview ---
@app.route('/server-official/api/analytics', methods=['GET'])
def so_api_analytics():
    so, err = _so_require_login()
    if err: return err
    guild_id = request.args.get('guild_id') or None
    return jsonify({"ok": True, "data": so.get_analytics(guild_id)})


@app.route('/server-official/api/overview', methods=['GET'])
def so_api_overview():
    so, err = _so_require_login()
    if err: return err
    gid = (request.args.get('guild_id') or '').strip()
    overview = so.get_full_overview(gid or None)
    # Filter the live guild list to ONLY the logged-in user's admin servers.
    live = _so_live_state(gid, user_guild_ids=_user_admin_guild_ids())
    return jsonify({"ok": True, "data": overview, "live": live})


# --- Guild Config ---
@app.route('/server-official/api/config', methods=['GET'])
def so_api_get_config():
    so, err = _so_require_login()
    if err: return err
    gid = request.args.get('guild_id', '').strip()
    if not gid:
        return jsonify({"ok": False, "error": "guild_id required"}), 400
    return jsonify({"ok": True, "data": so.get_guild_config(gid)})


@app.route('/server-official/api/config', methods=['POST'])
def so_api_set_config():
    so, err = _so_require_login()
    if err: return err
    payload = request.get_json(silent=True) or {}
    gid = (payload.pop('guild_id', '') or '').strip()
    if not gid:
        return jsonify({"ok": False, "error": "guild_id required"}), 400

    # Save to local state first so the dashboard's stored value stays in sync
    # even if Discord rejects the live update (e.g. permission missing).
    cfg = so.update_guild_config(gid, payload)
    so.add_bot_action("config_update", f"Updated config for guild {gid}", _current_actor())

    # If the user changed the bot's display name, ALSO push the rename to
    # Discord so the bot is actually renamed on the server (not just in the
    # dashboard view). We surface the live status back to the UI.
    discord_status = None
    if 'display_name' in payload:
        try:
            nick_result = so.set_guild_bot_nickname(gid, payload.get('display_name') or '')
            discord_status = nick_result.get('discord_status')
            if not nick_result.get('ok', True):
                # Soft-fail: keep the saved config, but tell the UI Discord
                # rejected the rename so the user can fix permissions.
                return jsonify({
                    "ok": False,
                    "data": cfg,
                    "discord_status": discord_status,
                    "error": discord_status or "Discord rejected the rename",
                }), 200
        except Exception as e:
            app.logger.warning(f"[server-official] nickname push failed: {e}")
            discord_status = f"Saved locally; Discord update failed ({e.__class__.__name__})"

    resp = {"ok": True, "data": cfg}
    if discord_status:
        resp["discord_status"] = discord_status
    return jsonify(resp)


# --- Broadcast ---
@app.route('/server-official/api/broadcast', methods=['POST'])
def so_api_broadcast():
    so, err = _so_require_login()
    if err: return err
    payload = request.get_json(silent=True) or {}
    channels = payload.get('channels') or []
    title = (payload.get('title') or '').strip() or '📣 Announcement'
    message = (payload.get('message') or '').strip()
    if not channels or not message:
        return jsonify({"ok": False, "error": "channels and message required"}), 400
    bot = so.bot
    if not bot or not bot.is_ready():
        return jsonify({"ok": False, "error": "Bot not ready"}), 503

    import asyncio
    async def _do():
        posted = 0
        for cid in channels:
            try:
                ch = bot.get_channel(int(cid)) or await bot.fetch_channel(int(cid))
                from datetime import datetime, timezone
                import discord
                e = discord.Embed(title=title, description=message, color=0x5b8cff,
                                  timestamp=datetime.now(timezone.utc))
                e.set_footer(text="Server Official • Byte Labs")
                await ch.send(embed=e)
                posted += 1
            except Exception as ex:
                app.logger.warning(f"[broadcast] {cid} -> {ex}")
        return posted

    fut = asyncio.run_coroutine_threadsafe(_do(), bot.loop)
    posted = fut.result(timeout=20)
    so.add_bot_action("broadcast", f"Broadcast to {posted} channel(s) from dashboard", _current_actor())
    return jsonify({"ok": True, "posted": posted})


# --- Templates ---
@app.route('/server-official/api/templates', methods=['GET'])
def so_api_templates():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.list_templates()})


@app.route('/server-official/api/templates', methods=['POST'])
def so_api_create_template():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    name = (p.get('name') or '').strip()
    title = (p.get('title') or '').strip() or '📣 Announcement'
    message = (p.get('message') or '').strip()
    if not name or not message:
        return jsonify({"ok": False, "error": "name and message required"}), 400
    item = so.add_template(p.get('guild_id'), name, title, message, p.get('color'))
    return jsonify({"ok": True, "data": item})


@app.route('/server-official/api/templates/<tid>', methods=['DELETE'])
def so_api_delete_template(tid):
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": so.delete_template(tid)})


# --- Polls ---
@app.route('/server-official/api/polls', methods=['GET'])
def so_api_polls():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.list_polls(request.args.get('guild_id'))})


# --- Reminders ---
@app.route('/server-official/api/reminders', methods=['GET'])
def so_api_reminders():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.list_reminders()})


@app.route('/server-official/api/reminders/<rid>', methods=['DELETE'])
def so_api_delete_reminder(rid):
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": so.delete_reminder(rid)})


# --- Stickies ---
@app.route('/server-official/api/stickies', methods=['GET'])
def so_api_stickies():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.list_stickies()})


@app.route('/server-official/api/stickies', methods=['POST'])
def so_api_set_sticky():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    cid = (p.get('channel_id') or '').strip()
    msg = (p.get('message') or '').strip()
    if not cid.isdigit() or not msg:
        return jsonify({"ok": False, "error": "channel_id (numeric) and message required"}), 400
    item = so.set_sticky(cid, msg, guild_id=p.get('guild_id'))
    so.add_bot_action("sticky_set", f"Sticky set on channel {cid}", _current_actor())
    return jsonify({"ok": True, "data": item})


@app.route('/server-official/api/stickies/<cid>', methods=['DELETE'])
def so_api_remove_sticky(cid):
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": so.remove_sticky(cid)})


# --- Notes ---
@app.route('/server-official/api/notes', methods=['GET'])
def so_api_notes():
    so, err = _so_require_login()
    if err: return err
    gid = request.args.get('guild_id', '').strip()
    if not gid:
        return jsonify({"ok": False, "error": "guild_id required"}), 400
    return jsonify({"ok": True, "data": so.list_admin_notes(gid)})


@app.route('/server-official/api/notes', methods=['POST'])
def so_api_add_note():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    gid = (p.get('guild_id') or '').strip()
    text = (p.get('text') or '').strip()
    if not gid or not text:
        return jsonify({"ok": False, "error": "guild_id and text required"}), 400
    return jsonify({"ok": True, "data": so.add_admin_note(gid, text, _current_actor())})


@app.route('/server-official/api/notes/<nid>', methods=['DELETE'])
def so_api_del_note(nid):
    so, err = _so_require_login()
    if err: return err
    gid = request.args.get('guild_id', '').strip()
    if not gid:
        return jsonify({"ok": False, "error": "guild_id required"}), 400
    return jsonify({"ok": so.delete_admin_note(gid, nid)})


# --- Goals ---
@app.route('/server-official/api/goals', methods=['GET'])
def so_api_goals():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.list_goals(request.args.get('guild_id'))})


@app.route('/server-official/api/goals', methods=['POST'])
def so_api_add_goal():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    gid = (p.get('guild_id') or '').strip()
    gtype = (p.get('type') or '').strip()
    target = int(p.get('target') or 0)
    if not gid or gtype not in {'messages', 'members'} or target <= 0:
        return jsonify({"ok": False, "error": "guild_id, type (messages|members), target>0 required"}), 400
    return jsonify({"ok": True, "data": so.add_goal(gid, gtype, target)})


@app.route('/server-official/api/goals/<gid_>', methods=['DELETE'])
def so_api_del_goal(gid_):
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": so.delete_goal(gid_)})


# --- Logs ---
@app.route('/server-official/api/server_logs', methods=['GET'])
def so_api_server_logs():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.get_server_logs(request.args.get('guild_id'))})


@app.route('/server-official/api/bot_actions', methods=['GET'])
def so_api_bot_actions():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.get_bot_action_logs()})


@app.route('/server-official/api/announcement_history', methods=['GET'])
def so_api_ann_history():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.list_scheduled_announcements(include_posted=True)})


@app.route('/server-official/api/setup_history', methods=['GET'])
def so_api_setup_hist():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.get_setup_history(request.args.get('guild_id'))})


# --- Setup template (apply via running bot) ---
@app.route('/server-official/api/setup_template', methods=['POST'])
def so_api_setup_template():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    gid = (p.get('guild_id') or '').strip()
    template = (p.get('template') or '').strip().lower()
    if not gid or template not in so.SERVER_TEMPLATES:
        return jsonify({"ok": False, "error": f"guild_id + template ({', '.join(so.SERVER_TEMPLATES.keys())}) required"}), 400
    bot = so.bot
    if not bot or not bot.is_ready():
        return jsonify({"ok": False, "error": "Bot not ready"}), 503
    g = bot.get_guild(int(gid))
    if not g:
        return jsonify({"ok": False, "error": "Bot is not in that server"}), 404

    import asyncio, discord
    spec = so.SERVER_TEMPLATES[template]
    summary = {"categories": [], "channels": [], "voice": [], "roles": []}

    async def _do():
        try:
            for cname in spec["categories"]:
                cat = discord.utils.get(g.categories, name=cname)
                if not cat:
                    cat = await g.create_category(cname)
                    summary["categories"].append(cname)
                for chan_name in spec["channels"].get(cname, []):
                    if not discord.utils.get(g.text_channels, name=chan_name):
                        await g.create_text_channel(chan_name, category=cat)
                        summary["channels"].append(chan_name)
                for vname in spec["voice"].get(cname, []):
                    if not discord.utils.get(g.voice_channels, name=vname):
                        await g.create_voice_channel(vname, category=cat)
                        summary["voice"].append(vname)
            for rname, rcolor in spec["roles"]:
                if not discord.utils.get(g.roles, name=rname):
                    await g.create_role(name=rname, colour=discord.Colour(rcolor))
                    summary["roles"].append(rname)
        except discord.Forbidden:
            return False
        return True

    fut = asyncio.run_coroutine_threadsafe(_do(), bot.loop)
    ok = fut.result(timeout=60)
    if not ok:
        return jsonify({"ok": False, "error": "Bot lacks permissions to apply template"}), 403
    so.add_setup_history(gid, f"template:{template}", summary)
    so.add_bot_action("setup_template", f"Applied template '{template}' from dashboard", _current_actor())
    return jsonify({"ok": True, "data": summary})


# --- SP4RK Custom Commands (global; applies to all SP4RK guilds) ---
def _sp4rk_require_login():
    """Require a logged-in dashboard session for SP4RK custom-commands routes."""
    if not session.get('logged_in'):
        return None, (jsonify({"ok": False, "error": "not authenticated"}), 401)
    return True, None


@app.route('/api/custom_commands', methods=['GET'])
def sp4rk_api_cc_list():
    _, err = _sp4rk_require_login()
    if err: return err
    import custom_commands as _cc
    return jsonify({"ok": True, "data": _cc.list_commands("sp4rk", _cc.GLOBAL_GID)})


@app.route('/api/custom_commands', methods=['POST'])
def sp4rk_api_cc_add():
    _, err = _sp4rk_require_login()
    if err: return err
    import custom_commands as _cc
    p = request.get_json(silent=True) or {}
    trig = (p.get('trigger') or '').strip()
    result_text = (p.get('result') or p.get('response') or '').strip()
    description = (p.get('description') or '').strip()
    use_embed = bool(p.get('use_embed', False))
    if not trig or not result_text:
        return jsonify({"ok": False, "error": "trigger and result required"}), 400
    try:
        data = _cc.add_command("sp4rk", _cc.GLOBAL_GID, trig,
                               description=description, result=result_text, use_embed=use_embed)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "data": data})


@app.route('/api/custom_commands', methods=['DELETE'])
def sp4rk_api_cc_del():
    _, err = _sp4rk_require_login()
    if err: return err
    import custom_commands as _cc
    trig = (request.args.get('trigger') or '').strip()
    if not trig:
        return jsonify({"ok": False, "error": "trigger required"}), 400
    return jsonify({"ok": _cc.remove_command("sp4rk", _cc.GLOBAL_GID, trig)})


# --- Custom Commands ---
@app.route('/server-official/api/custom_commands', methods=['GET'])
def so_api_cc_list():
    so, err = _so_require_login()
    if err: return err
    gid = request.args.get('guild_id', '').strip()
    if not gid:
        return jsonify({"ok": False, "error": "guild_id required"}), 400
    return jsonify({"ok": True, "data": so.list_custom_commands(gid)})


@app.route('/server-official/api/custom_commands', methods=['POST'])
def so_api_cc_add():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    gid = (p.get('guild_id') or '').strip()
    trig = (p.get('trigger') or '').strip()
    # Accept both "result" (new) and "response" (legacy)
    result_text = (p.get('result') or p.get('response') or '').strip()
    description = (p.get('description') or '').strip()
    use_embed = bool(p.get('use_embed', False))
    if not gid or not trig or not result_text:
        return jsonify({"ok": False, "error": "guild_id, trigger, result required"}), 400
    try:
        data = so.add_custom_command(gid, trig, result=result_text,
                                     description=description, use_embed=use_embed)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "data": data})


@app.route('/server-official/api/custom_commands', methods=['DELETE'])
def so_api_cc_del():
    so, err = _so_require_login()
    if err: return err
    gid = request.args.get('guild_id', '').strip()
    trig = request.args.get('trigger', '').strip()
    if not gid or not trig:
        return jsonify({"ok": False, "error": "guild_id and trigger required"}), 400
    return jsonify({"ok": so.remove_custom_command(gid, trig)})


# --- Incoming Webhooks ---
@app.route('/server-official/api/webhooks_in', methods=['GET'])
def so_api_wh_list():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.list_incoming_webhooks(request.args.get('guild_id'))})


@app.route('/server-official/api/webhooks_in', methods=['POST'])
def so_api_wh_create():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    gid = (p.get('guild_id') or '').strip()
    name = (p.get('name') or '').strip()
    cid = (p.get('channel_id') or '').strip()
    if not gid or not name or not cid.isdigit():
        return jsonify({"ok": False, "error": "guild_id, name, channel_id required"}), 400
    return jsonify({"ok": True, "data": so.add_incoming_webhook(gid, name, cid)})


@app.route('/server-official/api/webhooks_in/<wid>', methods=['DELETE'])
def so_api_wh_del(wid):
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": so.delete_incoming_webhook(wid)})


@app.route('/server-official/api/webhook_in/<secret>', methods=['POST'])
def so_api_wh_fire(secret):
    """Public endpoint: third-party services POST here to fire an embed in the linked channel."""
    so = _so_module()
    if not so:
        return jsonify({"ok": False, "error": "Bot module unavailable"}), 503
    wh = so.find_webhook_by_secret(secret)
    if not wh:
        return jsonify({"ok": False, "error": "Invalid webhook"}), 404
    p = request.get_json(silent=True) or {}
    title = (p.get('title') or '📥 Webhook').strip()
    message = (p.get('message') or '').strip()
    if not message:
        return jsonify({"ok": False, "error": "message required"}), 400
    bot = so.bot
    if not bot or not bot.is_ready():
        return jsonify({"ok": False, "error": "Bot not ready"}), 503

    import asyncio, discord
    from datetime import datetime, timezone

    async def _send():
        ch = bot.get_channel(int(wh['channel_id']))
        if ch is None:
            ch = await bot.fetch_channel(int(wh['channel_id']))
        e = discord.Embed(title=title, description=message[:2000], color=0x22d3ee,
                          timestamp=datetime.now(timezone.utc))
        e.set_footer(text=f"via {wh['name']}")
        await ch.send(embed=e)

    asyncio.run_coroutine_threadsafe(_send(), bot.loop)
    so.add_bot_action("webhook_in", f"Incoming webhook '{wh['name']}' fired", "external")
    return jsonify({"ok": True})


# --- External Hooks ---
@app.route('/server-official/api/external_hooks', methods=['GET'])
def so_api_hooks():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.list_external_hooks()})


@app.route('/server-official/api/external_hooks', methods=['POST'])
def so_api_hook_add():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    name = (p.get('name') or '').strip()
    url = (p.get('url') or '').strip()
    events = p.get('events') or []
    if not name or not url.startswith('http'):
        return jsonify({"ok": False, "error": "name and valid url required"}), 400
    return jsonify({"ok": True, "data": so.add_external_hook(name, url, events)})


@app.route('/server-official/api/external_hooks/<hid>', methods=['DELETE'])
def so_api_hook_del(hid):
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": so.delete_external_hook(hid)})


# --- Backups ---
@app.route('/server-official/api/backups', methods=['GET'])
def so_api_backups():
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": True, "data": so.list_backups()})


@app.route('/server-official/api/backups', methods=['POST'])
def so_api_backup_create():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    label = (p.get('label') or 'manual').strip()
    return jsonify({"ok": True, "data": so.create_backup(label)})


@app.route('/server-official/api/backups/<bid>/restore', methods=['POST'])
def so_api_backup_restore(bid):
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": so.restore_backup(bid)})


@app.route('/server-official/api/backups/<bid>', methods=['DELETE'])
def so_api_backup_delete(bid):
    so, err = _so_require_login()
    if err: return err
    return jsonify({"ok": so.delete_backup(bid)})


# --- Reset selected guild's state ---
@app.route('/server-official/api/reset_guild', methods=['POST'])
def so_api_reset_guild():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    gid = (p.get('guild_id') or '').strip()
    if not gid:
        return jsonify({"ok": False, "error": "guild_id required"}), 400
    # Always backup first
    so.create_backup(label=f"pre-reset-{gid}")
    state = so.load_state()
    state["server_configs"].pop(gid, None)
    state["daily_checkins"].pop(gid, None)
    state["custom_commands"].pop(gid, None)
    state["admin_notes"].pop(gid, None)
    state["activity_stats"].pop(gid, None)
    state["invite_counts"].pop(gid, None)
    state["server_goals"] = [g for g in state["server_goals"] if g.get("guild_id") != gid]
    state["scheduled_announcements"] = [a for a in state["scheduled_announcements"] if a.get("guild_id") != gid]
    state["polls"] = [p for p in state["polls"] if p.get("guild_id") != gid]
    state["server_logs"] = [l for l in state["server_logs"] if l.get("guild_id") != gid]
    so.save_state(state)
    so.add_bot_action("reset_guild", f"Reset all state for guild {gid}", _current_actor())
    return jsonify({"ok": True})


# --- Embed builder ---
@app.route('/server-official/api/post_embed', methods=['POST'])
def so_api_post_embed():
    so, err = _so_require_login()
    if err: return err
    p = request.get_json(silent=True) or {}
    cid = (p.get('channel_id') or '').strip()
    title = (p.get('title') or '📣 Announcement').strip()
    message = (p.get('message') or '').strip()
    color = p.get('color')
    if not cid.isdigit() or not message:
        return jsonify({"ok": False, "error": "channel_id (numeric) and message required"}), 400
    bot = so.bot
    if not bot or not bot.is_ready():
        return jsonify({"ok": False, "error": "Bot not ready"}), 503

    import asyncio, discord
    from datetime import datetime, timezone

    async def _send():
        ch = bot.get_channel(int(cid)) or await bot.fetch_channel(int(cid))
        e = discord.Embed(title=title, description=message[:4000],
                          color=int(color) if color else 0x5b8cff,
                          timestamp=datetime.now(timezone.utc))
        e.set_footer(text="Server Official • Byte Labs")
        await ch.send(embed=e)

    fut = asyncio.run_coroutine_threadsafe(_send(), bot.loop)
    fut.result(timeout=15)
    so.add_bot_action("embed_post", f"Embed posted to #{cid}", _current_actor())
    return jsonify({"ok": True})


@app.route('/sp4rk')
def sp4rk_landing():
    """The original SP4RK landing page (now lives under /sp4rk)."""
    if is_maintenance_mode() and not is_dev_session():
        return render_template('under_maintenance.html', maintenance_eta=_load_dev_state().get('maintenance_eta','')), 200

    # Recommended permissions for the bot (Administrator). Override with DISCORD_BOT_PERMISSIONS env var.
    bot_permissions = os.environ.get("DISCORD_BOT_PERMISSIONS", "8")
    invite_url = ""
    if DISCORD_CLIENT_ID:
        invite_url = (
            f"https://discord.com/oauth2/authorize"
            f"?client_id={DISCORD_CLIENT_ID}"
            f"&scope=bot+applications.commands"
            f"&permissions={bot_permissions}"
        )
    return render_template(
        'landing.html',
        invite_url=invite_url,
        dev_unlocked=is_dev_session(),
        maintenance_on=is_maintenance_mode(),
        logged_in=bool(session.get('logged_in')),
    )

# ---- DEV ROUTES (PIN-protected) ----
@app.route('/dev/unlock', methods=['POST'])
def dev_unlock():
    """Unlock the dev menu by submitting the correct PIN."""
    pin = (request.json or {}).get('pin', '').strip() if request.is_json else request.form.get('pin', '').strip()
    if not pin:
        return jsonify({"ok": False, "error": "PIN required"}), 400
    if pin != DEV_PIN:
        return jsonify({"ok": False, "error": "Wrong PIN"}), 403
    session.permanent = True
    session['dev_unlocked'] = True
    session.modified = True
    return jsonify({"ok": True})

@app.route('/dev/lock', methods=['POST'])
def dev_lock():
    session.pop('dev_unlocked', None)
    session.modified = True
    return jsonify({"ok": True})

@app.route('/dev/state', methods=['GET'])
def dev_state():
    return jsonify({
        "dev_unlocked": is_dev_session(),
        "maintenance":  is_maintenance_mode(),
    })

@app.route('/dev/maintenance/preview')
def dev_maintenance_preview():
    state = _load_dev_state()
    return render_template(
        'under_maintenance.html',
        maintenance_eta=state.get("maintenance_eta", "")
    ), 200

@app.route('/dev/maintenance/eta', methods=['GET'])
def dev_maintenance_eta_get():
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Locked"}), 403
    state = _load_dev_state()
    return jsonify({"ok": True, "eta": state.get("maintenance_eta", "")})

@app.route('/dev/maintenance/eta', methods=['POST'])
def dev_maintenance_eta_set():
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Locked"}), 403
    payload = request.json or {}
    eta = str(payload.get("eta", "")).strip()
    state = _load_dev_state()
    state["maintenance_eta"] = eta
    _save_dev_state(state)
    return jsonify({"ok": True, "eta": eta})

@app.route('/dev/maintenance', methods=['POST'])
def dev_maintenance_toggle():
    """Turn maintenance mode on/off. Requires unlocked dev session."""
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Locked"}), 403
    payload = request.json or {}
    enabled = bool(payload.get('enabled'))
    state = _load_dev_state()
    state['maintenance'] = enabled
    _save_dev_state(state)
    return jsonify({"ok": True, "maintenance": enabled})

# ---- DEV: SITE SETTINGS (theme + hero + section text) ----
@app.route('/dev/site_settings', methods=['GET'])
def dev_site_settings_get():
    """Read current site settings (public — used to render landing page)."""
    return jsonify({
        "ok": True,
        "settings": get_site_settings(),
        "defaults": SITE_SETTINGS_DEFAULTS,
    })

@app.route('/dev/site_settings', methods=['POST'])
def dev_site_settings_set():
    """Update site settings. Requires unlocked dev session."""
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Locked"}), 403
    payload = request.json or {}
    updates = payload.get('settings') or {}
    if not isinstance(updates, dict):
        return jsonify({"ok": False, "error": "Bad payload"}), 400
    saved = save_site_settings(updates)
    add_dashboard_log("SITE_SETTINGS", f"Updated {len(updates)} field(s)")
    return jsonify({"ok": True, "settings": saved})

@app.route('/dev/site_settings/reset', methods=['POST'])
def dev_site_settings_reset():
    """Reset all site settings back to defaults. Requires unlocked dev session."""
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Locked"}), 403
    state = _load_dev_state()
    state['site_settings'] = {}
    _save_dev_state(state)
    add_dashboard_log("SITE_SETTINGS", "Reset to defaults")
    return jsonify({"ok": True, "settings": get_site_settings()})

# ---- PUBLIC FEEDBACK (no Discord login required) ----
@app.route('/api/public_feedback', methods=['POST'])
def api_public_feedback_submit():
    """Public landing-page feedback. Anyone can submit."""
    data = request.get_json(silent=True) or {}
    try:
        rating = int(data.get('rating', 0))
    except (TypeError, ValueError):
        rating = 0
    if rating < 1 or rating > 5:
        return jsonify({"ok": False, "error": "Rating must be between 1 and 5"}), 400
    name = (data.get('name') or 'Anonymous').strip()[:80] or 'Anonymous'
    comment = (data.get('comment') or '').strip()
    if len(comment) > 2000:
        comment = comment[:2000]
    entry = {
        "discord_id": "",
        "user_name":  name,
        "rating":     rating,
        "comment":    comment,
        "source":     "landing",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    saved_remote = supabase_save_feedback(entry)
    items = _load_feedback_file()
    items.insert(0, entry)
    _save_feedback_file(items[:500])
    add_dashboard_log("FEEDBACK", f"{name} rated {rating}/5 (landing)")
    return jsonify({"ok": True, "remote": saved_remote})

@app.route('/api/public_feedback', methods=['GET'])
def api_public_feedback_list():
    """List recent landing-page feedback (latest first)."""
    try:
        limit = max(1, min(int(request.args.get('limit', 6)), 30))
    except (TypeError, ValueError):
        limit = 6
    rows = supabase_list_feedback(limit=200)
    if rows is None:
        rows = _load_feedback_file()[:200]
    landing_only = [r for r in rows if (r.get('source') == 'landing')][:limit]
    total = len(landing_only)
    avg = round(sum((r.get('rating') or 0) for r in landing_only) / total, 2) if total else 0
    return jsonify({"ok": True, "items": landing_only, "count": total, "average": avg})

# ---- BOT REQUESTS ("Ask for a Bot" form) ----
@app.route('/api/bot_request', methods=['POST'])
def api_bot_request_submit():
    """Public submission of a custom-bot request from the landing page."""
    data = request.get_json(silent=True) or {}

    def _s(key, max_len=500):
        v = (data.get(key) or '').strip()
        return v[:max_len]

    name        = _s('name', 120)
    email       = _s('email', 200)
    discord     = _s('discord_handle', 120)
    bot_name    = _s('bot_name', 120)
    bot_purpose = _s('bot_purpose', 120)
    description = _s('description', 4000)
    features    = _s('features', 4000)
    needs_dash  = bool(data.get('needs_dashboard'))
    bot_token   = _s('bot_token', 200)
    has_token   = bool(bot_token)
    timeline    = _s('timeline', 80)
    budget      = _s('budget', 80)
    notes       = _s('notes', 2000)

    # Required fields
    missing = [k for k, v in (("name", name), ("email", email), ("description", description)) if not v]
    if missing:
        return jsonify({"ok": False, "error": f"Missing required: {', '.join(missing)}"}), 400
    if "@" not in email or "." not in email:
        return jsonify({"ok": False, "error": "Invalid email address"}), 400

    entry = {
        "id":             datetime.now(timezone.utc).strftime("req_%Y%m%d_%H%M%S_") + str(int(datetime.now(timezone.utc).timestamp() * 1000) % 100000),
        "name":           name,
        "email":          email,
        "discord_handle": discord,
        "bot_name":       bot_name,
        "bot_purpose":    bot_purpose,
        "description":    description,
        "features":       features,
        "needs_dashboard": needs_dash,
        "bot_token":      bot_token,    # stored as-is; visible only in dev menu
        "has_token":      has_token,
        "timeline":       timeline,
        "budget":         budget,
        "notes":          notes,
        "status":         "new",
        "created_at":     datetime.now(timezone.utc).isoformat(),
    }
    _save_bot_request(entry)
    add_dashboard_log("BOT_REQUEST", f"{name} <{email}> — '{bot_name or 'Untitled bot'}'")
    return jsonify({"ok": True, "id": entry["id"]})

@app.route('/api/bot_requests', methods=['GET'])
def api_bot_requests_list():
    """List all bot requests. Dev session required (contains user emails / tokens)."""
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Locked"}), 403
    try:
        limit = max(1, min(int(request.args.get('limit', 50)), 500))
    except (TypeError, ValueError):
        limit = 50
    items = _load_bot_requests()[:limit]
    # Mask tokens by default; real value still in storage. Dev can see partial.
    masked = []
    for r in items:
        rr = dict(r)
        tok = rr.get("bot_token") or ""
        if tok:
            rr["bot_token_preview"] = (tok[:6] + "…" + tok[-4:]) if len(tok) > 12 else "set"
        rr.pop("bot_token", None)
        masked.append(rr)
    return jsonify({"ok": True, "items": masked, "count": len(masked)})

@app.route('/api/bot_requests/<rid>', methods=['DELETE'])
def api_bot_request_delete(rid):
    """Delete a bot request. Dev session required."""
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Locked"}), 403
    if not _delete_bot_request(rid):
        return jsonify({"ok": False, "error": "Not found"}), 404
    add_dashboard_log("BOT_REQUEST", f"Deleted request {rid}")
    return jsonify({"ok": True})


# ---- Dev-only "Forms" page ----
# Dedicated full-page admin view for every form submission. Reachable from
# the Developer Tools kebab menu under the "Forms" item. Outside of a
# dev-unlocked session the page returns 404 to avoid leaking its existence.
@app.route('/dev/forms', methods=['GET'])
def dev_forms_page():
    if not is_dev_session():
        # Return a real 404 so unauthenticated visitors can't even confirm
        # the page exists. Devs see the full inbox.
        return render_template('login.html',
                               error="Page not found.",
                               discord_configured=bool(DISCORD_CLIENT_ID)), 404
    storage = "Postgres" if _db_forms.is_available() else "Local JSON file"
    return render_template(
        'dev_forms.html',
        dev_unlocked=True,
        storage_backend=storage,
    )

# ---- DEV: LICENSE MANAGEMENT ----
@app.route('/dev/licenses', methods=['GET'])
def dev_licenses_list():
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    all_users = _lm.list_all()
    result = []
    for discord_id, rec in all_users.items():
        user_rec = find_user_by_discord_id(discord_id) or {}
        result.append({
            "discord_id": discord_id,
            "tier":       rec.get("tier", "free"),
            "note":       rec.get("note", ""),
            "granted_at": rec.get("granted_at", ""),
            "username":   user_rec.get("name") or user_rec.get("discord_username") or rec.get("note", ""),
        })
    result.sort(key=lambda x: x.get("tier") == "premium", reverse=True)
    return jsonify({"ok": True, "licenses": result})


@app.route('/dev/licenses/grant', methods=['POST'])
def dev_licenses_grant():
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    discord_id = str(data.get('discord_id', '')).strip()
    tier = data.get('tier', 'free')
    note = data.get('note', '')
    if not discord_id:
        return jsonify({"ok": False, "error": "Discord ID is required"}), 400
    if tier not in ('free', 'premium'):
        return jsonify({"ok": False, "error": "Invalid tier"}), 400
    try:
        _lm.grant_tier(discord_id, tier, note=note, granted_by="admin")
        user_rec = find_user_by_discord_id(discord_id) or {}
        if user_rec:
            user_rec['tier'] = tier
            save_single_user(user_rec)
        add_dashboard_log("🔑 GRANT-TIER", f"Granted {tier.upper()} to Discord ID {discord_id} ({note})")
        return jsonify({"ok": True, "discord_id": discord_id, "tier": tier})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/dev/licenses/revoke', methods=['POST'])
def dev_licenses_revoke():
    if not is_dev_session():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    discord_id = str(data.get('discord_id', '')).strip()
    if not discord_id:
        return jsonify({"ok": False, "error": "No Discord ID provided"}), 400
    ok = _lm.revoke_user(discord_id)
    user_rec = find_user_by_discord_id(discord_id) or {}
    if user_rec:
        user_rec.pop('tier', None)
        save_single_user(user_rec)
    if ok:
        add_dashboard_log("🔑 REVOKE-TIER", f"Revoked tier for Discord ID {discord_id}")
    return jsonify({"ok": ok})


# ---- AUTH: LOGIN (Discord-only) ----
@app.route('/login')
def login():
    return render_template(
        'login.html',
        error=None,
        discord_configured=bool(DISCORD_CLIENT_ID),
    )

@app.route('/logout')
def logout():
    name = session.get('user_name', 'User')
    add_dashboard_log("🚪 LOGOUT", f"{name} logged out")
    session.clear()
    return redirect(url_for('login'))

# ---- DISCORD OAUTH2 ----
def _login_error(msg):
    """Helper: render login page with an error."""
    return render_template(
        'login.html',
        error=msg,
        discord_configured=bool(DISCORD_CLIENT_ID),
    )

@app.route('/auth/discord')
def auth_discord():
    if not DISCORD_CLIENT_ID:
        return _login_error("Discord login is not configured. Contact the administrator.")

    redirect_uri = (DISCORD_REDIRECT_URI or url_for('auth_discord_callback', _external=True)).strip()
    state = secrets.token_urlsafe(16)
    session.permanent = True
    session['oauth_state'] = state
    session.modified = True

    # Build the authorize URL with proper percent-encoding so Discord
    # doesn't reject `redirect_uri` as "Not a well formed URL."
    params = {
        "client_id":     DISCORD_CLIENT_ID,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        # `guilds` lets us read the user's server list so the Server Official
        # dashboard can show only the servers THEY have access to (instead of
        # every guild the bot is in).
        "scope":         "identify guilds",
        "state":         state,
    }
    oauth_url = "https://discord.com/api/oauth2/authorize?" + urlencode(params)
    return redirect(oauth_url)

@app.route('/auth/discord/callback')
def auth_discord_callback():
    error = request.args.get('error')
    if error:
        return _login_error("Discord authorization was denied.")

    code  = request.args.get('code', '')
    state = request.args.get('state', '')

    if not code or state != session.get('oauth_state'):
        return _login_error("Invalid OAuth state. Please try again.")

    session.pop('oauth_state', None)

    redirect_uri = DISCORD_REDIRECT_URI or url_for('auth_discord_callback', _external=True)

    try:
        token_res = http_requests.post(
            f"{DISCORD_API_BASE}/oauth2/token",
            data={
                'client_id':     DISCORD_CLIENT_ID,
                'client_secret': DISCORD_CLIENT_SECRET,
                'grant_type':    'authorization_code',
                'code':          code,
                'redirect_uri':  redirect_uri,
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10
        )
        token_data = token_res.json()
    except Exception as e:
        return _login_error(f"Discord token exchange failed: {e}")

    access_token = token_data.get('access_token')
    if not access_token:
        return _login_error("Could not get Discord access token. Try again.")

    try:
        user_res = http_requests.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        discord_user = user_res.json()
    except Exception as e:
        return _login_error(f"Could not fetch Discord profile: {e}")

    # Fetch the user's guild list so we can scope dashboards (e.g. Server
    # Official) to only the servers this user actually has access to.
    # We keep guilds where the user is the owner OR has Manage Server (0x20).
    # Best-effort: failures here MUST NOT block login.
    user_admin_guild_ids = []
    try:
        guilds_res = http_requests.get(
            f"{DISCORD_API_BASE}/users/@me/guilds",
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        guilds_payload = guilds_res.json() if guilds_res.status_code == 200 else []
        if isinstance(guilds_payload, list):
            MANAGE_GUILD = 0x20
            ADMINISTRATOR = 0x8
            for g in guilds_payload:
                try:
                    perms = int(g.get('permissions', 0))
                except (TypeError, ValueError):
                    perms = 0
                if g.get('owner') or (perms & (MANAGE_GUILD | ADMINISTRATOR)):
                    gid = str(g.get('id', '')).strip()
                    if gid:
                        user_admin_guild_ids.append(gid)
    except Exception as e:
        app.logger.warning(f"[oauth] could not fetch user guilds: {e}")

    discord_id       = str(discord_user.get('id', ''))
    discord_username = discord_user.get('username', 'Unknown')
    discord_global   = discord_user.get('global_name') or discord_username
    discord_avatar   = discord_user.get('avatar') or ''
    avatar_url = (
        f"https://cdn.discordapp.com/avatars/{discord_id}/{discord_avatar}.png"
        if discord_avatar else ''
    )
    discord_email    = discord_user.get('email') or ''

    if not discord_id:
        return _login_error("Could not read Discord profile. Try again.")

    user = find_user_by_discord_id(discord_id)
    is_new = user is None
    if is_new:
        user = {
            'discord_id':       discord_id,
            'discord_username': discord_username,
            'name':             discord_global,
            'avatar_url':       avatar_url,
            'email':            discord_email,
            'created_at':       datetime.now().strftime('%Y-%m-%d'),
        }
    else:
        user['discord_username'] = discord_username
        user['name'] = discord_global
        if avatar_url:
            user['avatar_url'] = avatar_url
        if discord_email:
            user['email'] = discord_email
    save_single_user(user)
    if is_new:
        add_dashboard_log("📝 DISCORD-REGISTER", f"New Discord login: {discord_global} ({discord_id})")

    session.permanent = True
    session['logged_in'] = True
    session['discord_id'] = discord_id
    session['user_name'] = discord_global
    # Cache the user's admin/owner guild ids on the session so per-user
    # dashboards (Server Official, etc.) can scope correctly without an extra
    # Discord API call on every request.
    session['user_admin_guild_ids'] = user_admin_guild_ids
    session.modified = True
    add_dashboard_log("✅ LOGIN", f"{discord_global} logged in via Discord")

    # Auto-detect and apply tier — no manual code entry needed.
    # Check the license manager (Discord-ID keyed) for an assigned tier.
    # Admins always get premium automatically.
    _admin_discord_id = os.environ.get("ADMIN_DISCORD_ID", "").strip()
    _is_admin_user = (
        (user or {}).get("is_admin")
        or (discord_id and _admin_discord_id and discord_id == _admin_discord_id)
    )
    if _is_admin_user:
        _auto_tier = "premium"
    else:
        _auto_tier = _lm.get_user_tier(discord_id)

    # Persist tier on user record so the dashboard reads it consistently
    if user.get("tier") != _auto_tier:
        user["tier"] = _auto_tier
        try:
            save_single_user(user)
        except Exception:
            pass

    # Honor a "return to" target set before login
    next_url = session.pop('post_login_redirect', None)
    if next_url:
        return redirect(next_url)
    return redirect(url_for('landing'))

# ---- REMOVED: UID-based register/forgot-password (Discord-only auth) ----
@app.route('/register')
@app.route('/forgot-password')
def _legacy_auth_redirect():
    return redirect(url_for('login'))

# ---- LICENSE ACTIVATION (legacy redirect — no longer needed) ----
@app.route('/activate-license', methods=['GET', 'POST'])
def activate_license():
    # Tiers are now automatically applied on login — no manual code entry needed.
    return redirect(url_for('landing'))


# ---- MAIN DASHBOARD ----
@app.route('/dashboard')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    discord_id = session.get('discord_id', '')
    if not discord_id:
        session.clear()
        return redirect(url_for('login'))

    # Force re-auth if guild list is absent (old session) so the server
    # picker is always scoped to only the user's admin servers.
    if 'user_admin_guild_ids' not in session:
        redirect_target = url_for('index')
        session.clear()
        session['post_login_redirect'] = redirect_target
        return redirect(url_for('login'))

    current_user = find_user_by_discord_id(discord_id)
    if not current_user:
        # The OAuth callback already authenticated this user and stored their
        # identity in the session. If the persisted record can't be read back
        # (ephemeral filesystem on Render, transient Supabase error, etc.),
        # rebuild it from the session instead of silently logging the user out.
        session_name = session.get('user_name', 'User')
        current_user = {
            'discord_id':       discord_id,
            'discord_username': session_name,
            'name':             session_name,
            'created_at':       datetime.now().strftime('%Y-%m-%d'),
        }
        try:
            save_single_user(current_user)
        except Exception as e:
            print(f"⚠️ Re-saving user from session failed: {e}")
    session['user_name'] = current_user.get('name', 'User')

    # ── Tier: check license manager first, fall back to user record ──
    _admin_discord_id = os.environ.get("ADMIN_DISCORD_ID", "").strip()
    _is_admin = (
        current_user.get("is_admin")
        or (discord_id and _admin_discord_id and discord_id == _admin_discord_id)
    )
    if _is_admin:
        user_tier = "premium"
    else:
        lm_tier = _lm.get_user_tier(discord_id)
        user_tier = lm_tier if lm_tier != "free" else current_user.get("tier", "free")
    is_premium = (user_tier == "premium")
    user_license_code = ""

    live = _sp4rk_live_state(user_guild_ids=_user_admin_guild_ids())

    try:
        return render_template(
            'index.html',
            config=config,
            is_admin=False,
            current_user_name=current_user.get('name', 'User'),
            current_uid='',
            current_session_token='',
            pending_users=[],
            all_users=[],
            dev_unlocked=is_dev_session(),
            user_tier=user_tier,
            user_license_code=user_license_code or current_user.get('license_code', ''),
            is_premium=is_premium,
            live=live,
        )
    except Exception as e:
        print(f"❌ Dashboard template render error: {e}")
        traceback.print_exc()
        safe_name = html_escape(str(current_user.get('name', 'User')))
        safe_uid = ''
        safe_token = ''
        return f"""
        <!doctype html>
        <html>
        <head>
            <title>SP4RK Dashboard</title>
            <style>
                body {{ font-family: Arial, sans-serif; background:#07101f; color:#e8f0ff; padding:40px; }}
                .card {{ max-width:720px; margin:auto; background:#0f1d38; border:1px solid rgba(58,143,255,.25); border-radius:14px; padding:28px; }}
                a {{ color:#5ab4ff; }}
                code {{ background:#07101f; color:#5ab4ff; padding:3px 7px; border-radius:6px; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>SP4RK Dashboard</h1>
                <p>You are logged in as <strong>{safe_name}</strong>.</p>
                <p>UID: <code>{safe_uid}</code></p>
                <p>Session token: <code>{safe_token}</code></p>
                <p>The full dashboard template hit a render error, but your login session is working. Check the server logs for the template error details.</p>
                <p><a href="/dashboard">Retry dashboard</a> · <a href="/logout">Logout</a></p>
            </div>
        </body>
        </html>
        """

# ---- UPDATE SETTINGS ----
@app.route('/update', methods=['POST'])
def update():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    global config
    for key in TEXT_KEYS:
        if key in request.form:
            config[key] = request.form.get(key, "").strip()
    for key in TOGGLE_KEYS:
        config[key] = "ON" if request.form.get(key) else "OFF"
    try:
        config["warning_limit"] = int(request.form.get("warning_limit", 30))
    except ValueError:
        config["warning_limit"] = 30
    save_config()
    _trigger_bot_config_refresh()
    return redirect(url_for('index'))

# ---- API: SAVE SETTINGS ----
@app.route('/api/save', methods=['POST'])
def api_save():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    global config
    data = request.get_json() or {}
    for key in TEXT_KEYS:
        if key in data:
            config[key] = str(data[key]).strip()
    for key in TOGGLE_KEYS:
        if key in data:
            config[key] = "ON" if data[key] else "OFF"
    if "warning_limit" in data:
        try:
            config["warning_limit"] = int(data["warning_limit"])
        except ValueError:
            pass
    save_config()
    _trigger_bot_config_refresh()
    add_dashboard_log("⚙️ SETTINGS", "Configuration updated via dashboard")
    return jsonify({"status": "success", "message": "Settings saved!"})

# ---- API: GET CONFIG ----
@app.route('/api/config')
def api_config():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(config)

@app.route('/api/bot_config')
def api_bot_config():
    return jsonify({key: config.get(key) for key in PUBLIC_BOT_CONFIG_KEYS})

# ---- API: PER-GUILD CONFIG (GET) ----
@app.route('/api/guild_config', methods=['GET'])
def api_guild_config_get():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    guild_id = request.args.get('guild_id', '').strip()
    if not guild_id:
        return jsonify(config)
    cfg = get_guild_config(guild_id)
    return jsonify(cfg)

# ---- API: PER-GUILD CONFIG (POST / SAVE) ----
@app.route('/api/guild_config', methods=['POST'])
def api_guild_config_save():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    guild_id = str(data.pop('guild_id', '')).strip()
    updates = {}
    for key in TEXT_KEYS:
        if key in data:
            updates[key] = str(data[key]).strip()
    for key in TOGGLE_KEYS:
        if key in data:
            updates[key] = "ON" if data[key] else "OFF"
    if "warning_limit" in data:
        try:
            updates["warning_limit"] = int(data["warning_limit"])
        except (ValueError, TypeError):
            pass
    if guild_id:
        save_guild_config(guild_id, updates)
        add_dashboard_log("⚙️ GUILD-SETTINGS", f"Config updated for guild {guild_id}")
    else:
        global config
        config.update(updates)
        save_config()
        add_dashboard_log("⚙️ SETTINGS", "Global configuration updated via dashboard")
    _trigger_bot_config_refresh()
    return jsonify({"status": "success", "message": "Settings saved!"})

# ---- API: BOT CONFIG BY GUILD (for bot worker process) ----
@app.route('/api/bot_config/<guild_id>')
def api_bot_config_guild(guild_id):
    cfg = get_guild_config(str(guild_id).strip())
    return jsonify({key: cfg.get(key) for key in PUBLIC_BOT_CONFIG_KEYS})

# ---- API: LOGS ----
@app.route('/api/logs')
def api_logs():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    limit = int(request.args.get('limit', 100))
    return jsonify(action_logs[:limit])

@app.route('/api/add_log', methods=['POST'])
def api_add_log():
    data = request.get_json() or {}
    add_dashboard_log(data.get("type", "INFO"), data.get("description", ""))
    return jsonify({"status": "success"})

# ---- API: USER FEEDBACK (star ratings) ----
@app.route('/api/feedback', methods=['POST'])
def api_feedback_submit():
    if not session.get('logged_in'):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    try:
        rating = int(data.get('rating', 0))
    except (TypeError, ValueError):
        rating = 0
    if rating < 1 or rating > 5:
        return jsonify({"ok": False, "error": "Rating must be between 1 and 5"}), 400
    comment = (data.get('comment') or '').strip()
    if len(comment) > 2000:
        comment = comment[:2000]
    source = (data.get('source') or 'main').strip()[:64] or 'main'
    entry = {
        "discord_id": session.get('discord_id', ''),
        "user_name":  session.get('user_name', 'User'),
        "rating":     rating,
        "comment":    comment,
        "source":     source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    saved_remote = supabase_save_feedback(entry)
    # Always keep a local copy as a safety net (and so it shows even when
    # Supabase is offline).
    items = _load_feedback_file()
    items.insert(0, entry)
    _save_feedback_file(items[:500])
    add_dashboard_log("FEEDBACK", f"{entry['user_name']} rated {rating}/5 ({source})")
    return jsonify({"ok": True, "remote": saved_remote})


@app.route('/api/feedback', methods=['GET'])
def api_feedback_list():
    if not session.get('logged_in'):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    try:
        limit = max(1, min(int(request.args.get('limit', 10)), 50))
    except (TypeError, ValueError):
        limit = 10
    rows = supabase_list_feedback(limit=limit)
    if rows is None:  # Supabase unavailable -> use local file
        rows = _load_feedback_file()[:limit]
    # Compute aggregate stats from whichever source we used
    total = len(rows)
    avg = round(sum((r.get('rating') or 0) for r in rows) / total, 2) if total else 0
    return jsonify({"ok": True, "items": rows, "count": total, "average": avg})

# ---- API: EXPORT REPORT ----
@app.route('/api/export/report')
def export_report():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401

    warnings_file_data = {}
    if os.path.exists('warnings.json'):
        with open('warnings.json', 'r') as f:
            try:
                warnings_file_data = json.load(f)
            except Exception:
                pass

    export_data = {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config_snapshot": {k: v for k, v in config.items() if k not in ("bot_avatar",)},
        "live_warnings": warnings_db,
        "persistent_warnings": warnings_file_data,
        "action_logs": action_logs
    }

    response = make_response(json.dumps(export_data, indent=4))
    fname = f"sentinel_report_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    response.headers["Content-Disposition"] = f"attachment; filename={fname}"
    response.headers["Content-Type"] = "application/json"
    add_dashboard_log("📥 EXPORT", "Moderation report exported")
    return response

# ---- API: WARNINGS ----
@app.route('/api/get_warnings')
def get_warnings():
    return jsonify(warnings_db)

@app.route('/api/add_warning', methods=['POST'])
def add_warning_api():
    data = request.get_json() or {}
    member_id = data.get("member_id")
    username = data.get("username")
    count = data.get("warnings", 0)
    for w in warnings_db:
        if w["id"] == member_id:
            w["warnings"] = count
            return jsonify({"status": "success"})
    warnings_db.append({"id": member_id, "username": username, "warnings": count})
    add_dashboard_log("⚠️ WARNING", f"{username} now has {count} warning(s)")
    return jsonify({"status": "success"})

@app.route('/api/clear_warning', methods=['POST'])
def clear_warning_api():
    data = request.get_json() or {}
    member_id = data.get("member_id")
    global warnings_db
    entry = next((w for w in warnings_db if w["id"] == member_id), None)
    if entry:
        add_dashboard_log("🧹 CLEAR", f"Warnings cleared for {entry['username']}")
    warnings_db = [w for w in warnings_db if w["id"] != member_id]
    return jsonify({"status": "success"})

# ---- API: CONFIRM ACTION ----
@app.route('/api/confirm_action', methods=['POST'])
def confirm_action():
    data = request.get_json() or {}
    member_id = data.get("member_id")
    action = data.get("action")
    print(f"⚡ Dashboard: {action.upper()} approved for member {member_id}")
    add_dashboard_log(f"{'⛔' if action=='ban' else '🚪'} {action.upper()}", f"Dashboard action approved for member {member_id}")
    return jsonify({"status": "success", "member_id": member_id, "action": action})

# ---- API: BOT STATUS & RESTART ----
@app.route('/api/bot_status')
def bot_status():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    # The dashboard treats the main bot as "online" if either (a) it lives
    # in this Python process (the restart callback is wired up) or (b) the
    # configured DISCORD_TOKEN is valid and Discord is reachable — which
    # covers the common Render setup where the bot runs as a separate
    # service. The restart button itself only makes sense for case (a).
    in_process = _bot_restart_callback is not None
    token_ok = False
    try:
        from bot_health import token_is_live
        token_ok = token_is_live(os.environ.get("DISCORD_TOKEN", ""))
    except Exception:
        token_ok = False
    return jsonify({
        "status": "ok",
        "restart_available": bool(in_process or token_ok),
        "in_process": in_process,
        "remote_online": token_ok and not in_process,
    })

@app.route('/api/connection_status')
def connection_status():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401

    discord_token  = bool(os.environ.get("DISCORD_TOKEN", "").strip())
    client_id      = bool(os.environ.get("DISCORD_CLIENT_ID", "").strip())
    client_secret  = bool(os.environ.get("DISCORD_CLIENT_SECRET", "").strip())
    supabase_ok    = supabase is not None
    gemini_ok      = bool(os.environ.get("GEMINI_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip())

    # Email: need a login user AND a password
    smtp_login_user = (
        os.environ.get("SMTP_USER", "").strip()
        or os.environ.get("GMAIL_USER", "").strip()
        or os.environ.get("SMTP_EMAIL", "").strip()
    )
    smtp_password = (
        os.environ.get("GMAIL_APP_PASSWORD", "")
        or os.environ.get("SMTP_PASS", "")
        or os.environ.get("SMTP_PASSWORD", "")
    ).strip()
    email_ok  = bool(smtp_login_user and smtp_password)
    smtp_host = os.environ.get("SMTP_HOST", "")

    session_ok = bool(os.environ.get("FLASK_SECRET_KEY", "").strip() or os.environ.get("SESSION_SECRET", "").strip())

    redirect_uri = os.environ.get("DISCORD_REDIRECT_URI", "").strip()

    return jsonify({
        "discord_bot":    discord_token,
        "discord_oauth":  client_id and client_secret,
        "discord_redirect": bool(redirect_uri),
        "supabase":       supabase_ok,
        "gemini":         gemini_ok,
        "email":          email_ok,
        "smtp_host":      smtp_host or None,
        "session_stable": session_ok,
        "bot_in_process": _bot_restart_callback is not None,
    })

@app.route('/api/restart_bot', methods=['POST'])
def restart_bot():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    if _bot_restart_callback:
        started = _bot_restart_callback()
        add_dashboard_log("🔄 RESTART", "Bot restart triggered from dashboard")
        msg = "Bot thread started." if started else "Bot is already running."
        return jsonify({"status": "success", "message": msg})
    return jsonify({"status": "error", "message": "Restart not available."}), 503

# ---- API: GIVEAWAYS ----
@app.route('/api/giveaways')
def api_giveaways():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        import giveaway as giveaway_mod
        return jsonify(giveaway_mod.get_all_giveaways())
    except Exception as e:
        return jsonify([])

@app.route('/api/giveaway/set_winner', methods=['POST'])
def api_giveaway_set_winner():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    message_id = data.get('message_id')
    winner_name = (data.get('winner_name') or '').strip()
    if not message_id or not winner_name:
        return jsonify({"error": "message_id and winner_name are required"}), 400
    try:
        import giveaway as giveaway_mod
        giveaway = giveaway_mod.force_set_winner(message_id, winner_name)
        if not giveaway:
            return jsonify({"error": "No active giveaway found with that ID"}), 404
        prize = giveaway.get('prize', 'Unknown')
        add_dashboard_log("👑 MANUAL WINNER", f"Giveaway '{prize}' — winner manually set to: {winner_name}")
        # Announce in Discord if the bot is running in the same process
        try:
            import main as bot_main
            import asyncio
            if hasattr(bot_main, 'bot') and bot_main.bot.is_ready():
                channel_id = giveaway.get('channel_id')
                async def _announce():
                    ch = bot_main.bot.get_channel(int(channel_id))
                    if ch:
                        await ch.send(
                            f"🎉 **Giveaway Winner!** 👑 **{winner_name}** has been manually selected "
                            f"as the winner of **{prize}**!"
                        )
                loop = bot_main.bot.loop
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(_announce(), loop)
        except Exception as disc_err:
            print(f"⚠️ Discord giveaway announce error: {disc_err}")
        return jsonify({"status": "success", "prize": prize, "winner": winner_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---- API: TICKETS ----
@app.route('/api/tickets')
def api_tickets():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        import tickets as tickets_mod
        return jsonify(tickets_mod.get_all_tickets())
    except Exception as e:
        return jsonify([])

# ---- API: AUTO-RESPONDER ----
@app.route('/api/autoresponder')
def api_autoresponder():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        import autoresponder as ar_mod
        return jsonify(ar_mod.get_all_responses())
    except Exception as e:
        return jsonify([])

@app.route('/api/autoresponder/add', methods=['POST'])
def api_autoresponder_add():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    trigger = data.get('trigger', '').strip()
    response = data.get('response', '').strip()
    match = data.get('match', 'contains')
    if not trigger or not response:
        return jsonify({"error": "Missing trigger or response"}), 400
    try:
        import autoresponder as ar_mod
        is_new = ar_mod.add_response(trigger, response, match)
        add_dashboard_log("🤖 AUTO-RESPOND", f"{'Added' if is_new else 'Updated'} trigger: {trigger}")
        return jsonify({"status": "success", "new": is_new})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/autoresponder/remove', methods=['POST'])
def api_autoresponder_remove():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    trigger = data.get('trigger', '').strip()
    try:
        import autoresponder as ar_mod
        removed = ar_mod.remove_response(trigger)
        if removed:
            add_dashboard_log("🤖 AUTO-RESPOND", f"Removed trigger: {trigger}")
        return jsonify({"status": "success", "removed": removed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---- API: REACTION ROLES ----
@app.route('/api/reaction_roles')
def api_reaction_roles():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        import reaction_roles as rr_mod
        return jsonify(rr_mod.get_all_rules())
    except Exception as e:
        return jsonify([])

@app.route('/api/reaction_roles/remove', methods=['POST'])
def api_reaction_roles_remove():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    message_id = data.get('message_id')
    emoji = data.get('emoji', '')
    try:
        import reaction_roles as rr_mod
        removed = rr_mod.remove_rule(message_id, emoji)
        return jsonify({"status": "success", "removed": removed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---- API: MENTION REACTIONS ----
@app.route('/api/mention_reactions', methods=['GET'])
def api_mention_reactions_get():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    guild_id = request.args.get('guild_id', '')
    try:
        import mention_reactions as mr_mod
        return jsonify(mr_mod.get_rules(guild_id or None))
    except Exception as e:
        return jsonify([])

@app.route('/api/mention_reactions/add', methods=['POST'])
def api_mention_reactions_add():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    guild_id = data.get('guild_id', '').strip()
    user_id  = data.get('user_id', '').strip()
    emoji    = data.get('emoji', '').strip()
    label    = data.get('label', '').strip()
    if not guild_id or not user_id or not emoji:
        return jsonify({"error": "guild_id, user_id and emoji are required"}), 400
    try:
        import mention_reactions as mr_mod
        is_new = mr_mod.add_rule(guild_id, user_id, emoji, label)
        return jsonify({"status": "success", "new": is_new})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/mention_reactions/remove', methods=['POST'])
def api_mention_reactions_remove():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    guild_id = data.get('guild_id', '').strip()
    user_id  = data.get('user_id', '').strip()
    if not guild_id or not user_id:
        return jsonify({"error": "guild_id and user_id are required"}), 400
    try:
        import mention_reactions as mr_mod
        removed = mr_mod.remove_rule(guild_id, user_id)
        return jsonify({"status": "success", "removed": removed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---- API: LEVELING ----
@app.route('/api/leveling/config', methods=['GET'])
def api_leveling_config_get():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    guild_id = request.args.get('guild_id', '').strip()
    if not guild_id:
        return jsonify({"error": "guild_id required"}), 400
    try:
        import leveling as lv
        return jsonify(lv.get_config(guild_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/leveling/config', methods=['POST'])
def api_leveling_config_set():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    guild_id = data.pop('guild_id', '').strip()
    if not guild_id:
        return jsonify({"error": "guild_id required"}), 400
    try:
        import leveling as lv
        lv.set_config(guild_id, **data)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/leveling/leaderboard', methods=['GET'])
def api_leveling_leaderboard():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    guild_id = request.args.get('guild_id', '').strip()
    if not guild_id:
        return jsonify([])
    try:
        import leveling as lv
        return jsonify(lv.get_leaderboard(guild_id, limit=25))
    except Exception as e:
        return jsonify([])

@app.route('/api/leveling/reset_user', methods=['POST'])
def api_leveling_reset_user():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    guild_id = data.get('guild_id', '').strip()
    user_id  = data.get('user_id', '').strip()
    if not guild_id or not user_id:
        return jsonify({"error": "guild_id and user_id required"}), 400
    try:
        import leveling as lv, json, os
        path = lv._XP_FILE
        if os.path.exists(path):
            db = json.load(open(path))
            db.pop(f"{guild_id}:{user_id}", None)
            lv._save_file(path, db)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---- API: STARBOARD ----
@app.route('/api/starboard/config', methods=['GET'])
def api_starboard_config_get():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    guild_id = request.args.get('guild_id', '').strip()
    if not guild_id:
        return jsonify({"error": "guild_id required"}), 400
    try:
        import starboard as sb
        return jsonify(sb.get_config(guild_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/starboard/config', methods=['POST'])
def api_starboard_config_set():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    guild_id = data.pop('guild_id', '').strip()
    if not guild_id:
        return jsonify({"error": "guild_id required"}), 400
    try:
        import starboard as sb
        sb.set_config(guild_id, **data)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---- API: STATS OVERVIEW ----
@app.route('/api/stats')
def api_stats():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    import os, json as _json
    total_warnings = 0
    try:
        if os.path.exists('warnings.json'):
            with open('warnings.json') as f:
                wdb = _json.load(f)
            for guild in wdb.values():
                if isinstance(guild, dict):
                    for v in guild.values():
                        if isinstance(v, dict):
                            total_warnings += v.get("count", 0)
                        elif isinstance(v, int):
                            total_warnings += v
    except Exception:
        pass
    active_giveaways = 0
    try:
        if os.path.exists('giveaways.json'):
            with open('giveaways.json') as f:
                gd = _json.load(f)
            active_giveaways = len([g for g in gd.get("giveaways", []) if not g.get("ended")])
    except Exception:
        pass
    open_tickets = 0
    try:
        if os.path.exists('tickets.json'):
            with open('tickets.json') as f:
                td = _json.load(f)
            open_tickets = len([t for t in td.get("tickets", []) if t.get("status") == "open"])
    except Exception:
        pass
    ar_count = 0
    try:
        if os.path.exists('autoresponder.json'):
            with open('autoresponder.json') as f:
                ar = _json.load(f)
            ar_count = len(ar.get("responses", []))
    except Exception:
        pass
    rr_count = 0
    try:
        if os.path.exists('reaction_roles.json'):
            with open('reaction_roles.json') as f:
                rr = _json.load(f)
            rr_count = len(rr.get("rules", []))
    except Exception:
        pass
    return jsonify({
        "total_warnings": total_warnings,
        "active_giveaways": active_giveaways,
        "open_tickets": open_tickets,
        "autoresponders": ar_count,
        "reaction_roles": rr_count
    })

# ---- SERVER OFFICIAL: AVATAR / PERMISSION ----
@app.route('/server-official/api/permission', methods=['GET'])
def so_api_permission():
    """Tell the dashboard whether the logged-in user can manage the bot in the given guild."""
    so, err = _so_require_login()
    if err: return err
    gid = request.args.get('guild_id', '').strip()
    uid = str(session.get('discord_id') or '')
    if not gid:
        return jsonify({"ok": True, "data": {"allowed": False, "reason": "Select a server first", "role": ""}})
    if not uid:
        return jsonify({"ok": True, "data": {"allowed": False, "reason": "No Discord user in session", "role": ""}})
    return jsonify({"ok": True, "data": so.member_can_manage_bot(gid, uid)})


@app.route('/server-official/api/avatar', methods=['GET'])
def so_api_avatar_get():
    so, err = _so_require_login()
    if err: return err
    gid = request.args.get('guild_id', '').strip()
    if not gid:
        return jsonify({"ok": False, "error": "guild_id required"}), 400
    return jsonify({"ok": True, "data": {"url": so.get_guild_avatar_url(gid)}})


@app.route('/server-official/api/avatar', methods=['POST'])
def so_api_avatar_upload():
    so, err = _so_require_login()
    if err: return err
    gid = (request.form.get('guild_id') or '').strip()
    uid = str(session.get('discord_id') or '')
    if not gid:
        return jsonify({"ok": False, "error": "guild_id required"}), 400
    perm = so.member_can_manage_bot(gid, uid)
    if not perm.get("allowed"):
        return jsonify({"ok": False, "error": perm.get("reason") or "Permission denied"}), 403
    if 'image' not in request.files:
        return jsonify({"ok": False, "error": "No image uploaded"}), 400
    f = request.files['image']
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Empty file"}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'png'
    data = f.read()
    result = so.save_guild_avatar(gid, data, ext)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "Save failed")}), 400
    so.add_bot_action("avatar_change", f"Avatar updated for guild {gid} ({result.get('discord_status')})", _current_actor())
    return jsonify({"ok": True, "data": result})


@app.route('/server-official/api/avatar', methods=['DELETE'])
def so_api_avatar_delete():
    so, err = _so_require_login()
    if err: return err
    gid = (request.args.get('guild_id') or '').strip()
    uid = str(session.get('discord_id') or '')
    if not gid:
        return jsonify({"ok": False, "error": "guild_id required"}), 400
    perm = so.member_can_manage_bot(gid, uid)
    if not perm.get("allowed"):
        return jsonify({"ok": False, "error": perm.get("reason") or "Permission denied"}), 403
    result = so.delete_guild_avatar(gid)
    so.add_bot_action("avatar_reset", f"Avatar reset for guild {gid} ({result.get('discord_status')})", _current_actor())
    return jsonify({"ok": True, "data": result})


# ============================================================
# 🎨 EMBED EDITOR API (shared by SP4RK + Server Official)
# ============================================================
import embeds as _embeds_mod


def _embeds_for_bot(bot: str, guild_id=None):
    """Return [{key, label, default, override, effective, ...}] for a bot."""
    items = []
    for entry in _embeds_mod.list_for_bot(bot):
        key = entry["key"]
        items.append({
            "key": key,
            "label": entry.get("label", key),
            "category": entry.get("category", ""),
            "description": entry.get("description", ""),
            "placeholders": entry.get("placeholders", []),
            "default": entry["default"],
            "override": _embeds_mod.get_override(key, guild_id=guild_id),
            "effective": _embeds_mod.get_effective(key, guild_id=guild_id),
            "has_override": _embeds_mod.has_override(key, guild_id=guild_id),
        })
    return items


@app.route('/api/embeds', methods=['GET'])
def api_embeds_list():
    """List SP4RK embed templates (with optional ?guild_id=)."""
    gid = request.args.get("guild_id")
    return jsonify({"ok": True, "items": _embeds_for_bot("sp4rk", guild_id=gid)})


@app.route('/api/embeds', methods=['POST'])
def api_embeds_save():
    """Save an SP4RK embed override. Body: {key, override:{title,description,color,footer,...}, guild_id?}"""
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    override = body.get("override") or {}
    gid = body.get("guild_id")
    if not key or not key.startswith("sp4rk."):
        return jsonify({"ok": False, "error": "invalid key"}), 400
    try:
        _embeds_mod.save_override(key, override, guild_id=gid)
        actor = _current_actor() if "_current_actor" in globals() else "dashboard"
        try:
            so.add_bot_action("embed_edit", f"Edited embed {key}" + (f" (guild {gid})" if gid else " (global)"), actor)
        except Exception:
            pass
        return jsonify({"ok": True, "effective": _embeds_mod.get_effective(key, guild_id=gid)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route('/api/embeds', methods=['DELETE'])
def api_embeds_reset():
    """Reset an SP4RK embed override to default. Body: {key, guild_id?}"""
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    gid = body.get("guild_id")
    if not key:
        return jsonify({"ok": False, "error": "missing key"}), 400
    _embeds_mod.reset_override(key, guild_id=gid)
    return jsonify({"ok": True, "effective": _embeds_mod.get_effective(key, guild_id=gid)})


@app.route('/api/embeds/preview', methods=['POST'])
def api_embeds_preview():
    """Preview an embed (rendered with sample placeholders).
    Body: {key, override?, guild_id?}  — override is the unsaved draft.
    Returns the rendered embed as a dict for client-side preview."""
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "missing key"}), 400
    try:
        preview = _embeds_mod.preview(key, payload=body.get("override"), guild_id=body.get("guild_id"))
        return jsonify({"ok": True, "preview": preview})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ---- Server Official embed editor (same logic, namespaced) ----
@app.route('/server-official/api/embeds', methods=['GET'])
def so_api_embeds_list():
    gid = request.args.get("guild_id")
    return jsonify({"ok": True, "items": _embeds_for_bot("server_official", guild_id=gid)})


@app.route('/server-official/api/embeds', methods=['POST'])
def so_api_embeds_save():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    override = body.get("override") or {}
    gid = body.get("guild_id")
    if not key or not key.startswith("so."):
        return jsonify({"ok": False, "error": "invalid key"}), 400
    try:
        _embeds_mod.save_override(key, override, guild_id=gid)
        try:
            so.add_bot_action("embed_edit", f"Edited embed {key}" + (f" (guild {gid})" if gid else " (global)"), _current_actor())
        except Exception:
            pass
        return jsonify({"ok": True, "effective": _embeds_mod.get_effective(key, guild_id=gid)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route('/server-official/api/embeds', methods=['DELETE'])
def so_api_embeds_reset():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    gid = body.get("guild_id")
    if not key:
        return jsonify({"ok": False, "error": "missing key"}), 400
    _embeds_mod.reset_override(key, guild_id=gid)
    return jsonify({"ok": True, "effective": _embeds_mod.get_effective(key, guild_id=gid)})


@app.route('/server-official/api/embeds/preview', methods=['POST'])
def so_api_embeds_preview():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "missing key"}), 400
    try:
        preview = _embeds_mod.preview(key, payload=body.get("override"), guild_id=body.get("guild_id"))
        return jsonify({"ok": True, "preview": preview})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ---- HEALTH CHECK ----
@app.route('/health')
@app.route('/ping')
def health():
    """Minimal plain-text endpoint for cron-job.org / uptime monitors.
    Returns a tiny response so monitors never hit the 'output too large' limit.
    """
    import time as _t
    import sys as _sys
    bot_alive = False
    try:
        bot_mod = _sys.modules.get('main')
        if bot_mod:
            t = getattr(bot_mod, '_bot_thread', None)
            bot_alive = bool(t and t.is_alive())
    except Exception:
        pass
    body = f"OK bot={'up' if bot_alive else 'off'} ts={int(_t.time())}"
    return body, 200, {'Content-Type': 'text/plain; charset=utf-8'}

# ---- RUN ----
def run_dashboard(port=None):
    if port is None:
        port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
