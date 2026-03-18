import openai
import asyncio

# --- Use your OpenAI key ---
openai.api_key = "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"

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