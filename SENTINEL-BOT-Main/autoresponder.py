import json
import os
import re
import time
import requests

AUTORESPONDER_FILE = "autoresponder.json"
_TABLE = "sentinel_autoresponder"

GEMINI_KEY = os.environ.get("GEMINI_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")

# Per-channel cooldown so the AI doesn't fire on every single short message.
_AI_COOLDOWN = {}            # channel_id -> last_timestamp
_AI_COOLDOWN_SECONDS = 20    # min seconds between AI generations per channel

# Skip generating for these (already-handled or noise)
_AI_SKIP_PREFIXES = ("!", "/", "?", ".", "$", "-", "+", ">", "<")


def _get_supabase():
    try:
        from dashboard import supabase
        return supabase
    except Exception:
        return None


# ── Local JSON fallback ──────────────────────────────────────────────────────

def _local_load():
    if os.path.exists(AUTORESPONDER_FILE):
        try:
            with open(AUTORESPONDER_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {"responses": []}
    return {"responses": []}


def _local_save(data):
    tmp = AUTORESPONDER_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, AUTORESPONDER_FILE)


def _row_to_dict(row):
    """Map Supabase row → {trigger, response, match} expected elsewhere."""
    if not row:
        return None
    return {
        "trigger":  row.get("trigger", ""),
        "response": row.get("response", ""),
        "match":    row.get("match_type", "contains"),
    }


# ── Public API ───────────────────────────────────────────────────────────────

def load_responses():
    sb = _get_supabase()
    if sb:
        try:
            res = sb.table(_TABLE).select("*").order("id", desc=False).execute()
            return {"responses": [_row_to_dict(r) for r in (res.data or [])]}
        except Exception as e:
            print(f"[AR] Supabase load error: {e}")
    return _local_load()


def save_responses(data):
    """Local-only JSON save (fallback)."""
    _local_save(data)


def add_response(trigger, response, match_type="contains"):
    """Add or update a trigger. Returns True if new, False if updated."""
    sb = _get_supabase()
    trigger_lower = trigger.lower()
    if sb:
        try:
            existing = sb.table(_TABLE).select("id").eq("trigger", trigger_lower).execute()
            if existing.data:
                sb.table(_TABLE).update({
                    "response":   response,
                    "match_type": match_type,
                }).eq("trigger", trigger_lower).execute()
                return False
            sb.table(_TABLE).insert({
                "trigger":    trigger_lower,
                "response":   response,
                "match_type": match_type,
            }).execute()
            return True
        except Exception as e:
            print(f"[AR] Supabase add error: {e}")

    # fallback
    data = _local_load()
    for r in data["responses"]:
        if r["trigger"].lower() == trigger_lower:
            r["response"] = response
            r["match"] = match_type
            _local_save(data)
            return False
    data["responses"].append({
        "trigger":  trigger_lower,
        "response": response,
        "match":    match_type,
    })
    _local_save(data)
    return True


def remove_response(trigger):
    sb = _get_supabase()
    trigger_lower = trigger.lower()
    if sb:
        try:
            res = sb.table(_TABLE).delete().eq("trigger", trigger_lower).execute()
            return bool(res.data)
        except Exception as e:
            print(f"[AR] Supabase remove error: {e}")
    data = _local_load()
    before = len(data["responses"])
    data["responses"] = [r for r in data["responses"] if r["trigger"].lower() != trigger_lower]
    _local_save(data)
    return len(data["responses"]) < before


def get_all_responses():
    return load_responses().get("responses", [])


def _is_learnable(message):
    """Decide whether this message is a candidate for AI auto-learn."""
    content = (message.content or "").strip()
    if not content:
        return False
    if content.startswith(_AI_SKIP_PREFIXES):
        return False
    if len(content) > 30:                       # only short utterances
        return False
    if len(content.split()) > 4:                # max 4 words
        return False
    if message.attachments or message.mentions: # not media / pings
        return False
    if "http://" in content or "https://" in content:
        return False
    return True


def _ai_generate_reply(trigger):
    """
    Ask Gemini for a short, friendly Discord reply for `trigger`.
    Returns a string, or None on failure / no API key.
    """
    if not GEMINI_KEY:
        return None
    prompt = (
        "You are SP4RK, a friendly Discord bot. A user just sent the message below. "
        "Reply with ONE short, warm, natural reply (max 12 words). "
        "Do NOT use quotes. Do NOT explain. Just the reply text.\n\n"
        f"Message: {trigger}"
    )
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.8, "maxOutputTokens": 60},
            },
            timeout=8,
        )
        if not r.ok:
            return None
        text = (
            r.json()
            .get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        ).strip().strip('"').strip("'")
        # Trim to one line
        text = text.splitlines()[0].strip() if text else ""
        if not text:
            return None
        # Cap length so Gemini can't return paragraphs
        return text[:200]
    except Exception as e:
        print(f"[AR] AI generate error: {e}")
        return None


async def check_message(message):
    data = load_responses()
    content = (message.content or "").lower().strip()
    if not content:
        return False

    # 1. Try existing rules first
    for rule in data.get("responses", []):
        trigger = rule["trigger"].lower()
        match_type = rule.get("match", "contains")
        matched = False
        if match_type == "exact":
            matched = content == trigger
        elif match_type == "startswith":
            matched = content.startswith(trigger)
        elif match_type == "regex":
            try:
                matched = bool(re.search(trigger, content))
            except Exception:
                pass
        else:
            matched = trigger in content
        if matched:
            response = (
                rule["response"]
                .replace("{user}", message.author.mention)
                .replace("{username}", message.author.display_name)
            )
            await message.channel.send(response)
            return True

    # No rule matched — do not auto-generate AI replies for unmatched messages.
    # AI auto-learn was removed because it caused the bot to send random words
    # (e.g. "oh", "hey there", "hi") in response to any short message.
    return False
