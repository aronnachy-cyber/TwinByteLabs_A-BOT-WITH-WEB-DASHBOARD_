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

A small Gemini wrapper (`ai_format_response`) is exposed so both bots can share
the same AI formatting pass — it takes the admin's stored "result" text plus
optional description/user-message context and returns a polished Discord reply.
"""
import json
import os
import time
from typing import Dict, List, Optional, Any

import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")

_FILES = {
    "sp4rk": "custom_commands_sp4rk.json",
    "so":    "custom_commands_so.json",
}

GLOBAL_GID = "*"

_AI_COOLDOWN: Dict[int, float] = {}
_AI_COOLDOWN_SECONDS = 5  # short — explicit triggers, but stops spam-flood of API


# ── Storage ──────────────────────────────────────────────────────────────────

def _path(bot: str) -> str:
    return _FILES.get(bot, _FILES["sp4rk"])


def _load(bot: str) -> dict:
    p = _path(bot)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save(bot: str, data: dict) -> None:
    p = _path(bot)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, p)


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


# ── Public CRUD ──────────────────────────────────────────────────────────────

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


# ── Matching ─────────────────────────────────────────────────────────────────

def match_message(bot: str, guild_id: Optional[str], content: str) -> Optional[Dict[str, Any]]:
    """
    Inspect `content` and return the matching custom command (if any).

    Triggers fire when a message starts with `!<trigger>` (case-insensitive).
    For SP4RK we also fall back to global (`*`) commands because the SP4RK
    dashboard doesn't yet expose per-guild custom commands.
    """
    if not content:
        return None
    text = content.strip()
    if not text.startswith("!") or len(text) < 2:
        return None
    word = text[1:].split(maxsplit=1)[0].lower()
    if not word:
        return None

    if guild_id is not None:
        cmd = get_command(bot, str(guild_id), word)
        if cmd:
            return cmd

    if bot == "sp4rk":
        cmd = get_command(bot, GLOBAL_GID, word)
        if cmd:
            return cmd

    return None


# ── AI formatter (Gemini) ────────────────────────────────────────────────────

def ai_format_response(description: str, result: str, user_message: str = "") -> Optional[str]:
    """
    Ask Gemini to polish `result` into a natural Discord reply, taking the admin's
    description and the user's actual message into account.

    Returns the formatted text, or None if Gemini is unavailable / fails.
    """
    if not GEMINI_KEY:
        return None
    desc = (description or "").strip()
    res = (result or "").strip()
    if not res:
        return None

    prompt = (
        "You are a helpful Discord bot. A server admin has set up a custom "
        "command and provided you with a reference answer to share when a user "
        "triggers it.\n\n"
        f"ADMIN NOTE / DESCRIPTION:\n{desc or '(none provided)'}\n\n"
        f"REFERENCE ANSWER (must be conveyed faithfully):\n{res}\n\n"
        f"USER MESSAGE THAT TRIGGERED THE COMMAND:\n{user_message or '(none)'}\n\n"
        "Rewrite the reference answer as a friendly, clear Discord reply for the user. "
        "Keep it concise (max ~120 words). Preserve every fact and instruction from the reference. "
        "Do NOT invent new information. Do NOT wrap the whole reply in quotes. "
        "Plain Discord-friendly text — light markdown (bold/lists) is fine, no headings."
    )
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": 320},
            },
            timeout=10,
        )
        if not r.ok:
            return None
        text = (
            r.json()
            .get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        ).strip()
        # Strip surrounding quotes if Gemini wrapped the whole reply
        if len(text) > 2 and text[0] == text[-1] and text[0] in ('"', "'"):
            text = text[1:-1].strip()
        return text or None
    except Exception as e:
        print(f"[CC] AI format error: {e}")
        return None


def cooldown_ok(channel_id: int) -> bool:
    """Per-channel rate limit guard so the AI can't be flood-triggered."""
    now = time.time()
    last = _AI_COOLDOWN.get(channel_id, 0)
    if now - last < _AI_COOLDOWN_SECONDS:
        return False
    _AI_COOLDOWN[channel_id] = now
    return True
