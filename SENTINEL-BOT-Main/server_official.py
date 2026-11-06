"""
server_official.py — NVD Official Discord bot + data layer.

Started as a daemon thread by wsgi.py when SERVER_TOKEN is set.
The dashboard (dashboard.py) imports this module and calls its public
functions to read/write data and send commands to the live bot.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands, tasks

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("SERVER_TOKEN", "").strip()
_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Persistent JSON store helpers
# ---------------------------------------------------------------------------
_STORE_LOCK = threading.Lock()

_STORE_FILE = os.path.join(_DATA_DIR, "so_data.json")

def _load_store() -> dict:
    try:
        if os.path.exists(_STORE_FILE):
            with open(_STORE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_store(data: dict) -> None:
    try:
        with open(_STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"[server-official] store save error: {e}")

def _get(key: str, default=None):
    with _STORE_LOCK:
        store = _load_store()
        return store.get(key, default if default is not None else [])

def _set(key: str, value) -> None:
    with _STORE_LOCK:
        store = _load_store()
        store[key] = value
        _save_store(store)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="so!", intents=intents, help_command=None)

_bot_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None

# ---------------------------------------------------------------------------
# Bot events
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    global _loop
    _loop = asyncio.get_event_loop()
    print(f"[NVD Official] Logged in as {bot.user} ({bot.user.id})")
    print(f"[NVD Official] Connected to {len(bot.guilds)} guild(s)")
    try:
        await bot.tree.sync()
        print("[NVD Official] Slash commands synced.")
    except Exception as e:
        print(f"[NVD Official] Slash sync error: {e}")
    if not _check_announcements.is_running():
        _check_announcements.start()
    if not _check_reminders.is_running():
        _check_reminders.start()
    if not _sticky_watcher.is_running():
        _sticky_watcher.start()
    if not _check_giveaways.is_running():
        _check_giveaways.start()


@bot.event
async def on_member_join(member: discord.Member):
    """Track join events for growth analytics."""
    cfg = get_guild_config(str(member.guild.id))
    welcome_ch_id = cfg.get("welcome_channel_id", "")
    welcome_msg = cfg.get("welcome_message", "")
    if welcome_ch_id and welcome_msg:
        try:
            ch = bot.get_channel(int(welcome_ch_id))
            if ch:
                msg = welcome_msg.replace("{user}", member.mention).replace("{server}", member.guild.name)
                await ch.send(msg)
        except Exception:
            pass
    _record_join(str(member.guild.id))


@bot.event
async def on_member_remove(member: discord.Member):
    _record_leave(str(member.guild.id))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    _record_message(str(message.guild.id))

    # ── Custom commands ─────────────────────────────────────────────────────
    content_lower = message.content.strip().lower()
    cmds = list_custom_commands(str(message.guild.id))
    for cc in cmds:
        trigger = cc.get("trigger", "").strip().lower()
        if trigger and content_lower == trigger:
            result_text = cc.get("result") or cc.get("response") or ""
            if result_text:
                try:
                    if cc.get("use_embed"):
                        e = discord.Embed(description=result_text, color=0x5b8cff)
                        await message.channel.send(embed=e)
                    else:
                        await message.channel.send(result_text)
                except Exception:
                    pass
            break

    # ── Sticky message logic ─────────────────────────────────────────────────
    stickies = _get("stickies") or {}
    cid = str(message.channel.id)
    if cid in stickies:
        sticky = stickies[cid]
        last_msg_id = sticky.get("last_msg_id")
        try:
            if last_msg_id:
                try:
                    old = await message.channel.fetch_message(int(last_msg_id))
                    await old.delete()
                except Exception:
                    pass
            sent = await message.channel.send(sticky["message"])
            stickies[cid]["last_msg_id"] = str(sent.id)
            _set("stickies", stickies)
        except Exception as e:
            print(f"[sticky] error in #{message.channel.name}: {e}")
    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------
@tasks.loop(seconds=30)
async def _check_announcements():
    """Post scheduled announcements whose time has come."""
    announcements = _get("announcements") or []
    now = datetime.now(timezone.utc)
    changed = False
    for ann in announcements:
        if ann.get("posted"):
            continue
        when = _parse_iso(ann.get("scheduled_for", ""))
        if when and now >= when:
            try:
                ch = bot.get_channel(int(ann["channel_id"])) or await bot.fetch_channel(int(ann["channel_id"]))
                color = int(ann.get("color", "0x5b8cff").lstrip("#"), 16) if ann.get("color") else 0x5b8cff
                e = discord.Embed(
                    title=ann.get("title") or "📣 Announcement",
                    description=ann["message"],
                    color=color,
                    timestamp=now,
                )
                if ann.get("image_url"):
                    e.set_image(url=ann["image_url"])
                e.set_footer(text="NVD Official • ")
                await ch.send(embed=e)
                ann["posted"] = True
                ann["posted_at"] = _now_iso()
                changed = True
                print(f"[NVD Official] Posted announcement {ann['id']} to channel {ann['channel_id']}")
            except Exception as ex:
                print(f"[NVD Official] Announcement send error: {ex}")
    if changed:
        _set("announcements", announcements)


@tasks.loop(seconds=60)
async def _check_reminders():
    """Fire reminders when due."""
    reminders = _get("reminders") or []
    now = datetime.now(timezone.utc)
    remaining = []
    for r in reminders:
        when = _parse_iso(r.get("due_at", ""))
        if when and now >= when:
            try:
                ch = bot.get_channel(int(r["channel_id"])) or await bot.fetch_channel(int(r["channel_id"]))
                e = discord.Embed(title="⏰ Reminder", description=r["message"], color=0xf1c40f, timestamp=now)
                await ch.send(embed=e)
            except Exception as ex:
                print(f"[reminder] fire error: {ex}")
        else:
            remaining.append(r)
    if len(remaining) != len(reminders):
        _set("reminders", remaining)


@tasks.loop(minutes=10)
async def _sticky_watcher():
    """Periodic no-op; sticky logic is in on_message."""
    pass


# ---------------------------------------------------------------------------
# Public API — called by dashboard.py
# ---------------------------------------------------------------------------

def is_running() -> bool:
    return bot.is_ready() if bot else False


# ── Growth / analytics tracking ──────────────────────────────────────────────
def _record_join(guild_id: str):
    key = f"growth_{guild_id}"
    data = _get(key) or {"joins": [], "leaves": [], "messages": []}
    data.setdefault("joins", []).append(_now_iso())
    data["joins"] = data["joins"][-500:]  # keep last 500
    _set(key, data)

def _record_leave(guild_id: str):
    key = f"growth_{guild_id}"
    data = _get(key) or {"joins": [], "leaves": [], "messages": []}
    data.setdefault("leaves", []).append(_now_iso())
    data["leaves"] = data["leaves"][-500:]
    _set(key, data)

def _record_message(guild_id: str):
    key = f"growth_{guild_id}"
    data = _get(key) or {"joins": [], "leaves": [], "messages": []}
    today = datetime.now(timezone.utc).date().isoformat()
    msgs = data.setdefault("messages", [])
    if msgs and msgs[-1].get("date") == today:
        msgs[-1]["count"] = msgs[-1].get("count", 0) + 1
    else:
        msgs.append({"date": today, "count": 1})
    data["messages"] = msgs[-90:]  # 90 days
    _set(key, data)


def get_analytics(guild_id: Optional[str] = None) -> dict:
    if guild_id:
        key = f"growth_{guild_id}"
        data = _get(key) or {}
        g = bot.get_guild(int(guild_id)) if guild_id and bot.is_ready() else None
        return {
            "guild_id": guild_id,
            "guild_name": g.name if g else guild_id,
            "member_count": g.member_count if g else 0,
            "joins_total": len(data.get("joins", [])),
            "leaves_total": len(data.get("leaves", [])),
            "messages_today": (data.get("messages") or [{}])[-1].get("count", 0) if data.get("messages") else 0,
        }
    # Aggregate across all guilds
    result = []
    if bot.is_ready():
        for g in bot.guilds:
            result.append(get_analytics(str(g.id)))
    return result


def get_server_growth(guild_id: Optional[str], days: int = 30) -> dict:
    key = f"growth_{guild_id or 'global'}"
    data = _get(key) or {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

    joins = [j for j in data.get("joins", []) if j[:10] >= cutoff]
    leaves = [l for l in data.get("leaves", []) if l[:10] >= cutoff]
    messages = [m for m in data.get("messages", []) if m.get("date", "") >= cutoff]

    net = len(joins) - len(leaves)
    g = None
    if guild_id and bot.is_ready():
        try:
            g = bot.get_guild(int(guild_id))
        except Exception:
            pass

    total = g.member_count if g else 0
    growth_pct = round((net / max(total - net, 1)) * 100, 1) if total else 0
    health = min(100, max(0, 50 + growth_pct))

    chart = []
    for m in messages[-days:]:
        chart.append({"date": m["date"], "messages": m.get("count", 0)})

    return {
        "guild_id": guild_id,
        "days": days,
        "joins": len(joins),
        "leaves": len(leaves),
        "net_growth": net,
        "growth_pct": growth_pct,
        "health_score": round(health),
        "member_count": total,
        "chart": chart,
    }


def get_full_overview(guild_id: Optional[str] = None) -> dict:
    announcements = _get("announcements") or []
    giveaways = _get("giveaways") or []
    templates = _get("templates") or []
    polls = _get("polls") or []
    reminders = _get("reminders") or []
    stickies = _get("stickies") or {}
    goals = _get("goals") or []
    bot_actions = _get("bot_actions") or []

    def _pending(items):
        return [i for i in items if not i.get("posted") and not i.get("ended")]

    return {
        "scheduled_count":      len(_pending(announcements)),
        "active_giveaways":     len([g for g in giveaways if not g.get("ended")]),
        "templates_count":      len(templates),
        "polls_active":         len([p for p in polls if not p.get("ended")]),
        "reminders_pending":    len(reminders),
        "stickies":             len(stickies),
        "goals_count":          len(goals),
        "backups_count":        0,
        "custom_commands_count":0,
        "external_hooks":       0,
        "incoming_webhooks":    0,
        "logs_count":           len(bot_actions),
        "bot_actions_count":    len(bot_actions),
    }


# ── Guild config ──────────────────────────────────────────────────────────────
def get_guild_config(guild_id: str) -> dict:
    configs = _get("guild_configs") or {}
    return configs.get(str(guild_id), {
        "guild_id": guild_id,
        "log_channel_id": "",
        "welcome_channel_id": "",
        "welcome_message": "Welcome {user} to {server}!",
        "display_name": "",
        "announcement_channel_id": "",
        "prefix": "so!",
    })


def update_guild_config(guild_id: str, updates: dict) -> dict:
    configs = _get("guild_configs") or {}
    gid = str(guild_id)
    cfg = configs.get(gid, {"guild_id": gid})
    cfg.update({k: v for k, v in updates.items() if v is not None})
    configs[gid] = cfg
    _set("guild_configs", configs)
    return cfg


def set_guild_bot_nickname(guild_id: str, nickname: str) -> dict:
    if not bot.is_ready():
        return {"ok": False, "discord_status": "Bot not ready"}
    loop = getattr(bot, "loop", None)
    if not loop:
        return {"ok": False, "discord_status": "No event loop"}

    async def _do():
        try:
            g = bot.get_guild(int(guild_id)) or await bot.fetch_guild(int(guild_id))
            me = g.get_member(bot.user.id) or await g.fetch_member(bot.user.id)
            await me.edit(nick=nickname or None)
            return {"ok": True, "discord_status": "Nickname updated"}
        except discord.Forbidden:
            return {"ok": False, "discord_status": "Missing permission to change nickname"}
        except Exception as e:
            return {"ok": False, "discord_status": str(e)}

    fut = asyncio.run_coroutine_threadsafe(_do(), loop)
    try:
        return fut.result(timeout=10)
    except Exception as e:
        return {"ok": False, "discord_status": str(e)}


# ── Bot actions log ───────────────────────────────────────────────────────────
def add_bot_action(action_type: str, description: str, actor: str = "system") -> None:
    actions = _get("bot_actions") or []
    actions.append({
        "id": str(uuid.uuid4()),
        "type": action_type,
        "description": description,
        "actor": actor,
        "ts": _now_iso(),
    })
    actions = actions[-200:]  # keep last 200
    _set("bot_actions", actions)


# ── Scheduled announcements ───────────────────────────────────────────────────
def list_scheduled_announcements(include_posted: bool = False) -> list:
    items = _get("announcements") or []
    if not include_posted:
        items = [i for i in items if not i.get("posted")]
    return sorted(items, key=lambda x: x.get("scheduled_for", ""))


def add_scheduled_announcement(
    channel_id: str, message: str, scheduled_for: str,
    title: Optional[str] = None, guild_id: Optional[str] = None,
    color: Optional[str] = None, image_url: Optional[str] = None,
) -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "channel_id": channel_id,
        "guild_id": guild_id or "",
        "title": title or "📣 Announcement",
        "message": message,
        "scheduled_for": scheduled_for,
        "color": color or "#5b8cff",
        "image_url": image_url or "",
        "posted": False,
        "created_at": _now_iso(),
    }
    items = _get("announcements") or []
    items.append(item)
    _set("announcements", items)
    return item


def delete_scheduled_announcement(ann_id: str) -> bool:
    items = _get("announcements") or []
    new = [i for i in items if i.get("id") != ann_id]
    if len(new) == len(items):
        return False
    _set("announcements", new)
    return True


def save_announcement_image(data: bytes, ext: str) -> dict:
    static_dir = os.path.join(_DATA_DIR, "static", "so_uploads")
    os.makedirs(static_dir, exist_ok=True)
    fname = f"{uuid.uuid4()}.{ext.lstrip('.')}"
    fpath = os.path.join(static_dir, fname)
    try:
        with open(fpath, "wb") as f:
            f.write(data)
        url = f"/static/so_uploads/{fname}"
        return {"ok": True, "url": url}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Templates ─────────────────────────────────────────────────────────────────
def list_templates() -> list:
    return _get("templates") or []


def add_template(guild_id: Optional[str], name: str, title: str, message: str, color: Optional[str] = None) -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "guild_id": guild_id or "",
        "name": name,
        "title": title,
        "message": message,
        "color": color or "#5b8cff",
        "created_at": _now_iso(),
    }
    items = _get("templates") or []
    items.append(item)
    _set("templates", items)
    return item


def delete_template(tid: str) -> bool:
    items = _get("templates") or []
    new = [i for i in items if i.get("id") != tid]
    _set("templates", new)
    return len(new) < len(items)


# ── Giveaways ─────────────────────────────────────────────────────────────────
def list_giveaways() -> list:
    return _get("giveaways") or []


# ── Polls ──────────────────────────────────────────────────────────────────────
def list_polls(guild_id: Optional[str] = None) -> list:
    items = _get("polls") or []
    if guild_id:
        items = [i for i in items if i.get("guild_id") == str(guild_id)]
    return items


# ── Reminders ─────────────────────────────────────────────────────────────────
def list_reminders() -> list:
    return _get("reminders") or []


def delete_reminder(rid: str) -> bool:
    items = _get("reminders") or []
    new = [i for i in items if i.get("id") != rid]
    _set("reminders", new)
    return len(new) < len(items)


# ── Stickies ──────────────────────────────────────────────────────────────────
def list_stickies() -> list:
    stickies = _get("stickies") or {}
    return list(stickies.values())


def set_sticky(channel_id: str, message: str, guild_id: Optional[str] = None) -> dict:
    stickies = _get("stickies") or {}
    item = {
        "channel_id": str(channel_id),
        "guild_id": guild_id or "",
        "message": message,
        "created_at": _now_iso(),
        "last_msg_id": None,
    }
    stickies[str(channel_id)] = item
    _set("stickies", stickies)
    return item


def remove_sticky(channel_id: str) -> bool:
    stickies = _get("stickies") or {}
    if str(channel_id) not in stickies:
        return False
    del stickies[str(channel_id)]
    _set("stickies", stickies)
    return True


# ── Admin notes ───────────────────────────────────────────────────────────────
def list_admin_notes(guild_id: str) -> list:
    notes = _get("admin_notes") or []
    return [n for n in notes if n.get("guild_id") == str(guild_id)]


def add_admin_note(guild_id: str, text: str, actor: str = "dashboard") -> dict:
    note = {
        "id": str(uuid.uuid4()),
        "guild_id": str(guild_id),
        "text": text,
        "actor": actor,
        "created_at": _now_iso(),
    }
    notes = _get("admin_notes") or []
    notes.append(note)
    _set("admin_notes", notes)
    return note


def delete_admin_note(guild_id: str, note_id: str) -> bool:
    notes = _get("admin_notes") or []
    new = [n for n in notes if not (n.get("id") == note_id and n.get("guild_id") == str(guild_id))]
    _set("admin_notes", new)
    return len(new) < len(notes)


# ── Goals ─────────────────────────────────────────────────────────────────────
def list_goals(guild_id: Optional[str] = None) -> list:
    items = _get("goals") or []
    if guild_id:
        items = [i for i in items if i.get("guild_id") == str(guild_id)]
    return items


def add_goal(guild_id: str, goal_type: str, target: int) -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "guild_id": str(guild_id),
        "type": goal_type,
        "target": target,
        "current": 0,
        "created_at": _now_iso(),
    }
    items = _get("goals") or []
    items.append(item)
    _set("goals", items)
    return item


def delete_goal(goal_id: str) -> bool:
    items = _get("goals") or []
    new = [i for i in items if i.get("id") != goal_id]
    _set("goals", new)
    return len(new) < len(items)


def get_server_logs(guild_id: Optional[str] = None) -> list:
    """Return stored action/event logs for a guild."""
    actions = _get("bot_actions") or []
    if guild_id:
        actions = [a for a in actions if a.get("guild_id") == str(guild_id)]
    return list(reversed(actions[-100:]))


def get_bot_action_logs() -> list:
    """Return the most recent bot action log entries."""
    return list(reversed((_get("bot_actions") or [])[-100:]))


# ── Custom commands ────────────────────────────────────────────────────────────
def list_custom_commands(guild_id: str) -> list:
    all_cc = _get("custom_commands") or {}
    return all_cc.get(str(guild_id), [])

def add_custom_command(guild_id: str, trigger: str, result: str,
                       description: str = "", use_embed: bool = False) -> dict:
    all_cc = _get("custom_commands") or {}
    gid = str(guild_id)
    cmds = all_cc.get(gid, [])
    if any(c.get("trigger", "").lower() == trigger.lower() for c in cmds):
        raise ValueError(f"Trigger '{trigger}' already exists.")
    item = {
        "id": str(uuid.uuid4()),
        "guild_id": gid,
        "trigger": trigger.lower(),
        "result": result,
        "description": description,
        "use_embed": use_embed,
        "created_at": _now_iso(),
    }
    cmds.append(item)
    all_cc[gid] = cmds
    _set("custom_commands", all_cc)
    return item

def remove_custom_command(guild_id: str, trigger: str) -> bool:
    all_cc = _get("custom_commands") or {}
    gid = str(guild_id)
    cmds = all_cc.get(gid, [])
    new = [c for c in cmds if c.get("trigger", "").lower() != trigger.lower()]
    if len(new) == len(cmds):
        return False
    all_cc[gid] = new
    _set("custom_commands", all_cc)
    return True


# ── Polls ──────────────────────────────────────────────────────────────────────
def add_poll(guild_id: str, channel_id: str, question: str,
             options: List[str], message_id: str = "") -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "message_id": str(message_id),
        "question": question,
        "options": options,
        "ended": False,
        "created_at": _now_iso(),
    }
    items = _get("polls") or []
    items.append(item)
    _set("polls", items)
    return item

def end_poll(poll_id: str) -> bool:
    items = _get("polls") or []
    for p in items:
        if p.get("id") == poll_id:
            p["ended"] = True
            _set("polls", items)
            return True
    return False


# ── Giveaways ──────────────────────────────────────────────────────────────────
def add_giveaway(guild_id: str, channel_id: str, prize: str,
                 winners: int, ends_at: str, host: str,
                 message_id: str = "") -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "message_id": str(message_id),
        "prize": prize,
        "winners": winners,
        "host": host,
        "ends_at": ends_at,
        "ended": False,
        "created_at": _now_iso(),
    }
    items = _get("giveaways") or []
    items.append(item)
    _set("giveaways", items)
    return item

def end_giveaway(giveaway_id: str) -> bool:
    items = _get("giveaways") or []
    for g in items:
        if g.get("id") == giveaway_id:
            g["ended"] = True
            _set("giveaways", items)
            return True
    return False


# ── Reminders (user-facing add) ────────────────────────────────────────────────
def add_reminder(channel_id: str, message: str, due_at: str,
                 guild_id: str = "", user_id: str = "") -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "channel_id": str(channel_id),
        "guild_id": str(guild_id),
        "user_id": str(user_id),
        "message": message,
        "due_at": due_at,
        "created_at": _now_iso(),
    }
    items = _get("reminders") or []
    items.append(item)
    _set("reminders", items)
    return item


# ── Incoming webhooks ──────────────────────────────────────────────────────────
def list_incoming_webhooks(guild_id: Optional[str] = None) -> list:
    items = _get("incoming_webhooks") or []
    if guild_id:
        items = [i for i in items if i.get("guild_id") == str(guild_id)]
    return items

def add_incoming_webhook(guild_id: str, name: str, channel_id: str) -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "guild_id": str(guild_id),
        "name": name,
        "channel_id": str(channel_id),
        "token": str(uuid.uuid4()).replace("-", ""),
        "created_at": _now_iso(),
    }
    items = _get("incoming_webhooks") or []
    items.append(item)
    _set("incoming_webhooks", items)
    return item

def delete_incoming_webhook(wid: str) -> bool:
    items = _get("incoming_webhooks") or []
    new = [i for i in items if i.get("id") != wid]
    _set("incoming_webhooks", new)
    return len(new) < len(items)


# ---------------------------------------------------------------------------
# Background task: giveaway auto-end
# ---------------------------------------------------------------------------
@tasks.loop(seconds=30)
async def _check_giveaways():
    """Automatically end giveaways when their time is up."""
    giveaways = _get("giveaways") or []
    now = datetime.now(timezone.utc)
    changed = False
    for gw in giveaways:
        if gw.get("ended"):
            continue
        ends_at = _parse_iso(gw.get("ends_at", ""))
        if not ends_at or now < ends_at:
            continue
        # Time's up — pick winners from reactions
        try:
            ch = bot.get_channel(int(gw["channel_id"])) or await bot.fetch_channel(int(gw["channel_id"]))
            msg = await ch.fetch_message(int(gw["message_id"]))
            reaction = discord.utils.get(msg.reactions, emoji="🎉")
            entries = []
            if reaction:
                async for user in reaction.users():
                    if not user.bot:
                        entries.append(user)
            import random
            num_winners = min(gw.get("winners", 1), len(entries))
            if num_winners > 0:
                winners = random.sample(entries, num_winners)
                winner_mentions = " ".join(w.mention for w in winners)
                e = discord.Embed(
                    title="🎊 Giveaway Ended!",
                    description=f"🏆 **Prize:** {gw['prize']}\n🥇 **Winner(s):** {winner_mentions}\n🎗️ **Hosted by:** {gw['host']}",
                    color=0x2ecc71,
                )
                e.set_footer(text="NVD Official • ")
                await ch.send(embed=e)
                await ch.send(f"🎉 Congratulations {winner_mentions}! You won **{gw['prize']}**!")
            else:
                await ch.send(embed=discord.Embed(
                    title="😔 Giveaway Ended — No Winner",
                    description=f"No valid entries for **{gw['prize']}**.",
                    color=0x95a5a6,
                ))
        except Exception as ex:
            print(f"[giveaway-end] error: {ex}")
        gw["ended"] = True
        changed = True
    if changed:
        _set("giveaways", giveaways)


# ---------------------------------------------------------------------------
# ALL DISCORD COMMANDS
# ---------------------------------------------------------------------------

_POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

def _is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member.guild_permissions.manage_guild


# ── /ping ─────────────────────────────────────────────────────────────────────
@bot.tree.command(name="ping", description="Check if NVD Official is online and responding.")
async def slash_ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    e = discord.Embed(
        title="🏓 Pong!",
        description=f"NVD Official is online.\n⚡ Latency: **{latency}ms**",
        color=0x2ecc71,
    )
    e.set_footer(text="NVD Official • ")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ── /help ─────────────────────────────────────────────────────────────────────
@bot.tree.command(name="help", description="Show all available NVD Official commands.")
async def slash_help(interaction: discord.Interaction):
    e = discord.Embed(
        title="📖 NVD Official — Commands",
        description="All slash commands available in this server.",
        color=0x5b8cff,
    )
    e.add_field(name="📣 Announcements", value="`/announce` — Post an announcement immediately", inline=False)
    e.add_field(name="📊 Polls", value="`/poll` — Create a reaction poll (up to 10 options)", inline=False)
    e.add_field(name="🎉 Giveaways", value="`/giveaway` — Start a giveaway with a timer", inline=False)
    e.add_field(name="⏰ Reminders", value="`/remind` — Set a timed reminder in a channel", inline=False)
    e.add_field(name="📌 Sticky Messages", value="`/sticky` — Pin a message to the bottom of a channel\n`/unsticky` — Remove the sticky from a channel", inline=False)
    e.add_field(name="⚙️ Custom Commands", value="`/cc add` — Add a custom command\n`/cc remove` — Remove a custom command\n`/cc list` — List all custom commands", inline=False)
    e.add_field(name="🛠️ Utilities", value="`/ping` — Check bot latency\n`/serverinfo` — Show server information", inline=False)
    e.set_footer(text="NVD Official •  | Prefix: so!")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ── /serverinfo ───────────────────────────────────────────────────────────────
@bot.tree.command(name="serverinfo", description="Show information about this server.")
async def slash_serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    if not g:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    bots  = sum(1 for m in g.members if m.bot)
    humans = g.member_count - bots if g.member_count else 0
    e = discord.Embed(title=f"📋 {g.name}", color=0x5b8cff)
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    e.add_field(name="👑 Owner", value=f"<@{g.owner_id}>", inline=True)
    e.add_field(name="👥 Members", value=f"{humans} humans • {bots} bots", inline=True)
    e.add_field(name="📁 Channels", value=f"{len(g.text_channels)} text • {len(g.voice_channels)} voice", inline=True)
    e.add_field(name="🏷️ Roles", value=str(len(g.roles)), inline=True)
    e.add_field(name="😀 Emojis", value=str(len(g.emojis)), inline=True)
    e.add_field(name="🔒 Verification", value=str(g.verification_level).title(), inline=True)
    e.add_field(name="📅 Created", value=f"<t:{int(g.created_at.timestamp())}:D>", inline=True)
    e.set_footer(text=f"Server ID: {g.id}  •  NVD Official")
    await interaction.response.send_message(embed=e)


# ── /announce ─────────────────────────────────────────────────────────────────
@bot.tree.command(name="announce", description="Post an announcement embed immediately.")
@discord.app_commands.describe(
    channel="Channel to post in",
    title="Announcement title",
    message="Announcement message",
    color="Embed color hex (e.g. #ff0000). Default: blue",
)
async def slash_announce(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    message: str,
    color: str = "#5b8cff",
):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    try:
        hex_color = int(color.lstrip("#"), 16)
    except Exception:
        hex_color = 0x5b8cff
    e = discord.Embed(
        title=title,
        description=message,
        color=hex_color,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text=f"Announced by {interaction.user}  •  NVD Official")
    await channel.send(embed=e)
    add_bot_action("announce", f"Announcement posted to #{channel.name}: {title}", str(interaction.user))
    await interaction.response.send_message(f"✅ Announcement posted in {channel.mention}!", ephemeral=True)


# ── /poll ─────────────────────────────────────────────────────────────────────
@bot.tree.command(name="poll", description="Create a reaction poll.")
@discord.app_commands.describe(
    question="The poll question",
    options="Comma-separated options (e.g. Yes, No, Maybe). Up to 10.",
    channel="Channel to post in (defaults to current channel)",
)
async def slash_poll(
    interaction: discord.Interaction,
    question: str,
    options: str,
    channel: Optional[discord.TextChannel] = None,
):
    ch = channel or interaction.channel
    opts = [o.strip() for o in options.split(",") if o.strip()][:10]
    if len(opts) < 2:
        await interaction.response.send_message("❌ Provide at least 2 comma-separated options.", ephemeral=True)
        return
    desc = "\n".join(f"{_POLL_EMOJIS[i]}  {opt}" for i, opt in enumerate(opts))
    e = discord.Embed(
        title=f"📊 {question}",
        description=desc,
        color=0x5b8cff,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text=f"Poll by {interaction.user}  •  React to vote!")
    await interaction.response.send_message("✅ Poll created!", ephemeral=True)
    sent = await ch.send(embed=e)
    for i in range(len(opts)):
        try:
            await sent.add_reaction(_POLL_EMOJIS[i])
        except Exception:
            pass
    add_poll(str(interaction.guild_id), str(ch.id), question, opts, message_id=str(sent.id))
    add_bot_action("poll", f"Poll created: {question}", str(interaction.user))


# ── /giveaway ─────────────────────────────────────────────────────────────────
@bot.tree.command(name="giveaway", description="Start a giveaway.")
@discord.app_commands.describe(
    prize="What are you giving away?",
    duration="Duration in minutes",
    winners="Number of winners (default 1)",
    channel="Channel to host the giveaway in (defaults to current)",
)
async def slash_giveaway(
    interaction: discord.Interaction,
    prize: str,
    duration: int,
    winners: int = 1,
    channel: Optional[discord.TextChannel] = None,
):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    if duration < 1:
        await interaction.response.send_message("❌ Duration must be at least 1 minute.", ephemeral=True)
        return
    ch = channel or interaction.channel
    ends_dt = datetime.now(timezone.utc) + timedelta(minutes=duration)
    ends_ts = int(ends_dt.timestamp())
    e = discord.Embed(
        title="🎉  G I V E A W A Y  🎉",
        description=(
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 **Prize:** {prize}\n"
            f"👑 **Winners:** {winners}\n"
            f"🎗️ **Hosted by:** {interaction.user.mention}\n"
            f"⏰ **Ends:** <t:{ends_ts}:R>  (<t:{ends_ts}:F>)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"React with 🎉 to enter! Good luck! 🍀"
        ),
        color=0xf5b400,
        timestamp=ends_dt,
    )
    e.set_footer(text="NVD Official • Giveaway — ends at")
    await interaction.response.send_message("✅ Giveaway started!", ephemeral=True)
    sent = await ch.send(embed=e)
    await sent.add_reaction("🎉")
    gw = add_giveaway(
        str(interaction.guild_id), str(ch.id), prize, winners,
        ends_dt.isoformat(), str(interaction.user), message_id=str(sent.id),
    )
    add_bot_action("giveaway", f"Giveaway started: {prize} ({duration}min)", str(interaction.user))


# ── /remind ───────────────────────────────────────────────────────────────────
@bot.tree.command(name="remind", description="Set a timed reminder in a channel.")
@discord.app_commands.describe(
    message="What to remind about",
    minutes="How many minutes from now",
    channel="Channel to send the reminder (defaults to current)",
)
async def slash_remind(
    interaction: discord.Interaction,
    message: str,
    minutes: int,
    channel: Optional[discord.TextChannel] = None,
):
    if minutes < 1:
        await interaction.response.send_message("❌ Minutes must be at least 1.", ephemeral=True)
        return
    ch = channel or interaction.channel
    due = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    add_reminder(str(ch.id), message, due.isoformat(),
                 guild_id=str(interaction.guild_id), user_id=str(interaction.user.id))
    await interaction.response.send_message(
        f"✅ Reminder set! I'll remind you in **{minutes} minute(s)** in {ch.mention}.", ephemeral=True
    )
    add_bot_action("reminder", f"Reminder set: {message} in {minutes}min", str(interaction.user))


# ── /sticky ───────────────────────────────────────────────────────────────────
@bot.tree.command(name="sticky", description="Set a sticky message in a channel (re-posts after every new message).")
@discord.app_commands.describe(
    message="Message to keep pinned at the bottom",
    channel="Channel to apply sticky to (defaults to current)",
)
async def slash_sticky(
    interaction: discord.Interaction,
    message: str,
    channel: Optional[discord.TextChannel] = None,
):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    ch = channel or interaction.channel
    set_sticky(str(ch.id), message, guild_id=str(interaction.guild_id))
    await interaction.response.send_message(f"✅ Sticky message set in {ch.mention}.", ephemeral=True)
    add_bot_action("sticky_set", f"Sticky set in #{ch.name}", str(interaction.user))


@bot.tree.command(name="unsticky", description="Remove the sticky message from a channel.")
@discord.app_commands.describe(channel="Channel to remove sticky from (defaults to current)")
async def slash_unsticky(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    ch = channel or interaction.channel
    removed = remove_sticky(str(ch.id))
    if removed:
        await interaction.response.send_message(f"✅ Sticky removed from {ch.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ No sticky found in {ch.mention}.", ephemeral=True)


# ── /cc (custom commands) ─────────────────────────────────────────────────────
cc_group = discord.app_commands.Group(name="cc", description="Manage custom commands for this server.")

@cc_group.command(name="add", description="Add a custom command that triggers on an exact message.")
@discord.app_commands.describe(trigger="The exact message that triggers this command", response="What the bot replies")
async def cc_add(interaction: discord.Interaction, trigger: str, response: str):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    try:
        add_custom_command(str(interaction.guild_id), trigger, result=response)
        await interaction.response.send_message(
            f"✅ Custom command added!\n**Trigger:** `{trigger}`\n**Response:** {response}", ephemeral=True
        )
    except ValueError as err:
        await interaction.response.send_message(f"❌ {err}", ephemeral=True)

@cc_group.command(name="remove", description="Remove a custom command by its trigger word.")
@discord.app_commands.describe(trigger="The trigger of the command to remove")
async def cc_remove(interaction: discord.Interaction, trigger: str):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return
    removed = remove_custom_command(str(interaction.guild_id), trigger)
    if removed:
        await interaction.response.send_message(f"✅ Removed custom command: `{trigger}`", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ No custom command found with trigger `{trigger}`.", ephemeral=True)

@cc_group.command(name="list", description="List all custom commands in this server.")
async def cc_list(interaction: discord.Interaction):
    cmds = list_custom_commands(str(interaction.guild_id))
    if not cmds:
        await interaction.response.send_message("No custom commands set up yet.", ephemeral=True)
        return
    lines = [f"`{c['trigger']}` → {c.get('result') or c.get('response', '')}" for c in cmds[:25]]
    e = discord.Embed(title="⚙️ Custom Commands", description="\n".join(lines), color=0x5b8cff)
    e.set_footer(text=f"{len(cmds)} command(s)  •  NVD Official")
    await interaction.response.send_message(embed=e, ephemeral=True)

bot.tree.add_command(cc_group)


# ── Prefix commands (so!) ─────────────────────────────────────────────────────

@bot.command(name="ping")
async def prefix_ping(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(embed=discord.Embed(
        title="🏓 Pong!",
        description=f"NVD Official is online.\n⚡ Latency: **{latency}ms**",
        color=0x2ecc71,
    ))

@bot.command(name="help")
async def prefix_help(ctx):
    e = discord.Embed(title="📖 NVD Official — Commands", color=0x5b8cff)
    e.add_field(name="Prefix", value="`so!`", inline=False)
    e.add_field(name="Slash commands", value="`/ping` `/help` `/announce` `/poll` `/giveaway` `/remind` `/sticky` `/unsticky` `/cc` `/serverinfo`", inline=False)
    e.add_field(name="Prefix commands", value="`so!ping` `so!help` `so!serverinfo`", inline=False)
    e.set_footer(text="NVD Official • ")
    await ctx.send(embed=e)

@bot.command(name="serverinfo")
async def prefix_serverinfo(ctx):
    g = ctx.guild
    bots  = sum(1 for m in g.members if m.bot)
    humans = g.member_count - bots
    e = discord.Embed(title=f"📋 {g.name}", color=0x5b8cff)
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    e.add_field(name="👑 Owner", value=f"<@{g.owner_id}>", inline=True)
    e.add_field(name="👥 Members", value=f"{humans} humans • {bots} bots", inline=True)
    e.add_field(name="📁 Channels", value=f"{len(g.text_channels)} text • {len(g.voice_channels)} voice", inline=True)
    e.add_field(name="📅 Created", value=f"<t:{int(g.created_at.timestamp())}:D>", inline=True)
    e.set_footer(text=f"Server ID: {g.id}  •  NVD Official")
    await ctx.send(embed=e)


# ---------------------------------------------------------------------------
# Bot start / stop
# ---------------------------------------------------------------------------
def run_bot():
    if not TOKEN:
        print("[NVD Official] SERVER_TOKEN not set — bot will not start.")
        return
    retry_delay = 5
    while True:
        print("[NVD Official] Connecting to Discord...")
        try:
            bot.run(TOKEN, reconnect=True)
        except discord.errors.LoginFailure:
            print("[NVD Official] ❌ Invalid SERVER_TOKEN.")
            break
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print("[NVD Official] Rate limited — retrying in 60s")
                time.sleep(60)
            else:
                print(f"[NVD Official] HTTP error: {e} — retrying in {retry_delay}s")
                time.sleep(retry_delay)
        except Exception as e:
            print(f"[NVD Official] Crashed: {e} — retrying in {retry_delay}s")
            time.sleep(retry_delay)


def start_bot_thread() -> bool:
    global _bot_thread
    if not TOKEN:
        return False
    if _bot_thread and _bot_thread.is_alive():
        return False
    _bot_thread = threading.Thread(target=run_bot, daemon=True, name="server-official-bot")
    _bot_thread.start()
    print("[NVD Official] Bot thread started.")
    return True
