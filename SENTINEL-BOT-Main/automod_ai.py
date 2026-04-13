import asyncio
import os
import json
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")

async def handle_message_ai(message, add_warning):
    """
    Uses Gemini moderation to detect bad messages.
    Deletes message and issues warning if violation detected.
    """
    try:
        if not GEMINI_KEY:
            return
        prompt = (
            "You are a Discord moderation classifier. Flag abusive profanity, sexual profanity, "
            "harassment, hate, threats, or Bangla/English slurs. Return only JSON with flagged and reason.\n\n"
            f"Message: {message.content}"
        )
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=8
        )
        if not response.ok:
            return
        text = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            result = json.loads(cleaned)
        except Exception:
            result = {"flagged": '"flagged": true' in text.lower(), "reason": text[:120]}

        if result.get("flagged"):
            await message.delete()
            await add_warning(message.author, reason=f"Gemini Moderation: {result.get('reason', 'violation')}")
            await message.channel.send(
                f"⚠️ {message.author.mention}, your message was removed by AI moderation."
            )

    except Exception as e:
        print(f"AI moderation error: {e}