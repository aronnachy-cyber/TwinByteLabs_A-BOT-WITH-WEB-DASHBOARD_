import discord
import requests
import re

# --- Profanity word lists by level ---
PROFANITY_MEDIUM = ["damn", "crap", "hell", "ass", "piss"]
PROFANITY_HIGH   = PROFANITY_MEDIUM + ["shit", "bitch", "bastard", "cunt", "fuck", "dick", "cock", "pussy"]

# --- Leet-speak / symbol substitutions ---
_LEET = {'@': 'a', '0': 'o', '1': 'i', '3': 'e', '$': 's',
         '4': 'a', '5': 's', '!': 'i', '+': 't', '7': 't', '9': 'g'}

def _normalize(text):
    """Lowercase + replace common leet/symbol substitutions."""
    t = text.lower()
    for k, v in _LEET.items():
        t = t.replace(k, v)
    return t

def _contains_word(content, word):
    """
    Check if a bad word appears in content, accounting for obfuscation:
      - Direct match:          'fuck'    in 'you fuck off'
      - Punct separators:      'fuck'    in 'f.u.c.k' / 'f-u-c-k' / 'f_u_c_k' / 'f*u*c*k'
      - Space separators:      'fuck'    in 'f u c k'
      - Mixed separators:      'fuck'    in 'f. u. c. k'
    """
    # 1. Direct match (already lowercased + leet-subbed)
    if word in content:
        return True
    # 2. Strip all punctuation-style separators between letters: . - _ * ~
    stripped_punct = re.sub(r'[.\-_*~]', '', content)
    if word in stripped_punct:
        return True
    # 3. Strip all whitespace (catches 'f u c k', 'f  u  c  k')
    stripped_spaces = re.sub(r'\s+', '', content)
    if word in stripped_spaces:
        return True
    # 4. Strip both punctuation AND spaces together (catches 'f. u. c. k')
    stripped_all = re.sub(r'[.\-_*~\s]', '', content)
    if word in stripped_all:
        return True
    return False

def _message_contains_bad_word(content, word_list):
    normalized = _normalize(content)
    return any(_contains_word(normalized, w) for w in word_list)

async def send_webhook_log(config, title, description, color=0xff6b6b):
    webhook_url = config.get("webhook_log", "").strip()
    if not webhook_url or not webhook_url.startswith("http"):
        return
    try:
        payload = {
            "embeds": [{
                "title": title,
                "description": description,
                "color": color
            }]
        }
        requests.post(webhook_url, json=payload, timeout=3)
    except Exception as e:
        print(f"Webhook log error: {e}")

async def auto_dm_warning_user(member, reason, guild_name):
    try:
        await member.send(
            f"⚠️ **Sentinel OS — Warning**\n"
            f"You received a warning in **{guild_name}**.\n"
            f"**Reason:** {reason}"
        )
    except Exception:
        pass

async def handle_message(message, config):
    """Returns True if the message was handled (deleted/warned), False otherwise."""
    content = message.content

    # 1. Custom blacklist + banned words
    all_bad = (config.get("banned_words", "") or "") + "," + (config.get("auto_custom_blacklist", "") or "")
    if all_bad.strip(","):
        words = [w.strip().lower() for w in all_bad.split(",") if w.strip()]
        if _message_contains_bad_word(content, words):
            await message.delete()
            if config.get("auto_dm_warning") == "ON":
                await auto_dm_warning_user(message.author, "Inappropriate language", message.guild.name)
            await message.channel.send(
                f"⚠️ {message.author.mention}, that word is not allowed here.", delete_after=7
            )
            await send_webhook_log(
                config, "🚫 Blacklisted Word Detected",
                f"**User:** {message.author} ({message.author.id})\n"
                f"**Channel:** {message.channel.mention}\n"
                f"**Reason:** Blacklisted/banned word"
            )
            return True

    # 2. Profanity filter by level
    level = config.get("profanity_level", "low")
    if level == "medium":
        profanity_list = PROFANITY_MEDIUM
    elif level == "high":
        profanity_list = PROFANITY_HIGH
    else:
        profanity_list = []

    if profanity_list and _message_contains_bad_word(content, profanity_list):
        await message.delete()
        if config.get("auto_dm_warning") == "ON":
            await auto_dm_warning_user(message.author, "Profanity is not allowed", message.guild.name)
        await message.channel.send(
            f"🤬 {message.author.mention}, profanity is not allowed here.", delete_after=7
        )
        await send_webhook_log(
            config, "🤬 Profanity Detected",
            f"**User:** {message.author} ({message.author.id})\n"
            f"**Channel:** {message.channel.mention}\n"
            f"**Level:** {level}"
        )
        return True

    # 3. Block Discord invites
    if config.get("auto_block_invites") == "ON":
        if "discord.gg/" in content.lower() or "discord.com/invite/" in content.lower():
            await message.delete()
            if config.get("auto_dm_warning") == "ON":
                await auto_dm_warning_user(message.author, "Posting Discord invites is not allowed", message.guild.name)
            await message.channel.send("🚫 Discord invites are not allowed!", delete_after=5)
            await send_webhook_log(
                config, "🚫 Discord Invite Blocked",
                f"**User:** {message.author} ({message.author.id})\n"
                f"**Channel:** {message.channel.mention}"
            )
            return True

    # 4. Block links
    if config.get("auto_block_links") == "ON" or config.get("mod_block_links") == "ON":
        if re.search(r'https?://\S+', content):
            await message.delete()
            if config.get("auto_dm_warning") == "ON":
                await auto_dm_warning_user(message.author, "Posting links is not allowed", message.guild.name)
            await message.channel.send("🔗 Links are blocked here!", delete_after=5)
            await send_webhook_log(
                config, "🔗 Link Blocked",
                f"**User:** {message.author} ({message.author.id})\n"
                f"**Channel:** {message.channel.mention}"
            )
            return True

    # 5. Block mass mentions
    if config.get("auto_block_mentions") == "ON" or config.get("mod_block_mentions") == "ON":
        if len(message.mentions) > 3 or message.mention_everyone:
            await message.delete()
            if config.get("auto_dm_warning") == "ON":
                await auto_dm_warning_user(message.author, "Mass mentions are not allowed", message.guild.name)
            await message.channel.send("🔔 Mass mentions are not allowed!", delete_after=5)
            await send_webhook_log(
                config, "🔔 Mass Mention Blocked",
                f"**User:** {message.author} ({message.author.id})\n"
                f"**Channel:** {message.channel.mention}\n"
                f"**Mentions:** {len(message.mentions)}"
            )
            return True

    return False
