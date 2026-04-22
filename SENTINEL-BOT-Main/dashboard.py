from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
import os
import json
import secrets
import requests as http_requests
import traceback
from html import escape as html_escape
from datetime import datetime
from supabase import create_client, Client as SupabaseClient

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

DEFAULT_CONFIG = {
    "prefix": "!",
    "bot_status": "online",
    "theme_color": "#3498db",
    "welcome_msg": "Hello {user}, welcome to the server!",
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
    "leveling": "ON",
    "level_up_message": "{user} You have reached a milestone.upgraded to {level} discord.gg",
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

# ---- USERS MANAGEMENT (Discord-only, Supabase-backed with JSON fallback) ----
USERS_FILE = 'users.json'

def _normalize_user(u):
    if not isinstance(u, dict) or not u.get('discord_id'):
        return None
    u['discord_id'] = str(u['discord_id'])
    if not u.get('name'):
        u['name'] = u.get('discord_username') or 'User'
    if not u.get('created_at'):
        u['created_at'] = datetime.now().strftime('%Y-%m-%d')
    return u

def load_users():
    if supabase:
        try:
            res = supabase.table("sentinel_users").select("*").execute()
            return [u for u in (res.data or []) if _normalize_user(u)]
        except Exception as e:
            print(f"⚠️ Supabase load_users error: {e}")
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
    if supabase:
        try:
            existing = supabase.table("sentinel_users").select("discord_id").eq("discord_id", u["discord_id"]).execute()
            if existing.data:
                supabase.table("sentinel_users").update(u).eq("discord_id", u["discord_id"]).execute()
            else:
                supabase.table("sentinel_users").insert(u).execute()
            return
        except Exception as e:
            print(f"⚠️ Supabase save_single_user error: {e}")
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
    "wall_filter", "anti_zalgo", "grad_punish", "leveling",
]

TEXT_KEYS = [
    "prefix", "bot_status", "theme_color", "welcome_msg", "bot_avatar",
    "log_channel", "profanity_level", "mute_role", "auto_role",
    "auto_custom_blacklist", "webhook_log", "banned_words",
    "youtube_channel_link", "youtube_notify_msg", "multi_channel",
    "owner_dm_id", "ticket_category", "level_up_message",
    "spam_threshold", "spam_window", "caps_percent",
    "emoji_limit", "wall_newlines", "wall_length",
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

# ---- LANDING PAGE ----
@app.route('/')
def landing():
    return render_template('landing.html')

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

    redirect_uri = DISCORD_REDIRECT_URI or request.host_url.rstrip('/') + '/auth/discord/callback'
    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state

    oauth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=identify"
        f"&state={state}"
    )
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

    redirect_uri = DISCORD_REDIRECT_URI or request.host_url.rstrip('/') + '/auth/discord/callback'

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

    discord_id       = str(discord_user.get('id', ''))
    discord_username = discord_user.get('username', 'Unknown')
    discord_global   = discord_user.get('global_name') or discord_username

    if not discord_id:
        return _login_error("Could not read Discord profile. Try again.")

    user = find_user_by_discord_id(discord_id)
    if not user:
        user = {
            'discord_id':       discord_id,
            'discord_username': discord_username,
            'name':             discord_global,
            'created_at':       datetime.now().strftime('%Y-%m-%d'),
        }
        save_single_user(user)
        add_dashboard_log("📝 DISCORD-REGISTER", f"New Discord login: {discord_global} ({discord_id})")
    else:
        user['discord_username'] = discord_username
        user['name'] = discord_global
        save_single_user(user)

    session['logged_in'] = True
    session['discord_id'] = discord_id
    session['user_name'] = discord_global
    add_dashboard_log("✅ LOGIN", f"{discord_global} logged in via Discord")
    return redirect(url_for('index'))

# ---- REMOVED: UID-based register/forgot-password (Discord-only auth) ----
@app.route('/register')
@app.route('/forgot-password')
def _legacy_auth_redirect():
    return redirect(url_for('login'))

# ---- MAIN DASHBOARD ----
@app.route('/dashboard')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    discord_id = session.get('discord_id', '')
    current_user = find_user_by_discord_id(discord_id) if discord_id else None
    if not current_user:
        session.clear()
        return redirect(url_for('login'))
    session['user_name'] = current_user.get('name', 'User')

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
            <title>Sentinel Dashboard</title>
            <style>
                body {{ font-family: Arial, sans-serif; background:#07101f; color:#e8f0ff; padding:40px; }}
                .card {{ max-width:720px; margin:auto; background:#0f1d38; border:1px solid rgba(58,143,255,.25); border-radius:14px; padding:28px; }}
                a {{ color:#5ab4ff; }}
                code {{ background:#07101f; color:#5ab4ff; padding:3px 7px; border-radius:6px; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Sentinel Dashboard</h1>
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
    alive = _bot_restart_callback is not None
    return jsonify({"status": "ok", "restart_available": alive})

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

# ---- API: XP LEADERBOARD ----
@app.route('/api/xp/leaderboard')
def api_xp_leaderboard():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        import leveling as lev_mod
        result = []
        for uid, data in lev_mod.get_all_leaderboard(50):
            total_xp = data.get("xp", 0)
            level, xp_in, xp_needed = lev_mod.get_xp_progress(total_xp)
            result.append({
                "guild_id": data.get("guild_id", ""),
                "user_id": uid,
                "username": data.get("username", uid),
                "xp": total_xp,
                "level": level,
                "messages": data.get("messages", 0),
                "xp_in": xp_in,
                "xp_needed": xp_needed
            })
        return jsonify(result)
    except Exception as e:
        return jsonify([])

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

# ---- HEALTH CHECK ----
@app.route('/health')
def health():
    return 'OK', 200

# ---- RUN ----
def run_dashboard(port=None):
    if port is None:
        port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
