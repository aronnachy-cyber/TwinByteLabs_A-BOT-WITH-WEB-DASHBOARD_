from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
import os
import json
import random
import string
import secrets
import threading
import requests as http_requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

DISCORD_CLIENT_ID     = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI  = os.environ.get("DISCORD_REDIRECT_URI", "")
DISCORD_API_BASE      = "https://discord.com/api/v10"

def send_email(to_address, subject, html_body):
    smtp_user     = (os.environ.get("SMTP_USER") or os.environ.get("SMTP_EMAIL", ""))
    smtp_password = (os.environ.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASS")
                     or os.environ.get("SMTP_ID") or os.environ.get("SMTP_KEY", ""))
    smtp_host     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port     = int(os.environ.get("SMTP_PORT", "587"))
    smtp_from     = os.environ.get("SMTP_FROM") or smtp_user

    if not smtp_user or not smtp_password:
        print("⚠️  SMTP not configured — email not sent.")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Sentinel OS <{smtp_from}>"
        msg["To"]      = to_address
        msg.attach(MIMEText(html_body, "html"))
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_from, to_address, msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_from, to_address, msg.as_string())
        print(f"✅ Email sent to {to_address}")
        return True
    except Exception as e:
        print(f"❌ Email send error: {e}")
        return False

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

# ---- USERS MANAGEMENT ----
USERS_FILE = 'users.json'

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            try:
                return json.load(f).get('users', [])
            except Exception:
                return []
    return []

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump({'users': users}, f, indent=4)

def find_user_by_uid(uid):
    for u in load_users():
        if u.get('uid', '').upper() == uid.upper():
            return u
    return None

def generate_uid(name):
    clean = ''.join(c for c in name.upper() if c.isalpha())[:4]
    clean = clean.ljust(4, 'X')
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"TBLS-{clean}-{code}"

# ---- BOT RESTART CALLBACK ----
_bot_restart_callback = None

def set_bot_restart_callback(fn):
    global _bot_restart_callback
    _bot_restart_callback = fn

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
login_attempts = {}
MAX_ATTEMPTS = 3
LOCKOUT_MINS = 5

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

# ---- LANDING PAGE ----
@app.route('/')
def landing():
    return render_template('landing.html')

# ---- AUTH: LOGIN (UID-based) ----
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    ip = request.remote_addr or "unknown"

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
        expected = str(session.get('captcha_answer', ''))

        if captcha_input != expected:
            error = "❌ Wrong captcha answer!"
        else:
            uid = request.form.get('uid', '').strip().upper()
            user = find_user_by_uid(uid)

            if not user:
                if ip not in login_attempts:
                    login_attempts[ip] = {"count": 0, "locked_until": None}
                login_attempts[ip]["count"] += 1
                count = login_attempts[ip]["count"]
                if count >= MAX_ATTEMPTS:
                    login_attempts[ip]["locked_until"] = datetime.now() + timedelta(minutes=LOCKOUT_MINS)
                    error = f"🔒 Too many failed attempts! Locked for {LOCKOUT_MINS} minutes."
                else:
                    left = MAX_ATTEMPTS - count
                    error = f"❌ Invalid UID. {left} attempt(s) left."
            elif user['status'] == 'pending':
                error = "⏳ Your registration is pending admin approval. Please wait."
            elif user['status'] == 'rejected':
                error = "❌ Your registration was rejected. Contact the admin."
            elif user['status'] == 'approved':
                session['logged_in'] = True
                session['uid'] = user['uid']
                session['is_admin'] = user.get('is_admin', False)
                session['user_name'] = user.get('name', 'User')
                login_attempts.pop(ip, None)
                add_dashboard_log("🔑 LOGIN", f"{user['name']} ({user['uid']}) logged in from {ip}")
                return redirect(url_for('index'))

    question, answer = generate_captcha()
    session['captcha_answer'] = answer
    return render_template('login.html', error=error, captcha_question=question, locked=False)

@app.route('/logout')
def logout():
    name = session.get('user_name', 'User')
    add_dashboard_log("🚪 LOGOUT", f"{name} logged out")
    session.clear()
    return redirect(url_for('login'))

# ---- DISCORD OAUTH2 ----
def _login_error(msg):
    """Helper: render login page with an error and fresh captcha."""
    question, answer = generate_captcha()
    session['captcha_answer'] = answer
    return render_template('login.html', error=msg, captcha_question=question, locked=False)

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

    discord_id       = discord_user.get('id', '')
    discord_username = discord_user.get('username', 'Unknown')
    discord_global   = discord_user.get('global_name') or discord_username

    users = load_users()
    existing = next((u for u in users if u.get('discord_id') == discord_id), None)

    if existing:
        if existing.get('status') == 'approved':
            session['logged_in']  = True
            session['user_name']  = existing['name']
            session['uid']        = existing['uid']
            session['is_admin']   = existing.get('is_admin', False)
            add_dashboard_log("✅ LOGIN", f"{existing['name']} logged in via Discord")
            return redirect(url_for('index'))
        elif existing.get('status') == 'rejected':
            return _login_error("Your account has been rejected. Contact the administrator.")
        else:
            return _login_error(f"Your account is pending admin approval. Your UID is: {existing['uid']}")

    uid = generate_uid(discord_global)
    while any(u['uid'] == uid for u in users):
        uid = generate_uid(discord_global)

    new_user = {
        'uid':              uid,
        'name':             discord_global,
        'discord_id':       discord_id,
        'discord_username': discord_username,
        'status':           'pending',
        'is_admin':         False,
        'created_at':       datetime.now().strftime('%Y-%m-%d')
    }
    users.append(new_user)
    save_users(users)
    add_dashboard_log("📝 DISCORD-REGISTER", f"New Discord login: {discord_global} ({uid}) — pending approval")

    return _login_error(
        f"Welcome {discord_global}! Your account has been created and is pending admin approval. "
        f"Your UID is: {uid}"
    )

# ---- REGISTER ----
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name  = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        if not name or len(name) < 2:
            return render_template('register.html', error="Please enter a valid name (at least 2 characters).")

        uid = generate_uid(name)
        users = load_users()
        while any(u['uid'] == uid for u in users):
            uid = generate_uid(name)

        if email and any(u.get('email','').lower() == email for u in users):
            return render_template('register.html', error="That email is already registered.")

        users.append({
            'uid':        uid,
            'name':       name,
            'email':      email,
            'status':     'pending',
            'is_admin':   False,
            'created_at': datetime.now().strftime('%Y-%m-%d')
        })
        save_users(users)
        add_dashboard_log("📝 REGISTER", f"New registration: {name} ({uid}) — pending approval")

        if email:
            email_body = f"""
                <div style="background:#07101f;color:#e8f0ff;font-family:sans-serif;padding:40px;border-radius:12px;max-width:480px;margin:auto;">
                  <h2 style="color:#3a8fff;">🛡️ Sentinel OS</h2>
                  <p>Hi <strong>{name}</strong>, your registration has been received!</p>
                  <p>Your UID is:</p>
                  <div style="background:#0d1a33;border:1px solid #1e3a6e;border-radius:8px;padding:14px 20px;font-size:1.3rem;letter-spacing:3px;color:#3a8fff;text-align:center;margin:16px 0;">
                    {uid}
                  </div>
                  <p style="color:#7a93b8;font-size:0.88rem;">An admin will review and approve your account. Once approved, use your UID to log in.</p>
                  <p style="color:#7a93b8;font-size:0.8rem;margin-top:24px;">Sentinel OS — Your Server's Guardian</p>
                </div>
                """
            threading.Thread(target=send_email, args=(email, "Sentinel OS — Registration Received", email_body), daemon=True).start()

        return render_template('register.html', success=True, uid=uid, name=name)

    return render_template('register.html')

# ---- FORGOT PASSWORD ----
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email:
            return render_template('forgot_password.html', error="Please enter your email address.")

        users = load_users()
        user = next((u for u in users if u.get('email', '').lower() == email), None)

        if not user:
            return render_template('forgot_password.html',
                error="No account found with that email address.")

        recovery_body = f"""
            <div style="background:#07101f;color:#e8f0ff;font-family:sans-serif;padding:40px;border-radius:12px;max-width:480px;margin:auto;">
              <h2 style="color:#3a8fff;">🛡️ Sentinel OS — UID Recovery</h2>
              <p>Hi <strong>{user['name']}</strong>, here is your UID:</p>
              <div style="background:#0d1a33;border:1px solid #1e3a6e;border-radius:8px;padding:14px 20px;font-size:1.3rem;letter-spacing:3px;color:#3a8fff;text-align:center;margin:16px 0;">
                {user['uid']}
              </div>
              <p style="color:#7a93b8;font-size:0.88rem;">Use this UID to log in to your Sentinel OS dashboard.</p>
              <p style="color:#7a93b8;font-size:0.8rem;margin-top:24px;">If you did not request this, please ignore this email.</p>
              <p style="color:#7a93b8;font-size:0.8rem;">Sentinel OS — Your Server's Guardian</p>
            </div>
            """
        threading.Thread(target=send_email, args=(email, "Sentinel OS — Your UID", recovery_body), daemon=True).start()
        add_dashboard_log("📧 UID-RECOVERY", f"UID recovery requested for {email} ({user['name']})")
        return render_template('forgot_password.html', success=True)

    return render_template('forgot_password.html')

# ---- MAIN DASHBOARD ----
@app.route('/dashboard')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    users = load_users()
    pending_users = [u for u in users if u['status'] == 'pending']
    all_users = users

    return render_template(
        'index.html',
        config=config,
        is_admin=session.get('is_admin', False),
        current_user_name=session.get('user_name', 'User'),
        current_uid=session.get('uid', ''),
        pending_users=pending_users,
        all_users=all_users
    )

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

# ---- API: USER MANAGEMENT (admin only) ----
@app.route('/api/users')
def api_users():
    if not session.get('logged_in') or not session.get('is_admin'):
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(load_users())

@app.route('/api/approve_user', methods=['POST'])
def approve_user():
    if not session.get('logged_in') or not session.get('is_admin'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    uid = data.get('uid', '').upper()
    users = load_users()
    for u in users:
        if u['uid'].upper() == uid:
            u['status'] = 'approved'
            add_dashboard_log("✅ APPROVED", f"User {u['name']} ({uid}) approved")
            break
    save_users(users)
    return jsonify({"status": "success"})

@app.route('/api/reject_user', methods=['POST'])
def reject_user():
    if not session.get('logged_in') or not session.get('is_admin'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    uid = data.get('uid', '').upper()
    users = load_users()
    for u in users:
        if u['uid'].upper() == uid:
            u['status'] = 'rejected'
            add_dashboard_log("❌ REJECTED", f"User {u['name']} ({uid}) rejected")
            break
    save_users(users)
    return jsonify({"status": "success"})

@app.route('/api/delete_user', methods=['POST'])
def delete_user():
    if not session.get('logged_in') or not session.get('is_admin'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    uid = data.get('uid', '').upper()
    users = load_users()
    users = [u for u in users if u['uid'].upper() != uid or u.get('is_admin')]
    save_users(users)
    add_dashboard_log("🗑️ DELETED", f"User {uid} removed")
    return jsonify({"status": "success"})

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
    if not session.get('logged_in') or not session.get('is_admin'):
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
        import json, os
        if not os.path.exists('xp.json'):
            return jsonify([])
        with open('xp.json') as f:
            xp_data = json.load(f)
        result = []
        for guild_id, members in xp_data.items():
            for uid, data in members.items():
                result.append({
                    "user_id": uid,
                    "username": data.get("username", uid),
                    "xp": data.get("xp", 0),
                    "level": lev_mod.get_level_from_xp(data.get("xp", 0)),
                    "messages": data.get("messages", 0)
                })
        result.sort(key=lambda x: x["xp"], reverse=True)
        return jsonify(result[:20])
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
