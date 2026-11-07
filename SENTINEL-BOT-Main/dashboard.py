n\x1a\n'),
        'jpg': data.startswith(b'\xff\xd8\xff'),
        'jpeg': data.startswith(b'\xff\xd8\xff'),
        'gif': data.startswith((b'GIF87a', b'GIF89a')),
        'webp': len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP',
    }
    if not signatures.get(ext):
        return jsonify({"ok": False, "error": "The file content does not match its image type"}), 400
    try:
        import server_official as _so_upload
        saved = _so_upload.save_announcement_image(data, ext)
        if not saved.get('ok'):
            return jsonify({"ok": False, "error": saved.get('error', 'Upload failed')}), 500
        absolute_url = request.host_url.rstrip('/') + saved['url']
        return jsonify({"ok": True, "url": absolute_url})
    except Exception as ex:
        app.logger.exception(f'[dashboard_image_upload] {ex}')
        return jsonify({"ok": False, "error": "Image upload failed"}), 500


# ---- SQL EDITOR ----
@app.route('/server-official/api/sql/schema', methods=['GET'])
def so_api_sql_schema():
    """Return all so_* tables with column metadata for the schema browser."""
    so, err = _so_require_login()
    if err:
        return err
    try:
        import db_so as _db_so
        tables = _db_so.list_tables()
        return jsonify({'ok': True, 'tables': tables})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'tables': []}), 500


@app.route('/server-official/api/sql', methods=['POST'])
def so_api_sql_run():
    """Execute a SQL query against the Postgres database (admin only)."""
    so, err = _so_require_login()
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    query = (payload.get('query') or '').strip()
    if not query:
        return jsonify({'ok': False, 'error': 'Query is required'}), 400

    try:
        import db_so as _db_so
        if not _db_so.is_available():
            return jsonify({'ok': False, 'error': 'Database not configured. Set DATABASE_URL to enable SQL editor.'}), 503
        result = _db_so.run_sql(query, max_rows=500)
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ---- LEGAL PAGES ----
@app.route('/terms')
def legal_terms():
    return render_template('terms.html')


@app.route('/privacy')
def legal_privacy():
    return render_template('privacy.html')


# ---- SEO: robots.txt & sitemap.xml ----
@app.route('/robots.txt')
def robots_txt():
    host = request.host_url.rstrip('/')
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /server-official/api/\n"
        "Disallow: /dashboard\n"
        "Disallow: /server-official/dashboard\n"
        "Disallow: /dev/\n"
        f"Sitemap: {host}/sitemap.xml\n"
    )
    return body, 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route('/sitemap.xml')
def sitemap_xml():
    host = request.host_url.rstrip('/')
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    pages = [
        ('/',        '1.0', 'weekly'),
        ('/bots',    '0.9', 'weekly'),
        ('/sp4rk',   '0.8', 'monthly'),
        ('/terms',   '0.5', 'yearly'),
        ('/privacy', '0.5', 'yearly'),
    ]
    items = ''.join(
        f"  <url><loc>{host}{path}</loc><lastmod>{today}</lastmod>"
        f"<changefreq>{freq}</changefreq><priority>{pri}</priority></url>\n"
        for path, pri, freq in pages
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'{items}'
        '</urlset>'
    )
    return xml, 200, {'Content-Type': 'application/xml; charset=utf-8'}




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


# ---- BOT DIAGNOSTICS ----
@app.route('/bot-debug')
def bot_diagnostics():
    """Public diagnostic endpoint — shows exactly why the bot is or isn't running.
    Visit <your-domain>/bot-debug to debug bot startup issues.
    """
    import sys as _sys
    import time as _t

    token_raw = os.environ.get("DISCORD_TOKEN", "")
    token_present = bool(token_raw.strip())
    start_in_web = os.environ.get("START_BOT_IN_WEB", "(not set)")
    disabled = start_in_web.lower() in {"0", "false", "no", "off"}

    bot_alive = False
    bot_thread_state = "not started"
    try:
        bot_mod = _sys.modules.get('main')
        if bot_mod:
            t = getattr(bot_mod, '_bot_thread', None)
            if t is None:
                bot_thread_state = "thread object is None"
            elif t.is_alive():
                bot_alive = True
                bot_thread_state = "alive ✅"
            else:
                bot_thread_state = "dead / crashed ❌"
        else:
            bot_thread_state = "main module not imported (wsgi didn't start bot)"
    except Exception as _e:
        bot_thread_state = f"error checking thread: {_e}"

    lines = [
        "=== NVD Official Diagnostics ===",
        f"timestamp          : {int(_t.time())}",
        "",
        "--- Environment ---",
        f"DISCORD_TOKEN set  : {'YES' if token_present else 'NO ← bot will not start'}",
        f"START_BOT_IN_WEB   : {start_in_web} {'← bot is DISABLED in web process' if disabled else ''}",
        f"SUPABASE_URL set   : {'YES' if os.environ.get('SUPABASE_URL') else 'NO (using fallback)'}",
        f"DATABASE_URL set   : {'YES' if os.environ.get('DATABASE_URL') else 'NO'}",
        "",
        "--- Bot Thread ---",
        f"bot_alive          : {'YES ✅' if bot_alive else 'NO ❌'}",
        f"thread state       : {bot_thread_state}",
        "",
        "--- Diagnosis ---",
    ]

    if not token_present:
        lines.append("❌ DISCORD_TOKEN is missing or empty. Set it in your environment variables.")
    elif disabled:
        lines.append("❌ START_BOT_IN_WEB is set to a falsy value. Change it to 'true'.")
    elif bot_alive:
        lines.append("✅ Bot is running normally.")
    else:
        lines.append("⚠️  Token is set and START_BOT_IN_WEB is not disabled, but the bot thread is not alive.")
        lines.append("    Check your service logs for startup errors (invalid token, import error, etc).")

    return "\n".join(lines), 200, {'Content-Type': 'text/plain; charset=utf-8'}

# ═══════════════════════════════════════════════════════════════
# OWNER / DEV PANEL
# Access: POST /owner/unlock {"key": DEV_PIN}  →  GET /owner
# Console helper (injected in landing): sp4rkDev('pin')
# ═══════════════════════════════════════════════════════════════

def _owner_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('owner_unlocked'):
            return _owner_pin_page()
        return f(*args, **kwargs)
    return wrapper

def _owner_pin_page(error=None):
    err_html = f'<div style="color:#ff4757;font-size:.82rem;margin-top:10px;"><i class="fas fa-circle-xmark"></i> {error}</div>' if error else ''
    return '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Owner Panel — Authenticate</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{background:#050d1a;color:#d0e8ff;font-family:'Inter',system-ui,sans-serif;
  min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;}
.card{background:#0d1c30;border:1px solid rgba(0,180,255,.2);border-radius:16px;
  padding:36px 32px;width:100%;max-width:360px;text-align:center;}
.logo{width:52px;height:52px;border-radius:12px;
  background:linear-gradient(135deg,#003a5c,#005f8a);border:1px solid rgba(0,180,255,.3);
  display:flex;align-items:center;justify-content:center;margin:0 auto 18px;
  font-size:.85rem;font-weight:900;color:#00c4ff;font-family:monospace;}
h1{font-size:1.1rem;font-weight:700;margin-bottom:6px;}
p{font-size:.8rem;color:#4a7a9b;margin-bottom:24px;}
input{width:100%;background:#07111f;border:1px solid rgba(0,180,255,.2);border-radius:10px;
  padding:13px 16px;font-size:1.1rem;color:#d0e8ff;text-align:center;letter-spacing:4px;
  outline:none;font-family:monospace;margin-bottom:4px;}
input:focus{border-color:#00c4ff;}
button{width:100%;margin-top:14px;padding:13px;border-radius:10px;border:none;
  background:linear-gradient(135deg,#005f8a,#0099cc);color:#fff;
  font-size:.9rem;font-weight:700;cursor:pointer;font-family:inherit;}
button:hover{filter:brightness(1.12);}
.err{color:#ff4757;font-size:.82rem;margin-top:10px;}
.back{display:block;margin-top:18px;font-size:.78rem;color:#4a7a9b;text-decoration:none;}
.back:hover{color:#00c4ff;}
</style>
</head>
<body>
<div class="card">
  <div class="logo">OWN</div>
  <h1>Owner Panel</h1>
  <p>Enter your PIN to continue</p>
  <form method="POST" action="/owner/pin-login">
    <input type="password" name="pin" placeholder="••••••••" autofocus autocomplete="off" inputmode="numeric">
    ''' + err_html + '''
    <button type="submit"><i class="fas fa-unlock"></i> Unlock</button>
  </form>
  <a href="/" class="back"><i class="fas fa-arrow-left"></i> Back to home</a>
</div>
</body>
</html>''', 200

@app.route('/owner/pin-login', methods=['POST'])
def owner_pin_login():
    pin = str(request.form.get('pin', '')).strip()
    if pin == DEV_PIN:
        session['owner_unlocked'] = True
        session.permanent = True
        _append_security_event('OWNER_LOGIN', 'Successful owner panel login')
        return redirect('/owner')
    _append_security_event('FAILED_LOGIN', 'Wrong PIN entered on owner panel login')
    return _owner_pin_page(error='Incorrect PIN — try again.')

@app.route('/owner/unlock', methods=['POST'])
def owner_unlock():
    data = request.get_json(silent=True) or {}
    key = str(data.get('key', '')).strip()
    if key and key == DEV_PIN:
        session['owner_unlocked'] = True
        session.permanent = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid PIN"}), 403

@app.route('/owner/lock')
def owner_lock():
    session.pop('owner_unlocked', None)
    return redirect('/')

@app.route('/owner')
@_owner_required
def owner_panel():
    return render_template('owner.html')

# ── OVERVIEW ──────────────────────────────────────────────────
@app.route('/api/owner/overview')
@_owner_required
def api_owner_overview():
    import threading as _th
    import sys as _sys

    # Bot thread alive check
    bot_alive = False
    try:
        bot_mod = _sys.modules.get('main')
        if bot_mod:
            t = getattr(bot_mod, '_bot_thread', None)
            bot_alive = bool(t and t.is_alive())
    except Exception:
        pass

    # Guild count (live)
    guild_count = 0
    try:
        bot_mod = _sys.modules.get('main')
        b = getattr(bot_mod, 'bot', None) if bot_mod else None
        if b and b.is_ready():
            guild_count = len(b.guilds)
        else:
            bot_guild_path = os.path.join(os.path.dirname(__file__), 'bot_guilds.json')
            if os.path.exists(bot_guild_path):
                with open(bot_guild_path) as _f:
                    guild_count = len(json.load(_f))
    except Exception:
        pass

    # User count
    try:
        users = load_users()
        user_count = len(users) if isinstance(users, list) else 0
    except Exception:
        user_count = 0

    # Uptime
    try:
        bot_mod = _sys.modules.get('main')
        start_ts = getattr(bot_mod, '_start_time', None) if bot_mod else None
        if start_ts:
            delta = __import__('datetime').datetime.utcnow() - start_ts
            h, rem = divmod(int(delta.total_seconds()), 3600)
            m, s   = divmod(rem, 60)
            uptime_str = f"{h}h {m}m" if h else f"{m}m {s}s"
        else:
            uptime_str = '—'
    except Exception:
        uptime_str = '—'

    # Thread count
    thread_count = len(_th.enumerate())

    # Current presence from config
    current_presence = {
        "status":        config.get("bot_status", "online"),
        "activity_type": config.get("bot_activity_type", "none"),
        "activity_text": config.get("bot_activity_text", ""),
    }

    return jsonify({
        "bot_alive":        bot_alive,
        "guild_count":      guild_count,
        "user_count":       user_count,
        "uptime_str":       uptime_str,
        "thread_count":     thread_count,
        "token_set":        bool(os.environ.get("DISCORD_TOKEN", "").strip()),
        "supabase":         supabase is not None,
        "gemini":           bool(os.environ.get("GEMINI_KEY","").strip() or os.environ.get("GOOGLE_API_KEY","").strip()),
        "maintenance":      is_maintenance_mode(),
        "current_presence": current_presence,
        "recent_logs":      action_logs[:10],
    })

# ── BOT PRESENCE ──────────────────────────────────────────────
@app.route('/api/owner/bot_presence', methods=['GET'])
@_owner_required
def api_owner_presence_get():
    return jsonify({
        "status":        config.get("bot_status", "online"),
        "activity_type": config.get("bot_activity_type", "none"),
        "activity_text": config.get("bot_activity_text", ""),
    })

@app.route('/api/owner/bot_presence', methods=['POST'])
@_owner_required
def api_owner_presence_set():
    global config
    data = request.get_json(silent=True) or {}
    status = str(data.get('status', 'online')).strip()
    act_type = str(data.get('activity_type', 'none')).strip()
    act_text = str(data.get('activity_text', '')).strip()
    valid_statuses = {'online','idle','dnd','invisible'}
    valid_types    = {'none','playing','watching','listening','competing','custom'}
    if status not in valid_statuses:
        return jsonify({"error": "Invalid status"}), 400
    if act_type not in valid_types:
        return jsonify({"error": "Invalid activity type"}), 400
    config['bot_status']       = status
    config['bot_activity_type'] = act_type
    config['bot_activity_text'] = act_text
    save_config()
    _trigger_bot_config_refresh()
    add_dashboard_log("🤖 PRESENCE", f"Status → {status}, activity → {act_type} {act_text}".strip())
    return jsonify({"ok": True})

# ── ALL SERVERS ────────────────────────────────────────────────
@app.route('/api/owner/servers')
@_owner_required
def api_owner_servers():
    import sys as _sys
    guilds = []
    try:
        bot_mod = _sys.modules.get('main')
        b = getattr(bot_mod, 'bot', None) if bot_mod else None
        if b and b.is_ready():
            for g in b.guilds:
                icon_hash = getattr(g, 'icon', None)
                icon_url  = str(icon_hash.url) if icon_hash else None
                guilds.append({
                    "id":           str(g.id),
                    "name":         g.name,
                    "member_count": g.member_count or 0,
                    "icon_url":     icon_url,
                })
            guilds.sort(key=lambda x: x['member_count'], reverse=True)
            return jsonify({"guilds": guilds, "source": "live"})
    except Exception:
        pass
    # Fallback: bot_guilds.json
    try:
        bot_guild_path = os.path.join(os.path.dirname(__file__), 'bot_guilds.json')
        if os.path.exists(bot_guild_path):
            with open(bot_guild_path) as _f:
                guilds = json.load(_f)
            return jsonify({"guilds": guilds, "source": "cache"})
    except Exception:
        pass
    return jsonify({"guilds": [], "source": "unavailable"})

# ── GLOBAL CONFIG ─────────────────────────────────────────────
@app.route('/api/owner/global_config', methods=['GET'])
@_owner_required
def api_owner_global_config_get():
    safe_keys = ['prefix','theme_color','warning_limit','automod_on','anti_raid','daily_report',
                 'bot_status','bot_activity_type','bot_activity_text','server_description','server_rules','bot_avatar']
    return jsonify({k: config.get(k) for k in safe_keys})

@app.route('/api/owner/global_config', methods=['POST'])
@_owner_required
def api_owner_global_config_set():
    global config
    data = request.get_json(silent=True) or {}
    text_keys   = ['prefix','theme_color','server_description','server_rules']
    toggle_keys = ['automod_on','anti_raid','daily_report']
    for k in text_keys:
        if k in data:
            config[k] = str(data[k]).strip()
    for k in toggle_keys:
        if k in data:
            config[k] = "ON" if data[k] else "OFF"
    if 'warning_limit' in data:
        try:
            config['warning_limit'] = int(data['warning_limit'])
        except (ValueError, TypeError):
            pass
    save_config()
    _trigger_bot_config_refresh()
    add_dashboard_log("⚙️ OWNER-CONFIG", "Global config updated via owner panel")
    return jsonify({"ok": True})

# ── USERS ──────────────────────────────────────────────────────
@app.route('/api/owner/users')
@_owner_required
def api_owner_users():
    try:
        users = load_users()
        if not isinstance(users, list):
            users = list(users.values()) if isinstance(users, dict) else []
    except Exception:
        users = []
    return jsonify({"users": users, "count": len(users)})

# ── DEV STATE ─────────────────────────────────────────────────
@app.route('/api/owner/dev_state')
@_owner_required
def api_owner_dev_state():
    return jsonify(_load_dev_state())

@app.route('/api/owner/maintenance', methods=['POST'])
@_owner_required
def api_owner_maintenance():
    data  = request.get_json(silent=True) or {}
    state = _load_dev_state()
    state['maintenance']     = bool(data.get('maintenance', False))
    state['maintenance_eta'] = str(data.get('maintenance_eta', '')).strip()
    _save_dev_state(state)
    add_dashboard_log("🔧 MAINTENANCE", f"Mode → {'ON' if state['maintenance'] else 'OFF'}")
    return jsonify({"ok": True})

# ── BOT RESTART (owner panel proxy) ───────────────────────────
@app.route('/api/owner/restart', methods=['POST'])
@_owner_required
def api_owner_restart():
    if _bot_restart_callback:
        ok = _bot_restart_callback()
        add_dashboard_log("🔄 RESTART", "Bot restart triggered via owner panel")
        return jsonify({"ok": ok, "message": "✅ Restart triggered" if ok else "❌ Restart failed"})
    return jsonify({"ok": False, "error": "Restart not available (bot not in-process)"}), 400

# ── CONFIG REFRESH ────────────────────────────────────────────
@app.route('/api/owner/refresh_config', methods=['POST'])
@_owner_required
def api_owner_refresh_config():
    _trigger_bot_config_refresh()
    return jsonify({"ok": True})

# ── SYSTEM STATS (CPU / RAM) ───────────────────────────────────
@app.route('/api/owner/system_stats')
@_owner_required
def api_owner_system_stats():
    import resource as _res, time as _time
    try:
        mem_kb = _res.getrusage(_res.RUSAGE_SELF).ru_maxrss
        import platform as _plat
        mem_mb = round(mem_kb / (1024 * 1024) if _plat.system() == 'Darwin' else mem_kb / 1024, 1)
    except Exception:
        mem_mb = None

    def _read_cpu():
        try:
            with open('/proc/stat') as _f:
                parts = _f.readline().split()
            idle = int(parts[4])
            total = sum(int(x) for x in parts[1:])
            return idle, total
        except Exception:
            return None, None

    idle1, total1 = _read_cpu()
    _time.sleep(0.15)
    idle2, total2 = _read_cpu()
    if idle1 is not None and total2 is not None and (total2 - total1) > 0:
        cpu_pct = round(100 * (1 - (idle2 - idle1) / (total2 - total1)), 1)
    else:
        cpu_pct = None
    return jsonify({"cpu_pct": cpu_pct, "mem_mb": mem_mb})

# ── SECURITY LOG ───────────────────────────────────────────────
import datetime as _dt

_SECURITY_LOG_FILE = os.path.join(os.path.dirname(__file__), 'security_log.json')

def _append_security_event(event_type, detail, ip=None):
    events = []
    try:
        if os.path.exists(_SECURITY_LOG_FILE):
            with open(_SECURITY_LOG_FILE) as _f:
                events = json.load(_f)
    except Exception:
        pass
    events.insert(0, {
        "type": event_type,
        "detail": detail,
        "ip": ip or (request.remote_addr if request else ''),
        "time": _dt.datetime.utcnow().isoformat()
    })
    events = events[:500]
    try:
        with open(_SECURITY_LOG_FILE, 'w') as _f:
            json.dump(events, _f)
    except Exception:
        pass

@app.route('/api/owner/security_log')
@_owner_required
def api_owner_security_log():
    try:
        if os.path.exists(_SECURITY_LOG_FILE):
            with open(_SECURITY_LOG_FILE) as _f:
                return jsonify({"events": json.load(_f)[:100]})
    except Exception:
        pass
    return jsonify({"events": []})

# ── BLACKLIST ──────────────────────────────────────────────────
_BLACKLIST_FILE = os.path.join(os.path.dirname(__file__), 'blacklist.json')

def _load_blacklist():
    """Load blacklist — Postgres primary, JSON file fallback."""
    try:
        import db_spark as _ds
        if _ds.is_available():
            bl = _ds.get_blacklist()
            if bl and (bl.get('users') or bl.get('servers')):
                return bl
            # Postgres available but empty — check if JSON has data to migrate
            try:
                if os.path.exists(_BLACKLIST_FILE):
                    with open(_BLACKLIST_FILE) as _f:
                        json_bl = json.load(_f)
                    if isinstance(json_bl, dict) and (json_bl.get('users') or json_bl.get('servers')):
                        _ds.save_blacklist(json_bl)
                        print("✅ Blacklist migrated JSON → Postgres")
                        return json_bl
            except Exception:
                pass
            return {"users": [], "servers": []}
    except Exception:
        pass
    # Fallback: JSON file
    try:
        if os.path.exists(_BLACKLIST_FILE):
            with open(_BLACKLIST_FILE) as _f:
                return json.load(_f)
    except Exception:
        pass
    return {"users": [], "servers": []}

def _save_blacklist(bl):
    """Persist blacklist — Postgres primary, JSON file fallback."""
    try:
        import db_spark as _ds
        if _ds.is_available():
            _ds.save_blacklist(bl)
            return
    except Exception:
        pass
    try:
        with open(_BLACKLIST_FILE, 'w') as _f:
            json.dump(bl, _f, indent=2)
    except Exception as _e:
        print(f"[blacklist] save to JSON failed: {_e}")

@app.route('/api/owner/blacklist')
@_owner_required
def api_owner_blacklist_get():
    return jsonify(_load_blacklist())

@app.route('/api/owner/blacklist/user', methods=['POST'])
@_owner_required
def api_owner_blacklist_user():
    data = request.get_json(silent=True) or {}
    uid = str(data.get('id', '')).strip()
    reason = str(data.get('reason', 'No reason')).strip()
    if not uid:
        return jsonify({"error": "User ID required"}), 400
    bl = _load_blacklist()
    if any(u['id'] == uid for u in bl['users']):
        return jsonify({"error": "Already blacklisted"}), 409
    bl['users'].append({"id": uid, "reason": reason, "added": _dt.datetime.utcnow().isoformat()})
    _save_blacklist(bl)
    add_dashboard_log("🚫 BLACKLIST", f"User {uid} blacklisted: {reason}")
    return jsonify({"ok": True})

@app.route('/api/owner/blacklist/user/<uid>', methods=['DELETE'])
@_owner_required
def api_owner_unblacklist_user(uid):
    bl = _load_blacklist()
    before = len(bl['users'])
    bl['users'] = [u for u in bl['users'] if u['id'] != uid]
    _save_blacklist(bl)
    add_dashboard_log("✅ UNBLACKLIST", f"User {uid} removed from blacklist")
    return jsonify({"ok": True, "removed": before - len(bl['users'])})

@app.route('/api/owner/blacklist/server', methods=['POST'])
@_owner_required
def api_owner_blacklist_server():
    data = request.get_json(silent=True) or {}
    sid = str(data.get('id', '')).strip()
    reason = str(data.get('reason', 'No reason')).strip()
    if not sid:
        return jsonify({"error": "Server ID required"}), 400
    bl = _load_blacklist()
    if any(s['id'] == sid for s in bl['servers']):
        return jsonify({"error": "Already blacklisted"}), 409
    bl['servers'].append({"id": sid, "reason": reason, "added": _dt.datetime.utcnow().isoformat()})
    _save_blacklist(bl)
    add_dashboard_log("🚫 BLACKLIST", f"Server {sid} blacklisted: {reason}")
    return jsonify({"ok": True})

@app.route('/api/owner/blacklist/server/<sid>', methods=['DELETE'])
@_owner_required
def api_owner_unblacklist_server(sid):
    bl = _load_blacklist()
    before = len(bl['servers'])
    bl['servers'] = [s for s in bl['servers'] if s['id'] != sid]
    _save_blacklist(bl)
    add_dashboard_log("✅ UNBLACKLIST", f"Server {sid} removed from blacklist")
    return jsonify({"ok": True, "removed": before - len(bl['servers'])})

# ── LEAVE SERVER ───────────────────────────────────────────────
@app.route('/api/owner/leave_server', methods=['POST'])
@_owner_required
def api_owner_leave_server():
    import sys as _sys
    data = request.get_json(silent=True) or {}
    guild_id = str(data.get('guild_id', '')).strip()
    if not guild_id:
        return jsonify({"error": "Guild ID required"}), 400
    try:
        bot_mod = _sys.modules.get('main')
        b = getattr(bot_mod, 'bot', None) if bot_mod else None
        if b and b.is_ready():
            import asyncio as _asyncio
            guild = b.get_guild(int(guild_id))
            if guild:
                _asyncio.run_coroutine_threadsafe(guild.leave(), b.loop).result(timeout=8)
                add_dashboard_log("👋 LEAVE", f"Left server {guild.name} ({guild_id})")
                return jsonify({"ok": True, "name": guild.name})
            return jsonify({"error": "Guild not found in bot's server list"}), 404
    except Exception as _e:
        return jsonify({"error": str(_e)}), 500
    return jsonify({"error": "Bot not ready"}), 503

# ── BROADCAST ──────────────────────────────────────────────────
@app.route('/api/owner/broadcast', methods=['POST'])
@_owner_required
def api_owner_broadcast():
    import sys as _sys
    data = request.get_json(silent=True) or {}
    message = str(data.get('message', '')).strip()
    target = str(data.get('target', 'all')).strip()
    channel_hint = str(data.get('channel_hint', 'general')).strip() or 'general'
    if not message:
        return jsonify({"error": "Message required"}), 400

    sent_count = 0
    try:
        bot_mod = _sys.modules.get('main')
        b = getattr(bot_mod, 'bot', None) if bot_mod else None
        if b and b.is_ready():
            import asyncio as _asyncio, discord as _discord

            async def _do_broadcast():
                nonlocal sent_count
                guilds_to_send = b.guilds if target == 'all' else [g for g in b.guilds if str(g.id) == target]
                for guild in guilds_to_send:
                    ch = None
                    hints = [channel_hint, 'general', 'announcements', 'chat', 'bot', 'main']
                    for c in guild.text_channels:
                        if any(h in c.name.lower() for h in hints) and c.permissions_for(guild.me).send_messages:
                            ch = c
                            break
                    if not ch:
                        for c in guild.text_channels:
                            if c.permissions_for(guild.me).send_messages:
                                ch = c
                                break
                    if ch:
                        try:
                            embed = _discord.Embed(description=message, color=0x00c4ff)
                            embed.set_author(name="📢 Announcement from NVD Official")
                            await ch.send(embed=embed)
                            sent_count += 1
                        except Exception:
                            pass

            _asyncio.run_coroutine_threadsafe(_do_broadcast(), b.loop).result(timeout=30)
        else:
            return jsonify({"error": "Bot is not ready — cannot broadcast"}), 503
    except Exception as _e:
        return jsonify({"error": str(_e)}), 500

    add_dashboard_log("📢 BROADCAST", f"Sent to {sent_count} server(s): {message[:60]}")
    return jsonify({"ok": True, "sent_count": sent_count})

# ── FEATURE TOGGLES ────────────────────────────────────────────
_FEATURE_KEYS = ['automod_on', 'anti_raid', 'daily_report', 'leveling_enabled', 'tickets_enabled', 'giveaways_enabled']

@app.route('/api/owner/features', methods=['GET'])
@_owner_required
def api_owner_features_get():
    return jsonify({k: config.get(k, 'OFF') for k in _FEATURE_KEYS})

@app.route('/api/owner/features', methods=['POST'])
@_owner_required
def api_owner_features_set():
    global config
    data = request.get_json(silent=True) or {}
    for k in _FEATURE_KEYS:
        if k in data:
            config[k] = "ON" if data[k] else "OFF"
    save_config()
    _trigger_bot_config_refresh()
    add_dashboard_log("🔧 FEATURES", "Feature toggles updated via owner panel")
    return jsonify({"ok": True})

# ── DISABLED COMMANDS ──────────────────────────────────────────
@app.route('/api/owner/disabled_commands', methods=['GET'])
@_owner_required
def api_owner_disabled_commands_get():
    return jsonify({"disabled": config.get('disabled_commands', [])})

@app.route('/api/owner/disabled_commands', methods=['POST'])
@_owner_required
def api_owner_disabled_commands_set():
    global config
    data = request.get_json(silent=True) or {}
    cmd = str(data.get('command', '')).strip().lower()
    action = str(data.get('action', 'disable')).strip()
    if not cmd:
        return jsonify({"error": "Command name required"}), 400
    disabled = list(config.get('disabled_commands', []))
    if action == 'disable':
        if cmd not in disabled:
            disabled.append(cmd)
    else:
        disabled = [c for c in disabled if c != cmd]
    config['disabled_commands'] = disabled
    save_config()
    _trigger_bot_config_refresh()
    add_dashboard_log("🔒 CMD", f"Command '{cmd}' {action}d globally")
    return jsonify({"ok": True, "disabled": disabled})

# ── DB BACKUP ──────────────────────────────────────────────────
@app.route('/api/owner/backup')
@_owner_required
def api_owner_backup():
    backup = {
        "timestamp": _dt.datetime.utcnow().isoformat(),
        "config": {k: v for k, v in config.items() if k != 'bot_avatar'},
        "action_logs": action_logs[:500],
    }
    try:
        backup["users"] = load_users()
    except Exception:
        backup["users"] = []
    try:
        backup["blacklist"] = _load_blacklist()
    except Exception:
        backup["blacklist"] = {}
    filename = f'sp4rk-backup-{_dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")}.json'
    resp = make_response(json.dumps(backup, indent=2, default=str))
    resp.headers['Content-Type'] = 'application/json'
    resp.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return resp

# ── CLEAR CACHE ────────────────────────────────────────────────
@app.route('/api/owner/clear_cache', methods=['POST'])
@_owner_required
def api_owner_clear_cache():
    try:
        _load_guild_configs.cache_clear() if hasattr(_load_guild_configs, 'cache_clear') else None
    except Exception:
        pass
    add_dashboard_log("🗑️ CACHE", "Guild config cache cleared via owner panel")
    return jsonify({"ok": True})

# ── ALL LOGS ───────────────────────────────────────────────────
@app.route('/api/owner/all_logs')
@_owner_required
def api_owner_all_logs():
    return jsonify({"logs": action_logs[:300]})

# ── REPORTS ────────────────────────────────────────────────────
@app.route('/api/owner/reports')
@_owner_required
def api_owner_reports():
    try:
        feedback_path = os.path.join(os.path.dirname(__file__), '..', 'feedback.json')
        if not os.path.exists(feedback_path):
            feedback_path = os.path.join(os.path.dirname(__file__), 'feedback.json')
        if os.path.exists(feedback_path):
            with open(feedback_path) as _f:
                data = json.load(_f)
            feedback = data.get('feedback', data) if isinstance(data, dict) else data
            if isinstance(feedback, list):
                return jsonify({"reports": feedback, "count": len(feedback)})
    except Exception:
        pass
    return jsonify({"reports": [], "count": 0})

# ---- RUN ----
def run_dashboard(port=None):
    if port is None:
        port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
