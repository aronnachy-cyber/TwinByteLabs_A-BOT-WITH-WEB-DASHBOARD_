"""
Server Official — Byte Labs server-management bot.

Pure server management (no moderation). Features:
  1. Server Setup Automation (roles, channels, templates)
  2. Member Management (welcome roles, /userinfo, join/leave logs)
  3. Server Analytics (messages/day, top chatters, voice activity)
  4. Announcement System (announce, schedule, broadcast, templates)
  5. Utility Tools (polls, reminders, sticky, notes, embed builder)
  6. Engagement (daily check-in, server goals)
  7. Logging (channel/role create/delete, bot actions)
  8. Configuration Dashboard (web UI)
  9. Automation (welcome msgs, auto pin, auto cleanup, scheduled backups)
 10. Integration (custom commands, webhooks, external API hooks)

State: server_official_state.json (single file shared with the dashboard).
"""
from __future__ import annotations

import os
import json
import asyncio
import logging
import threading
import random
import re
from datetime import datetime, timezone, timedelta, date
from typing import Optional, List, Dict, Any, Tuple
import uuid

import time
import discord
from discord.ext import commands, tasks
import requests

logger = logging.getLogger("server_official")
logging.basicConfig(level=logging.INFO)

# ============================================================
# STORAGE — local JSON (working copy) + Supabase (durable)
# ============================================================
_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_official_state.json")
_state_lock = threading.RLock()  # reentrant: callers can wrap full read-modify-write

# --- Supabase client (single row JSONB mirror of the local state) ---
SUPABASE_TABLE = "server_official_state"
SUPABASE_ROW_ID = 1
_SUPABASE_FLUSH_INTERVAL = 15.0  # seconds between durable flushes
_supabase_client = None
_dirty_state: Optional[Dict[str, Any]] = None     # latest state pending Supabase write
_dirty_lock = threading.Lock()
_flusher_started = False

try:
    from supabase import create_client as _sb_create_client
    _SB_URL = os.environ.get("SUPABASE_URL", "")
    _SB_KEY = os.environ.get("SUPABASE_KEY", "")
    if _SB_URL and _SB_KEY:
        _supabase_client = _sb_create_client(_SB_URL, _SB_KEY)
        logger.info("[server_official] Supabase persistence enabled")
    else:
        logger.info("[server_official] Supabase env not set — using local JSON only")
except Exception as _sb_err:
    logger.warning(f"[server_official] Supabase import failed: {_sb_err}; using local JSON only")
    _supabase_client = None


def _supabase_load_full_state() -> Optional[Dict[str, Any]]:
    """Try to fetch the persisted state row from Supabase. Returns None on miss/error."""
    if not _supabase_client:
        return None
    try:
        res = _supabase_client.table(SUPABASE_TABLE).select("data").eq("id", SUPABASE_ROW_ID).execute()
        rows = res.data or []
        if not rows:
            return None
        data = rows[0].get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return None
        if isinstance(data, dict):
            return data
        return None
    except Exception as e:
        logger.warning(f"[server_official] Supabase load error: {e}")
        return None


def _supabase_save_full_state(state: Dict[str, Any]) -> bool:
    """Upsert the entire state JSON into the single Supabase row."""
    if not _supabase_client:
        return False
    try:
        payload = {
            "id": SUPABASE_ROW_ID,
            "data": state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _supabase_client.table(SUPABASE_TABLE).upsert(payload, on_conflict="id").execute()
        return True
    except Exception as e:
        logger.warning(f"[server_official] Supabase save error: {e}")
        return False


def _start_supabase_flusher():
    """Start a background daemon that periodically pushes the dirty state to Supabase.

    We keep the local JSON as the hot working copy (fast, in-process) and only
    flush to Supabase on a timer to avoid hammering the network on every bot
    event. State is therefore eventually-consistent within a few seconds."""
    global _flusher_started
    if _flusher_started or not _supabase_client:
        return
    _flusher_started = True

    def _loop():
        while True:
            time.sleep(_SUPABASE_FLUSH_INTERVAL)
            try:
                with _dirty_lock:
                    snapshot = _dirty_state
                    cleared = snapshot is not None
                    if cleared:
                        # Only consume the snapshot once we're committed to saving it
                        pending = snapshot
                if not cleared:
                    continue
                ok = _supabase_save_full_state(pending)
                if ok:
                    with _dirty_lock:
                        # Don't drop newer pending state that arrived during the network call.
                        if _dirty_state is pending:
                            globals()["_dirty_state"] = None
            except Exception as e:
                logger.warning(f"[server_official] flusher tick failed: {e}")

    t = threading.Thread(target=_loop, name="ServerOfficialSupabaseFlusher", daemon=True)
    t.start()

DEFAULT_STATE: Dict[str, Any] = {
    # --- existing ---
    "scheduled_announcements": [],
    "giveaways": [],
    "invite_counts": {},
    "invite_cache": {},
    "settings": {
        "default_announce_color": 0x10b981,
        "embed_footer": "Server Official • Byte Labs",
    },

    # --- new (per-guild config) ---
    "server_configs": {},   # guild_id -> { welcome_role_id, welcome_channel_id, welcome_message,
                            #               leave_message, log_channel_id,
                            #               auto_pin_threshold, auto_cleanup_age_days }

    # --- engagement ---
    "server_goals": [],     # [{id, guild_id, type, target, current, ends_at, created_at, done}]

    # --- utility ---
    "polls": [],            # [{id, guild_id, channel_id, message_id, question, options:[{label, votes}], ends_at, ended}]
    "reminders": [],        # [{id, user_id, guild_id, channel_id, text, due_at, created_at, sent}]
    "sticky_messages": {},  # channel_id -> { guild_id, message, last_message_id, enabled }
    "admin_notes": {},      # guild_id -> [{id, text, author, created_at}]
    "announcement_templates": [],  # [{id, guild_id, name, title, message, color}]

    # --- analytics ---
    "activity_stats": {},   # guild_id -> { messages_per_day:{YYYY-MM-DD:n}, msg_per_user:{uid:n},
                            #               voice_minutes:{uid:n}, joins_per_day:{YYYY-MM-DD:n},
                            #               leaves_per_day:{YYYY-MM-DD:n} }

    # --- logging (non-mod) ---
    "server_logs": [],      # [{id, guild_id, type, description, ts}]
    "bot_action_logs": [],  # [{id, ts, type, summary, actor}]
    "setup_history": [],    # [{id, guild_id, ts, action, details, undo}]

    # --- integrations ---
    "custom_commands": {},  # guild_id -> { trigger -> response }
    "webhooks_in": [],      # [{id, guild_id, name, secret, channel_id, created_at}]
    "external_hooks": [],   # [{id, name, url, events, created_at}]

    # --- backups ---
    "config_backups": [],   # [{id, ts, label, snapshot}]
}


def _merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure every default top-level key (and settings sub-key) is present."""
    for k, v in DEFAULT_STATE.items():
        if k not in data:
            data[k] = json.loads(json.dumps(v))
    for sk, sv in DEFAULT_STATE["settings"].items():
        data["settings"].setdefault(sk, sv)
    return data


def _hydrate_local_from_supabase_once() -> None:
    """On first load, if local JSON is missing/empty but Supabase has a row,
    seed the local file from the durable copy so the bot picks up where it
    left off after a container restart."""
    try:
        if os.path.exists(_STATE_FILE) and os.path.getsize(_STATE_FILE) > 2:
            return
        remote = _supabase_load_full_state()
        if not remote:
            return
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(remote, f, indent=2, default=str)
        logger.info("[server_official] Hydrated local state from Supabase")
    except Exception as e:
        logger.warning(f"[server_official] hydrate-from-supabase failed: {e}")


def load_state() -> Dict[str, Any]:
    with _state_lock:
        # First call also kicks off the durable-flush background thread.
        _start_supabase_flusher()
        _hydrate_local_from_supabase_once()
        if not os.path.exists(_STATE_FILE):
            return _merge_defaults(json.loads(json.dumps(DEFAULT_STATE)))
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"[server_official] load_state error: {e}; resetting")
            return _merge_defaults(json.loads(json.dumps(DEFAULT_STATE)))
        return _merge_defaults(data)


def save_state(state: Dict[str, Any]) -> None:
    with _state_lock:
        tmp = _STATE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
            os.replace(tmp, _STATE_FILE)
        except Exception as e:
            logger.error(f"[server_official] save_state error: {e}")
            return
    # Mark as dirty for the periodic Supabase flusher (cheap, non-blocking).
    if _supabase_client:
        try:
            with _dirty_lock:
                # Snapshot via JSON round-trip so later in-place mutations don't
                # leak into the pending Supabase payload.
                globals()["_dirty_state"] = json.loads(json.dumps(state, default=str))
        except Exception as e:
            logger.warning(f"[server_official] mark-dirty failed: {e}")


def _gen_id() -> str:
    return uuid.uuid4().hex[:10]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _today() -> str:
    return date.today().isoformat()


def parse_duration(s: str) -> Optional[int]:
    """Parse '10m', '2h', '3d', '90s', '1w' or a plain integer (seconds)."""
    if not s:
        return None
    s = s.strip().lower()
    try:
        return int(s)
    except ValueError:
        pass
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    if len(s) >= 2 and s[-1] in units:
        try:
            n = float(s[:-1])
            return int(n * units[s[-1]])
        except ValueError:
            return None
    return None


# ============================================================
# CONFIG / LOGS HELPERS
# ============================================================
DEFAULT_GUILD_CONFIG = {
    "display_name": "",             # custom display name for the bot in this server's dashboard
    "welcome_role_id": "",
    "welcome_channel_id": "",
    "welcome_message": "👋 Welcome {user} to **{server}**! You are member #{count}.",
    "leave_message": "👋 {user} has left **{server}**.",
    "log_channel_id": "",
    "auto_pin_threshold": 0,        # 0 = disabled
    "auto_cleanup_age_days": 0,     # 0 = disabled (cleanup of bot's own old messages)
    "broadcast_default_color": 0x5b8cff,
}


def get_guild_config(guild_id: str) -> Dict[str, Any]:
    state = load_state()
    cfg = state["server_configs"].get(str(guild_id), {})
    out = dict(DEFAULT_GUILD_CONFIG)
    out.update(cfg)
    return out


def update_guild_config(guild_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    state = load_state()
    cur = dict(DEFAULT_GUILD_CONFIG)
    cur.update(state["server_configs"].get(str(guild_id), {}))
    for k, v in updates.items():
        if k in DEFAULT_GUILD_CONFIG:
            cur[k] = v
    state["server_configs"][str(guild_id)] = cur
    save_state(state)
    return cur


def add_server_log(guild_id: Optional[str], log_type: str, description: str) -> Dict[str, Any]:
    state = load_state()
    item = {
        "id": _gen_id(),
        "guild_id": str(guild_id) if guild_id else None,
        "type": log_type,
        "description": description,
        "ts": _now_iso(),
    }
    state["server_logs"].insert(0, item)
    state["server_logs"] = state["server_logs"][:500]
    save_state(state)
    return item


def add_bot_action(action_type: str, summary: str, actor: str = "system") -> Dict[str, Any]:
    state = load_state()
    item = {
        "id": _gen_id(),
        "type": action_type,
        "summary": summary,
        "actor": actor,
        "ts": _now_iso(),
    }
    state["bot_action_logs"].insert(0, item)
    state["bot_action_logs"] = state["bot_action_logs"][:300]
    save_state(state)
    return item


def add_setup_history(guild_id: str, action: str, details: Dict[str, Any]) -> Dict[str, Any]:
    state = load_state()
    item = {
        "id": _gen_id(),
        "guild_id": str(guild_id),
        "action": action,
        "details": details,
        "ts": _now_iso(),
    }
    state["setup_history"].insert(0, item)
    state["setup_history"] = state["setup_history"][:200]
    save_state(state)
    return item


# ============================================================
# DASHBOARD HELPERS — ANNOUNCEMENTS / GIVEAWAYS / INVITES (existing)
# ============================================================
def add_scheduled_announcement(
    guild_id: Optional[str],
    channel_id: str,
    title: str,
    message: str,
    scheduled_for_iso: str,
    color: Optional[int] = None,
    created_by: str = "dashboard",
    image_url: Optional[str] = None,
) -> Dict[str, Any]:
    state = load_state()
    item = {
        "id": _gen_id(),
        "guild_id": str(guild_id) if guild_id else None,
        "channel_id": str(channel_id),
        "title": title or "📣 Announcement",
        "message": message,
        "scheduled_for": scheduled_for_iso,
        "color": int(color) if color is not None else state["settings"]["default_announce_color"],
        "posted": False,
        "created_by": created_by,
        "created_at": _now_iso(),
        "image_url": (image_url or "").strip() or None,
    }
    state["scheduled_announcements"].append(item)
    save_state(state)
    add_bot_action("schedule", f"Scheduled announcement for {scheduled_for_iso}", created_by)
    return item


def list_scheduled_announcements(include_posted: bool = False) -> List[Dict[str, Any]]:
    state = load_state()
    items = state.get("scheduled_announcements", [])
    if not include_posted:
        items = [a for a in items if not a.get("posted")]
    return sorted(items, key=lambda a: a.get("scheduled_for", ""))


def delete_scheduled_announcement(announce_id: str) -> bool:
    state = load_state()
    before = len(state["scheduled_announcements"])
    state["scheduled_announcements"] = [a for a in state["scheduled_announcements"] if a.get("id") != announce_id]
    save_state(state)
    return len(state["scheduled_announcements"]) < before


def list_giveaways(include_ended: bool = True) -> List[Dict[str, Any]]:
    state = load_state()
    items = state.get("giveaways", [])
    if not include_ended:
        items = [g for g in items if not g.get("ended")]
    return sorted(items, key=lambda g: g.get("ends_at", ""), reverse=True)


def get_invite_leaderboard(guild_id: Optional[str] = None) -> List[Dict[str, Any]]:
    state = load_state()
    counts = state.get("invite_counts", {})
    if guild_id:
        gc = counts.get(str(guild_id), {})
        return [{"guild_id": str(guild_id), "user_id": uid, "joins": n}
                for uid, n in sorted(gc.items(), key=lambda kv: kv[1], reverse=True)]
    out = []
    for gid, gmap in counts.items():
        for uid, n in gmap.items():
            out.append({"guild_id": gid, "user_id": uid, "joins": n})
    return sorted(out, key=lambda x: x["joins"], reverse=True)


# ============================================================
# DASHBOARD HELPERS — TEMPLATES, NOTES, GOALS, REMINDERS, STICKIES
# ============================================================
def add_template(guild_id: Optional[str], name: str, title: str, message: str, color: Optional[int] = None) -> Dict[str, Any]:
    state = load_state()
    item = {
        "id": _gen_id(),
        "guild_id": str(guild_id) if guild_id else None,
        "name": name,
        "title": title,
        "message": message,
        "color": int(color) if color is not None else state["settings"]["default_announce_color"],
        "created_at": _now_iso(),
    }
    state["announcement_templates"].append(item)
    save_state(state)
    return item


def list_templates(guild_id: Optional[str] = None) -> List[Dict[str, Any]]:
    state = load_state()
    items = state["announcement_templates"]
    if guild_id:
        items = [t for t in items if t.get("guild_id") in (None, str(guild_id))]
    return items


def delete_template(template_id: str) -> bool:
    state = load_state()
    before = len(state["announcement_templates"])
    state["announcement_templates"] = [t for t in state["announcement_templates"] if t["id"] != template_id]
    save_state(state)
    return len(state["announcement_templates"]) < before


def add_admin_note(guild_id: str, text: str, author: str) -> Dict[str, Any]:
    state = load_state()
    notes = state["admin_notes"].setdefault(str(guild_id), [])
    item = {"id": _gen_id(), "text": text, "author": author, "created_at": _now_iso()}
    notes.insert(0, item)
    state["admin_notes"][str(guild_id)] = notes[:200]
    save_state(state)
    return item


def list_admin_notes(guild_id: str) -> List[Dict[str, Any]]:
    state = load_state()
    return state["admin_notes"].get(str(guild_id), [])


def delete_admin_note(guild_id: str, note_id: str) -> bool:
    state = load_state()
    notes = state["admin_notes"].get(str(guild_id), [])
    new = [n for n in notes if n["id"] != note_id]
    state["admin_notes"][str(guild_id)] = new
    save_state(state)
    return len(new) < len(notes)


def add_goal(guild_id: str, goal_type: str, target: int, ends_at_iso: Optional[str] = None) -> Dict[str, Any]:
    state = load_state()
    item = {
        "id": _gen_id(),
        "guild_id": str(guild_id),
        "type": goal_type,
        "target": int(target),
        "current": 0,
        "ends_at": ends_at_iso,
        "created_at": _now_iso(),
        "done": False,
    }
    state["server_goals"].append(item)
    save_state(state)
    return item


def list_goals(guild_id: Optional[str] = None) -> List[Dict[str, Any]]:
    state = load_state()
    items = state["server_goals"]
    if guild_id:
        items = [g for g in items if g["guild_id"] == str(guild_id)]
    # refresh "current" for each known type
    stats = state.get("activity_stats", {})
    for g in items:
        gs = stats.get(g["guild_id"], {})
        if g["type"] == "messages":
            g["current"] = sum(gs.get("messages_per_day", {}).values())
        elif g["type"] == "members":
            try:
                guild = bot.get_guild(int(g["guild_id"])) if bot else None
                g["current"] = guild.member_count if guild else g.get("current", 0)
            except Exception:
                pass
        if g["target"] and g["current"] >= g["target"]:
            g["done"] = True
    return items


def delete_goal(goal_id: str) -> bool:
    state = load_state()
    before = len(state["server_goals"])
    state["server_goals"] = [g for g in state["server_goals"] if g["id"] != goal_id]
    save_state(state)
    return len(state["server_goals"]) < before


def add_reminder(user_id: str, channel_id: str, text: str, due_at_iso: str, guild_id: Optional[str] = None) -> Dict[str, Any]:
    state = load_state()
    item = {
        "id": _gen_id(),
        "user_id": str(user_id),
        "channel_id": str(channel_id),
        "guild_id": str(guild_id) if guild_id else None,
        "text": text,
        "due_at": due_at_iso,
        "created_at": _now_iso(),
        "sent": False,
    }
    state["reminders"].append(item)
    save_state(state)
    return item


def list_reminders(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    state = load_state()
    items = [r for r in state["reminders"] if not r.get("sent")]
    if user_id:
        items = [r for r in items if r["user_id"] == str(user_id)]
    return sorted(items, key=lambda r: r["due_at"])


def delete_reminder(reminder_id: str) -> bool:
    state = load_state()
    before = len(state["reminders"])
    state["reminders"] = [r for r in state["reminders"] if r["id"] != reminder_id]
    save_state(state)
    return len(state["reminders"]) < before


def set_sticky(channel_id: str, message: str, guild_id: Optional[str] = None) -> Dict[str, Any]:
    state = load_state()
    item = {
        "guild_id": str(guild_id) if guild_id else None,
        "message": message,
        "last_message_id": None,
        "enabled": True,
        "updated_at": _now_iso(),
    }
    state["sticky_messages"][str(channel_id)] = item
    save_state(state)
    return item


def remove_sticky(channel_id: str) -> bool:
    state = load_state()
    if str(channel_id) in state["sticky_messages"]:
        state["sticky_messages"].pop(str(channel_id))
        save_state(state)
        return True
    return False


def list_stickies() -> List[Dict[str, Any]]:
    state = load_state()
    return [{"channel_id": cid, **data} for cid, data in state["sticky_messages"].items()]


# ============================================================
# CUSTOM COMMANDS / WEBHOOKS / HOOKS / BACKUPS
# ============================================================
def add_custom_command(guild_id: str, trigger: str, response: str) -> Dict[str, Any]:
    state = load_state()
    cmds = state["custom_commands"].setdefault(str(guild_id), {})
    cmds[trigger.lower()] = response
    save_state(state)
    return {"trigger": trigger.lower(), "response": response}


def remove_custom_command(guild_id: str, trigger: str) -> bool:
    state = load_state()
    cmds = state["custom_commands"].get(str(guild_id), {})
    if trigger.lower() in cmds:
        cmds.pop(trigger.lower())
        save_state(state)
        return True
    return False


def list_custom_commands(guild_id: str) -> Dict[str, str]:
    state = load_state()
    return state["custom_commands"].get(str(guild_id), {})


def add_external_hook(name: str, url: str, events: List[str]) -> Dict[str, Any]:
    state = load_state()
    item = {
        "id": _gen_id(),
        "name": name,
        "url": url,
        "events": events,
        "created_at": _now_iso(),
    }
    state["external_hooks"].append(item)
    save_state(state)
    return item


def list_external_hooks() -> List[Dict[str, Any]]:
    return load_state()["external_hooks"]


def delete_external_hook(hook_id: str) -> bool:
    state = load_state()
    before = len(state["external_hooks"])
    state["external_hooks"] = [h for h in state["external_hooks"] if h["id"] != hook_id]
    save_state(state)
    return len(state["external_hooks"]) < before


def _fire_one_hook(hook: Dict[str, Any], event: str, payload: Dict[str, Any]) -> None:
    try:
        requests.post(
            hook["url"],
            json={"event": event, "payload": payload},
            timeout=5,
        )
    except Exception as e:
        logger.warning(f"[hooks] {hook.get('name','?')} failed: {e}")


def fire_external_hooks(event: str, payload: Dict[str, Any]) -> None:
    """Fire-and-forget POST to every hook subscribed to `event`.

    Each request runs in its own daemon thread so a slow webhook target
    can never block the event loop (when called from a slash command) or
    a Flask request (when called from the dashboard) for up to 5s each.
    """
    import threading
    hooks = list_external_hooks()
    for h in hooks:
        if event not in (h.get("events") or []):
            continue
        threading.Thread(
            target=_fire_one_hook,
            args=(h, event, payload),
            daemon=True,
            name=f"ext-hook-{h.get('name','?')}",
        ).start()


def add_incoming_webhook(guild_id: str, name: str, channel_id: str) -> Dict[str, Any]:
    import secrets as _secrets
    state = load_state()
    item = {
        "id": _gen_id(),
        "guild_id": str(guild_id),
        "name": name,
        "channel_id": str(channel_id),
        "secret": _secrets.token_urlsafe(24),
        "created_at": _now_iso(),
    }
    state["webhooks_in"].append(item)
    save_state(state)
    return item


def list_incoming_webhooks(guild_id: Optional[str] = None) -> List[Dict[str, Any]]:
    state = load_state()
    items = state["webhooks_in"]
    if guild_id:
        items = [w for w in items if w["guild_id"] == str(guild_id)]
    return items


def delete_incoming_webhook(webhook_id: str) -> bool:
    state = load_state()
    before = len(state["webhooks_in"])
    state["webhooks_in"] = [w for w in state["webhooks_in"] if w["id"] != webhook_id]
    save_state(state)
    return len(state["webhooks_in"]) < before


def find_webhook_by_secret(secret: str) -> Optional[Dict[str, Any]]:
    for w in load_state()["webhooks_in"]:
        if w.get("secret") == secret:
            return w
    return None


def create_backup(label: str = "manual") -> Dict[str, Any]:
    state = load_state()
    snapshot = {k: state[k] for k in state if k != "config_backups"}
    item = {"id": _gen_id(), "ts": _now_iso(), "label": label, "snapshot": snapshot}
    state["config_backups"].insert(0, item)
    state["config_backups"] = state["config_backups"][:30]
    save_state(state)
    add_bot_action("backup", f"Created backup ({label})")
    return {"id": item["id"], "ts": item["ts"], "label": item["label"]}


def list_backups() -> List[Dict[str, Any]]:
    state = load_state()
    return [{"id": b["id"], "ts": b["ts"], "label": b["label"]} for b in state["config_backups"]]


def restore_backup(backup_id: str) -> bool:
    state = load_state()
    bk = next((b for b in state["config_backups"] if b["id"] == backup_id), None)
    if not bk:
        return False
    snapshot = bk["snapshot"]
    new_state = json.loads(json.dumps(DEFAULT_STATE))
    for k, v in snapshot.items():
        new_state[k] = v
    new_state["config_backups"] = state["config_backups"]
    save_state(new_state)
    add_bot_action("restore", f"Restored backup {backup_id}")
    return True


def delete_backup(backup_id: str) -> bool:
    state = load_state()
    before = len(state["config_backups"])
    state["config_backups"] = [b for b in state["config_backups"] if b["id"] != backup_id]
    save_state(state)
    return len(state["config_backups"]) < before


# ============================================================
# ANALYTICS HELPERS
# ============================================================
def _bump_messages_for(guild_id: str, user_id: str) -> None:
    # Hold the state lock across the whole read-modify-write so two messages
    # arriving on different threads can't both read the same value, each add
    # 1, and overwrite each other (which silently undercounted activity).
    with _state_lock:
        state = load_state()
        gs = state["activity_stats"].setdefault(str(guild_id), {})
        gs.setdefault("messages_per_day", {})
        gs.setdefault("msg_per_user", {})
        today = _today()
        gs["messages_per_day"][today] = gs["messages_per_day"].get(today, 0) + 1
        gs["msg_per_user"][str(user_id)] = gs["msg_per_user"].get(str(user_id), 0) + 1
        # trim history to last 60 days (only the per-day buckets — per-user is all-time)
        cutoff = (date.today() - timedelta(days=60)).isoformat()
        gs["messages_per_day"] = {d: v for d, v in gs["messages_per_day"].items() if d >= cutoff}
        save_state(state)


def _bump_join(guild_id: str) -> None:
    with _state_lock:
        state = load_state()
        gs = state["activity_stats"].setdefault(str(guild_id), {})
        gs.setdefault("joins_per_day", {})
        today = _today()
        gs["joins_per_day"][today] = gs["joins_per_day"].get(today, 0) + 1
        # Trim to the same 60-day window the chart shows so the file can't grow forever.
        cutoff = (date.today() - timedelta(days=60)).isoformat()
        gs["joins_per_day"] = {d: v for d, v in gs["joins_per_day"].items() if d >= cutoff}
        save_state(state)


def _bump_leave(guild_id: str) -> None:
    with _state_lock:
        state = load_state()
        gs = state["activity_stats"].setdefault(str(guild_id), {})
        gs.setdefault("leaves_per_day", {})
        today = _today()
        gs["leaves_per_day"][today] = gs["leaves_per_day"].get(today, 0) + 1
        cutoff = (date.today() - timedelta(days=60)).isoformat()
        gs["leaves_per_day"] = {d: v for d, v in gs["leaves_per_day"].items() if d >= cutoff}
        save_state(state)


def _add_voice_minutes(guild_id: str, user_id: str, minutes: float) -> None:
    if minutes <= 0:
        return
    with _state_lock:
        state = load_state()
        gs = state["activity_stats"].setdefault(str(guild_id), {})
        gs.setdefault("voice_minutes", {})
        gs["voice_minutes"][str(user_id)] = round(gs["voice_minutes"].get(str(user_id), 0) + minutes, 2)
        save_state(state)


def _resolve_user_name(guild_id: Optional[str], user_id: str) -> str:
    """Look up a user's actual display name through the live bot cache.

    Tries (in order): guild member nickname → guild member display_name →
    global User.name → "User …last4" placeholder. Never raises.
    """
    try:
        uid_int = int(user_id)
    except (TypeError, ValueError):
        return f"User {user_id}"
    # 1) Per-guild member (gives nickname / server display name).
    if guild_id:
        try:
            gid_int = int(guild_id)
            g = bot.get_guild(gid_int) if bot is not None else None
            if g is not None:
                m = g.get_member(uid_int)
                if m is not None:
                    return m.display_name or m.name or f"User {user_id}"
        except Exception:
            pass
    # 2) Try every guild the bot can see (covers cross-guild aggregate view).
    if bot is not None:
        try:
            for g in bot.guilds:
                m = g.get_member(uid_int)
                if m is not None:
                    return m.display_name or m.name or f"User {user_id}"
        except Exception:
            pass
        # 3) Global user cache.
        try:
            u = bot.get_user(uid_int)
            if u is not None:
                return u.global_name or u.name or f"User {user_id}"
        except Exception:
            pass
    # 4) Last-resort placeholder.
    s = str(user_id)
    return f"User …{s[-4:]}" if len(s) > 4 else (s or "Unknown")


def _name_top_entries(entries, guild_id: Optional[str]):
    """Decorate sorted (uid, value) tuples with a resolved display name.

    Returns a list of [uid, value, name] triples — the chart code on the
    dashboard reads index 2 first and falls back to index 0.
    """
    out = []
    for uid, val in entries:
        out.append([uid, val, _resolve_user_name(guild_id, uid)])
    return out


def get_analytics(guild_id: Optional[str] = None) -> Dict[str, Any]:
    state = load_state()
    if guild_id:
        gs = state["activity_stats"].get(str(guild_id), {})
        chatters = sorted(gs.get("msg_per_user", {}).items(), key=lambda kv: kv[1], reverse=True)[:10]
        voice    = sorted(gs.get("voice_minutes", {}).items(), key=lambda kv: kv[1], reverse=True)[:10]
        return {
            "guild_id": str(guild_id),
            "messages_per_day": gs.get("messages_per_day", {}),
            "joins_per_day": gs.get("joins_per_day", {}),
            "leaves_per_day": gs.get("leaves_per_day", {}),
            "top_chatters": _name_top_entries(chatters, guild_id),
            "top_voice":    _name_top_entries(voice,    guild_id),
        }
    # aggregate across all guilds
    msg_per_day: Dict[str, int] = {}
    joins_per_day: Dict[str, int] = {}
    leaves_per_day: Dict[str, int] = {}
    top_chatters: Dict[str, int] = {}
    top_voice: Dict[str, float] = {}
    for gid, gs in state["activity_stats"].items():
        for d, n in (gs.get("messages_per_day") or {}).items():
            msg_per_day[d] = msg_per_day.get(d, 0) + n
        for d, n in (gs.get("joins_per_day") or {}).items():
            joins_per_day[d] = joins_per_day.get(d, 0) + n
        for d, n in (gs.get("leaves_per_day") or {}).items():
            leaves_per_day[d] = leaves_per_day.get(d, 0) + n
        for uid, n in (gs.get("msg_per_user") or {}).items():
            top_chatters[uid] = top_chatters.get(uid, 0) + n
        for uid, n in (gs.get("voice_minutes") or {}).items():
            top_voice[uid] = top_voice.get(uid, 0) + n
    chatters = sorted(top_chatters.items(), key=lambda kv: kv[1], reverse=True)[:10]
    voice    = sorted(top_voice.items(),    key=lambda kv: kv[1], reverse=True)[:10]
    return {
        "guild_id": None,
        "messages_per_day": msg_per_day,
        "joins_per_day": joins_per_day,
        "leaves_per_day": leaves_per_day,
        "top_chatters": _name_top_entries(chatters, None),
        "top_voice":    _name_top_entries(voice,    None),
    }


# ============================================================
# THE BOT
# ============================================================
TOKEN = os.environ.get("SERVER_TOKEN", "")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.invites = True
intents.reactions = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# In-memory voice session tracking (start timestamps)
_voice_sessions: Dict[Tuple[int, int], datetime] = {}


def _embed(title: str, description: str = "", color: int = 0x10b981) -> discord.Embed:
    state = load_state()
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    e.set_footer(text=state["settings"].get("embed_footer", "Server Official"))
    return e


# ---------- INVITE TRACKING ----------
async def _refresh_invite_cache():
    state = load_state()
    cache = state.get("invite_cache", {})
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            continue
        cache[str(guild.id)] = {
            inv.code: {"uses": inv.uses or 0, "inviter_id": str(inv.inviter.id) if inv.inviter else None}
            for inv in invites
        }
    state["invite_cache"] = cache
    save_state(state)


# ============================================================
# EVENTS
# ============================================================
@bot.event
async def on_ready():
    logger.info(f"[server_official] Logged in as {bot.user} (id={bot.user.id})")
    await _refresh_invite_cache()
    try:
        synced = await bot.tree.sync()
        logger.info(f"[server_official] Synced {len(synced)} slash commands")
    except Exception as e:
        logger.error(f"[server_official] sync error: {e}")
    if not scheduler_tick.is_running():
        scheduler_tick.start()
    if not reminder_tick.is_running():
        reminder_tick.start()
    if not daily_backup_tick.is_running():
        daily_backup_tick.start()
    if not auto_cleanup_tick.is_running():
        auto_cleanup_tick.start()


@bot.event
async def on_invite_create(invite: discord.Invite):
    if not invite.guild:
        return
    state = load_state()
    cache = state.setdefault("invite_cache", {})
    g = cache.setdefault(str(invite.guild.id), {})
    g[invite.code] = {
        "uses": invite.uses or 0,
        "inviter_id": str(invite.inviter.id) if invite.inviter else None,
    }
    save_state(state)


@bot.event
async def on_invite_delete(invite: discord.Invite):
    if not invite.guild:
        return
    state = load_state()
    g = state.get("invite_cache", {}).get(str(invite.guild.id), {})
    g.pop(invite.code, None)
    save_state(state)


async def _post_log(guild: discord.Guild, log_type: str, description: str):
    add_server_log(str(guild.id), log_type, description)
    cfg = get_guild_config(str(guild.id))
    cid = cfg.get("log_channel_id")
    if cid:
        ch = guild.get_channel(int(cid)) if cid.isdigit() else None
        if ch:
            try:
                import embeds as _emb
                e = _emb.render(
                    "so.log_event",
                    guild_id=guild.id,
                    log_type=log_type,
                    description=description,
                )
                await ch.send(embed=e)
            except Exception:
                pass


@bot.event
async def on_member_join(member: discord.Member):
    g = member.guild
    cfg = get_guild_config(str(g.id))
    _bump_join(str(g.id))

    # Welcome role
    role_id = cfg.get("welcome_role_id")
    if role_id and role_id.isdigit():
        role = g.get_role(int(role_id))
        if role:
            try:
                await member.add_roles(role, reason="Server Official: welcome role")
            except Exception:
                pass

    # Welcome message
    wc_id = cfg.get("welcome_channel_id")
    msg_tpl = cfg.get("welcome_message", "")
    if wc_id and wc_id.isdigit() and msg_tpl:
        ch = g.get_channel(int(wc_id))
        if ch:
            text = msg_tpl.replace("{user}", member.mention).replace("{server}", g.name).replace("{count}", str(g.member_count or 0))
            try:
                import embeds as _emb
                e = _emb.render(
                    "so.welcome",
                    guild_id=g.id,
                    user=member,
                    server=g,
                    welcome_text=text,
                    member_number=g.member_count or 0,
                    account_age=f"<t:{int(member.created_at.timestamp())}:R>",
                )
                if not e.thumbnail or not e.thumbnail.url:
                    e.set_thumbnail(url=member.display_avatar.url)
                await ch.send(content=member.mention, embed=e)
            except Exception:
                pass

    await _post_log(g, "MEMBER JOIN", f"✅ {member.mention} joined the server. Total members: **{g.member_count}**.")

    # Invite tracking
    state = load_state()
    cache = state.get("invite_cache", {})
    counts = state.setdefault("invite_counts", {}).setdefault(str(g.id), {})
    try:
        current = await g.invites()
    except (discord.Forbidden, discord.HTTPException):
        current = []
    inviter_id = None
    new_cache_for_guild = {}
    old_for_guild = cache.get(str(g.id), {})
    for inv in current:
        new_cache_for_guild[inv.code] = {
            "uses": inv.uses or 0,
            "inviter_id": str(inv.inviter.id) if inv.inviter else None,
        }
        prev = old_for_guild.get(inv.code, {"uses": 0})
        if (inv.uses or 0) > prev.get("uses", 0) and inviter_id is None and inv.inviter:
            inviter_id = str(inv.inviter.id)
    cache[str(g.id)] = new_cache_for_guild
    if inviter_id:
        counts[inviter_id] = counts.get(inviter_id, 0) + 1
    save_state(state)


@bot.event
async def on_member_remove(member: discord.Member):
    g = member.guild
    cfg = get_guild_config(str(g.id))
    _bump_leave(str(g.id))
    wc_id = cfg.get("welcome_channel_id")
    msg_tpl = cfg.get("leave_message", "")
    if wc_id and wc_id.isdigit() and msg_tpl:
        ch = g.get_channel(int(wc_id))
        if ch:
            text = msg_tpl.replace("{user}", f"**{member.display_name}**").replace("{server}", g.name)
            try:
                import embeds as _emb
                e = _emb.render(
                    "so.leave",
                    guild_id=g.id,
                    user=member,
                    server=g,
                    leave_text=text,
                    members_remaining=g.member_count or 0,
                )
                if not e.thumbnail or not e.thumbnail.url:
                    e.set_thumbnail(url=member.display_avatar.url)
                await ch.send(embed=e)
            except Exception:
                pass
    await _post_log(g, "MEMBER LEAVE", f"👋 **{member.display_name}** left the server. Members remaining: **{g.member_count}**.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return await bot.process_commands(message)
    g = message.guild
    cfg = get_guild_config(str(g.id))

    # Activity tracking
    _bump_messages_for(str(g.id), str(message.author.id))

    # Custom commands (! prefix)
    cmds = list_custom_commands(str(g.id))
    if message.content.startswith("!") and cmds:
        trig = message.content[1:].split()[0].lower() if len(message.content) > 1 else ""
        if trig in cmds:
            try:
                await message.channel.send(cmds[trig])
            except Exception:
                pass

    # Sticky message refresh
    state = load_state()
    sticky = state["sticky_messages"].get(str(message.channel.id))
    if sticky and sticky.get("enabled"):
        # Only re-post if the previous sticky isn't the latest message
        last_id = sticky.get("last_message_id")
        try:
            if last_id and int(last_id) != message.id:
                # Delete previous sticky if any
                try:
                    old = await message.channel.fetch_message(int(last_id))
                    await old.delete()
                except Exception:
                    pass
                sent = await message.channel.send(embed=_embed("📌 Sticky", sticky["message"], color=0x5b8cff))
                sticky["last_message_id"] = str(sent.id)
                state["sticky_messages"][str(message.channel.id)] = sticky
                save_state(state)
            elif not last_id:
                sent = await message.channel.send(embed=_embed("📌 Sticky", sticky["message"], color=0x5b8cff))
                sticky["last_message_id"] = str(sent.id)
                state["sticky_messages"][str(message.channel.id)] = sticky
                save_state(state)
        except Exception:
            pass

    await bot.process_commands(message)


@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    key = (member.guild.id, member.id)
    now = datetime.now(timezone.utc)
    # Joined a voice channel
    if (before.channel is None) and (after.channel is not None):
        _voice_sessions[key] = now
    # Left a voice channel
    elif (before.channel is not None) and (after.channel is None):
        start = _voice_sessions.pop(key, None)
        if start:
            mins = (now - start).total_seconds() / 60.0
            _add_voice_minutes(str(member.guild.id), str(member.id), mins)


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    await _post_log(channel.guild, "CHANNEL_CREATE", f"#{channel.name} (`{channel.id}`) created")


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    await _post_log(channel.guild, "CHANNEL_DELETE", f"#{channel.name} (`{channel.id}`) deleted")


@bot.event
async def on_guild_channel_update(before, after):
    if before.name != after.name:
        await _post_log(after.guild, "CHANNEL_RENAME", f"#{before.name} → #{after.name}")


@bot.event
async def on_guild_role_create(role: discord.Role):
    await _post_log(role.guild, "ROLE_CREATE", f"Role @{role.name} (`{role.id}`) created")


@bot.event
async def on_guild_role_delete(role: discord.Role):
    await _post_log(role.guild, "ROLE_DELETE", f"Role @{role.name} (`{role.id}`) deleted")


@bot.event
async def on_guild_role_update(before, after):
    if before.name != after.name:
        await _post_log(after.guild, "ROLE_RENAME", f"@{before.name} → @{after.name}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if not payload.guild_id:
        return
    g = bot.get_guild(payload.guild_id)
    if not g:
        return
    cfg = get_guild_config(str(g.id))
    threshold = int(cfg.get("auto_pin_threshold") or 0)
    # Auto-pin
    if threshold > 0:
        ch = g.get_channel(payload.channel_id)
        if ch and isinstance(ch, discord.TextChannel):
            try:
                msg = await ch.fetch_message(payload.message_id)
                if msg.pinned:
                    return
                total = sum(r.count for r in msg.reactions)
                if total >= threshold:
                    await msg.pin(reason="Server Official: auto-pin threshold reached")
                    await _post_log(g, "AUTO_PIN", f"Pinned message in #{ch.name}")
            except Exception:
                pass

    # Poll vote tracking
    state = load_state()
    for poll in state["polls"]:
        if poll.get("ended") or str(poll["message_id"]) != str(payload.message_id):
            continue
        for opt in poll["options"]:
            if str(payload.emoji) == opt.get("emoji"):
                opt["votes"] = int(opt.get("votes", 0)) + 1
                save_state(state)
                return


# ============================================================
# SLASH COMMANDS — SETUP
# ============================================================
def _need_manage_guild(interaction: discord.Interaction) -> bool:
    return bool(interaction.user.guild_permissions.manage_guild)


@bot.tree.command(name="setup_roles", description="Auto-create core roles (Member, VIP, Moderator marker, Bot).")
@discord.app_commands.describe(prefix="Optional prefix added to each role name (e.g. 'BL ')")
async def setup_roles(interaction: discord.Interaction, prefix: Optional[str] = ""):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    g = interaction.guild
    if not g:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    targets = [
        ("Member", 0x5b8cff),
        ("VIP", 0xf59e0b),
        ("Moderator", 0x10b981),
        ("Bot", 0x6b7280),
    ]
    created = []
    skipped = []
    for name, color in targets:
        full = f"{prefix or ''}{name}".strip()
        existing = discord.utils.get(g.roles, name=full)
        if existing:
            skipped.append(full)
            continue
        try:
            r = await g.create_role(name=full, colour=discord.Colour(color), reason="Server Official: setup_roles")
            created.append(r.name)
        except discord.Forbidden:
            return await interaction.followup.send("❌ I lack the **Manage Roles** permission.", ephemeral=True)
    add_setup_history(str(g.id), "setup_roles", {"created": created, "skipped": skipped})
    add_bot_action("setup_roles", f"{len(created)} created, {len(skipped)} existed", str(interaction.user))
    await interaction.followup.send(
        embed=_embed("🛠️ Setup Roles",
                     f"**Created:** {', '.join(created) or '—'}\n**Already existed:** {', '.join(skipped) or '—'}"),
        ephemeral=True,
    )


@bot.tree.command(name="setup_channels", description="Auto-create core channels (rules, info, general, logs).")
async def setup_channels(interaction: discord.Interaction, category: Optional[str] = "Server"):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    g = interaction.guild
    if not g:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    cat = discord.utils.get(g.categories, name=category)
    if not cat:
        try:
            cat = await g.create_category(category, reason="Server Official: setup_channels")
        except discord.Forbidden:
            return await interaction.followup.send("❌ I lack the **Manage Channels** permission.", ephemeral=True)
    targets = ["rules", "info", "general", "announcements", "logs"]
    created = []
    skipped = []
    for name in targets:
        existing = discord.utils.get(g.text_channels, name=name)
        if existing:
            skipped.append(name)
            continue
        try:
            await g.create_text_channel(name, category=cat, reason="Server Official: setup_channels")
            created.append(name)
        except Exception:
            pass
    add_setup_history(str(g.id), "setup_channels", {"category": category, "created": created, "skipped": skipped})
    add_bot_action("setup_channels", f"{len(created)} created, {len(skipped)} existed", str(interaction.user))
    await interaction.followup.send(
        embed=_embed("🛠️ Setup Channels",
                     f"**Category:** {cat.name}\n**Created:** {', '.join('#'+c for c in created) or '—'}\n"
                     f"**Already existed:** {', '.join('#'+c for c in skipped) or '—'}"),
        ephemeral=True,
    )


SERVER_TEMPLATES = {
    "community": {
        "categories": ["Welcome", "Community", "Voice"],
        "channels": {
            "Welcome": ["rules", "info", "introductions"],
            "Community": ["general", "announcements", "off-topic", "memes"],
            "Voice": [],
        },
        "voice": {"Voice": ["General VC", "Music", "AFK"]},
        "roles": [("Member", 0x5b8cff), ("VIP", 0xf59e0b), ("Newbie", 0x6b7280)],
    },
    "gaming": {
        "categories": ["Lobby", "Games", "Voice"],
        "channels": {
            "Lobby": ["rules", "lfg", "general"],
            "Games": ["valorant", "minecraft", "roblox"],
            "Voice": [],
        },
        "voice": {"Voice": ["Lobby", "Squad 1", "Squad 2"]},
        "roles": [("Player", 0x10b981), ("Streamer", 0xa855f7), ("Newbie", 0x6b7280)],
    },
    "study": {
        "categories": ["Common", "Subjects", "Voice"],
        "channels": {
            "Common": ["rules", "general", "resources"],
            "Subjects": ["math", "science", "coding", "languages"],
            "Voice": [],
        },
        "voice": {"Voice": ["Study Room", "Group Study"]},
        "roles": [("Student", 0x10b981), ("Mentor", 0xf59e0b)],
    },
}


@bot.tree.command(name="setup_template", description="Apply a one-shot server template (community, gaming, study).")
@discord.app_commands.describe(template="Which template to apply")
async def setup_template(interaction: discord.Interaction, template: str):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    g = interaction.guild
    if not g:
        return await interaction.response.send_message("Use in a server.", ephemeral=True)
    template = template.lower()
    if template not in SERVER_TEMPLATES:
        return await interaction.response.send_message(
            f"❌ Unknown template. Choose: {', '.join(SERVER_TEMPLATES.keys())}", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    spec = SERVER_TEMPLATES[template]
    summary = {"categories": [], "channels": [], "voice": [], "roles": []}
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
        return await interaction.followup.send("❌ Missing permissions to apply template.", ephemeral=True)
    add_setup_history(str(g.id), f"template:{template}", summary)
    add_bot_action("setup_template", f"Applied template '{template}'", str(interaction.user))
    await interaction.followup.send(
        embed=_embed(f"🚀 Template '{template}' applied",
                     f"Categories: {len(summary['categories'])}\nChannels: {len(summary['channels'])}\n"
                     f"Voice: {len(summary['voice'])}\nRoles: {len(summary['roles'])}"),
        ephemeral=True,
    )


# ============================================================
# SLASH COMMANDS — MEMBER MANAGEMENT
# ============================================================
# NOTE: /userinfo and /serverinfo were removed from Server Official —
# those are general info commands handled by SP4RK. Server Official focuses
# strictly on server-official duties: announcements, schedules, giveaways,
# invite tracking, welcome flow, scheduled engagement, and admin utilities.


@bot.tree.command(name="set_welcome_role", description="Set the role auto-assigned on join.")
@discord.app_commands.describe(role="Role to assign new members")
async def set_welcome_role(interaction: discord.Interaction, role: discord.Role):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    update_guild_config(str(interaction.guild_id), {"welcome_role_id": str(role.id)})
    add_bot_action("config", f"Welcome role set to @{role.name}", str(interaction.user))
    await interaction.response.send_message(f"✅ New members will receive {role.mention}.", ephemeral=True)


@bot.tree.command(name="set_log_channel", description="Set the channel for non-mod server change logs.")
@discord.app_commands.describe(channel="Channel to receive logs")
async def set_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    update_guild_config(str(interaction.guild_id), {"log_channel_id": str(channel.id)})
    add_bot_action("config", f"Log channel set to #{channel.name}", str(interaction.user))
    await interaction.response.send_message(f"✅ Logs will go to {channel.mention}.", ephemeral=True)


# ============================================================
# SLASH COMMANDS — ANNOUNCEMENTS / SCHEDULES / GIVEAWAYS
# ============================================================
@bot.tree.command(name="announce", description="Post a styled announcement to a channel right now.")
@discord.app_commands.describe(
    message="The body of the announcement",
    channel="Where to post (defaults to current channel)",
    title="Optional title (default: 📣 Announcement)",
    ping_everyone="Ping @everyone with the announcement",
    ping_role="A role to ping",
)
async def slash_announce(
    interaction: discord.Interaction,
    message: str,
    channel: Optional[discord.TextChannel] = None,
    title: Optional[str] = None,
    ping_everyone: Optional[bool] = False,
    ping_role: Optional[discord.Role] = None,
):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    target = channel or interaction.channel
    embed = _embed(title or "📣 Announcement", message, color=0x3b82f6)
    embed.set_author(name=f"From {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
    embed.set_footer(text=f"Posted in #{target.name} • Server Official")
    parts = []
    if ping_everyone:
        parts.append("@everyone")
    if ping_role:
        parts.append(ping_role.mention)
    content = " ".join(parts) if parts else None
    try:
        await target.send(content=content, embed=embed,
                          allowed_mentions=discord.AllowedMentions(everyone=ping_everyone, roles=True))
        add_bot_action("announce", f"Announcement posted in #{target.name}", str(interaction.user))
        fire_external_hooks("announcement_posted", {"channel_id": str(target.id), "title": title or "Announcement", "message": message})
        await interaction.response.send_message(f"✅ Announcement posted in {target.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"❌ I don't have permission to post in {target.mention}.", ephemeral=True)


@bot.tree.command(name="broadcast", description="Post the same announcement to multiple channels.")
@discord.app_commands.describe(
    message="The body of the announcement",
    channels="Mention multiple channels separated by spaces (e.g. #a #b #c)",
    title="Optional title",
)
async def slash_broadcast(interaction: discord.Interaction, message: str, channels: str, title: Optional[str] = None):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    g = interaction.guild
    ids = [int(m) for m in re.findall(r"<#(\d+)>", channels)]
    if not ids:
        return await interaction.response.send_message("❌ Mention at least one channel.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    posted = 0
    for cid in ids:
        ch = g.get_channel(cid)
        if not ch:
            continue
        try:
            await ch.send(embed=_embed(title or "📣 Announcement", message))
            posted += 1
        except Exception:
            pass
    add_bot_action("broadcast", f"Broadcast to {posted} channel(s)", str(interaction.user))
    await interaction.followup.send(f"✅ Posted in **{posted}** channel(s).", ephemeral=True)


@bot.tree.command(name="schedule", description="Schedule an announcement for later (e.g. 30m, 2h, 1d).")
async def slash_schedule(interaction: discord.Interaction, message: str, when: str,
                         channel: Optional[discord.TextChannel] = None, title: Optional[str] = None):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    seconds = parse_duration(when)
    if not seconds or seconds < 5:
        return await interaction.response.send_message("❌ Invalid duration. Try `30m`, `2h`, `1d`, or `90s`.", ephemeral=True)
    when_dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    target = channel or interaction.channel
    item = add_scheduled_announcement(
        guild_id=str(interaction.guild_id) if interaction.guild_id else None,
        channel_id=str(target.id),
        title=title or "📣 Announcement",
        message=message,
        scheduled_for_iso=when_dt.isoformat(),
        created_by=str(interaction.user.id),
    )
    await interaction.response.send_message(
        f"✅ Scheduled — will post in {target.mention} <t:{int(when_dt.timestamp())}:R>\n`ID: {item['id']}`",
        ephemeral=True,
    )


@bot.tree.command(name="list_schedules", description="List pending scheduled announcements.")
async def slash_list_schedules(interaction: discord.Interaction):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    items = [a for a in list_scheduled_announcements()
             if a.get("guild_id") == str(interaction.guild_id) or a.get("guild_id") is None]
    if not items:
        return await interaction.response.send_message("No pending scheduled announcements.", ephemeral=True)
    desc = []
    for a in items[:15]:
        when = _parse_iso(a["scheduled_for"])
        ts = f"<t:{int(when.timestamp())}:R>" if when else a["scheduled_for"]
        snippet = (a["message"][:60] + "…") if len(a["message"]) > 60 else a["message"]
        desc.append(f"`{a['id']}` — {ts}\n> {snippet}")
    await interaction.response.send_message(embed=_embed("⏰ Pending Scheduled Announcements", "\n\n".join(desc)), ephemeral=True)


@bot.tree.command(name="cancel_schedule", description="Cancel a pending scheduled announcement by ID.")
async def slash_cancel_schedule(interaction: discord.Interaction, announcement_id: str):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    if delete_scheduled_announcement(announcement_id):
        await interaction.response.send_message(f"🗑️ Cancelled `{announcement_id}`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ No announcement found with ID `{announcement_id}`.", ephemeral=True)


@bot.tree.command(name="giveaway", description="Start a giveaway with a 🎉 reaction.")
async def slash_giveaway(interaction: discord.Interaction, prize: str, duration: str,
                         winners: Optional[int] = 1, channel: Optional[discord.TextChannel] = None):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    seconds = parse_duration(duration)
    if not seconds or seconds < 10:
        return await interaction.response.send_message("❌ Invalid duration.", ephemeral=True)
    winners = max(1, min(int(winners or 1), 25))
    target = channel or interaction.channel
    ends_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    embed = _embed(f"🎉 GIVEAWAY: {prize}",
                   f"React with 🎉 to enter!\n\n**Winners:** {winners}\n"
                   f"**Ends:** <t:{int(ends_at.timestamp())}:R>\n"
                   f"**Hosted by:** {interaction.user.mention}", color=0xf59e0b)
    try:
        await interaction.response.defer(ephemeral=True)
        msg = await target.send(embed=embed)
        await msg.add_reaction("🎉")
    except discord.Forbidden:
        return await interaction.followup.send(f"❌ I can't post in {target.mention}.", ephemeral=True)
    state = load_state()
    state["giveaways"].append({
        "id": _gen_id(), "guild_id": str(interaction.guild_id), "channel_id": str(target.id),
        "message_id": str(msg.id), "prize": prize, "winners": winners,
        "host_id": str(interaction.user.id), "ends_at": ends_at.isoformat(),
        "created_at": _now_iso(), "ended": False, "winner_ids": [],
    })
    save_state(state)
    add_bot_action("giveaway_start", f"Giveaway '{prize}' started", str(interaction.user))
    await interaction.followup.send(f"✅ Giveaway started in {target.mention}.", ephemeral=True)


@bot.tree.command(name="list_giveaways", description="List active giveaways.")
async def slash_list_giveaways(interaction: discord.Interaction):
    items = [g for g in list_giveaways(include_ended=False) if g.get("guild_id") == str(interaction.guild_id)]
    if not items:
        return await interaction.response.send_message("No active giveaways.", ephemeral=True)
    desc = []
    for g in items[:15]:
        when = _parse_iso(g["ends_at"])
        ts = f"<t:{int(when.timestamp())}:R>" if when else g["ends_at"]
        desc.append(f"`{g['id']}` — **{g['prize']}** ({g['winners']} winners) — {ts}")
    await interaction.response.send_message(embed=_embed("🎁 Active Giveaways", "\n".join(desc)), ephemeral=True)


@bot.tree.command(name="end_giveaway", description="End a giveaway early and pick winners now.")
async def slash_end_giveaway(interaction: discord.Interaction, giveaway_id: str):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    state = load_state()
    target = next((g for g in state["giveaways"] if g["id"] == giveaway_id and not g.get("ended")), None)
    if not target:
        return await interaction.response.send_message(f"❌ No active giveaway `{giveaway_id}`.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    await _finalize_giveaway(target)
    await interaction.followup.send(f"✅ Ended `{giveaway_id}`.", ephemeral=True)


# ============================================================
# SLASH COMMANDS — UTILITIES (poll, remind, sticky, note, embed)
# ============================================================
NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


@bot.tree.command(name="poll", description="Create a reaction poll with up to 10 options.")
@discord.app_commands.describe(
    question="What's the question?",
    options="Comma-separated answers (e.g. 'Yes, No, Maybe')",
    duration="How long the poll runs (e.g. 1h, 1d). Optional.",
)
async def slash_poll(interaction: discord.Interaction, question: str, options: str, duration: Optional[str] = None):
    opts = [o.strip() for o in options.split(",") if o.strip()][:10]
    if len(opts) < 2:
        return await interaction.response.send_message("❌ Provide at least 2 options.", ephemeral=True)
    body = "\n".join(f"{NUMBER_EMOJIS[i]} {o}" for i, o in enumerate(opts))
    e = _embed(f"📊 {question}", body, color=0x8b5cf6)
    if duration:
        secs = parse_duration(duration)
        if secs and secs > 0:
            ends_at = datetime.now(timezone.utc) + timedelta(seconds=secs)
            e.add_field(name="Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
    await interaction.response.defer()
    msg = await interaction.channel.send(embed=e)
    for i in range(len(opts)):
        try:
            await msg.add_reaction(NUMBER_EMOJIS[i])
        except Exception:
            pass
    state = load_state()
    poll = {
        "id": _gen_id(),
        "guild_id": str(interaction.guild_id),
        "channel_id": str(interaction.channel.id),
        "message_id": str(msg.id),
        "question": question,
        "options": [{"emoji": NUMBER_EMOJIS[i], "label": o, "votes": 0} for i, o in enumerate(opts)],
        "created_at": _now_iso(),
        "ends_at": (datetime.now(timezone.utc) + timedelta(seconds=parse_duration(duration) or 0)).isoformat() if duration else None,
        "ended": False,
    }
    state["polls"].append(poll)
    save_state(state)
    await interaction.followup.send(f"✅ Poll created (`{poll['id']}`).", ephemeral=True)


@bot.tree.command(name="remind", description="Set a personal reminder.")
@discord.app_commands.describe(when="Duration like 30m, 2h, 1d", text="Reminder text")
async def slash_remind(interaction: discord.Interaction, when: str, text: str):
    secs = parse_duration(when)
    if not secs or secs < 5:
        return await interaction.response.send_message("❌ Invalid duration.", ephemeral=True)
    due = datetime.now(timezone.utc) + timedelta(seconds=secs)
    add_reminder(str(interaction.user.id), str(interaction.channel.id), text, due.isoformat(),
                 guild_id=str(interaction.guild_id) if interaction.guild_id else None)
    await interaction.response.send_message(f"⏰ I'll remind you <t:{int(due.timestamp())}:R>.", ephemeral=True)


@bot.tree.command(name="sticky", description="Set or remove a sticky message in this channel.")
@discord.app_commands.describe(action="set or remove", message="Text for the sticky (when action=set)")
async def slash_sticky(interaction: discord.Interaction, action: str, message: Optional[str] = None):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    action = action.lower()
    if action == "set":
        if not message:
            return await interaction.response.send_message("❌ Provide a message.", ephemeral=True)
        set_sticky(str(interaction.channel.id), message, guild_id=str(interaction.guild_id))
        await interaction.response.send_message("📌 Sticky set. It'll re-pin after each new message.", ephemeral=True)
    elif action == "remove":
        if remove_sticky(str(interaction.channel.id)):
            await interaction.response.send_message("🗑️ Sticky removed.", ephemeral=True)
        else:
            await interaction.response.send_message("No sticky was active here.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Use `set` or `remove`.", ephemeral=True)


@bot.tree.command(name="note", description="Admin notes (private to dashboard / staff).")
@discord.app_commands.describe(action="add | list | remove", text="Note text (for add)", note_id="Note id (for remove)")
async def slash_note(interaction: discord.Interaction, action: str, text: Optional[str] = None, note_id: Optional[str] = None):
    if not _need_manage_guild(interaction):
        return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
    g_id = str(interaction.guild_id)
    a = action.lower()
    if a == "add":
        if not text:
            return await interaction.response.send_message("❌ Provide text.", ephemeral=True)
        item = add_admin_note(g_id, text, str(interaction.user))
        await interaction.response.send_message(f"📝 Added note `{item['id']}`.", ephemeral=True)
    elif a == "list":
        notes = list_admin_notes(g_id)
        if not notes:
            return await interaction.response.send_message("No notes yet.", ephemeral=True)
        desc = "\n".join(f"`{n['id']}` — {n['text'][:80]}" for n in notes[:15])
        await interaction.response.send_message(embed=_embed("📝 Admin Notes", desc), ephemeral=True)
    elif a == "remove":
        if not note_id:
            return await interaction.response.send_message("❌ Provide note_id.", ephemeral=True)
        if delete_admin_note(g_id, note_id):
            await interaction.response.send_message("🗑️ Removed.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Not found.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Use add | list | remove.", ephemeral=True)


# ============================================================
# SLASH COMMANDS — ENGAGEMENT (goals)
# ============================================================
@bot.tree.command(name="goal", description="Set or list server goals.")
@discord.app_commands.describe(action="set | list", type_="messages | members", target="Target number (for set)")
async def slash_goal(interaction: discord.Interaction, action: str, type_: Optional[str] = None, target: Optional[int] = None):
    if action.lower() == "set":
        if not _need_manage_guild(interaction):
            return await interaction.response.send_message("❌ Manage Server required.", ephemeral=True)
        if type_ not in {"messages", "members"} or not target:
            return await interaction.response.send_message("❌ Use: type_=messages|members, target=<int>", ephemeral=True)
        item = add_goal(str(interaction.guild_id), type_, int(target))
        await interaction.response.send_message(f"🎯 Goal set: {type_} → {target} (`{item['id']}`)", ephemeral=True)
    elif action.lower() == "list":
        items = list_goals(str(interaction.guild_id))
        if not items:
            return await interaction.response.send_message("No goals set.", ephemeral=True)
        lines = [f"`{g['id']}` — {g['type']}: **{g['current']}/{g['target']}** {'✅' if g['done'] else ''}" for g in items]
        await interaction.response.send_message(embed=_embed("🎯 Server Goals", "\n".join(lines)))
    else:
        await interaction.response.send_message("❌ Use `set` or `list`.", ephemeral=True)


# ============================================================
# SLASH COMMANDS — INVITES / STATS / SERVERINFO / HELP
# ============================================================
@bot.tree.command(name="invites", description="Show invite counts.")
async def slash_invites(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    state = load_state()
    counts = state.get("invite_counts", {}).get(str(interaction.guild_id), {})
    if user:
        n = counts.get(str(user.id), 0)
        return await interaction.response.send_message(
            embed=_embed(f"📨 Invites — {user.display_name}", f"**{n}** invited"), ephemeral=True)
    if not counts:
        return await interaction.response.send_message("No invite data yet.", ephemeral=True)
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
    lines = []
    for i, (uid, n) in enumerate(top, 1):
        m = interaction.guild.get_member(int(uid)) if interaction.guild else None
        name = m.mention if m else f"<@{uid}>"
        medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"`#{i}`"
        lines.append(f"{medal} {name} — **{n}** invite(s)")
    await interaction.response.send_message(embed=_embed("📨 Invite Leaderboard", "\n".join(lines)))


@bot.tree.command(name="stats", description="Detailed server statistics.")
async def slash_stats(interaction: discord.Interaction):
    g = interaction.guild
    if not g:
        return await interaction.response.send_message("Use in a server.", ephemeral=True)
    bots_count = sum(1 for m in g.members if m.bot)
    humans = (g.member_count or 0) - bots_count
    online = sum(1 for m in g.members if m.status != discord.Status.offline)
    text_chans = sum(1 for c in g.channels if isinstance(c, discord.TextChannel))
    voice_chans = sum(1 for c in g.channels if isinstance(c, discord.VoiceChannel))
    state = load_state()
    today_msgs = state["activity_stats"].get(str(g.id), {}).get("messages_per_day", {}).get(_today(), 0)
    desc = (
        f"**Members:** {g.member_count}  •  👤 {humans} humans  •  🤖 {bots_count} bots\n"
        f"**Online:** {online}\n"
        f"**Channels:** 💬 {text_chans} text  •  🔊 {voice_chans} voice\n"
        f"**Roles:** {len(g.roles)}\n"
        f"**Boosts:** {g.premium_subscription_count} (Tier {g.premium_tier})\n"
        f"**Messages today:** {today_msgs}\n"
        f"**Created:** <t:{int(g.created_at.timestamp())}:D>\n"
        f"**Owner:** {g.owner.mention if g.owner else '—'}"
    )
    e = _embed(f"📊 {g.name} — Stats", desc, color=0x5b8cff)
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="help", description="What Server Official can do.")
async def slash_help(interaction: discord.Interaction):
    desc = (
        "**🛠️ Setup**\n"
        "`/setup_roles` `/setup_channels` `/setup_template` `/set_welcome_role` `/set_log_channel`\n\n"
        "**📣 Announcements**\n"
        "`/announce` `/broadcast` `/schedule` `/list_schedules` `/cancel_schedule`\n\n"
        "**🎉 Engagement**\n"
        "`/goal` `/giveaway` `/list_giveaways` `/end_giveaway`\n\n"
        "**🛠️ Utilities**\n"
        "`/poll` `/remind` `/sticky` `/note` `/invites` `/stats`\n\n"
        "_Looking for `/userinfo` or `/serverinfo`? Those are handled by **SP4RK** — "
        "Server Official focuses on official server duties only._\n\n"
        "Manage everything from the dashboard at **/server-official/dashboard**."
    )
    await interaction.response.send_message(embed=_embed("🛡️ Server Official — Help", desc))


# ============================================================
# SCHEDULER LOOPS
# ============================================================
async def _post_scheduled(item: Dict[str, Any]) -> bool:
    chan = bot.get_channel(int(item["channel_id"]))
    if chan is None:
        try:
            chan = await bot.fetch_channel(int(item["channel_id"]))
        except Exception as e:
            logger.warning(f"[server_official] scheduled post: cannot fetch channel: {e}")
            return False
    embed = _embed(item.get("title") or "📣 Announcement", item["message"], color=item.get("color", 0x10b981))
    embed.set_author(name="Scheduled Announcement")
    img_url = (item.get("image_url") or "").strip()
    if img_url:
        try:
            embed.set_image(url=img_url)
        except Exception as e:
            logger.warning(f"[server_official] scheduled post: invalid image_url ignored ({e})")
    try:
        await chan.send(embed=embed)
        fire_external_hooks("announcement_posted", {"channel_id": str(chan.id), "title": item.get("title"), "message": item["message"], "image_url": img_url or None})
        return True
    except Exception as e:
        logger.warning(f"[server_official] scheduled post failed: {e}")
        return False


async def _finalize_giveaway(g: Dict[str, Any]) -> None:
    state = load_state()
    target_g = next((x for x in state["giveaways"] if x["id"] == g["id"]), None)
    if not target_g or target_g.get("ended"):
        return
    chan = bot.get_channel(int(g["channel_id"]))
    if chan is None:
        try:
            chan = await bot.fetch_channel(int(g["channel_id"]))
        except Exception:
            target_g["ended"] = True
            save_state(state)
            return
    try:
        msg = await chan.fetch_message(int(g["message_id"]))
    except Exception:
        target_g["ended"] = True
        save_state(state)
        return
    entrants: List[discord.User] = []
    for reaction in msg.reactions:
        if str(reaction.emoji) == "🎉":
            async for u in reaction.users():
                if not u.bot and u not in entrants:
                    entrants.append(u)
            break
    winners_n = max(1, int(g.get("winners", 1)))
    if entrants:
        chosen = random.sample(entrants, k=min(winners_n, len(entrants)))
        winner_ids = [str(u.id) for u in chosen]
        winner_text = ", ".join(u.mention for u in chosen)
        result = f"🎉 **Winner(s):** {winner_text}\n**Prize:** {g['prize']}"
    else:
        winner_ids = []
        result = f"😞 No valid entries — couldn't pick a winner for **{g['prize']}**."
    embed = _embed("🎁 Giveaway Ended", result, color=0xf59e0b)
    try:
        await chan.send(embed=embed)
    except Exception:
        pass
    try:
        original = msg.embeds[0] if msg.embeds else None
        if original:
            new_embed = original.copy()
            new_embed.title = f"🎁 ENDED — {g['prize']}"
            new_embed.color = 0x6b7280
            await msg.edit(embed=new_embed)
    except Exception:
        pass
    target_g["ended"] = True
    target_g["winner_ids"] = winner_ids
    target_g["ended_at"] = _now_iso()
    save_state(state)
    add_bot_action("giveaway_end", f"Ended giveaway '{g['prize']}'")


@tasks.loop(seconds=30)
async def scheduler_tick():
    """Fire scheduled announcements + finalize ended giveaways.

    Hardened against:
      - Loop death from a single bad iteration (top-level try/except).
      - Naive/aware datetime crashes (per-item try/except around _parse_iso).
      - Duplicate-post race: we mark `posted=True` and `save_state` BEFORE
        the actual send, so if the bot crashes mid-send the announcement is
        not replayed on next boot.
    """
    if not bot.is_ready():
        return
    try:
        now = datetime.now(timezone.utc)
        state = load_state()
        changed = False
        for ann in state.get("scheduled_announcements", []):
            try:
                if ann.get("posted"):
                    continue
                when = _parse_iso(ann.get("scheduled_for", ""))
                if not (when and when <= now):
                    continue
                # Reserve first to avoid duplicate posting on crash/restart.
                ann["posted"] = True
                ann["posted_at"] = _now_iso()
                changed = True
                save_state(state)
                ok = await _post_scheduled(ann)
                if not ok:
                    # Mark failure so admins can see what happened, but keep
                    # `posted=True` to avoid retry storms.
                    ann["posted_at"] = None
                    ann["last_error"] = "post_scheduled returned False"
                    save_state(state)
            except Exception as e:
                logger.warning(f"[scheduler] announcement error: {e}")
        if changed:
            save_state(state)
        for g in list(state.get("giveaways", [])):
            try:
                if g.get("ended"):
                    continue
                ends = _parse_iso(g.get("ends_at", ""))
                if ends and ends <= now:
                    await _finalize_giveaway(g)
            except Exception as e:
                logger.warning(f"[scheduler] giveaway finalize error: {e}")
    except Exception as e:
        logger.error(f"[scheduler_tick] iteration crashed: {e}")


@tasks.loop(seconds=20)
async def reminder_tick():
    if not bot.is_ready():
        return
    try:
        state = load_state()
        now = datetime.now(timezone.utc)
        changed = False
        for r in state["reminders"]:
            try:
                if r.get("sent"):
                    continue
                due = _parse_iso(r.get("due_at", ""))
                if not (due and due <= now):
                    continue
                # Mark as sent FIRST so a crash mid-send won't double-fire.
                r["sent"] = True
                r["sent_at"] = _now_iso()
                changed = True
                ch = bot.get_channel(int(r["channel_id"]))
                if ch:
                    try:
                        await ch.send(f"⏰ <@{r['user_id']}> reminder: {r['text']}")
                    except (discord.NotFound, discord.Forbidden) as e:
                        logger.warning(f"[reminder] cannot deliver: {e}")
                    except Exception as e:
                        logger.warning(f"[reminder] send failed: {e}")
            except Exception as e:
                logger.warning(f"[reminder] item error: {e}")
        if changed:
            save_state(state)
    except Exception as e:
        logger.error(f"[reminder_tick] iteration crashed: {e}")


@tasks.loop(hours=24)
async def daily_backup_tick():
    if not bot.is_ready():
        return
    try:
        create_backup(label="auto-daily")
    except Exception as e:
        # Never let a backup failure (disk full, perm error, etc) kill the loop.
        try:
            logger.error(f"[daily_backup_tick] backup failed: {e}")
        except Exception:
            print(f"[daily_backup_tick] backup failed: {e}")


@tasks.loop(hours=6)
async def auto_cleanup_tick():
    """Delete this bot's own old messages older than configured days, per-guild."""
    if not bot.is_ready():
        return
    for guild in bot.guilds:
        cfg = get_guild_config(str(guild.id))
        days = int(cfg.get("auto_cleanup_age_days") or 0)
        if days <= 0:
            continue
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        for ch in guild.text_channels:
            try:
                async for msg in ch.history(limit=200, before=cutoff):
                    if msg.author == bot.user:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
            except Exception:
                continue


# ============================================================
# DYNO/CARL-BOT EXTRAS — Starboard + Embed Builder
# ============================================================
# These keys aren't in DEFAULT_GUILD_CONFIG so update_guild_config would
# strip them. Provide a passthrough writer for the extras module.
def _so_set_guild_config(guild_id: str, cfg: Dict[str, Any]) -> None:
    state = load_state()
    state["server_configs"][str(guild_id)] = cfg
    save_state(state)


try:
    import so_extras as _so_extras
    _so_extras.setup(
        bot,
        load_state=load_state,
        save_state=save_state,
        get_guild_config=get_guild_config,
        set_guild_config=_so_set_guild_config,
    )
except Exception as _e:
    logger.warning(f"[server_official] Failed to load so_extras: {_e}")


# ============================================================
# THREAD LAUNCHER
# ============================================================
_bot_thread: Optional[threading.Thread] = None


def run_bot():
    if not TOKEN:
        logger.warning("[server_official] SERVER_TOKEN not set — Server Official bot will not start.")
        return
    import time
    while True:
        logger.info("[server_official] Starting Server Official bot…")
        try:
            bot.run(TOKEN, reconnect=True, log_handler=None)
        except discord.errors.LoginFailure:
            logger.error("[server_official] Invalid SERVER_TOKEN.")
            return
        except discord.errors.HTTPException as e:
            if getattr(e, "status", None) == 429:
                logger.warning("[server_official] Rate limited; sleeping 60s")
                time.sleep(60)
                continue
            logger.error(f"[server_official] HTTPException: {e}")
            time.sleep(15)
        except Exception as e:
            logger.error(f"[server_official] crashed: {e}; restarting in 10s")
            time.sleep(10)


def start_bot_thread():
    global _bot_thread
    if _bot_thread and _bot_thread.is_alive():
        return _bot_thread
    _bot_thread = threading.Thread(target=run_bot, name="ServerOfficialBot", daemon=True)
    _bot_thread.start()
    return _bot_thread


def is_running() -> bool:
    """True when the Server Official bot is connected to Discord.

    Two valid sources of truth:
      1) The bot is running in *this* Python process — `_bot_thread` is alive.
      2) The bot is running in a *different* process (typical on Render where
         the dashboard and the bot are separate services). In that case the
         in-process thread is None, but a Discord REST API ping with the
         configured `SERVER_TOKEN` will succeed and prove the bot is reachable.
    """
    if _bot_thread and _bot_thread.is_alive() and TOKEN:
        return True
    try:
        from bot_health import token_is_live
        return token_is_live(TOKEN)
    except Exception:
        return False


# Helpers exposed for dashboard helpers below the main bot definition
def get_bot_action_logs(limit: int = 100) -> List[Dict[str, Any]]:
    return load_state()["bot_action_logs"][:limit]


def get_server_logs(guild_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    items = load_state()["server_logs"]
    if guild_id:
        items = [l for l in items if l.get("guild_id") in (None, str(guild_id))]
    return items[:limit]


def get_setup_history(guild_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    items = load_state()["setup_history"]
    if guild_id:
        items = [s for s in items if s.get("guild_id") == str(guild_id)]
    return items[:limit]


def list_polls(guild_id: Optional[str] = None) -> List[Dict[str, Any]]:
    items = load_state()["polls"]
    if guild_id:
        items = [p for p in items if p.get("guild_id") == str(guild_id)]
    return items


def get_full_overview(guild_id: Optional[str] = None) -> Dict[str, Any]:
    """Snapshot for the overview tab.

    When `guild_id` is provided every count is filtered to that single
    guild so the dashboard cards reflect the currently-selected server
    instead of bot-wide totals.
    """
    state = load_state()

    def _gmatch(item: Dict[str, Any]) -> bool:
        if not guild_id:
            return True
        return str(item.get("guild_id") or "") == str(guild_id)

    if guild_id:
        gid = str(guild_id)
        sticky_items = [v for v in state["sticky_messages"].values()
                        if str((v or {}).get("guild_id") or "") == gid]
        custom_cmd_count = len(state["custom_commands"].get(gid, {}) or {})
        # Logs and webhook lists carry a guild_id field where applicable.
        server_logs = [l for l in state["server_logs"]
                       if str((l or {}).get("guild_id") or "") == gid]
        bot_actions = [l for l in state["bot_action_logs"]
                       if str((l or {}).get("guild_id") or "") == gid]
        ext_hooks = [h for h in state["external_hooks"]
                     if str((h or {}).get("guild_id") or "") == gid]
        in_hooks = [w for w in state["webhooks_in"]
                    if str((w or {}).get("guild_id") or "") == gid]
        templates = [t for t in state["announcement_templates"]
                     if str((t or {}).get("guild_id") or "") in (gid, "")]
        backups = [b for b in state["config_backups"]
                   if str((b or {}).get("guild_id") or "") in (gid, "")]
    else:
        sticky_items = list(state["sticky_messages"].values())
        custom_cmd_count = sum(len(v or {}) for v in state["custom_commands"].values())
        server_logs = state["server_logs"]
        bot_actions = state["bot_action_logs"]
        ext_hooks = state["external_hooks"]
        in_hooks = state["webhooks_in"]
        templates = state["announcement_templates"]
        backups = state["config_backups"]

    return {
        "scheduled_count": len([a for a in state["scheduled_announcements"]
                                if not a.get("posted") and _gmatch(a)]),
        "active_giveaways": len([g for g in state["giveaways"]
                                 if not g.get("ended") and _gmatch(g)]),
        "templates_count": len(templates),
        "polls_active": len([p for p in state["polls"]
                             if not p.get("ended") and _gmatch(p)]),
        "reminders_pending": len([r for r in state["reminders"]
                                  if not r.get("sent") and _gmatch(r)]),
        "stickies": len(sticky_items),
        "goals_count": len([g for g in state["server_goals"] if _gmatch(g)]),
        "backups_count": len(backups),
        "custom_commands_count": custom_cmd_count,
        "external_hooks": len(ext_hooks),
        "incoming_webhooks": len(in_hooks),
        "logs_count": len(server_logs),
        "bot_actions_count": len(bot_actions),
    }


# ============================================================
# AVATAR / PERMISSION MANAGEMENT
# ============================================================
import asyncio as _asyncio

AVATAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "server_avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)

ANNOUNCEMENT_IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "announcement_images")
os.makedirs(ANNOUNCEMENT_IMG_DIR, exist_ok=True)


def save_announcement_image(image_bytes: bytes, ext: str) -> Dict[str, Any]:
    """Persist an announcement image and return its public-relative URL.

    Used by the scheduled-announcement form so users can attach a picture
    that the bot then renders via embed.set_image() at post time.
    """
    ext = (ext or "png").lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ("png", "jpg", "gif", "webp"):
        return {"ok": False, "error": "Unsupported image format. Use PNG, JPG, GIF, or WEBP."}
    if len(image_bytes) > 10 * 1024 * 1024:
        return {"ok": False, "error": "Image too large (max 10 MB)"}
    fname = f"{_gen_id()}.{ext}"
    path = os.path.join(ANNOUNCEMENT_IMG_DIR, fname)
    with open(path, "wb") as f:
        f.write(image_bytes)
    return {"ok": True, "filename": fname, "path": f"/static/announcement_images/{fname}"}


def member_can_manage_bot(guild_id: str, user_id: str) -> Dict[str, Any]:
    """Return {'allowed': bool, 'reason': str, 'role': str} — user must be guild owner OR have admin/manage_guild perm."""
    try:
        if not bot or not bot.is_ready():
            return {"allowed": False, "reason": "Bot not connected to Discord", "role": ""}
        g = bot.get_guild(int(guild_id))
        if not g:
            return {"allowed": False, "reason": "Bot is not in this server", "role": ""}
        m = g.get_member(int(user_id))
        if not m:
            return {"allowed": False, "reason": "You are not a member of this server", "role": ""}
        if g.owner_id == m.id:
            return {"allowed": True, "reason": "owner", "role": "Owner"}
        perms = m.guild_permissions
        if perms.administrator:
            return {"allowed": True, "reason": "administrator", "role": "Administrator"}
        if perms.manage_guild:
            return {"allowed": True, "reason": "manage_guild", "role": "Manage Server"}
        return {"allowed": False, "reason": "Requires Owner, Administrator, or Manage Server permission", "role": ""}
    except Exception as e:
        return {"allowed": False, "reason": f"Permission check failed: {e}", "role": ""}


def get_guild_avatar_path(guild_id: str) -> Optional[str]:
    """Return absolute path of stored avatar for guild, or None."""
    for ext in ("png", "jpg", "jpeg", "gif", "webp"):
        p = os.path.join(AVATAR_DIR, f"{guild_id}.{ext}")
        if os.path.exists(p):
            return p
    return None


def get_guild_avatar_url(guild_id: str) -> Optional[str]:
    """Return /static URL for guild avatar (cache-busted), or None."""
    p = get_guild_avatar_path(guild_id)
    if not p:
        return None
    fname = os.path.basename(p)
    mtime = int(os.path.getmtime(p))
    return f"/static/server_avatars/{fname}?v={mtime}"


def save_guild_avatar(guild_id: str, image_bytes: bytes, ext: str) -> Dict[str, Any]:
    """Save avatar bytes locally and best-effort apply to Discord per-guild bot profile."""
    ext = (ext or "png").lower().lstrip(".")
    if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
        return {"ok": False, "error": "Unsupported image format. Use PNG, JPG, GIF, or WEBP."}
    if len(image_bytes) > 10 * 1024 * 1024:
        return {"ok": False, "error": "Image too large (max 10 MB)"}
    # remove any pre-existing avatars for this guild (different ext)
    for old_ext in ("png", "jpg", "jpeg", "gif", "webp"):
        old = os.path.join(AVATAR_DIR, f"{guild_id}.{old_ext}")
        if os.path.exists(old):
            try: os.remove(old)
            except Exception: pass
    path = os.path.join(AVATAR_DIR, f"{guild_id}.{ext}")
    with open(path, "wb") as f:
        f.write(image_bytes)
    # best-effort: push to Discord's per-guild bot profile (newer feature)
    discord_status = "Saved locally (dashboard only)"
    try:
        if bot and bot.is_ready():
            g = bot.get_guild(int(guild_id))
            if g and getattr(g, "me", None):
                fut = _asyncio.run_coroutine_threadsafe(
                    g.me.edit(avatar=image_bytes),
                    bot.loop
                )
                try:
                    fut.result(timeout=10)
                    discord_status = "Applied to Discord per-server bot profile"
                except Exception as inner:
                    discord_status = f"Saved locally; Discord rejected per-guild avatar ({inner.__class__.__name__})"
    except Exception as e:
        discord_status = f"Saved locally; Discord update skipped ({e.__class__.__name__})"
    return {"ok": True, "url": get_guild_avatar_url(guild_id), "discord_status": discord_status}


def delete_guild_avatar(guild_id: str) -> Dict[str, Any]:
    """Remove stored custom avatar for guild and reset Discord per-guild profile."""
    removed = False
    for ext in ("png", "jpg", "jpeg", "gif", "webp"):
        p = os.path.join(AVATAR_DIR, f"{guild_id}.{ext}")
        if os.path.exists(p):
            try:
                os.remove(p)
                removed = True
            except Exception: pass
    discord_status = "Local avatar removed"
    try:
        if bot and bot.is_ready():
            g = bot.get_guild(int(guild_id))
            if g and getattr(g, "me", None):
                fut = _asyncio.run_coroutine_threadsafe(
                    g.me.edit(avatar=None),
                    bot.loop
                )
                try:
                    fut.result(timeout=10)
                    discord_status = "Reset Discord per-server bot avatar to default"
                except Exception as inner:
                    discord_status = f"Local avatar removed; Discord reset failed ({inner.__class__.__name__})"
    except Exception as e:
        discord_status = f"Local avatar removed; Discord reset skipped ({e.__class__.__name__})"
    return {"ok": True, "removed": removed, "discord_status": discord_status}


def set_guild_bot_nickname(guild_id: str, nickname: str) -> Dict[str, Any]:
    """Apply a per-guild nickname to the bot user on Discord.

    Discord nicknames are limited to 32 characters. Pass an empty string (or
    None) to clear the nickname — Discord will then fall back to the bot's
    global username for that server.

    Returns a dict with `ok`, `nickname` (what was actually applied) and a
    `discord_status` describing whether it landed on Discord or only in
    local state.
    """
    raw = (nickname or "").strip()
    new_nick = raw[:32] if raw else None  # discord.py expects None to clear

    out = {
        "ok": True,
        "nickname": new_nick or "",
        "discord_status": "Saved locally; bot not running",
    }

    try:
        if not (bot and bot.is_ready()):
            return out
        g = bot.get_guild(int(guild_id))
        if not g or not getattr(g, "me", None):
            out["discord_status"] = "Bot is not a member of that server"
            return out

        fut = _asyncio.run_coroutine_threadsafe(
            g.me.edit(nick=new_nick),
            bot.loop
        )
        try:
            fut.result(timeout=10)
            out["discord_status"] = (
                f"Renamed bot to “{new_nick}” on this server"
                if new_nick else "Reset bot nickname to default on this server"
            )
        except Exception as inner:
            # Most common cause: bot lacks the CHANGE_NICKNAME permission.
            cls = inner.__class__.__name__
            out["ok"] = False
            out["discord_status"] = (
                f"Saved locally; Discord rejected the rename ({cls}). "
                f"Make sure the bot's role has the “Change Nickname” permission."
            )
    except Exception as e:
        out["ok"] = False
        out["discord_status"] = f"Saved locally; Discord update skipped ({e.__class__.__name__})"
    return out
