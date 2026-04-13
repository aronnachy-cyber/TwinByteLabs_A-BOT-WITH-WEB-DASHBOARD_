import openai
import asyncio
import os

openai.api_key = os.environ.get("OPENAI_KEY", "")

async def handle_message_ai(message, add_warning):
    """
    Uses OpenAI moderation to detect bad messages.
    Deletes message and issues warning if violation detected.
    """
    try:
        response = openai.Moderation.create(
            input=message.content
        )
        result = response["results"][0]

        if result["flagged"]:
            await message.delete()
            await add_warning(message.author, reason="AI Moderation Violation")
            await message.channel.send(
                f"⚠️ {message.author.mention}, your message was removed by AI moderation."
            )

    except Exception as e:
        print(f"AI moderation error: {e}