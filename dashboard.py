from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
import os
import json
import random
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "sentinel_mega_secret_key_v2"

ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

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
    "daily_report": "OFF"
}

config = {**DEFAULT_CONFIG}

if os.path.exists('settings.json'):
    with open('settings.json', 'r') as f:
        try:
            config.update(json.load(f))
            print("📁 Settings loaded into Dashboard!")
        except Exception:
            pass

warnings_db = []

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

# ---- CAPTCHA ----
def generate_captcha():
    a = random.randint(1, 15)
    b = random.randint(1, 15)
    return f"{a} + {b}", a + b

# ---- LOGIN ATTEMPT TRACKING ----
login_attempts = {}  # ip -> {"count": int, "locked_until": datetime|None}
MAX_ATTEMPTS   = 3
LOCKOUT_MINS   = 5

TOGGLE_KEYS = [
    "mod_block_links", "mod_block_mentions",
    "auto_block_links", "auto_block_invites", "auto_block_mentions",
    "auto_dm_warning", "owner_approval",
    "anti_raid", "security_2fa", "role_monitor", "mass_ban_prevent",
    "temp_lockdown", "discord_alerts", "twitter_alerts", "youtube_alerts",
    "daily_report"
]

TEXT_KEYS = [
    "prefix", "bot_status", "theme_color", "welcome_msg", "bot_avatar",
    "log_channel", "profanity_level", "mute_role", "auto_role",
    "auto_custom_blacklist", "webhook_log", "banned_words",
    "youtube_channel_link", "youtube_notify_msg", "multi_channel",
    "owner_dm_id"
]

def save_config():
    with open('settings.json', 'w') as f:
        json.dump(config, f, indent=4)
    print("💾 Settings saved to settings.json")

# ---- AUTH ----
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    ip = request.remote_addr or "unknown"

    # Check lockout
    if ip in login_attempts:
        attempt = login_attempts[ip]
        if attempt.get('locked_until') and datetime.now() < attempt['locked_until']:
            secs = (attempt['locked_until'] - datetime.now()).seconds
            mins = max(1, (secs // 60) + 1)
            question, answer = generate_captcha()
            session['captcha_answer'] = answer
            return render_template('login.html',
                error=f"🔒 Too many failed attempts. Try again in {mins} minute(s).",
                captcha_question=question, locked=True)

    if request.method == 'POST':
        captcha_input = request.form.get('captcha_answer', '').strip()
        expected      = str(session.get('captcha_answer', ''))

        if captcha_input != expected:
            error = "❌ Wrong captcha answer!"
        elif request.form.get('username') == ADMIN_USER and request.form.get('password') == ADMIN_PASS:
            session['logged_in'] = True
            login_attempts.pop(ip, None)
            add_dashboard_log("🔑 LOGIN", f"Admin logged in from {ip}")
            return redirect(url_for('index'))
        else:
            if ip not in login_attempts:
                login_attempts[ip] = {"count": 0, "locked_until": None}
            login_attempts[ip]["count"] += 1
            count = login_attempts[ip]["count"]
            if count >= MAX_ATTEMPTS:
                login_attempts[ip]["locked_until"] = datetime.now() + timedelta(minutes=LOCKOUT_MINS)
                error = f"🔒 Too many failed attempts! Locked for {LOCKOUT_MINS} minutes."
            else:
                left = MAX_ATTEMPTS - count
                error = f"❌ Invalid credentials! {left} attempt(s) left."

    question, answer = generate_captcha()
    session['captcha_answer'] = answer
    return render_template('login.html', error=error, captcha_question=question, locked=False)

@app.route('/logout')
def logout():
    add_dashboard_log("🚪 LOGOUT", "Admin logged out")
    session.clear()
    return redirect(url_for('login'))

# ---- MAIN DASHBOARD ----
@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('index.html', config=config)

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
    add_dashboard_log("⚙️ SETTINGS", "Configuration updated via dashboard")
    return jsonify({"status": "success", "message": "Settings saved!"})

# ---- API: GET CONFIG ----
@app.route('/api/config')
def api_config():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(config)

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
    username  = data.get("username")
    count     = data.get("warnings", 0)
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
    data      = request.get_json() or {}
    member_id = data.get("member_id")
    action    = data.get("action")
    print(f"⚡ Dashboard: {action.upper()} approved for member {member_id}")
    add_dashboard_log(f"{'⛔' if action=='ban' else '🚪'} {action.upper()}", f"Dashboard action approved for member {member_id}")
    return jsonify({"status": "success", "member_id": member_id, "action": action})

# ---- RUN ----
def run_dashboard():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
