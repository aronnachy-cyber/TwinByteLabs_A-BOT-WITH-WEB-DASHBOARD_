import asyncio
import discord
import requests
import re
import time
import unicodedata
from collections import defaultdict, deque, Counter
from datetime import timedelta
from permissions import safe_delete

# How long an auto-mute lasts (minutes) for repeated-word spam / flood spam.
AUTOMUTE_MINUTES = 30
# How many times any single word must appear in one message before we mute.
REPEAT_WORD_THRESHOLD = 4
# Always-on rate limit (messages within window) → auto-mute.
RATE_LIMIT_COUNT  = 5
RATE_LIMIT_WINDOW = 5  # seconds

# Tracks users we've already auto-muted recently so we don't double-mute.
_recent_mutes = {}  # (guild_id, user_id) -> timestamp

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

def _dedupe(text):
    """Collapse consecutive repeated characters: 'fffffuuuucccckkkk' → 'fuck'."""
    return re.sub(r'(.)\1+', r'\1', text)

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
    # 5. Collapse consecutive repeated characters (e.g. "fffffuuuucccckkkk" → "fuck")
    deduped = _dedupe(content)
    if re.search(r'\b' + escaped + r'\b', deduped):
        return True
    # 6. Dedupe after stripping punctuation (e.g. "f..f..f..u..u..c..c..k..k" → "fuck")
    deduped_stripped = _dedupe(re.sub(r'[.\-_*~\s]', '', content))
    if deduped_stripped == word:
        return True
    return False

def _message_contains_bad_word(content, word_list):
    normalized = _normalize(content)
    return any(_contains_word(normalized, w) for w in word_list)

def _message_contains_strict_profanity(content):
    normalized = _normalize(content)
    compact = _compact(content)
    compact_deduped = _dedupe(compact)
    for word in STRICT_PROFANITY:
        target = _compact(word)
        if not target:
            continue
        if _contains_word(normalized, word.lower()) or target in compact or target in compact_deduped:
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
    """Send a log entry to a Discord webhook without blocking the event loop.

    Uses aiohttp instead of the sync `requests` library — the previous
    implementation could freeze the bot for up to 3 seconds per log entry
    when the webhook host was slow.
    """
    webhook_url = config.get("webhook_log", "").strip()
    if not webhook_url or not webhook_url.startswith("http"):
        return
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
        }]
    }
    try:
        import aiohttp
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=3)
        ) as session:
            await session.post(webhook_url, json=payload)
    except Exception as e:
        print(f"Webhook log error: {e}")

def _has_repeated_word_spam(content, threshold=REPEAT_WORD_THRESHOLD):
    """
    Detect a single message where the same word is repeated `threshold`+ times
    (e.g. "fuck fuck fuck fuck fuck"). Catches both consecutive runs
    ("a a a a a") and total-count repeats ("a b a c a d a e a").
    """
    if not content:
        return False
    tokens = re.findall(r'\S+', _normalize(content))
    if len(tokens) < threshold:
        return False
    # Consecutive run check
    run = 1
    for i in range(1, len(tokens)):
        if tokens[i] == tokens[i - 1]:
            run += 1
            if run >= threshold:
                return True
        else:
            run = 1
    # Total-count check
    counts = Counter(tokens)
    if counts and counts.most_common(1)[0][1] >= threshold:
        return True
    return False


def can_bot_action(target, bot_member):
    """Return (allowed: bool, reason: str). True only when SP4RK can safely act on target.

    Blocks: self, bots, server owner, members with admin/manage_guild perms (staff),
    and anyone whose top role is >= the bot's top role (Discord hierarchy).
    """
    try:
        if target is None or bot_member is None:
            return (False, "missing member context")
        if target.id == bot_member.id:
            return (False, "target is the bot itself")
        if getattr(target, "bot", False):
            return (False, "target is a bot")
        guild = getattr(target, "guild", None)
        if guild and guild.owner_id == target.id:
            return (False, "target is the server owner")
        perms = getattr(target, "guild_permissions", None)
        if perms and (perms.administrator or perms.manage_guild):
            return (False, "target is staff (Administrator / Manage Server)")
        if hasattr(target, "top_role") and hasattr(bot_member, "top_role"):
            if target.top_role >= bot_member.top_role:
                return (False, "target's role is equal to or higher than the bot's")
        return (True, "")
    except Exception as e:
        return (False, f"hierarchy check failed: {e}")


async def _instant_mute(message, reason, minutes=AUTOMUTE_MINUTES):
    """
    Auto-mute (timeout) the user for `minutes`. NO owner verification.
    Deletes the offending message and posts a short notice in-channel.
    """
    member = message.author
    guild = message.guild
    guild_id = guild.id if guild else 0
    user_id  = member.id

    # Hierarchy guard — never try to action staff or higher-role members.
    bot_member = guild.me if guild else None
    allowed, why = can_bot_action(member, bot_member)
    if not allowed:
        # Don't delete or warn — the user outranks the bot, leave their message alone.
        return False

    # Don't double-mute the same person within 30 seconds
    now = time.time()
    last = _recent_mutes.get((guild_id, user_id), 0)
    if now - last < 30:
        await safe_delete(message)
        return True
    _recent_mutes[(guild_id, user_id)] = now

    await safe_delete(message)

    try:
        until = discord.utils.utcnow() + timedelta(minutes=minutes)
        await member.timeout(until, reason=f"SP4RK auto-mute: {reason}")
        try:
            await message.channel.send(
                f"🔇 {member.mention} auto-muted for **{minutes} min** — {reason}",
                delete_after=10,
            )
        except Exception:
            pass
    except discord.Forbidden:
        try:
            await message.channel.send(
                f"⚠️ Could not auto-mute {member.mention} — missing permissions or hierarchy.",
                delete_after=8,
            )
        except Exception:
            pass
    except Exception as e:
        print(f"[automod] auto-mute error: {e}")
    return True




async def auto_dm_warning_user(member, reason, guild_name, count=None):
    """DM the offending user their warning when auto_dm_warning is ON in the dashboard."""
    try:
        text = (
            f"⚠️ **Warning"
            + (f" #{count}" if count else "")
            + f"** — Your message in **{guild_name}** was removed.\n"
            f"> **Reason:** {reason}\n"
            f"Please follow the server rules to avoid further action."
        )
        await member.send(text)
    except Exception:
        pass  # DMs may be closed — in-channel warning is the fallback.

async def apply_graduated_punishment(message, config, count, add_warning_fn):
    """Apply escalating punishments based on warning count.

    Two independent escalation modes can drive auto-actions; both are OFF by
    default so the bot does nothing extra unless the server explicitly opts in.

    1) `automod_on` (NEW master toggle, configured from the dashboard).
       Uses the simple chart the user requested:
           5 warns  → 5-minute mute
          10 warns  → 10-minute mute
          20 warns  → 30-minute tempban (real ban + scheduled unban)
          30 warns  → kick
       This takes precedence over `grad_punish` when both are ON.

    2) `grad_punish` (legacy graduated chart). Preserved for backward
       compatibility — only runs when `automod_on` is OFF.

    When BOTH are OFF the function is a no-op, which matches today's
    behaviour exactly (per the user's "nothing will be changed" requirement).
    """
    automod_on  = config.get("automod_on")  == "ON"
    legacy_on   = config.get("grad_punish") == "ON"
    if not (automod_on or legacy_on):
        return

    member = message.author
    guild  = message.guild

    # Hierarchy guard — never escalate against staff or higher-role members.
    bot_member = guild.me if guild else None
    allowed, why = can_bot_action(member, bot_member)
    if not allowed:
        await send_webhook_log(
            config,
            "🛡️ Escalation Skipped (Protected)",
            f"**User:** {member} ({member.id})\n**Warnings:** {count}\n**Why:** {why}",
        )
        return

    # ---- NEW Automod chart (takes precedence) ----
    if automod_on:
        try:
            if count >= 30:
                await member.kick(reason=f"SP4RK Automod: Auto-kick ({count} warnings)")
                await message.channel.send(
                    f"👢 {member.mention} has been **kicked** after {count} warnings.",
                    delete_after=10,
                )
                await send_webhook_log(
                    config, "👢 Automod Auto-Kick",
                    f"**User:** {member} ({member.id})\n**Warnings:** {count}",
                )
            elif count >= 20:
                # 30-minute tempban: real ban + scheduled unban via background task.
                await member.ban(
                    reason=f"SP4RK Automod: 30-min tempban ({count} warnings)",
                    delete_message_days=0,
                )
                await message.channel.send(
                    f"⛔ {member.mention} has been **temp-banned for 30 minutes** ({count} warnings).",
                    delete_after=10,
                )
                await send_webhook_log(
                    config, "⛔ Automod Tempban (30 min)",
                    f"**User:** {member} ({member.id})\n**Warnings:** {count}",
                )

                async def _unban_later(g, m_id, mention):
                    try:
                        await asyncio.sleep(30 * 60)
                        await g.unban(
                            discord.Object(id=m_id),
                            reason="SP4RK Automod: 30-min tempban expired",
                        )
                        await send_webhook_log(
                            config, "✅ Automod Tempban Expired",
                            f"**User ID:** {m_id} — auto-unbanned after 30 minutes.",
                        )
                    except Exception as e:
                        print(f"[automod] tempban auto-unban failed: {e}")

                asyncio.create_task(_unban_later(guild, member.id, member.mention))
            elif count >= 10:
                duration = discord.utils.utcnow() + timedelta(minutes=10)
                await member.timeout(duration, reason=f"SP4RK Automod: 10-min mute ({count} warnings)")
                await message.channel.send(
                    f"⏱️ {member.mention} has been **muted for 10 minutes** ({count} warnings).",
                    delete_after=10,
                )
                await send_webhook_log(
                    config, "⏱️ Automod Mute (10 min)",
                    f"**User:** {member} ({member.id})\n**Warnings:** {count}",
                )
            elif count >= 5:
                duration = discord.utils.utcnow() + timedelta(minutes=5)
                await member.timeout(duration, reason=f"SP4RK Automod: 5-min mute ({count} warnings)")
                await message.channel.send(
                    f"⏱️ {member.mention} has been **muted for 5 minutes** ({count} warnings).",
                    delete_after=10,
                )
                await send_webhook_log(
                    config, "⏱️ Automod Mute (5 min)",
                    f"**User:** {member} ({member.id})\n**Warnings:** {count}",
                )
        except discord.Forbidden:
            await send_webhook_log(
                config, "⚠️ Automod Action Failed (Forbidden)",
                f"**User:** {member} ({member.id})\n**Warnings:** {count}\n"
                f"SP4RK lacks the required permission. Check role hierarchy.",
            )
        except Exception as e:
            print(f"[automod] auto-action error: {e}")
        return  # NEW chart handled — don't also run the legacy one.

    # ---- LEGACY grad_punish chart (only when automod_on is OFF) ----
    try:
        if count >= 10:
            await member.ban(reason=f"SP4RK: Auto-ban ({count} warnings)")
            await message.channel.send(
                f"⛔ {member.mention} has been **banned** after accumulating {count} warnings.",
                delete_after=10
            )
        elif count >= 8:
            await member.kick(reason=f"SP4RK: Auto-kick ({count} warnings)")
            await message.channel.send(
                f"👢 {member.mention} has been **kicked** after {count} warnings.",
                delete_after=10
            )
        elif count >= 5:
            duration = discord.utils.utcnow() + timedelta(hours=1)
            await member.timeout(duration, reason=f"SP4RK: Auto-timeout ({count} warnings)")
            await message.channel.send(
                f"⏱️ {member.mention} has been **timed out for 1 hour** ({count} warnings).",
                delete_after=10
            )
        elif count >= 3:
            duration = discord.utils.utcnow() + timedelta(minutes=10)
            await member.timeout(duration, reason=f"SP4RK: Auto-timeout ({count} warnings)")
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

    # Allowlist — words listed here are NEVER treated as banned, even if
    # they appear in the strict profanity list or any blacklist (false-positive guard).
    allowlist_raw = (config.get("auto_allowlist", "") or "")
    allowlist = [w.strip().lower() for w in allowlist_raw.split(",") if w.strip()]
    if allowlist:
        normalized = _normalize(content)
        if any(_contains_word(normalized, w) for w in allowlist):
            return False  # message is exempt — bail out before any punishment

    # Hierarchy check — determines whether SP4RK can warn/punish this user.
    # We calculate this upfront but do NOT bail out yet: the always-on content
    # filters (blacklist + strict profanity) must still delete forbidden words
    # even in servers where the bot's role sits below normal member roles.
    bot_member = message.guild.me if message.guild else None
    _allowed_root, _why_root = can_bot_action(author, bot_member)

    async def _punish(reason, log_title, log_desc, dm=True):
        # Always delete the offending message.
        await safe_delete(message)

        # Always add a warning entry regardless of role hierarchy, so the
        # count is tracked even for staff/higher-role members.
        count = None
        if add_warning_fn:
            try:
                count = await add_warning_fn(author, reason)
            except Exception as e:
                print(f"[automod] add_warning_fn failed: {e}")

        # Always send an in-channel notification so the user and channel know
        # why the message was removed — even if the bot can't formally punish them.
        if not _allowed_root:
            # Bot cannot mute/kick/ban a higher-ranked user, but we still
            # notify the channel and log the warning.
            try:
                warn_text = f"⚠️ {author.mention} your message was removed — **{reason}**."
                if count:
                    warn_text += f" (Warning **#{count}**)"
                await channel.send(warn_text, delete_after=15)
            except Exception as e:
                print(f"[automod] in-channel notify failed: {e}")
            if count and config.get("auto_dm_warning") == "ON":
                await auto_dm_warning_user(
                    author, reason,
                    message.guild.name if message.guild else "the server",
                    count=count,
                )
            await send_webhook_log(config, log_title, log_desc)
            return True

        await send_webhook_log(config, log_title, log_desc)

        # In-channel warning to the offending member.
        try:
            warn_text = (
                f"⚠️ {author.mention} your message was removed — **{reason}**."
            )
            if count:
                warn_text += f" (Warning **#{count}**)"
            await channel.send(warn_text, delete_after=15)
        except Exception as e:
            print(f"[automod] in-channel warn failed: {e}")

        # DM the user if the auto_dm_warning toggle is ON in the dashboard.
        if count and config.get("auto_dm_warning") == "ON":
            await auto_dm_warning_user(
                author, reason,
                message.guild.name if message.guild else "the server",
                count=count,
            )

        if count and (config.get("automod_on") == "ON" or config.get("grad_punish") == "ON"):
            await apply_graduated_punishment(message, config, count, add_warning_fn)
        return True

    # -----------------------------------------------------------------------
    # ALWAYS-ON CONTENT FILTERS — fire in every server regardless of the
    # bot's position in the role hierarchy. The message is deleted even if
    # SP4RK can't warn the sender; warnings are skipped for protected users.
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # HIERARCHY-GATED CHECKS — all remaining checks require the bot to be
    # able to action the user (warn, mute, etc.). If the user outranks the
    # bot, bail out silently without touching anything.
    # -----------------------------------------------------------------------
    if not _allowed_root:
        return False

    # 0a. Repeated-word spam in a single message (e.g. "fuck fuck fuck fuck fuck")
    #     → instant 30-min auto-mute, NO owner approval, no warning bookkeeping.
    if _has_repeated_word_spam(content):
        await send_webhook_log(
            config,
            "🔇 Repeated-Word Spam Auto-Mute",
            f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}\n"
            f"**Action:** {AUTOMUTE_MINUTES}-min mute\n**Message:** {content[:200]}",
        )
        return await _instant_mute(message, "Repeated-word spam", AUTOMUTE_MINUTES)

    # 0b. Always-on rate limit (flooding) → instant 30-min auto-mute.
    guild_id = message.guild.id
    user_id  = author.id
    now      = time.time()
    q = _spam_tracker[guild_id][user_id]
    q.append(now)
    while q and now - q[0] > RATE_LIMIT_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT_COUNT:
        q.clear()
        await send_webhook_log(
            config,
            "🔇 Rate-Limit Auto-Mute",
            f"**User:** {author} ({author.id})\n**Channel:** {channel.mention}\n"
            f"**Action:** {AUTOMUTE_MINUTES}-min mute\n"
            f"**Reason:** Sent {RATE_LIMIT_COUNT}+ messages in {RATE_LIMIT_WINDOW}s",
        )
        return await _instant_mute(
            message, f"Flood: {RATE_LIMIT_COUNT}+ msgs / {RATE_LIMIT_WINDOW}s", AUTOMUTE_MINUTES
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
        _link_pattern = re.compile(
            r'https?://\S+'                         # http:// or https:// URLs
            r'|www\.\S+\.\S+'                       # www.example.com style
            r'|(?<!\w)[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}(?:/\S*)?(?<!\w)',  # bare domains like example.com/path
            re.IGNORECASE,
        )
        if _link_pattern.search(content):
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
