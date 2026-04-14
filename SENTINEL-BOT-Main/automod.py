import discord
import requests
import re
import time
import unicodedata
from collections import defaultdict, deque

# --- Profanity word lists by level ---
PROFANITY_MEDIUM = ["damn", "crap", "hell", "ass", "piss"]
PROFANITY_HIGH   = PROFANITY_MEDIUM + ["shit", "bitch", "bastard", "cunt", "fuck", "dick", "cock", "pussy"]
STRICT_PROFANITY = [
    "fuck", "fucker", "fuckyou", "motherfucker", "mf", "shit", "bitch", "bastard", "cunt",
    "dick", "cock", "pussy", "sex", "s!x", "slut", "whore",
    "madari", "madarchod", "madarchud", "maderchod", "maderchud", "madar chod", "madar chud",
    "mader chod", "mader chud", "maadar chod", "maadar chud", "chod", "chud", "choda",
    "chudi", "chutia", "chutiya", "chutmarani", "bhenchod", "behenchod", "banchod",
    "shala", "sala", "shalay", "salar", "harami", "kutta", "kutti",
    "bokachoda", "bokachuda", "khankir", "khanki", "magi",
    "চুদ", "চোদ", "চুদি", "চোদা", "মাদারচোদ", "মাদারচুদ", "মাগী", "খানকির", "বোকাচোদা",
    "শালা", "সালা", "হারামি", "কুত্তা", "কুত্তি"
]

# --- Leet-speak / symbol substitutions ---
_LEET = {'@': 'a', '0': 'o', '1': 'i', '3': 'e', '$': 's',
         '4': 'a', '5': 's', '!': 'i', '+': 't', '7': 't', '9': 'g', '*': ''}

# --- Per-user spam tracking: {guild_id: {user_id: deque of timestamps}} ---
_spam_tracker     = defaultdict(lambda: defaultdict(deque))
_duplicate_cache  = defaultdict(lambda: defaultdict(str))   # last message per user

def _normalize(text):
    t = unicodedata.normalize("NFKC", text).lower()
    for k, v in _LEET.items():
        t = t.replace(k, v)
    t = t.replace("!", "i")
    return t

def _compact(text):
    return re.sub(r'[^a-z0-9\u0980-\u09ff]+', '', _normalize(text))

def _contains_word(content, word):
    escaped = re.escape(word)
    # 1. Whole-word direct match (e.g. "hell" won't match "hello")
    if re.search(r'\b' + escaped + r'\b', content):
        return True
    # 2. Punct separators stripped, whole-word (e.g. "f.u.c.k" → "fuck")
    stripped_punct = re.sub(r'[.\-_*~]', '', content)
    if re.search(r'\b' + escaped + r'\b', stripped_punct):
        return True
    # 3. Space separators stripped, whole-word (e.g. "f u c k" → "fuck")
    #    Only trigger if original tokens form exactly the bad word letters
    tokens = content.split()
    if len(tokens) == len(word) and ''.join(tokens) == word:
        return True
    # 4. All separators stripped for mixed obfuscation (e.g. "f. u. c. k")
    #    Only flag if stripped result IS exactly the bad word (avoids "hello"→"hello" matching "hell")
    stripped_all = re.sub(r'[.\-_*~\s]', '', content)
    if stripped_all == word:
        return True
    return False

def _message_contains_bad_word(content, word_list):
    normalized = _normalize(content)
    return any(_contains_word(normalized, w) for w in word_list)

def _message_contains_strict_profanity(content):
    normalized = _normalize(content)
    compact = _compact(content)
    for word in STRICT_PROFANITY:
        target = _compact(word)
        if not target:
            continue
        if _contains_word(normalized, word.lower()) or target in compact:
            return True
    if re.search(r'\bs[\W_]*[!1i|e3][\W_]*x\b', content, re.IGNORECASE):
        return True
    return False

def _is_zalgo(text):
    """Detect zalgo/unicode abuse — excessive combining characters."""
    combining = sum(1 for c in text if unicodedata.category(c) == 'Mn')
    return combining > 10

def _emoji_count(text):
    count = 0
    for c in text:
        cp = ord(c)
        if 0x1F300 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF:
            count += 1
    count += len(re.findall(r'<a?:\w+:\d+>', text))
    return count

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
            f"⚠️ Sentinel Alert: Chill. That behavior ain't it. Fix it before this escalates."
        )
    except Exception:
        pass

async def apply_graduated_punishment(message, config, count, add_warning_fn):
    """Apply escalating punishments based on warning count."""
    if config.get("grad_punish") != "ON":
        return

    member = message.author
    guild  = message.guild

    try:
        if count >= 10:
            await member.ban(reason=f"Sentinel: Auto-ban ({count} warnings)")
            await message.channel.send(
                f"⛔ {member.mention} has been **banned** after accumulating {count} warnings.",
                delete_after=10
            )
        elif count >= 8:
            await member.kick(reason=f"Sentinel: Auto-kick ({count} warnings)")
            await message.channel.send(
                f"👢 {member.mention} has been **kicked** after {count} warnings.",
                delete_after=10
            )
        elif count >= 5:
            duration = discord.utils.utcnow() + discord.timedelta(hours=1)
            await member.timeout(duration, reason=f"Sentinel: Auto-timeout ({count} warnings)")
            await message.channel.send(
                f"⏱️ {member.mention} has been **timed out for 1 hour** ({count} warnings).",
                delete_after=10
            )
        elif count >= 3:
            duration = discord.utils.utcnow() + discord.timedelta(minutes=10)
            await member.timeout(duration, reason=f"Sentinel: Auto-timeout ({count} warnings)")
            await message.channel.send(
                f"⏱️ {member.mention} has been **timed out for 10 minutes** ({count} warnings).",
                delete_after=10
            )
    except discord.Forbidden:
        pass
    except Exception as e:
        print(f"Graduated punishment error: {e}")

async def check_spam(message, config):
    """Returns True if user is spamming (too many messages too fast)."""
    if config.get("spam_filter") != "ON":
        return False

    threshold = int(config.get("spam_threshold", 5))
    window    = int(config.get("spam_window", 5))

    guild_id  = message.guild.id
    user_id   = message.author.id
    now       = time.time()

    q = _spam_tracker[guild_id][user_id]
    q.append(now)
    while q and now - q[0] > window:
        q.popleft()

    return len(q) >= threshold

async def check_caps(message, config):
    """Returns True if message has excessive caps."""
    if config.get("caps_filter") != "ON":
        return False
    content = message.content
    if len(content) < 8:
        return False
    letters = [c for c in content if c.isalpha()]
    if len(letters) < 8:
        return False
    caps_pct = int(config.get("caps_percent", 70))
    upper_count = sum(1 for c in letters if c.isupper())
    return (upper_count / len(letters)) * 100 >= caps_pct

async def check_emoji_spam(message, config):
    """Returns True if message has too many emojis."""
    if config.get("emoji_filter") != "ON":
        return False
    limit = int(config.get("emoji_limit", 8))
    return _emoji_count(message.content) >= limit

async def check_duplicate(message, config):
    """Returns True if user is repeating the same message."""
    if config.get("duplicate_filter") != "ON":
        return False
    content = message.content.strip().lower()
    if len(content) < 5:
        return False
    guild_id = message.guild.id
    user_id  = message.author.id
    last     = _duplicate_cache[guild_id][user_id]
    _duplicate_cache[guild_id][user_id] = content
    return last == content

async def check_wall_of_text(message, config):
    """Returns True if message contains excessive newlines or is extremely long."""
    if config.get("wall_filter") != "ON":
        return False
    content = message.content
    newlines = content.count('\n')
    if newlines >= int(config.get("wall_newlines", 10)):
        return True
    if len(content) >= int(config.get("wall_length", 1000)):
        return True
    return False

async def check_zalgo(message, config):
    """Returns True if message contains zalgo text."""
    if config.get("anti_zalgo") != "ON":
        return False
    return _is_zalgo(message.content)

async def handle_message(message, config, add_warning_fn=None):
    """
    Returns True if the message was handled (deleted/warned), False otherwise.
    add_warning_fn: async callable(member, reason) -> new warning count
    """
    content = message.content
    author  = message.author
    channel = message.channel

    async def _punish(reason, log_title, log_desc, dm=True):
        await message.delete()
        if dm and config.get("auto_dm_warning") == "ON":
            await auto_dm_warning_user(author, reason, message.guild.name)
        await channel.send(
            f"⚠️ {author.mention} Warning issued. Don't test system limits.", delete_after=7
        )
        count = None
        if add_warning_fn:
            count = await add_warning_fn(author, reason)
        await send_webhook_log(config, log_title, log_desc)
        if count and config.get("grad_punish") == "ON":
            await apply_graduated_punishment(message, config, count, add_warning_fn)
        return True

    # 1. Custom blacklist + banned words
    all_bad = (config.get("banned_words", "") or "") + "," + (config.get("auto_custom_blacklist", "") or "")
    if all_bad.strip(","):
        words = [w.strip().lower() for w in all_bad.split(",") if w.strip()]
        if _message_contains_bad_word(content, words):
            return await _punish(
                "Blacklisted word",
                "🚫 Blacklisted Word Detected",
                f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}\n**Reason:** Banned/blacklisted word"
            )

    # 2. Always-on strict profanity filter
    if _message_contains_strict_profanity(content):
        return await _punish(
            "Strict profanity",
            "🤬 Strict Profanity Detected",
            f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}\n**Reason:** Bangla/English abusive profanity"
        )

    # 3. Profanity filter
    level = config.get("profanity_level", "low")
    profanity_list = PROFANITY_HIGH if level == "high" else (PROFANITY_MEDIUM if level == "medium" else [])
    if profanity_list and _message_contains_bad_word(content, profanity_list):
        return await _punish(
            "Profanity",
            "🤬 Profanity Detected",
            f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}\n**Level:** {level}"
        )

    # 4. Block Discord invites
    if config.get("auto_block_invites") == "ON":
        if "discord.gg/" in content.lower() or "discord.com/invite/" in content.lower():
            return await _punish(
                "Discord invite",
                "🚫 Discord Invite Blocked",
                f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}"
            )

    # 5. Block links
    if config.get("auto_block_links") == "ON" or config.get("mod_block_links") == "ON":
        if re.search(r'https?://\S+', content):
            return await _punish(
                "Link blocked",
                "🔗 Link Blocked",
                f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}"
            )

    # 6. Block mass mentions
    if config.get("auto_block_mentions") == "ON" or config.get("mod_block_mentions") == "ON":
        if len(message.mentions) > 3 or message.mention_everyone:
            return await _punish(
                "Mass mentions",
                "🔔 Mass Mention Blocked",
                f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}\n**Mentions:** {len(message.mentions)}"
            )

    # 7. Spam detection
    if await check_spam(message, config):
        return await _punish(
            "Message spam",
            "💬 Spam Detected",
            f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}\n**Threshold:** {config.get('spam_threshold', 5)} msgs/{config.get('spam_window', 5)}s"
        )

    # 8. Caps filter
    if await check_caps(message, config):
        return await _punish(
            "Excessive caps",
            "🔠 Caps Spam Detected",
            f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}\n**Threshold:** {config.get('caps_percent', 70)}%"
        )

    # 9. Emoji spam
    if await check_emoji_spam(message, config):
        return await _punish(
            "Emoji spam",
            "😂 Emoji Spam Detected",
            f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}\n**Emojis:** {_emoji_count(content)}"
        )

    # 10. Duplicate messages
    if await check_duplicate(message, config):
        return await _punish(
            "Duplicate message",
            "🔁 Duplicate Message",
            f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}"
        )

    # 11. Wall of text
    if await check_wall_of_text(message, config):
        return await _punish(
            "Wall of text",
            "📜 Wall of Text Detected",
            f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}\n**Length:** {len(content)} chars"
        )

    # 12. Zalgo text
    if await check_zalgo(message, config):
        return await _punish(
            "Zalgo/unicode abuse",
            "⚠️ Zalgo Text Detected",
            f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}"
        )

    return False
