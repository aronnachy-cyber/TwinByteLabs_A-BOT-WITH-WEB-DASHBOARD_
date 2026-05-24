"""
Shared custom-commands module used by both SP4RK (main.py) and Server Official
(server_official.py).

Schema (per bot, JSON file):
    {
        "<guild_id>": {
            "<trigger>": {
                "description": str,
                "result":      str,
                "use_embed":   bool,
                "created_at":  int  (unix ts)
            }
        }
    }

For SP4RK we use a single global "guild" key ("*") because the SP4RK dashboard
is global (no per-guild picker yet). For Server Official we use real guild ids,
because the SO dashboard has a server picker.

Supported placeholders in result text (replaced before sending / before AI):
    {user}               → @mention of the triggering user
    {user.name}          → display name
    {user.tag}           → full username (e.g. "joe" or "joe#1234")
    {user.id}            → user ID
    {channel}            → #channel mention
    {channel.name}       → channel name
    {channel.id}         → channel ID
    {server}             → server name  (alias: {server.name})
    {server.id}          → server ID
    {server.membercount} → current member count
    {prefix}             → server command prefix
    {args}               → text the user typed after the trigger word
    {args.upper}         → args uppercased
    {args.lower}         → args lowercased
    {args.title}         → args title-cased
"""
import json
import os
import time
from typing import Dict, List, Optional, Any

import requests

_FILES = {
    "sp4rk": "custom_commands_sp4rk.json",
    "so":    "custom_commands_so.json",
}

GLOBAL_GID = "*"

_AI_COOLDOWN: Dict[int, float] = {}
_AI_COOLDOWN_SECONDS = 5


# ── Storage ───────────────────────────────────────────────────────────────────

def _path(bot: str) -> str:
    return _FILES.get(bot, _FILES["sp4rk"])


def _load(bot: str) -> dict:
    try:
        import db_spark as _ds
        if _ds.is_available():
            val = _ds.get_blob("custom_commands", bot)
            if val is not None:
                return val
    except Exception:
        pass
    p = _path(bot)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save(bot: str, data: dict) -> None:
    try:
        import db_spark as _ds
        if _ds.is_available():
            _ds.set_blob("custom_commands", data, key=bot)
    except Exception:
        pass
    p = _path(bot)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, p)
    except Exception:
        pass


def _normalise_trigger(t: str) -> str:
    t = (t or "").strip().lower()
    if t.startswith("!"):
        t = t[1:]
    return t


def _coerce_payload(raw: Any) -> Dict[str, Any]:
    """Accept either legacy bare-string format or new dict format."""
    if isinstance(raw, str):
        return {"description": "", "result": raw, "use_embed": False, "created_at": None}
    if isinstance(raw, dict):
        return {
            "description": str(raw.get("description") or "").strip(),
            "result":      str(raw.get("result") or raw.get("response") or "").strip(),
            "use_embed":   bool(raw.get("use_embed", False)),
            "created_at":  raw.get("created_at"),
        }
    return {"description": "", "result": "", "use_embed": False, "created_at": None}


# ── Public CRUD ───────────────────────────────────────────────────────────────

def list_commands(bot: str, guild_id: str) -> List[Dict[str, Any]]:
    data = _load(bot)
    cmds = data.get(str(guild_id), {})
    out = []
    for trig, raw in cmds.items():
        payload = _coerce_payload(raw)
        out.append({"trigger": trig, **payload})
    out.sort(key=lambda x: x["trigger"])
    return out


def get_command(bot: str, guild_id: str, trigger: str) -> Optional[Dict[str, Any]]:
    data = _load(bot)
    trig = _normalise_trigger(trigger)
    cmds = data.get(str(guild_id), {})
    raw = cmds.get(trig)
    if raw is None:
        return None
    payload = _coerce_payload(raw)
    return {"trigger": trig, **payload}


def add_command(bot: str, guild_id: str, trigger: str, description: str = "",
                result: str = "", use_embed: bool = False) -> Dict[str, Any]:
    trig = _normalise_trigger(trigger)
    if not trig:
        raise ValueError("trigger required")
    if not (result or "").strip():
        raise ValueError("result required")

    data = _load(bot)
    gid = str(guild_id)
    cmds = data.setdefault(gid, {})
    cmds[trig] = {
        "description": (description or "").strip(),
        "result":      result.strip(),
        "use_embed":   bool(use_embed),
        "created_at":  int(time.time()),
    }
    _save(bot, data)
    return {"trigger": trig, **cmds[trig]}


def remove_command(bot: str, guild_id: str, trigger: str) -> bool:
    data = _load(bot)
    gid = str(guild_id)
    trig = _normalise_trigger(trigger)
    cmds = data.get(gid, {})
    if trig in cmds:
        cmds.pop(trig)
        _save(bot, data)
        return True
    return False


# ── Placeholder substitution ──────────────────────────────────────────────────

def substitute_placeholders(text: str, message, prefix: str = "!", args: str = "") -> str:
    """
    Replace {placeholder} tokens in `text` with real values from `message`.

    Supports all documented placeholders. Unknown/unmatchable tokens are left as-is
    so admins can notice a typo rather than silently getting blank output.
    """
    if not text:
        return text

    guild = getattr(message, "guild", None)
    author = getattr(message, "author", None)
    channel = getattr(message, "channel", None)

    def _safe(attr, fallback=""):
        try:
            return str(attr) if attr is not None else fallback
        except Exception:
            return fallback

    replacements = {
        # ── User ──────────────────────────────────────────────────────────
        "{user}":               _safe(getattr(author, "mention", None), "@user"),
        "{user.mention}":       _safe(getattr(author, "mention", None), "@user"),
        "{user.name}":          _safe(getattr(author, "display_name", None) or getattr(author, "name", None), "user"),
        "{user.username}":      _safe(getattr(author, "name", None), "user"),
        "{user.tag}":           _safe(author, "user"),
        "{user.id}":            _safe(getattr(author, "id", None), "0"),
        # ── Channel ───────────────────────────────────────────────────────
        "{channel}":            _safe(getattr(channel, "mention", None), "#channel"),
        "{channel.mention}":    _safe(getattr(channel, "mention", None), "#channel"),
        "{channel.name}":       _safe(getattr(channel, "name", None), "channel"),
        "{channel.id}":         _safe(getattr(channel, "id", None), "0"),
        # ── Server ────────────────────────────────────────────────────────
        "{server}":             _safe(getattr(guild, "name", None), "server"),
        "{server.name}":        _safe(getattr(guild, "name", None), "server"),
        "{server.id}":          _safe(getattr(guild, "id", None), "0"),
        "{server.membercount}": _safe(getattr(guild, "member_count", None), "?"),
        "{membercount}":        _safe(getattr(guild, "member_count", None), "?"),
        # ── Prefix ────────────────────────────────────────────────────────
        "{prefix}":             prefix,
        # ── Args (text after the trigger word) ────────────────────────────
        "{args}":               args,
        "{args.upper}":         args.upper(),
        "{args.lower}":         args.lower(),
        "{args.title}":         args.title(),
    }

    for token, value in replacements.items():
        text = text.replace(token, value)

    return text


# ── Matching ──────────────────────────────────────────────────────────────────

def match_message(bot: str, guild_id: Optional[str], content: str):
    """
    Inspect `content` and return the matching custom command (if any).
    Also returns the `args` string (text after the trigger word).

    Returns (cmd_dict, args_str) or (None, "").
    """
    if not content:
        return None, ""
    text = content.strip()
    if not text.startswith("!") or len(text) < 2:
        return None, ""
    parts = text[1:].split(maxsplit=1)
    word = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    if not word:
        return None, ""

    if guild_id is not None:
        cmd = get_command(bot, str(guild_id), word)
        if cmd:
            return cmd, args

    if bot == "sp4rk":
        cmd = get_command(bot, GLOBAL_GID, word)
        if cmd:
            return cmd, args

    return None, ""


def cooldown_ok(channel_id: int) -> bool:
    """Per-channel rate limit guard so AI can't be flood-triggered."""
    now = time.time()
    last = _AI_COOLDOWN.get(channel_id, 0)
    if now - last < _AI_COOLDOWN_SECONDS:
        return False
    _AI_COOLDOWN[channel_id] = now
    return True
