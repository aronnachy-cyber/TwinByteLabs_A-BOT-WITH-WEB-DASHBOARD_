import asyncio
import os
import json
import aiohttp
from permissions import safe_delete

GEMINI_KEY = os.environ.get("GEMINI_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")

async def handle_message_ai(message, add_warning):
    """
    Uses Gemini moderation to detect bad messages.
    Deletes message and issues warning if violation detected.
    Async (non-blocking) HTTP — so a slow Gemini response can't freeze the bot heartbeat.
    """
    try:
        if not GEMINI_KEY:
            return
        prompt = (
            "You are a strict Discord moderation classifier for Bangla, Banglish, Hindi/Urdu slang, and English. "
            "Read the whole message and decide if it is abusive slang/profanity or normal conversation. "
            "Flag abusive words, sexual profanity, harassment, hate, threats, and obfuscated spellings such as f*uck, fu ck, s!x, madarchod, maderchud, shala, sala. "
            "Return only valid JSON with flagged and reason.\n\n"
            f"Message: {message.content}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}
        }
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
        )
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status >= 400:
                    return
                body = await resp.json()
        text = body.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        if "{" in cleaned and "}" in cleaned:
            cleaned = cleaned[cleaned.find("{"):cleaned.rfind("}") + 1]
        try:
            result = json.loads(cleaned)
        except Exception:
            result = {"flagged": '"flagged": true' in text.lower(), "reason": text[:120]}

        if result.get("flagged"):
            await safe_delete(message)
            await add_warning(message.author, reason=f"Gemini Moderation: {result.get('reason', 'violation')}")
            await message.channel.send(
                f"⚠️ {message.author.mention}, your message was removed by AI moderation."
            )

    except asyncio.TimeoutError:
        print("AI moderation timeout (8s) — skipping message.")
    except Exception as e:
        print(f"AI moderation error: {e}")
