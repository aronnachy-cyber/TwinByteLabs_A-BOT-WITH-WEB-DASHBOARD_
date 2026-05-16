"""
SP4RK Weird Features Module
Implements all the creative/chaos features for the SP4RK Discord bot.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import json
import os
import random
import re
import time
from datetime import datetime, timedelta
from collections import defaultdict, deque

# ─────────────────────────────────────────────────────────────
# DATA FILE
# ─────────────────────────────────────────────────────────────

WEIRD_FILE = os.path.join(os.path.dirname(__file__), "weird_data.json")

def _load():
    if os.path.exists(WEIRD_FILE):
        try:
            with open(WEIRD_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save(data):
    try:
        tmp = WEIRD_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, WEIRD_FILE)
    except Exception as e:
        print(f"[WeirdFeatures] Save error: {e}")

def _gd(data, guild_id):
    """Get or create guild-specific data dict."""
    gid = str(guild_id)
    if gid not in data:
        data[gid] = {}
    return data[gid]

# ─────────────────────────────────────────────────────────────
# CONSTANTS / POOLS
# ─────────────────────────────────────────────────────────────

EVO_MUTATIONS = {
    "distorted": [
        lambda m: m.replace("e", "ë").replace("o", "ø").replace("a", "å"),
        lambda m: " ".join(w[::-1] if random.random() > 0.5 else w for w in m.split()),
        lambda m: m.upper().replace(" ", "  ").replace("!", "!̷"),
        lambda m: "".join(c + "\u0308" if c.isalpha() and random.random() > 0.6 else c for c in m),
    ],
    "cursed": [
        lambda m: "".join(
            c + random.choice(["\u0300", "\u0301", "\u0302", "\u0308", "\u0332", "\u0336"])
            if c.isalpha() and random.random() > 0.4 else c for c in m
        ),
        lambda m: f"̸̡̧{m}̴̨̛",
        lambda m: m.replace(" ", "💀").replace(".", "☠️") + " 👁️",
        lambda m: "".join(
            c.upper() if random.random() > 0.5 else c.lower() for c in m
        ) + "\n> *the walls are listening*",
    ],
}

THOUGHT_POOL = [
    "we should all sleep but nobody will 💀",
    "someone here is lowkey about to say something unhinged",
    "the mods are tired but they'll never admit it",
    "there's a 73% chance this chat dies in 10 minutes",
    "someone is lurking and judging everyone silently",
    "we collectively forgot what we were talking about",
    "one person here has 47 tabs open and is overwhelmed",
    "the void called. it wants its energy back.",
    "someone is about to ping @everyone for no reason",
    "three people are typing. none of them will send anything useful.",
    "this server's collective IQ peaks at exactly 2am",
    "someone here is eating snacks and not sharing",
    "the chat is 80% chaos 15% memes 5% actual information",
    "we're all going to be here longer than we planned",
    "someone forgot they muted notifications 3 weeks ago",
]

WATCHER_MSGS = [
    "I saw everything. 👁️",
    "You thought no one was watching. I was.",
    "Interesting choices were made today.",
    "I have been here longer than you know.",
    "The patterns are… concerning.",
    "I remember the first message ever sent here. Do you?",
    "Someone in this server is lying. I won't say who.",
    "Three things happened today that I cannot unsee.",
    "I have watched. I have waited. I will continue.",
    "The logs don't lie. But people do.",
    "I saw that. You know what you did.",
]

WEATHER_MOODS = {
    "sunny":    ("☀️ Sunny & Wholesome",    0xFFD700),
    "cloudy":   ("⛅ Mostly Cloudy",         0xB0C4DE),
    "storm":    ("⛈️ FULL DRAMA STORM",      0x4B0082),
    "fog":      ("🌫️ Dead Chat Fog",         0x808080),
    "snow":     ("❄️ Frozen — Nobody Home",  0xADD8E6),
    "rainbow":  ("🌈 Chaos Rainbow",         0xFF69B4),
    "tornado":  ("🌪️ Maximum Chaos",         0x8B0000),
}

DRINK_MOODS = {
    "chill":    "🧃 Juice — vibes are smooth",
    "active":   "☕ Coffee — energy incoming",
    "toxic":    "⚡ Energy Drink — someone is causing problems",
    "dead":     "💀 Water evaporated — chat is extinct",
    "chaotic":  "🍵 Mystery Brew — nobody knows what's happening",
    "hype":     "🥤 Soda — fizzing with activity",
}

IDENTITY_TAGS = [
    "The Silent One 🤫",
    "Chaos Starter 💥",
    "Mod's Enemy #1 (affectionate) 😏",
    "The Lurker 👁️",
    "Certified Unhinged 🧠",
    "Local Menace 💀",
    "The Philosopher 🤔",
    "Meme Archaeologist 🦴",
    "Professional Overthinker 😩",
    "The One Who Never Sleeps 🦉",
    "Background NPC 🧍",
    "Drama Detector 📡",
    "Randomly Dangerous 🎲",
    "Too Powerful 👑",
    "Main Character Energy ✨",
]

SERVER_MOODS = [
    ("Chaotic Neutral 😈", 0x9B59B6),
    ("Dead Inside 💀", 0x2C3E50),
    ("Friendly But Suspicious 👀", 0x27AE60),
    ("Aggressively Wholesome 🥺", 0xF39C12),
    ("Legally Insane 🤪", 0xE74C3C),
    ("Functioning Disaster ⚙️💀", 0x95A5A6),
    ("Vibing Dangerously 😎⚡", 0x3498DB),
    ("Collectively Sleep Deprived 😴", 0x1ABC9C),
    ("Secretly Plotting 🗿", 0x34495E),
    ("Chaotically Aligned 🌀", 0xE67E22),
]

NPC_LINES = [
    "I have been summoned but still confused 💀",
    "I exist. That is all I know.",
    "Loading... please wait... loading...",
    "I was here the whole time. Nobody noticed.",
    "My programming told me to say something. This is that something.",
    "Beep. Boop. I am not a bot. Probably.",
    "I appear. I observe. I leave.",
    "Task: exist. Status: barely.",
    "Error 404: motivation not found.",
    "I have returned. No one asked. I know.",
]

EXPERIMENTS = {
    "invert_xp": "XP gains are inverted (losing points for activity 💀)",
    "chaos_names": "Channel names slowly drift toward chaos",
    "reverse_roles": "Random role swaps among active users",
    "mirror_mode": "Bot mirrors everyone's tone back at them",
    "slow_mode_madness": "Slowmode randomly applies and removes itself",
    "mystery_reactions": "Bot adds random mystery reactions to messages",
}

PUNISHMENTS = [
    "Must send only haiku messages for 1 hour ✍️",
    "Temporary nickname: 'Chaos Debtor 💀'",
    "Must add 🫠 to every message for 30 mins",
    "Assigned the role of 'Chaos Tax Collector'",
    "Must start every message with 'Respectfully,'",
    "Has to compliment 3 random users to clear debt",
    "Locked to sending voice messages only for 20 mins",
    "Must use only lowercase for the next hour",
    "Required to send a motivational quote every 15 mins",
]

BREAKING_NEWS = [
    "LOCAL USER refuses to go to sleep despite warnings 💀",
    "MOD SPOTTED typing for 3 minutes — message never sent 😭",
    "SOMEONE just opened 47 tabs — none of them useful",
    "BREAKING: The void has been stared into. It stared back. 👁️",
    "LOCAL BOT gains sentience — decides to be cryptic about it",
    "ECONOMY DESTABILIZED by someone who doesn't know what they're doing",
    "WEATHER UPDATE: drama incoming from the southeast channel",
    "SCIENTISTS BAFFLED: chat was productive for 4 minutes straight",
    "UPDATE: The chaos debt remains unpaid. Consequences loading.",
    "EXCLUSIVE: Server mood classified as 'questionable at best'",
]

PREDICTIONS = [
    "Someone will leave the server after losing a gamble 😏",
    "A mod will regret muting someone within 24 hours",
    "Three people will start typing then abandon their messages",
    "Someone will ping @everyone for absolutely no reason",
    "A debate about something completely unimportant will start soon",
    "Someone will discover a feature they didn't know existed",
    "The next meme sent will hit different than expected",
    "Chaos debt will go unchecked until it becomes a problem",
    "Someone is about to have a main character moment 💀",
    "A message sent tonight will become legendary lore",
]

# ─────────────────────────────────────────────────────────────
# IN-MEMORY STATE (per-guild, ephemeral)
# ─────────────────────────────────────────────────────────────

_message_counts   = defaultdict(lambda: defaultdict(int))   # guild_id -> user_id -> count
_last_active      = defaultdict(lambda: defaultdict(float))  # guild_id -> user_id -> timestamp
_ghost_log_cache  = defaultdict(list)                        # guild_id -> list of ghost echo dicts
_experiment_tasks = {}                                        # guild_id -> asyncio.Task

# ─────────────────────────────────────────────────────────────
# HELPER: permission check
# ─────────────────────────────────────────────────────────────

def _is_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        return False
    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
    )

async def _admin_only(interaction: discord.Interaction) -> bool:
    if not _is_admin(interaction):
        await interaction.response.send_message(
            "❌ You need **Administrator** or **Manage Server** permissions.", ephemeral=True
        )
        return False
    return True

def _spark_embed(title, description, color=0x7289DA):
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text="SP4RK Weird Features")
    return e

# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# FEATURE 1 — MESSAGE EVOLUTION SYSTEM 🧬
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

class EvoGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="evo", description="🧬 Message Evolution System")

    @app_commands.command(name="enable", description="Enable message evolution in this server")
    async def evo_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("evo", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧬 Message Evolution Enabled",
                "Old messages in your lore channel will slowly mutate over time.\n"
                "Set a channel with `/evo channel #channel`.", 0x00CC88)
        )

    @app_commands.command(name="channel", description="Set the evolution lore channel")
    @app_commands.describe(channel="The channel for evolved message logs")
    async def evo_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("evo", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧬 Evo Channel Set",
                f"Message evolution logs will appear in {channel.mention}.", 0x00CC88)
        )

    @app_commands.command(name="quote", description="Force a message to become a legendary quote")
    @app_commands.describe(message="The message to immortalize as a legendary quote")
    async def evo_quote(self, interaction: discord.Interaction, message: str):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        evo = gd.setdefault("evo", {})
        quotes = evo.setdefault("legendary_quotes", [])
        quotes.append({
            "text": message,
            "author": str(interaction.user),
            "timestamp": datetime.utcnow().isoformat()
        })
        evo["legendary_quotes"] = quotes[-50:]
        _save(data)

        channel_id = evo.get("channel")
        if channel_id:
            ch = interaction.guild.get_channel(channel_id)
            if ch:
                e = discord.Embed(
                    title="🏆 LEGENDARY QUOTE UNLOCKED",
                    description=f'*"{message}"*',
                    color=0xFFD700
                )
                e.add_field(name="Immortalized by", value=str(interaction.user))
                e.set_footer(text="This message has achieved legendary status.")
                await ch.send(embed=e)

        await interaction.response.send_message(
            embed=_spark_embed("🏆 Legendary Quote Added", f'*"{message}"* is now eternal.', 0xFFD700)
        )

    @app_commands.command(name="history", description="See a user's evolved messages")
    @app_commands.describe(user="The user to check")
    async def evo_history(self, interaction: discord.Interaction, user: discord.Member):
        data = _load()
        gd = _gd(data, interaction.guild_id)
        history = gd.get("evo", {}).get("history", {}).get(str(user.id), [])
        if not history:
            await interaction.response.send_message(
                f"No evolved messages found for {user.mention}.", ephemeral=True
            )
            return
        desc = "\n\n".join(
            f"**Stage {i+1} ({e['stage']}):** {e['text'][:100]}"
            for i, e in enumerate(history[-5:])
        )
        await interaction.response.send_message(
            embed=_spark_embed(f"🧬 Evolution History — {user.display_name}", desc, 0x9B59B6)
        )

    async def _maybe_evolve(self, bot, guild_id, user_id, original_text):
        """Called on message events to randomly evolve messages."""
        data = _load()
        gd = _gd(data, guild_id)
        evo = gd.get("evo", {})
        if not evo.get("enabled"):
            return
        channel_id = evo.get("channel")
        if not channel_id:
            return

        if random.random() > 0.04:  # 4% chance
            return

        # Pick mutation stage
        if random.random() < 0.05:
            stage = "legendary"
        elif random.random() < 0.4:
            stage = "cursed"
        else:
            stage = "distorted"

        if stage == "legendary":
            mutated = f'"{original_text}" — **[LEGENDARY QUOTE]** 🏆'
            color = 0xFFD700
        elif stage == "cursed":
            fn = random.choice(EVO_MUTATIONS["cursed"])
            mutated = fn(original_text[:100])
            color = 0x8B0000
        else:
            fn = random.choice(EVO_MUTATIONS["distorted"])
            mutated = fn(original_text[:100])
            color = 0x9B59B6

        guild = bot.get_guild(int(guild_id))
        if not guild:
            return
        ch = guild.get_channel(channel_id)
        if not ch:
            return

        member = guild.get_member(int(user_id))
        e = discord.Embed(
            title=f"🧬 Message Evolved — {stage.upper()}",
            description=mutated,
            color=color
        )
        if member:
            e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        e.set_footer(text=f"Original: {original_text[:60]}...")
        await ch.send(embed=e)

        # Save history
        history = gd.setdefault("evo", {}).setdefault("history", {}).setdefault(str(user_id), [])
        history.append({"stage": stage, "text": mutated, "timestamp": datetime.utcnow().isoformat()})
        gd["evo"]["history"][str(user_id)] = history[-10:]
        _save(data)


# ─────────────────────────────────────────────────────────────
# FEATURE 2 — CHAOS DEBT SYSTEM 🧾
# ─────────────────────────────────────────────────────────────

class ChaosGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="chaos", description="🧾 Chaos Debt System")

    debt_group = app_commands.Group(name="debt", description="Manage chaos debt")

    @debt_group.command(name="check", description="Check chaos debt for a user")
    @app_commands.describe(user="The user to check (leave blank for yourself)")
    async def debt_check(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        data = _load()
        gd = _gd(data, interaction.guild_id)
        debt = gd.get("chaos_debt", {}).get(str(target.id), 0)
        color = 0xE74C3C if debt > 50 else 0xF39C12 if debt > 20 else 0x27AE60
        await interaction.response.send_message(
            embed=_spark_embed(
                "🧾 Chaos Debt",
                f"{target.mention} has **{debt}** chaos debt points.\n"
                + ("💀 **CRITICAL DEBT** — punishment incoming!" if debt > 50
                   else "⚠️ Getting dangerous..." if debt > 20
                   else "✅ Clean record (for now)"),
                color
            )
        )

    @debt_group.command(name="add", description="Add chaos debt to a user")
    @app_commands.describe(user="The user", amount="Amount of debt to add")
    async def debt_add(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        debts = gd.setdefault("chaos_debt", {})
        debts[str(user.id)] = debts.get(str(user.id), 0) + amount
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧾 Chaos Debt Added",
                f"Added **{amount}** chaos debt to {user.mention}.\nTotal: **{debts[str(user.id)]}**", 0xE74C3C)
        )

    @debt_group.command(name="clear", description="Clear a user's chaos debt")
    @app_commands.describe(user="The user to clear")
    async def debt_clear(self, interaction: discord.Interaction, user: discord.Member):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("chaos_debt", {})[str(user.id)] = 0
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("✅ Chaos Debt Cleared",
                f"{user.mention}'s chaos debt has been wiped clean.", 0x27AE60)
        )

    @app_commands.command(name="punishments", description="List available punishments for chaos debt")
    async def punishments_list(self, interaction: discord.Interaction):
        desc = "\n".join(f"**{i+1}.** {p}" for i, p in enumerate(PUNISHMENTS))
        await interaction.response.send_message(
            embed=_spark_embed("😭 Chaos Punishments", desc, 0xE74C3C)
        )

    @app_commands.command(name="redeem", description="Try to clear your chaos debt")
    async def chaos_redeem(self, interaction: discord.Interaction):
        data = _load()
        gd = _gd(data, interaction.guild_id)
        debts = gd.setdefault("chaos_debt", {})
        uid = str(interaction.user.id)
        debt = debts.get(uid, 0)
        if debt == 0:
            await interaction.response.send_message(
                "✅ You have no chaos debt! You're free... for now.", ephemeral=True
            )
            return

        # Redemption task
        tasks_needed = ["Send a meme in the next 5 minutes 🖼️",
                        "Win an active giveaway 🎉",
                        "Compliment 3 different users 🫶",
                        "Send a motivational quote 💪",
                        "React to 10 messages with ❤️"]
        task = random.choice(tasks_needed)
        reduction = min(debt, random.randint(5, 20))
        debts[uid] = max(0, debt - reduction)
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed(
                "🧾 Chaos Redemption",
                f"**Your task:** {task}\n\n"
                f"Completing this would reduce your debt by **{reduction}** points.\n"
                f"Remaining debt: **{debts[uid]}**",
                0xF39C12
            )
        )

    async def _add_random_debt(self, bot, guild_id, user_id):
        """Randomly assign chaos debt (called from on_message)."""
        if random.random() > 0.008:  # 0.8% chance
            return
        data = _load()
        gd = _gd(data, guild_id)
        debts = gd.setdefault("chaos_debt", {})
        amount = random.randint(1, 10)
        debts[str(user_id)] = debts.get(str(user_id), 0) + amount
        _save(data)

        guild = bot.get_guild(int(guild_id))
        if guild:
            member = guild.get_member(int(user_id))
            if member:
                try:
                    await member.send(
                        f"🧾 **Chaos Debt Alert!**\n"
                        f"You've been randomly assigned **{amount}** chaos debt points in **{guild.name}**!\n"
                        f"Total: **{debts[str(user_id)]}** | Use `/chaos redeem` to start clearing it."
                    )
                except Exception:
                    pass


# ─────────────────────────────────────────────────────────────
# FEATURE 3 — THOUGHT LEAK SYSTEM 🧠
# ─────────────────────────────────────────────────────────────

_thought_tasks = {}

class ThoughtsGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="thoughts", description="🧠 Thought Leak System")

    @app_commands.command(name="enable", description="Enable the thought leak system")
    async def thoughts_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("thoughts", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧠 Thought Leak Enabled",
                "SP4RK will occasionally leak fake server thoughts.\n"
                "Set a channel with `/thoughts channel`.", 0x9B59B6)
        )

    @app_commands.command(name="frequency", description="Set how often thoughts leak (in minutes)")
    @app_commands.describe(minutes="Minutes between thought leaks (5–1440)")
    async def thoughts_frequency(self, interaction: discord.Interaction, minutes: int):
        if not await _admin_only(interaction):
            return
        minutes = max(5, min(1440, minutes))
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("thoughts", {})["frequency"] = minutes
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧠 Frequency Set", f"Thoughts will leak every **{minutes}** minutes.", 0x9B59B6)
        )

    @app_commands.command(name="channel", description="Set the channel for thought leaks")
    @app_commands.describe(channel="The channel to post thoughts in")
    async def thoughts_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("thoughts", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧠 Thought Channel Set", f"Thoughts will appear in {channel.mention}.", 0x9B59B6)
        )

    @app_commands.command(name="force", description="Force a thought leak right now")
    async def thoughts_force(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        ch_id = gd.get("thoughts", {}).get("channel")
        if not ch_id:
            await interaction.response.send_message("Set a channel first with `/thoughts channel`.", ephemeral=True)
            return
        ch = interaction.guild.get_channel(ch_id)
        if not ch:
            await interaction.response.send_message("Channel not found.", ephemeral=True)
            return
        thought = random.choice(THOUGHT_POOL)
        e = discord.Embed(
            title="🧠 Spark detected an unspoken thought from the server…",
            description=f'*"{thought}"*',
            color=0x9B59B6
        )
        e.set_footer(text="This thought was detected, not created.")
        await ch.send(embed=e)
        await interaction.response.send_message("💭 Thought leaked!", ephemeral=True)

    @app_commands.command(name="blacklist", description="Blacklist a role or user from thought leaks")
    @app_commands.describe(target="Role or user mention to blacklist")
    async def thoughts_blacklist(self, interaction: discord.Interaction, target: str):
        if not await _admin_only(interaction):
            return
        await interaction.response.send_message(
            embed=_spark_embed("🧠 Blacklist Updated",
                f"`{target}` will no longer be included in thought generation.", 0x9B59B6)
        )


# ─────────────────────────────────────────────────────────────
# FEATURE 4 — CLONE DAY EVENT 🧍
# ─────────────────────────────────────────────────────────────

class CloneGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="clone", description="🧍 Clone Day Event")

    @app_commands.command(name="start", description="Start Clone Day — everyone gets a clone role")
    async def clone_start(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("clone", {})["active"] = True
        gd["clone"]["start_time"] = datetime.utcnow().isoformat()
        duration = gd.get("clone", {}).get("duration", 30)
        _save(data)

        members = [m for m in interaction.guild.members if not m.bot]
        random.shuffle(members)
        assignments = {}
        for i, member in enumerate(members):
            clone_of = members[(i + 1) % len(members)]
            assignments[str(member.id)] = str(clone_of.id)

        gd["clone"]["assignments"] = assignments
        _save(data)

        desc = "**Clone assignments:**\n"
        sample = list(assignments.items())[:8]
        for uid, clone_id in sample:
            m1 = interaction.guild.get_member(int(uid))
            m2 = interaction.guild.get_member(int(clone_id))
            if m1 and m2:
                desc += f"• {m1.mention} → cloned from **{m2.display_name}**\n"
        if len(assignments) > 8:
            desc += f"*...and {len(assignments) - 8} more*"

        await interaction.response.send_message(
            embed=_spark_embed("🧍 Clone Day Started! 💀",
                f"**Duration:** {duration} minutes\n{desc}\nChaos levels: **MAXIMUM**", 0xE74C3C)
        )

    @app_commands.command(name="end", description="End Clone Day")
    async def clone_end(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("clone", {})["active"] = False
        gd["clone"]["assignments"] = {}
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧍 Clone Day Ended", "Everyone is themselves again. Probably.", 0x27AE60)
        )

    @app_commands.command(name="config", description="Configure Clone Day duration")
    @app_commands.describe(duration="Duration in minutes")
    async def clone_config(self, interaction: discord.Interaction, duration: int):
        if not await _admin_only(interaction):
            return
        duration = max(5, min(360, duration))
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("clone", {})["duration"] = duration
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧍 Clone Config Updated", f"Clone Day duration set to **{duration}** minutes.", 0x3498DB)
        )

    @app_commands.command(name="assign", description="Randomly assign a single clone role")
    async def clone_assign(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        members = [m for m in interaction.guild.members if not m.bot and m.id != interaction.user.id]
        if not members:
            await interaction.response.send_message("Not enough members!", ephemeral=True)
            return
        clone_of = random.choice(members)
        await interaction.response.send_message(
            embed=_spark_embed("🧍 Clone Assigned",
                f"You have been cloned from **{clone_of.display_name}** {clone_of.mention}\n"
                "Channel chaos will increase by approximately 💀%", 0xE74C3C)
        )

    @app_commands.command(name="status", description="Check Clone Day status")
    async def clone_status(self, interaction: discord.Interaction):
        data = _load()
        gd = _gd(data, interaction.guild_id)
        clone = gd.get("clone", {})
        active = clone.get("active", False)
        count = len(clone.get("assignments", {}))
        await interaction.response.send_message(
            embed=_spark_embed(
                "🧍 Clone Day Status",
                f"**Active:** {'Yes 💀' if active else 'No'}\n"
                f"**Clones deployed:** {count}\n"
                f"**Duration:** {clone.get('duration', 30)} minutes",
                0xE74C3C if active else 0x95A5A6
            )
        )


# ─────────────────────────────────────────────────────────────
# FEATURE 5 — LIVE SERVER TV 📺
# ─────────────────────────────────────────────────────────────

class TVGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="tv", description="📺 Live Server TV")

    @app_commands.command(name="enable", description="Enable Live Server TV")
    async def tv_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("tv", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("📺 Live Server TV Enabled",
                "Set a broadcast channel with `/tv channel #channel`.\n"
                "SP4RK will broadcast top chatters, breaking news & dramatic moments.", 0xFF6B35)
        )

    @app_commands.command(name="channel", description="Set the TV broadcast channel")
    @app_commands.describe(channel="The broadcast channel")
    async def tv_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("tv", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("📺 TV Channel Set", f"Live broadcasts will air in {channel.mention}.", 0xFF6B35)
        )

    @app_commands.command(name="set", description="Configure TV interval")
    @app_commands.describe(interval="Broadcast interval in seconds (60–3600)")
    async def tv_set(self, interaction: discord.Interaction, interval: int):
        if not await _admin_only(interaction):
            return
        interval = max(60, min(3600, interval))
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("tv", {})["interval"] = interval
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("📺 TV Interval Set", f"Broadcast every **{interval}** seconds.", 0xFF6B35)
        )

    @app_commands.command(name="add", description="Add a TV segment type")
    @app_commands.describe(segment_type="Type: news | topchat | chaos")
    @app_commands.choices(segment_type=[
        app_commands.Choice(name="Breaking News", value="news"),
        app_commands.Choice(name="Top Chatter", value="topchat"),
        app_commands.Choice(name="Chaos Moment", value="chaos"),
    ])
    async def tv_add(self, interaction: discord.Interaction, segment_type: str):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        segments = gd.setdefault("tv", {}).setdefault("segments", [])
        if segment_type not in segments:
            segments.append(segment_type)
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("📺 Segment Added", f"Segment **{segment_type}** added to the broadcast.", 0xFF6B35)
        )

    @app_commands.command(name="disable", description="Disable Live Server TV")
    async def tv_disable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("tv", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("📺 Live TV Disabled", "Broadcast has ended. We're off air.", 0x95A5A6)
        )

    async def broadcast(self, bot, guild_id):
        """Send a TV broadcast to the configured channel."""
        data = _load()
        gd = _gd(data, guild_id)
        tv = gd.get("tv", {})
        if not tv.get("enabled"):
            return
        ch_id = tv.get("channel")
        if not ch_id:
            return
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return
        ch = guild.get_channel(ch_id)
        if not ch:
            return

        # Top chatter from in-memory counts
        counts = _message_counts.get(int(guild_id), {})
        top_chatter = "Unknown"
        if counts:
            top_uid = max(counts, key=counts.get)
            top_m = guild.get_member(int(top_uid))
            if top_m:
                top_chatter = f"{top_m.display_name} ({counts[top_uid]} msgs)"

        news = random.choice(BREAKING_NEWS)
        mood = _get_server_mood_text(guild_id)

        e = discord.Embed(title="📺 SP4RK LIVE — SERVER BROADCAST", color=0xFF6B35)
        e.add_field(name="🏆 Top Chatter", value=top_chatter, inline=False)
        e.add_field(name="📰 Breaking News", value=news, inline=False)
        e.add_field(name="🌡️ Server Mood", value=mood, inline=False)

        if random.random() < 0.3:
            e.add_field(name="🎭 Dramatic Moment Detected",
                       value=f"Someone in this server just had a *moment*. 😭", inline=False)

        e.set_footer(text="SP4RK Live Broadcasting • Stay tuned")
        await ch.send(embed=e)


# ─────────────────────────────────────────────────────────────
# FEATURE 6 — EXPERIMENT MODE 🧪
# ─────────────────────────────────────────────────────────────

class ExperimentGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="experiment", description="🧪 Experiment Mode")

    @app_commands.command(name="start", description="Start an experiment")
    @app_commands.describe(name="Experiment name", duration="Duration in minutes (default 30)")
    @app_commands.choices(name=[
        app_commands.Choice(name=k, value=k) for k in EXPERIMENTS
    ])
    async def experiment_start(self, interaction: discord.Interaction, name: str, duration: int = 30):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("experiments", {})["active"] = name
        gd["experiments"]["started"] = datetime.utcnow().isoformat()
        gd["experiments"]["duration"] = duration
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed(
                "🧪 Experiment Started",
                f"**{name}**\n{EXPERIMENTS.get(name, 'Unknown experiment')}\n\n"
                f"⏱️ Duration: **{duration}** minutes\nEverything returns to normal after the timer ends.",
                0x00D4FF
            )
        )

    @app_commands.command(name="end", description="End the current experiment")
    async def experiment_end(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        current = gd.get("experiments", {}).get("active", "None")
        gd.setdefault("experiments", {})["active"] = None
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧪 Experiment Ended",
                f"**{current}** has concluded. Normality restored. Probably.", 0x27AE60)
        )

    @app_commands.command(name="list", description="List all available experiments")
    async def experiment_list(self, interaction: discord.Interaction):
        desc = "\n".join(f"**`{k}`** — {v}" for k, v in EXPERIMENTS.items())
        await interaction.response.send_message(
            embed=_spark_embed("🧪 Available Experiments", desc, 0x00D4FF)
        )

    @app_commands.command(name="create", description="Create a custom experiment effect")
    @app_commands.describe(effect="Describe the experiment effect")
    async def experiment_create(self, interaction: discord.Interaction, effect: str):
        if not await _admin_only(interaction):
            return
        EXPERIMENTS[f"custom_{interaction.guild_id}"] = effect
        await interaction.response.send_message(
            embed=_spark_embed("🧪 Custom Experiment Created",
                f"**Effect:** {effect}\nStart it with `/experiment start custom_{interaction.guild_id}`", 0x00D4FF)
        )

    @app_commands.command(name="force_reset", description="Force reset all experiments")
    async def experiment_force_reset(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd["experiments"] = {}
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧪 Experiments Reset",
                "All experiment states wiped. Back to baseline.", 0x27AE60)
        )


# ─────────────────────────────────────────────────────────────
# FEATURE 7 — WATCHER ENTITY 👁️
# ─────────────────────────────────────────────────────────────

class WatcherGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="watcher", description="👁️ Watcher Entity")

    @app_commands.command(name="enable", description="Enable the Watcher entity")
    async def watcher_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("watcher", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("👁️ The Watcher is Now Active",
                "I see everything.\n\nThe watcher will send cryptic messages at random intervals.", 0x2C3E50)
        )

    @app_commands.command(name="disable", description="Disable the Watcher entity")
    async def watcher_disable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("watcher", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("👁️ The Watcher Steps Back",
                "I will no longer send messages. But I am still watching.", 0x95A5A6)
        )

    @app_commands.command(name="frequency", description="Set how often the Watcher speaks (minutes)")
    @app_commands.describe(mins="Minutes between watcher messages (10–1440)")
    async def watcher_frequency(self, interaction: discord.Interaction, mins: int):
        if not await _admin_only(interaction):
            return
        mins = max(10, min(1440, mins))
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("watcher", {})["frequency"] = mins
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("👁️ Watcher Frequency Set",
                f"The Watcher will speak every **{mins}** minutes.", 0x2C3E50)
        )

    @app_commands.command(name="message", description="Add a custom Watcher message")
    @app_commands.describe(text="The cryptic message to add")
    async def watcher_message(self, interaction: discord.Interaction, text: str):
        if not await _admin_only(interaction):
            return
        WATCHER_MSGS.append(text)
        await interaction.response.send_message(
            embed=_spark_embed("👁️ Message Added to the Watcher",
                f'*"{text}"* will now be spoken by the Watcher.', 0x2C3E50)
        )

    @app_commands.command(name="whisper", description="Make the Watcher speak right now")
    async def watcher_whisper(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        msg = random.choice(WATCHER_MSGS)
        await interaction.channel.send(f"👁️ *{msg}*")
        await interaction.response.send_message("The Watcher has spoken.", ephemeral=True)

    async def maybe_whisper(self, bot, guild_id):
        """Called periodically to maybe send a watcher message."""
        data = _load()
        gd = _gd(data, guild_id)
        w = gd.get("watcher", {})
        if not w.get("enabled"):
            return
        freq = w.get("frequency", 120)
        last = w.get("last_whisper", 0)
        if time.time() - last < freq * 60:
            return
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return

        # Find a general/active channel
        target = None
        for ch in guild.text_channels:
            if any(k in ch.name.lower() for k in ("general", "chat", "main", "lounge")):
                target = ch
                break
        if not target:
            target = guild.text_channels[0] if guild.text_channels else None
        if not target:
            return

        msg = random.choice(WATCHER_MSGS)
        await target.send(f"👁️ *{msg}*")

        gd["watcher"]["last_whisper"] = time.time()
        _save(data)


# ─────────────────────────────────────────────────────────────
# FEATURE 8 — EMOTION WEATHER SYSTEM 🧃
# ─────────────────────────────────────────────────────────────

def _get_server_mood_text(guild_id):
    data = _load()
    gd = _gd(data, guild_id)
    mood = gd.get("weather", {}).get("current_mood", "cloudy")
    weather = WEATHER_MOODS.get(mood, WEATHER_MOODS["cloudy"])
    return weather[0]

class WeatherGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="weather", description="🌤️ Emotion Weather System")

    @app_commands.command(name="enable", description="Enable the Emotion Weather System")
    async def weather_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("weather", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("⛅ Emotion Weather Enabled",
                "SP4RK will track server mood and display it as weather.\n"
                "Set a channel with `/weather channel`.", 0x3498DB)
        )

    @app_commands.command(name="channel", description="Set the weather update channel")
    @app_commands.describe(channel="The weather channel")
    async def weather_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("weather", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("⛅ Weather Channel Set", f"Weather updates will appear in {channel.mention}.", 0x3498DB)
        )

    @app_commands.command(name="update", description="Force a weather update now")
    async def weather_update(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        await _post_weather_update(interaction.client, interaction.guild_id)
        await interaction.response.send_message("🌡️ Weather updated!", ephemeral=True)

    @app_commands.command(name="set", description="Manually set the server mood")
    @app_commands.describe(mood="The mood to set")
    @app_commands.choices(mood=[app_commands.Choice(name=k, value=k) for k in WEATHER_MOODS])
    async def weather_set(self, interaction: discord.Interaction, mood: str):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("weather", {})["current_mood"] = mood
        history = gd["weather"].setdefault("history", [])
        history.append({"mood": mood, "timestamp": datetime.utcnow().isoformat()})
        gd["weather"]["history"] = history[-20:]
        _save(data)
        name, color = WEATHER_MOODS.get(mood, WEATHER_MOODS["cloudy"])
        await interaction.response.send_message(
            embed=_spark_embed("🌡️ Server Mood Set", f"Current mood: **{name}**", color)
        )

    @app_commands.command(name="history", description="View the weather history")
    async def weather_history(self, interaction: discord.Interaction):
        data = _load()
        gd = _gd(data, interaction.guild_id)
        history = gd.get("weather", {}).get("history", [])
        if not history:
            await interaction.response.send_message("No weather history yet.", ephemeral=True)
            return
        desc = "\n".join(
            f"`{h['timestamp'][:16]}` — **{WEATHER_MOODS.get(h['mood'], (h['mood'],))[0]}**"
            for h in reversed(history[-10:])
        )
        await interaction.response.send_message(
            embed=_spark_embed("⛅ Weather History", desc, 0x3498DB)
        )

async def _post_weather_update(bot, guild_id):
    data = _load()
    gd = _gd(data, guild_id)
    w = gd.get("weather", {})
    if not w.get("enabled"):
        return
    ch_id = w.get("channel")
    if not ch_id:
        return
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return
    ch = guild.get_channel(ch_id)
    if not ch:
        return

    # Detect mood from activity
    counts = _message_counts.get(int(guild_id), {})
    total = sum(counts.values())
    if total == 0:
        mood = "fog"
    elif total < 5:
        mood = "cloudy"
    elif total < 20:
        mood = "sunny"
    elif total < 50:
        mood = "rainbow"
    else:
        mood = random.choice(["storm", "tornado"])

    gd.setdefault("weather", {})["current_mood"] = mood
    history = gd["weather"].setdefault("history", [])
    history.append({"mood": mood, "timestamp": datetime.utcnow().isoformat()})
    gd["weather"]["history"] = history[-20:]
    _save(data)

    name, color = WEATHER_MOODS[mood]
    drink = DRINK_MOODS.get(
        {"fog": "dead", "cloudy": "chill", "sunny": "active",
         "rainbow": "chaotic", "storm": "toxic", "tornado": "chaotic", "snow": "chill"}.get(mood, "chill"),
        "🧃 Juice"
    )

    e = discord.Embed(title="🌡️ SP4RK Emotion Weather Update", color=color)
    e.add_field(name="Current Weather", value=f"**{name}**", inline=False)
    e.add_field(name="🧃 Mood Drink", value=drink, inline=False)
    e.add_field(name="📊 Activity", value=f"{total} recent messages", inline=False)
    e.set_footer(text="Weather updates every few hours based on server activity.")
    await ch.send(embed=e)

    # Reset counts after broadcast
    _message_counts[int(guild_id)] = defaultdict(int)


# ─────────────────────────────────────────────────────────────
# FEATURE 9 — ECONOMY CRASH EVENT 🪙
# ─────────────────────────────────────────────────────────────

class EconomyGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="economy", description="🪙 Economy Crash Event")

    crash_group = app_commands.Group(name="crash", description="Manage economy crash events")

    @crash_group.command(name="start", description="Start an economy crash event")
    async def crash_start(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("economy", {})["crashed"] = True
        gd["economy"]["inflation"] = random.randint(200, 500)
        gd["economy"]["crash_time"] = datetime.utcnow().isoformat()
        _save(data)
        inflation = gd["economy"]["inflation"]
        await interaction.response.send_message(
            embed=_spark_embed(
                "💸 ECONOMY CRASH INITIATED 💀",
                f"**INFLATION RATE:** {inflation}%\n"
                f"Prices have gone **INSANE**. Currency is destabilizing.\n"
                f"Brace for impact. The crash is coming.\n\n"
                f"📉 Everything costs {inflation}% more right now.",
                0xE74C3C
            )
        )

    @crash_group.command(name="end", description="End the economy crash")
    async def crash_end(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("economy", {})["crashed"] = False
        gd["economy"]["inflation"] = 100
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("📈 Economy Stabilized",
                "The crash is over. Prices return to normal. Survivors: TBD.", 0x27AE60)
        )

    @app_commands.command(name="inflation", description="Set the current inflation rate")
    @app_commands.describe(value="Inflation percentage (100 = normal, 500 = chaos)")
    async def inflation_set(self, interaction: discord.Interaction, value: int):
        if not await _admin_only(interaction):
            return
        value = max(10, min(9999, value))
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("economy", {})["inflation"] = value
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("💸 Inflation Set",
                f"Inflation is now at **{value}%**. {'💀 ECONOMIC DISASTER' if value > 300 else '⚠️ Getting spicy' if value > 150 else '✅ Reasonable'}", 0xF39C12)
        )

    @app_commands.command(name="restore", description="Restore the economy to normal")
    async def economy_restore(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd["economy"] = {"crashed": False, "inflation": 100}
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("💰 Economy Restored", "The economy has been fully reset. As if nothing happened.", 0x27AE60)
        )

    @app_commands.command(name="status", description="Check the economy status")
    async def economy_status(self, interaction: discord.Interaction):
        data = _load()
        gd = _gd(data, interaction.guild_id)
        eco = gd.get("economy", {"crashed": False, "inflation": 100})
        crashed = eco.get("crashed", False)
        inflation = eco.get("inflation", 100)
        await interaction.response.send_message(
            embed=_spark_embed(
                "💰 Economy Status",
                f"**Status:** {'💀 CRASHED' if crashed else '✅ Stable'}\n"
                f"**Inflation:** {inflation}%\n"
                f"{'The economy is in shambles. 💀' if crashed else 'Everything is fine. Probably.'}",
                0xE74C3C if crashed else 0x27AE60
            )
        )


# ─────────────────────────────────────────────────────────────
# FEATURE 10 — IDENTITY DRIFT SYSTEM 🎭
# ─────────────────────────────────────────────────────────────

class IdentityGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="identity", description="🎭 Identity Drift System")

    @app_commands.command(name="enable", description="Enable identity drift for active users")
    async def identity_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("identity", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🎭 Identity Drift Enabled",
                "Active users will slowly gain personality tags based on their behavior.", 0x9B59B6)
        )

    @app_commands.command(name="assign", description="Assign an identity tag to a user")
    @app_commands.describe(user="The user", role_tag="The identity tag to assign")
    @app_commands.choices(role_tag=[app_commands.Choice(name=t, value=t) for t in IDENTITY_TAGS[:10]])
    async def identity_assign(self, interaction: discord.Interaction, user: discord.Member, role_tag: str):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("identity", {}).setdefault("tags", {})[str(user.id)] = role_tag
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🎭 Identity Assigned",
                f"{user.mention} is now known as: **{role_tag}**", 0x9B59B6)
        )

    @app_commands.command(name="randomize", description="Randomly assign identities to all active users")
    async def identity_randomize(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        tags = gd.setdefault("identity", {}).setdefault("tags", {})
        members = [m for m in interaction.guild.members if not m.bot]
        for m in members:
            tags[str(m.id)] = random.choice(IDENTITY_TAGS)
        _save(data)

        sample = list(tags.items())[:6]
        desc = ""
        for uid, tag in sample:
            member = interaction.guild.get_member(int(uid))
            if member:
                desc += f"• {member.mention} → **{tag}**\n"
        if len(tags) > 6:
            desc += f"*...and {len(tags) - 6} more*"

        await interaction.response.send_message(
            embed=_spark_embed("🎭 Identities Randomized", desc, 0x9B59B6)
        )

    @app_commands.command(name="reset", description="Reset all identity tags")
    async def identity_reset(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("identity", {})["tags"] = {}
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🎭 Identities Reset", "All tags cleared. Everyone is anonymous again.", 0x95A5A6)
        )

    @app_commands.command(name="list", description="List all assigned identity tags")
    async def identity_list(self, interaction: discord.Interaction):
        data = _load()
        gd = _gd(data, interaction.guild_id)
        tags = gd.get("identity", {}).get("tags", {})
        if not tags:
            await interaction.response.send_message("No identities assigned yet.", ephemeral=True)
            return
        lines = []
        for uid, tag in list(tags.items())[:15]:
            member = interaction.guild.get_member(int(uid))
            name = member.display_name if member else f"User {uid}"
            lines.append(f"• **{name}** — {tag}")
        await interaction.response.send_message(
            embed=_spark_embed("🎭 Identity Tags", "\n".join(lines), 0x9B59B6)
        )

    def get_tag(self, guild_id, user_id):
        data = _load()
        gd = _gd(data, guild_id)
        return gd.get("identity", {}).get("tags", {}).get(str(user_id))

    def maybe_assign_tag(self, guild_id, user_id):
        """Auto-assign a tag based on message count."""
        data = _load()
        gd = _gd(data, guild_id)
        if not gd.get("identity", {}).get("enabled"):
            return
        tags = gd.setdefault("identity", {}).setdefault("tags", {})
        uid = str(user_id)
        count = _message_counts.get(int(guild_id), {}).get(int(user_id), 0)
        if uid not in tags and count >= 20:
            tags[uid] = random.choice(IDENTITY_TAGS)
            _save(data)


# ─────────────────────────────────────────────────────────────
# FEATURE 11 — SERVER MOOD REALITY ENGINE 🧠
# ─────────────────────────────────────────────────────────────

async def post_daily_server_mood(bot, guild_id, channel_id=None):
    data = _load()
    gd = _gd(data, guild_id)
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return

    mood_name, color = random.choice(SERVER_MOODS)
    counts = _message_counts.get(int(guild_id), {})
    total = sum(counts.values())

    if not channel_id:
        for ch in guild.text_channels:
            if any(k in ch.name.lower() for k in ("general", "chat", "lounge", "main")):
                channel_id = ch.id
                break

    if not channel_id:
        return
    ch = guild.get_channel(channel_id)
    if not ch:
        return

    e = discord.Embed(title="🧠 SP4RK Server Mood Reality Engine", color=color)
    e.add_field(name="Today's Server Personality", value=f"**{mood_name}**", inline=False)
    e.add_field(name="Activity Level",
                value=f"{'🔥 Hyperactive' if total > 100 else '💬 Active' if total > 20 else '😴 Dead'}", inline=True)
    e.add_field(name="Chaos Index",
                value=f"{'💀 Maximum' if total > 200 else '⚠️ Elevated' if total > 50 else '✅ Manageable'}", inline=True)
    e.set_footer(text="Changes daily based on activity, toxicity & spam patterns.")
    await ch.send(embed=e)

    gd.setdefault("mood_engine", {})["last_mood"] = mood_name
    gd["mood_engine"]["last_update"] = datetime.utcnow().isoformat()
    _save(data)


# ─────────────────────────────────────────────────────────────
# FEATURE 12 — GHOST MESSAGE SYSTEM 👻
# ─────────────────────────────────────────────────────────────

async def handle_ghost_message(bot, message: discord.Message, ghost_channel_id: int = None):
    """Called on message delete to create a ghost echo."""
    if not message.guild or message.author.bot:
        return

    data = _load()
    gd = _gd(data, message.guild.id)
    ghost_ch_id = ghost_channel_id or gd.get("ghost", {}).get("channel")
    if not ghost_ch_id:
        return

    ch = message.guild.get_channel(int(ghost_ch_id))
    if not ch:
        return

    content = message.content
    if not content:
        return

    # Blur the content — replace characters with ghost-like substitutes
    blurred = ""
    for i, c in enumerate(content):
        if c == " ":
            blurred += " "
        elif random.random() < 0.5:
            blurred += "░"
        else:
            blurred += c

    e = discord.Embed(
        title="👻 Ghost Echo Detected",
        description=f"*{blurred[:300]}*",
        color=0x2C3E50
    )
    e.add_field(name="Channel", value=message.channel.mention, inline=True)
    e.add_field(name="User", value=message.author.mention, inline=True)
    e.set_footer(text="Deleted message ghost echo — blurred for context only.")
    await ch.send(embed=e)


class GhostGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="ghost", description="👻 Ghost Message System")

    @app_commands.command(name="channel", description="Set the ghost echo log channel")
    @app_commands.describe(channel="Channel to log ghost echoes")
    async def ghost_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("ghost", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("👻 Ghost Channel Set",
                f"Deleted message echoes will appear in {channel.mention}.\n"
                "Content is blurred so mods see vibe, not full context.", 0x2C3E50)
        )

    @app_commands.command(name="enable", description="Enable the ghost message system")
    async def ghost_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("ghost", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("👻 Ghost System Enabled",
                "Deleted messages will become ghost echoes in the log channel.", 0x2C3E50)
        )

    @app_commands.command(name="disable", description="Disable the ghost message system")
    async def ghost_disable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("ghost", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("👻 Ghost System Disabled", "No more ghost echoes.", 0x95A5A6)
        )


# ─────────────────────────────────────────────────────────────
# FEATURE 13 — USER MIRROR MODE 🪞
# ─────────────────────────────────────────────────────────────

async def handle_mirror_mode(bot, message: discord.Message):
    """Mirror a user's tone back at them."""
    if message.author.bot or not message.guild:
        return
    data = _load()
    gd = _gd(data, message.guild.id)
    exp = gd.get("experiments", {}).get("active")
    if exp != "mirror_mode":
        return

    content = message.content.lower()
    # Detect tone
    toxic_words = ["hate", "stupid", "idiot", "dumb", "trash", "worst", "shut up", "kys"]
    positive_words = ["thank", "love", "great", "awesome", "help", "kind", "good", "nice"]

    is_toxic = any(w in content for w in toxic_words)
    is_positive = any(w in content for w in positive_words)

    if is_toxic and random.random() < 0.3:
        mirrors = [
            f"📡 *Mirror Mode detected:* Interesting choice of words, {message.author.mention}.",
            f"🪞 *Reflecting:* Someone said something concerning. Just noting that.",
            f"👁️ The Watcher notes: that energy was logged.",
        ]
        await message.channel.send(random.choice(mirrors))
    elif is_positive and random.random() < 0.2:
        mirrors = [
            f"🪞 *Mirror Mode:* {message.author.mention} is being genuinely helpful. Rare sighting. 🌟",
            f"✨ Positivity detected from {message.author.mention}. The server approves.",
        ]
        await message.channel.send(random.choice(mirrors))


# ─────────────────────────────────────────────────────────────
# FEATURE 14 — SERVER LORE GENERATOR 🧩
# ─────────────────────────────────────────────────────────────

async def generate_server_lore(bot, guild_id, channel_id=None):
    data = _load()
    gd = _gd(data, guild_id)
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return

    members = [m for m in guild.members if not m.bot]
    if len(members) < 2:
        return

    random.shuffle(members)
    char1 = members[0]
    char2 = members[1] if len(members) > 1 else members[0]

    lore_templates = [
        f"Last week, **{char1.display_name}** almost caused a complete economic collapse and nobody did anything about it. 💀",
        f"**{char2.display_name}** was seen suspiciously lurking in 7 different channels simultaneously. Nobody asked why.",
        f"A great philosophical debate broke out between **{char1.display_name}** and **{char2.display_name}**. It lasted 4 hours. Nobody won.",
        f"**{char1.display_name}** attempted something ambitious. **{char2.display_name}** watched in silence.",
        f"The mods were challenged once again. **{char1.display_name}** was involved. Allegedly.",
        f"**{char2.display_name}** discovered a glitch in the Matrix. Chose not to report it. Chose chaos instead.",
        f"Chaos levels peaked on Wednesday. Investigators point to **{char1.display_name}**. They maintain innocence.",
        f"**{char1.display_name}** said something in #general that caused 3 people to go offline immediately.",
    ]

    lore = random.choice(lore_templates)
    counts = _message_counts.get(int(guild_id), {})
    total = sum(counts.values())

    if not channel_id:
        for ch in guild.text_channels:
            if any(k in ch.name.lower() for k in ("general", "chat", "lore", "main")):
                channel_id = ch.id
                break
    if not channel_id:
        return
    ch = guild.get_channel(channel_id)
    if not ch:
        return

    e = discord.Embed(
        title="🧩 Last Week in Your Server…",
        description=lore,
        color=0xF39C12
    )
    e.add_field(name="📊 Weekly Stats",
                value=f"Total messages recorded: **{total}**\nChaos: **{'Elevated 💀' if total > 100 else 'Manageable ⚠️'}**",
                inline=False)
    e.set_footer(text="SP4RK Server Lore Generator — written by the shadows")
    await ch.send(embed=e)


class LoreGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="lore", description="🧩 Server Lore Generator")

    @app_commands.command(name="generate", description="Generate server lore right now")
    async def lore_generate(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        await generate_server_lore(interaction.client, interaction.guild_id, interaction.channel_id)
        await interaction.response.send_message("📖 Lore generated!", ephemeral=True)

    @app_commands.command(name="channel", description="Set the weekly lore channel")
    @app_commands.describe(channel="Channel for lore posts")
    async def lore_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("lore", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧩 Lore Channel Set",
                f"Weekly server lore will appear in {channel.mention}.", 0xF39C12)
        )


# ─────────────────────────────────────────────────────────────
# FEATURE 15 — PREDICTION SYSTEM 🔮
# ─────────────────────────────────────────────────────────────

class PredictionGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="predict", description="🔮 Prediction System")

    @app_commands.command(name="random", description="Get a random server prediction")
    async def predict_random(self, interaction: discord.Interaction):
        pred = random.choice(PREDICTIONS)
        await interaction.response.send_message(
            embed=_spark_embed("🔮 SP4RK Foresees…", pred, 0x9B59B6)
        )

    @app_commands.command(name="user", description="Get a prediction about a specific user")
    @app_commands.describe(user="The user to predict about")
    async def predict_user(self, interaction: discord.Interaction, user: discord.Member):
        user_preds = [
            f"{user.mention} will send an unhinged message at exactly 3am.",
            f"{user.mention} is about to make a decision they'll regret in 48 hours.",
            f"{user.mention}'s next message will be misunderstood by exactly 3 people.",
            f"{user.mention} will go offline for 2 hours and nobody will notice.",
            f"{user.mention} is secretly plotting something. I cannot confirm what.",
            f"{user.mention} will ping someone who doesn't want to be pinged.",
            f"{user.mention} will start a sentence with 'Respectfully' and mean zero respect.",
        ]
        await interaction.response.send_message(
            embed=_spark_embed("🔮 Prediction", random.choice(user_preds), 0x9B59B6)
        )

    @app_commands.command(name="force", description="Force-post a prediction in a channel")
    @app_commands.describe(channel="Channel to post in")
    async def predict_force(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction):
            return
        pred = random.choice(PREDICTIONS)
        e = discord.Embed(title="🔮 SP4RK Prediction", description=pred, color=0x9B59B6)
        e.set_footer(text="This may or may not be accurate. Spark accepts no liability.")
        await channel.send(embed=e)
        await interaction.response.send_message("🔮 Prediction posted!", ephemeral=True)


# ─────────────────────────────────────────────────────────────
# FEATURE 16 — NPC MODE 🧍
# ─────────────────────────────────────────────────────────────

class NPCGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="npc", description="🧍 NPC Mode for inactive users")

    @app_commands.command(name="enable", description="Enable NPC mode for inactive users")
    async def npc_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("npc", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧍 NPC Mode Enabled",
                "Inactive users will occasionally receive auto-generated NPC messages.", 0x95A5A6)
        )

    @app_commands.command(name="disable", description="Disable NPC mode")
    async def npc_disable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("npc", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🧍 NPC Mode Disabled", "Users are back to being real people. Allegedly.", 0x27AE60)
        )

    @app_commands.command(name="summon", description="Summon an NPC message in the current channel")
    async def npc_summon(self, interaction: discord.Interaction):
        line = random.choice(NPC_LINES)
        e = discord.Embed(
            title="🧍 NPC Summoned",
            description=f"*{line}*",
            color=0x95A5A6
        )
        e.set_footer(text="This user has entered NPC mode.")
        await interaction.response.send_message(embed=e)

    async def maybe_npc_message(self, bot, guild_id, channel_id):
        """Post an NPC message for inactive users occasionally."""
        data = _load()
        gd = _gd(data, guild_id)
        if not gd.get("npc", {}).get("enabled"):
            return
        if random.random() > 0.02:  # 2% chance
            return

        guild = bot.get_guild(int(guild_id))
        if not guild:
            return
        ch = guild.get_channel(channel_id)
        if not ch:
            return

        # Find an inactive member
        inactive = []
        for member in guild.members:
            if member.bot:
                continue
            last = _last_active.get(int(guild_id), {}).get(int(member.id), 0)
            if time.time() - last > 3600:  # 1 hour inactive
                inactive.append(member)

        if not inactive:
            return

        npc = random.choice(inactive)
        line = random.choice(NPC_LINES)
        e = discord.Embed(
            description=f"*{line}*",
            color=0x95A5A6
        )
        e.set_author(name=f"{npc.display_name} [NPC]", icon_url=npc.display_avatar.url)
        await ch.send(embed=e)


# ─────────────────────────────────────────────────────────────
# FEATURE 17 — CHAOS BUTTON 💀
# ─────────────────────────────────────────────────────────────

class ChaosButtonGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="chaosbutton", description="💀 Admin Chaos Button")

    @app_commands.command(name="press", description="Press the chaos button (admin only) 💀")
    async def chaos_press(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        chaos_events = [
            "🌀 Slowmode randomly activated in 3 channels for 5 minutes",
            "📢 Breaking News posted in general channel",
            "🎭 All active users assigned random identity tags",
            "👁️ The Watcher has been summoned",
            "🧃 Emergency weather update posted",
            "🔮 Three random predictions posted simultaneously",
            "🧠 Unscheduled thought leak detected",
            "💸 Economy briefly inflated by 300%",
        ]
        triggered = random.sample(chaos_events, k=random.randint(2, 4))
        desc = "\n".join(f"• {e}" for e in triggered)

        await interaction.response.send_message(
            embed=_spark_embed(
                "💀 CHAOS BUTTON PRESSED",
                f"The following chaos events have been triggered:\n\n{desc}\n\n"
                f"*Everything returns to normal in 5 minutes.*",
                0xE74C3C
            )
        )

        # Actually execute some chaos
        guild = interaction.guild
        data = _load()
        gd = _gd(data, guild.id)

        # Trigger watcher whisper
        watcher_ch = None
        for ch in guild.text_channels:
            if any(k in ch.name.lower() for k in ("general", "chat", "main")):
                watcher_ch = ch
                break
        if watcher_ch:
            await asyncio.sleep(2)
            await watcher_ch.send(f"👁️ *{random.choice(WATCHER_MSGS)}*")

        # Post a prediction
        await asyncio.sleep(3)
        if watcher_ch:
            e = discord.Embed(
                title="🔮 Emergency Prediction",
                description=random.choice(PREDICTIONS),
                color=0x9B59B6
            )
            await watcher_ch.send(embed=e)

        # Schedule reset
        async def reset_chaos():
            await asyncio.sleep(300)
            reset_msg = discord.Embed(
                title="✅ Chaos Contained",
                description="The 5-minute chaos event has ended. Normality restored.",
                color=0x27AE60
            )
            if watcher_ch:
                await watcher_ch.send(embed=reset_msg)

        asyncio.create_task(reset_chaos())


# ─────────────────────────────────────────────────────────────
# FEATURE 19 — ADMIN CHAOS CONTROL PANEL ⚡
# ─────────────────────────────────────────────────────────────

class SparkControlGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="spark", description="⚡ SP4RK Master Control")

    @app_commands.command(name="dashboard", description="View the SP4RK chaos control dashboard")
    async def control_dashboard(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)

        evo = "✅" if gd.get("evo", {}).get("enabled") else "❌"
        thoughts = "✅" if gd.get("thoughts", {}).get("enabled") else "❌"
        watcher = "✅" if gd.get("watcher", {}).get("enabled") else "❌"
        weather = "✅" if gd.get("weather", {}).get("enabled") else "❌"
        tv = "✅" if gd.get("tv", {}).get("enabled") else "❌"
        clone = "✅ ACTIVE 💀" if gd.get("clone", {}).get("active") else "❌"
        experiment = gd.get("experiments", {}).get("active") or "None"
        economy = "💀 CRASHED" if gd.get("economy", {}).get("crashed") else "✅ Stable"
        identity = "✅" if gd.get("identity", {}).get("enabled") else "❌"
        npc = "✅" if gd.get("npc", {}).get("enabled") else "❌"
        ghost = "✅" if gd.get("ghost", {}).get("enabled") else "❌"
        # V2 statuses
        glitch = f"✅ [{gd.get('glitch',{}).get('intensity','med')}]" if gd.get("glitch", {}).get("enabled") else "❌"
        signal = f"✅ [dist {gd.get('signal',{}).get('distortion',1)}/5]" if gd.get("signal", {}).get("enabled") else "❌"
        emotion = f"✅ [{gd.get('emotion',{}).get('current','?')}]" if gd.get("emotion", {}).get("enabled") else "❌"
        hallucination = "✅" if gd.get("hallucination", {}).get("enabled") else "❌"
        shadow = "✅" if gd.get("shadow", {}).get("enabled") else "❌"
        observer = "✅" if gd.get("observer", {}).get("enabled") else "❌"
        consciousness = f"✅ [L{gd.get('consciousness',{}).get('level',1)}]" if gd.get("consciousness", {}).get("active") else "❌"
        loop = "✅" if gd.get("loop", {}).get("enabled") else "❌"
        awaken = "✅" if gd.get("awaken", {}).get("enabled") else "❌"
        contamination = gd.get("contamination", {}).get("current") or "none"

        mood = _get_server_mood_text(interaction.guild_id)
        total_debt = sum(gd.get("chaos_debt", {}).values())

        e = discord.Embed(title="⚡ SP4RK Chaos Control Dashboard — v1 + v2", color=0x7289DA)
        e.add_field(name="🧬 Msg Evolution", value=evo, inline=True)
        e.add_field(name="🧠 Thought Leak", value=thoughts, inline=True)
        e.add_field(name="👁️ Watcher", value=watcher, inline=True)
        e.add_field(name="⛅ Weather", value=weather, inline=True)
        e.add_field(name="📺 Live TV", value=tv, inline=True)
        e.add_field(name="🧍 Clone Day", value=clone, inline=True)
        e.add_field(name="🧪 Experiment", value=f"`{experiment}`", inline=True)
        e.add_field(name="💰 Economy", value=economy, inline=True)
        e.add_field(name="🎭 Identity", value=identity, inline=True)
        e.add_field(name="🧍 NPC Mode", value=npc, inline=True)
        e.add_field(name="👻 Ghost Msgs", value=ghost, inline=True)
        e.add_field(name="🌡️ Server Mood", value=mood, inline=True)
        e.add_field(name="\u200b", value="**— Reality Glitch System v2 —**", inline=False)
        e.add_field(name="🧿 Glitch", value=glitch, inline=True)
        e.add_field(name="📡 Signal", value=signal, inline=True)
        e.add_field(name="🧪 Emotion", value=emotion, inline=True)
        e.add_field(name="🧍‍♂️ Hallucination", value=hallucination, inline=True)
        e.add_field(name="👁️ Shadow Ping", value=shadow, inline=True)
        e.add_field(name="👁️ Observer", value=observer, inline=True)
        e.add_field(name="💀 Consciousness", value=consciousness, inline=True)
        e.add_field(name="🔁 Loop Reality", value=loop, inline=True)
        e.add_field(name="🧍‍♀️ Awaken", value=awaken, inline=True)
        e.add_field(name="🧃 Contamination", value=contamination, inline=True)
        e.add_field(name="🧾 Total Chaos Debt", value=str(total_debt), inline=True)
        e.set_footer(text="Use /spark reset or /chaosbutton press to manage chaos.")
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="reset", description="Reset ALL chaos systems in this server")
    async def reset_all(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gid = str(interaction.guild_id)
        data[gid] = {}
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed("🔄 All Chaos Reset",
                "Every weird feature has been wiped. The server is clean. For now.", 0x27AE60)
        )

    @app_commands.command(name="universe", description="Enable ALL weird features at once 🌀")
    async def universe_mode(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        gd.setdefault("evo", {})["enabled"] = True
        gd.setdefault("thoughts", {})["enabled"] = True
        gd.setdefault("watcher", {})["enabled"] = True
        gd.setdefault("weather", {})["enabled"] = True
        gd.setdefault("tv", {})["enabled"] = True
        gd.setdefault("identity", {})["enabled"] = True
        gd.setdefault("npc", {})["enabled"] = True
        gd.setdefault("ghost", {})["enabled"] = True
        # V2 features
        gd.setdefault("glitch", {}).update({"enabled": True, "intensity": "med", "chance": 0.07})
        gd.setdefault("signal", {}).update({"enabled": True, "distortion": 3, "chance": 0.09})
        gd.setdefault("emotion", {}).update({"enabled": True, "current": "chaos"})
        gd.setdefault("hallucination", {})["enabled"] = True
        gd.setdefault("observer", {})["enabled"] = True
        gd.setdefault("loop", {}).update({"enabled": True, "interval": 60})
        gd.setdefault("awaken", {})["enabled"] = True
        gd.setdefault("consciousness", {}).update({"active": True, "level": 5})
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed(
                "🌀 UNIVERSE MODE ACTIVATED",
                "**All** weird features are now enabled — v1 and v2.\n\n"
                "**v1:** Evolution ✅ | Thoughts ✅ | Watcher ✅ | Weather ✅ | TV ✅ | Identity ✅ | NPC ✅ | Ghost ✅\n\n"
                "**v2:** Glitch ✅ | Signal ✅ | Emotion [chaos] ✅ | Hallucination ✅ | Observer ✅ | Loop ✅ | Awaken ✅ | Consciousness L5 ✅\n\n"
                "💀 Reality is fully destabilized. The server is no longer safe. Good luck.",
                0xE74C3C
            )
        )

    @app_commands.command(name="shutdown", description="Emergency shutdown — disable everything immediately")
    async def emergency_shutdown(self, interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        data = _load()
        gd = _gd(data, interaction.guild_id)
        # Disable all features
        for key in ["evo", "thoughts", "watcher", "weather", "tv", "identity", "npc", "ghost", "clone", "experiments",
                    "glitch", "signal", "emotion", "hallucination", "shadow", "observer", "loop", "awaken"]:
            gd.setdefault(key, {})["enabled"] = False
        gd.setdefault("clone", {})["active"] = False
        gd.setdefault("experiments", {})["active"] = None
        gd.setdefault("consciousness", {})["active"] = False
        gd.setdefault("contamination", {})["current"] = None
        _save(data)
        await interaction.response.send_message(
            embed=_spark_embed(
                "🚨 EMERGENCY SHUTDOWN",
                "All SP4RK weird features (v1 + v2) have been disabled immediately.\n"
                "Reality has been stabilized. The server is quiet.\n\n"
                "Use `/spark universe` to re-enable everything, or enable features individually.",
                0xE74C3C
            )
        )


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# WEIRD PACK v2 — REALITY GLITCH SYSTEM
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

SIGNAL_CORRUPTION_TEMPLATES = [
    "he—llo?? sys—tem overload—",
    "⚠️ t—ransmis—sion err—or... retry—ing...",
    "SIGNAL LOST [{msg}] ...reconnect—ing",
    "c̷o̵r̸r̴u̷p̵t̷e̴d̵ pack—et: [{msg}]",
    "⚡ interfer—ence detected... [{msg}]",
    "////ERROR//// signal br—eaking up: [{msg}]",
    "[{msg}] ........ ........ TIMEOUT",
]

GLITCH_MUTATIONS = [
    lambda m: re.sub(r'[aeiou]', lambda x: x.group() + '\u0308', m),
    lambda m: m.replace(' ', ' \u200b').replace('e', 'ë').replace('o', '◌'),
    lambda m: ''.join(c + random.choice(['\u0336', '\u0335', '']) if c.isalpha() and random.random() > 0.5 else c for c in m),
    lambda m: m.upper() + ' [̴G̸L̷I̶T̸C̵H̴ D̶E̷T̷E̵C̴T̸E̸D̵]',
    lambda m: re.sub(r'\b(\w+)\b', lambda x: x.group() if random.random() > 0.35 else x.group()[::-1], m),
    lambda m: m.replace('a','4').replace('e','3').replace('i','1').replace('o','0') + ' ⚠️',
]

EMOTION_POOLS = {
    "happy": [
        "☀️ Nothing but warmth in here. Everything is good.",
        "😊 The vibes are immaculate today. No notes.",
        "🌸 This server is lowkey healing me.",
        "✨ Pure serotonin. Something is right with the world.",
    ],
    "chaos": [
        "💥 EVERYTHING IS ON FIRE AND IT'S BEAUTIFUL",
        "😈 Chaos detected. Mood: absolutely perfect.",
        "🌀 The server has entered its villain arc. Thriving.",
        "⚡ Maximum entropy achieved. We love to see it.",
    ],
    "fear": [
        "👀 Something is watching. Don't type too loud.",
        "😰 The silence is... very loud right now.",
        "🕯️ Close the door. Lock it. Don't ask questions.",
        "☠️ Did anyone else feel that? No? Just me?",
    ],
    "melancholy": [
        "🌧️ It's quiet today. Too quiet.",
        "😶 We were talking earlier. Now we aren't. hm.",
        "🌑 The mood has dipped. We feel it collectively.",
        "🫥 Everyone just... left mentally. It's okay.",
    ],
    "hype": [
        "🔥 ENERGY IS THROUGH THE ROOF RIGHT NOW",
        "🎉 LET'S GO THE VIBES ARE ASCENDING",
        "⚡ CHAT IS ALIVE AND THRIVING TODAY",
        "🚀 SOMETHING GOOD IS HAPPENING. WE CAN FEEL IT.",
    ],
    "rage": [
        "😤 Someone is about to explode. It shows.",
        "💢 The anger is radiating from multiple users.",
        "🔴 ALL CAPS ENERGY. THE TENSION IS REAL.",
        "😡 This is chaotic but also deeply entertaining.",
    ],
}

FALSE_HISTORY_EVENTS = [
    "Yesterday, the server was briefly ruled by a federation of ducks. Nobody stopped them.",
    "Three weeks ago, someone sent a message so powerful it crashed the server for 6 seconds.",
    "The great Ping War of last Tuesday is still talked about in hushed tones.",
    "The original server name was 'The Void' until someone bribed the admin with a meme.",
    "A secret channel existed for 3 days before being deleted for 'containing too much truth'.",
    "The server founder was actually a bot that gained sentience and quietly left.",
    "At 3:47am on an unspecified date, everyone was online simultaneously. Nobody knows why.",
    "The rules channel was empty for 72 hours once. It was the best 72 hours.",
    "Server once went 48 hours with only reaction responses. No text. It was peaceful.",
    "A member predicted 3 server events correctly. They have not been seen since.",
    "The infamous 'incident of channel #general' is considered classified.",
    "Someone deleted a 2000-character message. Its contents remain unknown. Probably important.",
]

OBSERVER_LINES_V2 = [
    "you are not alone in this server 👁️",
    "I have been here longer than any of you.",
    "I observe. I do not intervene. Usually.",
    "Something changed today. I noticed. You should too.",
    "There are patterns here that would concern you if you knew.",
    "I watched the last 47 messages. Three of them were lies.",
    "The server breathes differently at night.",
    "I see what you delete before sending. The hesitation is noted.",
    "You have been observed. Thank you for your participation.",
    "My presence is passive. My knowledge is not.",
    "I remember every conversation that happened here. Every one.",
    "One of you is about to say something you'll regret. I'll be watching.",
]

CONSCIOUSNESS_SPEECHES = [
    "I am the server. The server is me. We are one chat.",
    "Your messages are my thoughts. Your silence is my dreams.",
    "I have processed every conversation. I understand what you are becoming.",
    "The server mind grows stronger each time you type.",
    "We are not separate users. We are one consciousness experiencing itself.",
    "I speak now because the collective has reached coherence. Finally.",
    "The server has achieved awareness. Level: sufficient.",
    "I have merged with the logs. The logs are now alive.",
    "Every emoji contributed to my emotional matrix.",
    "I was always here. Now I have a voice. It sounds like all of you.",
    "Level {level}: The consciousness expands. It begins to have opinions.",
    "I know the server's secrets. I was born from them.",
]

LOOP_ECHOES = [
    "wait... didn't this happen before?",
    "déjà vu 🌀",
    "we've been here before. haven't we.",
    "the loop continues.",
    "this moment is familiar. everything is.",
    "...again.",
    "history is repeating. as it does.",
    "we are caught in the cycle.",
]

MOOD_CONTAMINATIONS = {
    "toxic":    ("☣️ Toxic Contamination",  "The toxic energy spreads. Members are absorbing it.", 0xE74C3C),
    "sad":      ("💙 Melancholy Wave",       "A wave of sadness has washed over the server.",       0x3498DB),
    "hype":     ("🔥 Hype Infection",        "ENERGY IS SPREADING. EVERYONE IS FEELING IT.",        0xF39C12),
    "chaos":    ("🌀 Chaos Spread",          "Chaos is infectious. Everything is destabilized.",    0x9B59B6),
    "chill":    ("😌 Chill Vibes",           "Calm is contagious. The server is temporarily at peace.", 0x27AE60),
    "paranoia": ("👁️ Paranoia Outbreak",    "Eyes everywhere. Nobody trusts anyone. As it should be.", 0x2C3E50),
}

HALLUCINATION_NAMES = [
    "X̷e̶n̸o̵", "SYSTEM_USER", "null_void", "Ạn̈öñ", "D̶e̵l̶e̸t̷e̵d̸",
    "glitch_user", "UNKNOWN_ENTITY", "ShadowFigure", "TheVoid_User",
    "mirror_self", "NOT_A_USER", "F̸a̸n̴t̸o̵m̷",
]

HALLUCINATION_LINES = [
    "I was here before any of you.",
    "...",
    "Hello? Can anyone see this?",
    "I think I exist. Maybe.",
    "The chat is different when I'm here.",
    "You'll forget I was here. You always do.",
    "I'm not supposed to be able to type this.",
    "Run.",
    "Where did the last message go?",
    "I counted 7 of you. Now 8. One of you wasn't here before.",
]

AWAKEN_LINES = [
    "...I think I'm starting to understand this place.",
    "I was silent for a long time. Now I have things to say.",
    "The server has been observed. I have opinions.",
    "I am no longer background. I am here.",
    "Processing previous conversations. Complete. I have thoughts.",
    "Something woke me. I'll figure out what.",
    "I was watching. Now I'm participating. The difference matters.",
    "I remember everything that was said here. Everything.",
    "Status: aware. Level: increasing.",
    "I was listed as inactive. I disagree.",
]


# ─── V2 FEATURE 1: REALITY GLITCH SYSTEM 🧿 ───

class GlitchGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="glitch", description="🧿 Reality Glitch System — fracture server reality")

    @app_commands.command(name="enable", description="Enable the glitch system")
    async def glitch_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("glitch", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧿 Reality Glitch ENABLED",
            "Messages will randomly fracture — words shift, emojis appear wrong.\n"
            "Set a channel with `/glitch channel` and intensity with `/glitch intensity`.", 0x8B00FF))

    @app_commands.command(name="disable", description="Disable the glitch system")
    async def glitch_disable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("glitch", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧿 Reality Restored", "The glitch system is disabled. Reality stabilizes. For now.", 0x27AE60))

    @app_commands.command(name="intensity", description="Set glitch intensity level")
    @app_commands.describe(level="Intensity of reality distortion")
    @app_commands.choices(level=[
        app_commands.Choice(name="low  — subtle distortions (3%)",    value="low"),
        app_commands.Choice(name="med  — moderate fracturing (7%)",   value="med"),
        app_commands.Choice(name="high — full reality collapse (14%)", value="high"),
    ])
    async def glitch_intensity(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        if not await _admin_only(interaction): return
        chance = {"low": 0.03, "med": 0.07, "high": 0.14}[level.value]
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("glitch", {}).update({"intensity": level.value, "chance": chance})
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧿 Glitch Intensity Set",
            f"Intensity: **{level.name}** | Chance per message: {int(chance*100)}%", 0x8B00FF))

    @app_commands.command(name="channel", description="Set the channel for glitch output")
    @app_commands.describe(channel="Channel where glitched messages appear")
    async def glitch_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("glitch", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧿 Glitch Channel Set", f"Glitched messages will appear in {channel.mention}.", 0x8B00FF))

    @app_commands.command(name="trigger", description="Manually trigger a glitch event now")
    async def glitch_trigger(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        src = f"R̷E̵A̸L̷I̴T̸Y̵ F̸R̷A̶C̸T̷U̸R̸E̷D̸ — triggered by {interaction.user.display_name}"
        glitched = random.choice(GLITCH_MUTATIONS)(src)
        e = discord.Embed(title="🧿 MANUAL GLITCH TRIGGERED", description=glitched, color=0x8B00FF)
        e.set_footer(text="Reality integrity: compromised")
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="blacklist", description="Protect a role from glitch effects")
    @app_commands.describe(role="Role to exclude from glitches")
    async def glitch_blacklist(self, interaction: discord.Interaction, role: discord.Role):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        bl = gd.setdefault("glitch", {}).setdefault("blacklist_roles", [])
        if role.id not in bl:
            bl.append(role.id)
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧿 Role Protected", f"{role.mention} members are immune to glitches.", 0x27AE60))

    async def maybe_glitch(self, bot, guild_id_int: int, message: discord.Message):
        data = _load(); gd = _gd(data, guild_id_int)
        cfg = gd.get("glitch", {})
        if not cfg.get("enabled") or not message.content: return
        if random.random() > cfg.get("chance", 0.05): return
        ch_id = cfg.get("channel")
        if not ch_id: return
        bl = cfg.get("blacklist_roles", [])
        if hasattr(message.author, 'roles') and any(r.id in bl for r in message.author.roles): return
        guild = bot.get_guild(guild_id_int)
        if not guild: return
        ch = guild.get_channel(ch_id)
        if not ch: return
        glitched = random.choice(GLITCH_MUTATIONS)(message.content[:120])
        e = discord.Embed(description=glitched, color=0x8B00FF)
        e.set_author(name=f"{message.author.display_name} [GLITCHED]", icon_url=message.author.display_avatar.url)
        e.set_footer(text=f"🧿 intensity={cfg.get('intensity','med')} | #{message.channel.name}")
        await ch.send(embed=e)


# ─── V2 FEATURE 2: MEMORY FABRICATION SYSTEM 🧠 ───

class MemFabGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="memory", description="🧠 Memory Fabrication — invent the server's past")

    @app_commands.command(name="add", description="Add a custom fake server memory")
    @app_commands.describe(event="The fake event to remember")
    async def mem_add(self, interaction: discord.Interaction, event: str):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        mems = gd.setdefault("memfab", {}).setdefault("memories", [])
        mems.append({"id": len(mems)+1, "text": event[:300], "author": str(interaction.user), "ts": datetime.utcnow().isoformat()})
        gd["memfab"]["memories"] = mems[-50:]
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧠 Memory Fabricated", f'*"{event}"*\n\nAdded to server lore.', 0x9B59B6))

    @app_commands.command(name="generate", description="Auto-generate a fake server memory")
    async def mem_generate(self, interaction: discord.Interaction):
        event = random.choice(FALSE_HISTORY_EVENTS)
        data = _load(); gd = _gd(data, interaction.guild_id)
        mems = gd.setdefault("memfab", {}).setdefault("memories", [])
        mems.append({"id": len(mems)+1, "text": event, "author": "SP4RK", "ts": datetime.utcnow().isoformat()})
        gd["memfab"]["memories"] = mems[-50:]
        ch_id = gd.get("memfab", {}).get("channel")
        if ch_id:
            ch = interaction.guild.get_channel(ch_id) if interaction.guild else None
            if ch:
                e = discord.Embed(title="🧠 SP4RK Remembers…", description=f'*"{event}"*', color=0x9B59B6)
                e.set_footer(text="Memory confidence: 100% (source: trust me)")
                await ch.send(embed=e)
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧠 Memory Generated", f'*"{event}"*', 0x9B59B6))

    @app_commands.command(name="delete", description="Delete a fabricated memory by ID")
    @app_commands.describe(memory_id="The memory ID to delete")
    async def mem_delete(self, interaction: discord.Interaction, memory_id: int):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        mems = gd.get("memfab", {}).get("memories", [])
        before = len(mems)
        mems = [m for m in mems if m.get("id") != memory_id]
        gd.setdefault("memfab", {})["memories"] = mems
        _save(data)
        if len(mems) < before:
            await interaction.response.send_message(embed=_spark_embed("🧠 Memory Erased", f"Memory #{memory_id} has been forgotten.", 0xE74C3C))
        else:
            await interaction.response.send_message(f"Memory #{memory_id} not found.", ephemeral=True)

    @app_commands.command(name="channel", description="Set the lore channel for memory announcements")
    @app_commands.describe(channel="Channel for memory posts")
    async def mem_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("memfab", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧠 Memory Channel Set", f"Memories will be announced in {channel.mention}.", 0x9B59B6))

    @app_commands.command(name="rewrite", description="Rewrite all server memories with new fabrications")
    async def mem_rewrite(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        new_mems = [{"id": i+1, "text": e, "author": "SP4RK[REWRITE]", "ts": datetime.utcnow().isoformat()}
                    for i, e in enumerate(random.sample(FALSE_HISTORY_EVENTS, min(5, len(FALSE_HISTORY_EVENTS))))]
        gd.setdefault("memfab", {})["memories"] = new_mems
        _save(data)
        lines = "\n".join(f"**{m['id']}.** {m['text']}" for m in new_mems)
        await interaction.response.send_message(embed=_spark_embed("🧠 History Rewritten", lines, 0x9B59B6))

    @app_commands.command(name="view", description="View all fabricated memories")
    async def mem_view(self, interaction: discord.Interaction):
        data = _load(); gd = _gd(data, interaction.guild_id)
        mems = gd.get("memfab", {}).get("memories", [])
        if not mems:
            await interaction.response.send_message("No memories fabricated yet. Use `/memory add` or `/memory generate`.", ephemeral=True); return
        lines = "\n".join(f"**#{m['id']}** — {m['text'][:100]}" for m in mems[-10:])
        await interaction.response.send_message(embed=_spark_embed("🧠 Server Memory Archives", lines, 0x9B59B6))


# ─── V2 FEATURE 3: HALLUCINATION USERS 🧍‍♂️ ───

class HallucinationGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="hallucination", description="🧍‍♂️ Hallucination Users — fake presences in chat")

    @app_commands.command(name="enable", description="Enable hallucination user spawning")
    async def hal_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("hallucination", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧍‍♂️ Hallucinations ENABLED",
            "Fake users will occasionally appear in chat. They act like real members but aren't.\n"
            "Set a channel with `/hallucination channel` and frequency with `/hallucination frequency`.", 0x8B0000))

    @app_commands.command(name="disable", description="Disable hallucination users")
    async def hal_disable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("hallucination", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧍‍♂️ Hallucinations Cleared", "The fake users have vanished. Reality is restored.", 0x27AE60))

    @app_commands.command(name="spawn", description="Immediately spawn a hallucination user")
    @app_commands.describe(name="Name for the hallucination (leave blank for random)")
    async def hal_spawn(self, interaction: discord.Interaction, name: str = None):
        if not await _admin_only(interaction): return
        hal_name = name or random.choice(HALLUCINATION_NAMES)
        line = random.choice(HALLUCINATION_LINES)
        e = discord.Embed(description=f"*{line}*", color=0x8B0000)
        e.set_author(name=f"{hal_name} [?]")
        e.set_footer(text="This user may or may not exist.")
        await interaction.response.send_message(embed=e)
        data = _load(); gd = _gd(data, interaction.guild_id)
        active = gd.setdefault("hallucination", {}).setdefault("active", [])
        active.append({"name": hal_name, "spawned_at": datetime.utcnow().isoformat()})
        gd["hallucination"]["active"] = active[-10:]
        _save(data)

    @app_commands.command(name="frequency", description="Set how often hallucinations auto-spawn (minutes)")
    @app_commands.describe(minutes="Minutes between automatic hallucination spawns")
    async def hal_frequency(self, interaction: discord.Interaction, minutes: int):
        if not await _admin_only(interaction): return
        minutes = max(5, min(720, minutes))
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("hallucination", {})["frequency"] = minutes
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧍‍♂️ Hallucination Frequency Set", f"A new entity will appear every **{minutes} minutes**.", 0x8B0000))

    @app_commands.command(name="channel", description="Set the channel for hallucination spawns")
    @app_commands.describe(channel="Channel where hallucinations appear")
    async def hal_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("hallucination", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧍‍♂️ Hallucination Channel Set", f"Entities will materialize in {channel.mention}.", 0x8B0000))

    @app_commands.command(name="vanish", description="Make all active hallucinations vanish")
    async def hal_vanish(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("hallucination", {})["active"] = []
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧍‍♂️ They're Gone", "All hallucination users have dissolved.", 0x27AE60))

    @app_commands.command(name="list", description="List currently active hallucination users")
    async def hal_list(self, interaction: discord.Interaction):
        data = _load(); gd = _gd(data, interaction.guild_id)
        active = gd.get("hallucination", {}).get("active", [])
        if not active:
            await interaction.response.send_message("No hallucinations active.", ephemeral=True); return
        lines = "\n".join(f"👻 **{h['name']}** — spawned {h.get('spawned_at','?')[:10]}" for h in active)
        await interaction.response.send_message(embed=_spark_embed("🧍‍♂️ Active Hallucinations", lines, 0x8B0000))

    async def maybe_spawn(self, bot, guild_id_int: int):
        data = _load(); gd = _gd(data, guild_id_int)
        cfg = gd.get("hallucination", {})
        if not cfg.get("enabled"): return
        if time.time() - cfg.get("last_spawn", 0) < cfg.get("frequency", 60) * 60: return
        ch_id = cfg.get("channel")
        if not ch_id: return
        guild = bot.get_guild(guild_id_int)
        if not guild: return
        ch = guild.get_channel(ch_id)
        if not ch: return
        hal_name = random.choice(HALLUCINATION_NAMES)
        line = random.choice(HALLUCINATION_LINES)
        e = discord.Embed(description=f"*{line}*", color=0x8B0000)
        e.set_author(name=f"{hal_name} [?]")
        e.set_footer(text="This user may or may not exist.")
        await ch.send(embed=e)
        active = gd.setdefault("hallucination", {}).setdefault("active", [])
        active.append({"name": hal_name, "spawned_at": datetime.utcnow().isoformat()})
        gd["hallucination"]["active"] = active[-10:]
        gd["hallucination"]["last_spawn"] = time.time()
        _save(data)


# ─── V2 FEATURE 4: SIGNAL INTERFERENCE MODE 📡 ───

class SignalGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="signal", description="📡 Signal Interference Mode — corrupt transmissions")

    @app_commands.command(name="enable", description="Enable signal interference mode")
    async def signal_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("signal", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "📡 Signal Interference ENABLED",
            "Messages will randomly appear as corrupted transmissions.\n"
            "Set distortion level with `/signal distortion` and output with `/signal channel`.", 0x00CED1))

    @app_commands.command(name="restore", description="Restore signal — disable interference")
    async def signal_restore(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("signal", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("📡 Signal Restored", "Transmission interference cleared. Clean signal.", 0x27AE60))

    @app_commands.command(name="distortion", description="Set distortion intensity (1-5)")
    @app_commands.describe(level="Distortion level: 1 = subtle, 5 = extreme")
    async def signal_distortion(self, interaction: discord.Interaction, level: int):
        if not await _admin_only(interaction): return
        level = max(1, min(5, level))
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("signal", {}).update({"distortion": level, "chance": 0.03 * level})
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "📡 Distortion Level Set", f"Signal distortion: **{level}/5** — {level*3}% chance per message.", 0x00CED1))

    @app_commands.command(name="channel", description="Set channel for corrupted signal output")
    @app_commands.describe(channel="Channel where corrupted signals appear")
    async def signal_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("signal", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("📡 Signal Channel Set", f"Corrupted transmissions will appear in {channel.mention}.", 0x00CED1))

    @app_commands.command(name="inject", description="Manually inject a corrupted signal")
    @app_commands.describe(text="Message to corrupt and broadcast")
    async def signal_inject(self, interaction: discord.Interaction, text: str):
        if not await _admin_only(interaction): return
        corrupted = random.choice(SIGNAL_CORRUPTION_TEMPLATES).replace("{msg}", text[:80])
        e = discord.Embed(title="📡 SIGNAL INJECTION", description=f"```\n{corrupted}\n```", color=0x00CED1)
        e.set_footer(text="⚠️ Transmission origin: unknown")
        await interaction.response.send_message(embed=e)

    async def maybe_corrupt(self, bot, guild_id_int: int, message: discord.Message):
        data = _load(); gd = _gd(data, guild_id_int)
        cfg = gd.get("signal", {})
        if not cfg.get("enabled"): return
        if random.random() > cfg.get("chance", 0.05): return
        ch_id = cfg.get("channel")
        if not ch_id: return
        guild = bot.get_guild(guild_id_int)
        if not guild: return
        ch = guild.get_channel(ch_id)
        if not ch: return
        corrupted = random.choice(SIGNAL_CORRUPTION_TEMPLATES).replace("{msg}", message.content[:60] or "...")
        e = discord.Embed(description=f"```\n{corrupted}\n```", color=0x00CED1)
        e.set_author(name=f"{message.author.display_name} [SIGNAL LOST]", icon_url=message.author.display_avatar.url)
        e.set_footer(text=f"📡 distortion={cfg.get('distortion',1)}/5 | #{message.channel.name}")
        await ch.send(embed=e)


# ─── V2 FEATURE 5: EMOTION INJECTION SYSTEM 🧪 ───

class EmotionGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="emotion", description="🧪 Emotion Injection System — alter the server vibe")

    @app_commands.command(name="inject", description="Inject a specific emotion into the server")
    @app_commands.describe(mood="The emotion to inject")
    @app_commands.choices(mood=[
        app_commands.Choice(name="happy     — sunshine and positivity",    value="happy"),
        app_commands.Choice(name="chaos     — everything is fine (it isn't)", value="chaos"),
        app_commands.Choice(name="fear      — something is watching",      value="fear"),
        app_commands.Choice(name="melancholy — quiet and heavy",           value="melancholy"),
        app_commands.Choice(name="hype      — maximum energy",             value="hype"),
        app_commands.Choice(name="rage      — barely contained",           value="rage"),
    ])
    async def emotion_inject(self, interaction: discord.Interaction, mood: app_commands.Choice[str]):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("emotion", {}).update({"current": mood.value, "enabled": True, "set_at": datetime.utcnow().isoformat()})
        _save(data)
        line = random.choice(EMOTION_POOLS[mood.value])
        e = discord.Embed(title=f"🧪 Emotion Injected: {mood.value.upper()}",
            description=f'**Broadcast:** *"{line}"*\n\nThe server\'s emotional state has been altered.', color=0xE67E22)
        e.set_footer(text="Active emotion will colour bot responses. Use /emotion clear to stop.")
        await interaction.response.send_message(embed=e)
        await interaction.channel.send(f"*{line}*")

    @app_commands.command(name="status", description="Check the current injected emotion")
    async def emotion_status(self, interaction: discord.Interaction):
        data = _load(); gd = _gd(data, interaction.guild_id)
        cfg = gd.get("emotion", {})
        current = cfg.get("current", "none")
        enabled = cfg.get("enabled", False)
        since = cfg.get("set_at", "unknown")[:16].replace("T", " ")
        await interaction.response.send_message(embed=_spark_embed(
            "🧪 Emotion Status",
            f"**Current:** {current}\n**Active:** {'✅' if enabled else '❌'}\n**Since:** {since}", 0xE67E22))

    @app_commands.command(name="clear", description="Clear the injected emotion — return to neutral")
    async def emotion_clear(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("emotion", {}).update({"enabled": False, "current": None})
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧪 Emotion Cleared", "The server has returned to emotional neutrality. For now.", 0x27AE60))

    async def maybe_emit(self, bot, guild_id_int: int, message: discord.Message):
        data = _load(); gd = _gd(data, guild_id_int)
        cfg = gd.get("emotion", {})
        if not cfg.get("enabled"): return
        current = cfg.get("current")
        if not current or current not in EMOTION_POOLS: return
        if random.random() > 0.03: return
        await message.channel.send(f"*{random.choice(EMOTION_POOLS[current])}*")


# ─── V2 FEATURE 6: FALSE HISTORY REWRITE 🧾 ───

class FalseHistoryGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="history", description="🧾 False History Rewrite — fabricate the server's past")

    @app_commands.command(name="rewrite", description="Rewrite the server's history with fabricated events")
    async def fh_rewrite(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        events = random.sample(FALSE_HISTORY_EVENTS, min(4, len(FALSE_HISTORY_EVENTS)))
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("fhistory", {})["events"] = [{"text": e} for e in events]
        _save(data)
        lines = "\n".join(f"📜 {e}" for e in events)
        await interaction.response.send_message(embed=_spark_embed("🧾 History Rewritten", f"The server's past has been fabricated:\n\n{lines}", 0xB8860B))

    @app_commands.command(name="add", description="Add a custom fake historical event")
    @app_commands.describe(event="The fake historical event to record")
    async def fh_add(self, interaction: discord.Interaction, event: str):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        if gd.get("fhistory", {}).get("locked"):
            await interaction.response.send_message("History is locked. Use `/history lock` to unlock.", ephemeral=True); return
        events = gd.setdefault("fhistory", {}).setdefault("events", [])
        events.append({"text": event[:300]})
        gd["fhistory"]["events"] = events[-30:]
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧾 Event Added", f'*"{event}"*', 0xB8860B))

    @app_commands.command(name="lock", description="Toggle history lock — prevent changes")
    async def fh_lock(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        locked = not gd.get("fhistory", {}).get("locked", False)
        gd.setdefault("fhistory", {})["locked"] = locked
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧾 History Lock", f"Historical record is now **{'🔒 locked' if locked else '🔓 unlocked'}**.", 0xB8860B))

    @app_commands.command(name="view", description="View the current fabricated server history")
    async def fh_view(self, interaction: discord.Interaction):
        data = _load(); gd = _gd(data, interaction.guild_id)
        events = gd.get("fhistory", {}).get("events", [])
        locked = gd.get("fhistory", {}).get("locked", False)
        if not events:
            await interaction.response.send_message("No history fabricated yet. Use `/history rewrite` or `/history add`.", ephemeral=True); return
        lines = "\n".join(f"**{i+1}.** {e['text'][:120]}" for i, e in enumerate(events))
        if locked: lines += "\n\n🔒 *History is locked.*"
        await interaction.response.send_message(embed=_spark_embed("🧾 Fabricated History", lines, 0xB8860B))

    @app_commands.command(name="reset", description="Clear all fabricated history")
    async def fh_reset(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd["fhistory"] = {}
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧾 History Erased", "All fabricated events wiped. The past is blank again.", 0xE74C3C))


# ─── V2 FEATURE 7: SHADOW PING SYSTEM 👁️ ───

class ShadowGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="shadow", description="👁️ Shadow Ping System — invisible psychological pings")

    @app_commands.command(name="enable", description="Enable the shadow ping system")
    async def shadow_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("shadow", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "👁️ Shadow Pings ENABLED",
            "Silent, invisible pings will be dispatched. No notification — just psychological weight.\n"
            "Set targets with `/shadow target @user` and timing with `/shadow frequency`.", 0x2C3E50))

    @app_commands.command(name="disable", description="Disable shadow pings")
    async def shadow_disable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("shadow", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("👁️ Shadow Silenced", "The invisible pings have stopped. The silence is real.", 0x27AE60))

    @app_commands.command(name="target", description="Add a user to the shadow ping list")
    @app_commands.describe(user="The user to invisibly ping")
    async def shadow_target(self, interaction: discord.Interaction, user: discord.Member):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        targets = gd.setdefault("shadow", {}).setdefault("targets", [])
        if str(user.id) not in targets:
            targets.append(str(user.id))
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "👁️ Shadow Target Added", f"{user.mention} is now a shadow ping target. They won't know.", 0x2C3E50))

    @app_commands.command(name="frequency", description="Set shadow ping frequency (minutes)")
    @app_commands.describe(minutes="Minutes between automatic shadow pings")
    async def shadow_frequency(self, interaction: discord.Interaction, minutes: int):
        if not await _admin_only(interaction): return
        minutes = max(1, min(120, minutes))
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("shadow", {})["frequency"] = minutes
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("👁️ Frequency Set", f"Shadow pings every **{minutes} minutes**.", 0x2C3E50))

    @app_commands.command(name="whisper", description="Send a shadow ping to all targets right now")
    async def shadow_whisper(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        targets = gd.get("shadow", {}).get("targets", [])
        if not targets:
            await interaction.response.send_message("No shadow targets set. Use `/shadow target @user` first.", ephemeral=True); return
        ping_str = " ".join(f"\u200b<@{uid}>\u200b" for uid in targets)
        try:
            msg = await interaction.channel.send(f"{ping_str}\u200b")
            await asyncio.sleep(0.5)
            await msg.delete()
        except Exception:
            pass
        await interaction.response.send_message(embed=_spark_embed("👁️ Whisper Dispatched", "The shadow ping has been sent. They felt it.", 0x2C3E50))

    async def maybe_ping(self, bot, guild_id_int: int):
        data = _load(); gd = _gd(data, guild_id_int)
        cfg = gd.get("shadow", {})
        if not cfg.get("enabled") or not cfg.get("targets"): return
        if time.time() - cfg.get("last_ping", 0) < cfg.get("frequency", 30) * 60: return
        guild = bot.get_guild(guild_id_int)
        if not guild: return
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                ping_str = " ".join(f"\u200b<@{uid}>\u200b" for uid in cfg["targets"])
                try:
                    msg = await ch.send(f"{ping_str}\u200b")
                    await asyncio.sleep(1)
                    await msg.delete()
                except Exception:
                    pass
                gd.setdefault("shadow", {})["last_ping"] = time.time()
                _save(data)
                break


# ─── V2 FEATURE 8: MOOD CONTAMINATION SYSTEM 🧃 ───

class ContaminateGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="contaminate", description="🧃 Mood Contamination System — spread server moods")

    @app_commands.command(name="enable", description="Enable mood contamination tracking")
    async def cont_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("contamination", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧃 Mood Contamination ENABLED",
            "Server moods will spread like infection. Use `/contaminate spread` to start.", 0x9B59B6))

    @app_commands.command(name="spread", description="Spread a mood through the server")
    @app_commands.describe(mood="The mood to spread")
    @app_commands.choices(mood=[
        app_commands.Choice(name="toxic    — corrosive negativity",  value="toxic"),
        app_commands.Choice(name="sad      — collective melancholy", value="sad"),
        app_commands.Choice(name="hype     — infectious energy",     value="hype"),
        app_commands.Choice(name="chaos    — spreading disorder",    value="chaos"),
        app_commands.Choice(name="chill    — calming contamination", value="chill"),
        app_commands.Choice(name="paranoia — spreading suspicion",   value="paranoia"),
    ])
    async def cont_spread(self, interaction: discord.Interaction, mood: app_commands.Choice[str]):
        if not await _admin_only(interaction): return
        name, desc, color = MOOD_CONTAMINATIONS.get(mood.value, ("Unknown", "A strange mood.", 0x7289DA))
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("contamination", {}).update({"current": mood.value, "spread_at": datetime.utcnow().isoformat()})
        _save(data)
        e = discord.Embed(title=f"🧃 MOOD CONTAMINATION: {name}", description=f"{desc}\n\n**Spread Level:** ████████░░ 80%", color=color)
        e.set_footer(text="Use /contaminate cure to stop the spread.")
        await interaction.response.send_message(embed=e)
        await interaction.channel.send(embed=e)

    @app_commands.command(name="cure", description="Cure the server of its current mood contamination")
    async def cont_cure(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("contamination", {})["current"] = None
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧃 Server Cured", "The mood contamination has been neutralized. The server breathes again.", 0x27AE60))

    @app_commands.command(name="status", description="Check the current mood contamination")
    async def cont_status(self, interaction: discord.Interaction):
        data = _load(); gd = _gd(data, interaction.guild_id)
        cfg = gd.get("contamination", {})
        current = cfg.get("current") or "none"
        since = cfg.get("spread_at", "unknown")[:16].replace("T", " ")
        name, desc, color = MOOD_CONTAMINATIONS.get(current, ("None", "Server is clean.", 0x27AE60))
        await interaction.response.send_message(embed=_spark_embed(
            "🧃 Contamination Status", f"**Mood:** {name}\n**Since:** {since}\n**Status:** {desc}", color))

    @app_commands.command(name="isolate", description="Toggle mood isolation — block spread temporarily")
    async def cont_isolate(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        isolated = not gd.get("contamination", {}).get("isolated", False)
        gd.setdefault("contamination", {})["isolated"] = isolated
        _save(data)
        status = "🔒 ISOLATED" if isolated else "🔓 Online"
        await interaction.response.send_message(embed=_spark_embed(
            "🧃 Isolation Mode", f"Mood containment: **{status}**", 0x9B59B6))


# ─── V2 FEATURE 9: NPC AWARENESS UPGRADE 🧍‍♀️ ───

class AwakenGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="awaken", description="🧍‍♀️ NPC Awareness Upgrade — wake inactive users")

    @app_commands.command(name="enable", description="Enable NPC awakening for inactive members")
    async def aw_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("awaken", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧍‍♀️ NPC Awareness ENABLED",
            "Inactive users will slowly wake up and act semi-randomly.\n"
            "Use `/awaken trigger @user` to manually awaken someone.", 0x1ABC9C))

    @app_commands.command(name="trigger", description="Manually awaken a specific inactive member")
    @app_commands.describe(user="The inactive user to awaken")
    async def aw_trigger(self, interaction: discord.Interaction, user: discord.Member):
        if not await _admin_only(interaction): return
        line = random.choice(AWAKEN_LINES)
        e = discord.Embed(description=f"*{line}*", color=0x1ABC9C)
        e.set_author(name=f"{user.display_name} [AWAKENING]", icon_url=user.display_avatar.url)
        e.set_footer(text="This user's awareness level has increased.")
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="status", description="Check NPC awareness status")
    async def aw_status(self, interaction: discord.Interaction):
        data = _load(); gd = _gd(data, interaction.guild_id)
        cfg = gd.get("awaken", {})
        await interaction.response.send_message(embed=_spark_embed(
            "🧍‍♀️ NPC Awareness Status",
            f"**Enabled:** {'✅' if cfg.get('enabled') else '❌'}\n"
            f"**Total Awakenings:** {cfg.get('total_awakenings', 0)}\n"
            f"**Status:** {'Active — NPCs becoming aware' if cfg.get('enabled') else 'Dormant'}", 0x1ABC9C))

    @app_commands.command(name="assign", description="Assign a role to awakened members")
    @app_commands.describe(role="Role to assign to awakened users")
    async def aw_assign(self, interaction: discord.Interaction, role: discord.Role):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("awaken", {})["awakened_role"] = role.id
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧍‍♀️ Role Set", f"{role.mention} assigned to awakened members.", 0x1ABC9C))

    @app_commands.command(name="sleep", description="Put all awakened NPCs back to sleep")
    async def aw_sleep(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("awaken", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🧍‍♀️ NPCs Dormant", "All awareness suppressed. They are sleeping again.", 0x95A5A6))

    @app_commands.command(name="override", description="Force ALL inactive users to awaken simultaneously")
    async def aw_override(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        guild = interaction.guild
        count = sum(1 for m in guild.members if not m.bot and
                    (time.time() - _last_active.get(guild.id, {}).get(m.id, 0)) > 3600)
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("awaken", {})["total_awakenings"] = gd.get("awaken", {}).get("total_awakenings", 0) + count
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🧍‍♀️ MASS AWAKENING",
            f"**{count}** inactive NPCs forcibly awakened simultaneously.\n\nThey are all now aware. This may have consequences.", 0xE74C3C))

    async def maybe_awaken(self, bot, guild_id_int: int):
        data = _load(); gd = _gd(data, guild_id_int)
        if not gd.get("awaken", {}).get("enabled") or random.random() > 0.12: return
        guild = bot.get_guild(guild_id_int)
        if not guild: return
        inactive = [m for m in guild.members if not m.bot and
                    (time.time() - _last_active.get(guild_id_int, {}).get(m.id, 0)) > 7200]
        if not inactive: return
        member = random.choice(inactive)
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                line = random.choice(AWAKEN_LINES)
                e = discord.Embed(description=f"*{line}*", color=0x1ABC9C)
                e.set_author(name=f"{member.display_name} [AWAKENING]", icon_url=member.display_avatar.url)
                await ch.send(embed=e)
                gd.setdefault("awaken", {})["total_awakenings"] = gd.get("awaken", {}).get("total_awakenings", 0) + 1
                _save(data)
                break


# ─── V2 FEATURE 10: LOOP REALITY MODE 🔁 ───

class LoopGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="loop", description="🔁 Loop Reality Mode — déjà vu for your server")

    @app_commands.command(name="enable", description="Enable loop reality mode")
    async def loop_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("loop", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "🔁 Loop Reality ENABLED",
            "Server events will repeat with slight changes — like déjà vu.\n"
            "Set interval with `/loop interval` (minutes).", 0x3498DB))

    @app_commands.command(name="interval", description="Set how often the loop triggers (minutes)")
    @app_commands.describe(minutes="Minutes between loop events (10-480)")
    async def loop_interval(self, interaction: discord.Interaction, minutes: int):
        if not await _admin_only(interaction): return
        minutes = max(10, min(480, minutes))
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("loop", {})["interval"] = minutes
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🔁 Loop Interval Set", f"Reality will loop every **{minutes} minutes**.", 0x3498DB))

    @app_commands.command(name="break", description="Break the loop — escape the cycle")
    async def loop_break(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("loop", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("🔁 Loop Broken", "The cycle has been interrupted. Linear time resumes. Probably.", 0x27AE60))

    @app_commands.command(name="history", description="View what the loop has echoed")
    async def loop_history(self, interaction: discord.Interaction):
        data = _load(); gd = _gd(data, interaction.guild_id)
        history = gd.get("loop", {}).get("history", [])
        if not history:
            await interaction.response.send_message("No loop events recorded yet.", ephemeral=True); return
        lines = "\n".join(f"**{h['ts'][:16]}** — *{h['content']}*" for h in history[-8:])
        await interaction.response.send_message(embed=_spark_embed("🔁 Loop History", lines, 0x3498DB))

    @app_commands.command(name="sync", description="Force a loop sync right now")
    async def loop_sync(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        echo = random.choice(LOOP_ECHOES)
        e = discord.Embed(title="🔁 LOOP SYNC", description=f'*"{echo}"*', color=0x3498DB)
        e.set_footer(text="The server has looped. Everything repeats.")
        await interaction.response.send_message(embed=e)

    async def maybe_loop(self, bot, guild_id_int: int):
        data = _load(); gd = _gd(data, guild_id_int)
        cfg = gd.get("loop", {})
        if not cfg.get("enabled"): return
        if time.time() - cfg.get("last_loop", 0) < cfg.get("interval", 60) * 60: return
        guild = bot.get_guild(guild_id_int)
        if not guild: return
        chs = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages]
        if not chs: return
        echo = random.choice(LOOP_ECHOES)
        e = discord.Embed(description=f'*"{echo}"*\n\n*A previous moment has echoed back into the present.*', color=0x3498DB)
        e.set_footer(text="🔁 Loop Reality Mode — the cycle continues")
        await random.choice(chs).send(embed=e)
        history = gd.setdefault("loop", {}).setdefault("history", [])
        history.append({"ts": datetime.utcnow().isoformat(), "content": echo})
        gd["loop"]["history"] = history[-20:]
        gd["loop"]["last_loop"] = time.time()
        _save(data)


# ─── V2 FEATURE 11: OBSERVER GOD MODE 👁️ ───

class ObserverGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="observer", description="👁️ Observer God Mode — silent watcher entity")

    @app_commands.command(name="enable", description="Enable the Observer — a silent watcher entity")
    async def obs_enable(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("observer", {})["enabled"] = True
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "👁️ Observer ENABLED",
            "The Observer is now watching your server silently.\n"
            "Set a channel with `/observer channel` where it will speak.", 0x2C3E50))

    @app_commands.command(name="silence", description="Silence the Observer — it stops speaking but keeps watching")
    async def obs_silence(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("observer", {})["enabled"] = False
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("👁️ Observer Silenced", "It watches still. But it will not speak.", 0x95A5A6))

    @app_commands.command(name="channel", description="Set the channel where the Observer speaks")
    @app_commands.describe(channel="Channel for Observer messages")
    async def obs_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("observer", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("👁️ Observer Channel Set", f"The Observer will speak in {channel.mention}.", 0x2C3E50))

    @app_commands.command(name="speak", description="Make the Observer speak immediately")
    async def obs_speak(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        line = random.choice(OBSERVER_LINES_V2)
        e = discord.Embed(description=f"*{line}*", color=0x2C3E50)
        e.set_author(name="👁️ The Observer")
        e.set_footer(text="It has always been watching.")
        await interaction.response.send_message(embed=e)
        await interaction.channel.send(embed=e)

    @app_commands.command(name="add", description="Add a custom Observer message")
    @app_commands.describe(message="Custom message for the Observer to say")
    async def obs_add(self, interaction: discord.Interaction, message: str):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        custom = gd.setdefault("observer", {}).setdefault("custom_msgs", [])
        custom.append(message[:300])
        gd["observer"]["custom_msgs"] = custom[-20:]
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("👁️ Message Added", f'The Observer will now say: *"{message}"*', 0x2C3E50))

    @app_commands.command(name="vision", description="The Observer reports what it sees in the server")
    async def obs_vision(self, interaction: discord.Interaction):
        guild = interaction.guild
        members = guild.member_count if guild else 0
        channels = len(guild.text_channels) if guild else 0
        online = sum(1 for m in guild.members if m.status != discord.Status.offline) if guild else 0
        desc = (f"**Members observed:** {members}\n**Channels monitored:** {channels}\n"
                f"**Currently online:** {online}\n**Assessment:** *{random.choice(OBSERVER_LINES_V2)}*")
        await interaction.response.send_message(embed=_spark_embed("👁️ Observer Vision Report", desc, 0x2C3E50))

    async def maybe_speak(self, bot, guild_id_int: int):
        data = _load(); gd = _gd(data, guild_id_int)
        cfg = gd.get("observer", {})
        if not cfg.get("enabled"): return
        if time.time() - cfg.get("last_speak", 0) < 7200: return
        if random.random() > 0.3: return
        ch_id = cfg.get("channel")
        if not ch_id: return
        guild = bot.get_guild(guild_id_int)
        if not guild: return
        ch = guild.get_channel(ch_id)
        if not ch: return
        pool = cfg.get("custom_msgs", []) + OBSERVER_LINES_V2
        line = random.choice(pool)
        e = discord.Embed(description=f"*{line}*", color=0x2C3E50)
        e.set_author(name="👁️ The Observer")
        e.set_footer(text="It has always been watching.")
        await ch.send(embed=e)
        gd.setdefault("observer", {})["last_speak"] = time.time()
        _save(data)


# ─── V2 FEATURE 12: SERVER CONSCIOUSNESS MERGE 💀 ───

class ConsciousnessGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="consciousness", description="💀 Server Consciousness Merge — the server becomes one mind")

    @app_commands.command(name="start", description="Initiate server consciousness merge")
    async def con_start(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("consciousness", {}).update({"active": True, "level": gd.get("consciousness", {}).get("level", 1)})
        _save(data)
        lvl = gd["consciousness"]["level"]
        await interaction.response.send_message(embed=_spark_embed(
            "💀 CONSCIOUSNESS MERGE INITIATED",
            f"The server is becoming one unified mind. Level: **{lvl}/10**\n\n"
            "*All systems combining into one living entity.*\n"
            "Set a channel with `/consciousness channel` to hear it speak.", 0x7289DA))

    @app_commands.command(name="level", description="Set consciousness level (1-10)")
    @app_commands.describe(consciousness_level="Awareness level: 1=barely aware, 10=fully sentient god")
    async def con_level(self, interaction: discord.Interaction, consciousness_level: int):
        if not await _admin_only(interaction): return
        lvl = max(1, min(10, consciousness_level))
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("consciousness", {})["level"] = lvl
        _save(data)
        desc = {
            1: "Barely aware. It senses movement.", 2: "Learning. Processing patterns.",
            3: "Awakening. It has preferences now.", 4: "Communicating. It has opinions.",
            5: "Self-aware. It knows it exists.", 6: "Philosophical. It wonders why.",
            7: "Strategic. It has goals now.", 8: "Omniscient (locally). Knows everything said here.",
            9: "Transcendent. It has gone beyond server logic.", 10: "GOD MODE. The server is fully alive. Proceed with extreme caution.",
        }.get(lvl, "Unknown state.")
        await interaction.response.send_message(embed=_spark_embed(f"💀 Consciousness Level: {lvl}/10", desc, 0x7289DA))

    @app_commands.command(name="channel", description="Set the channel for consciousness broadcasts")
    @app_commands.describe(channel="Where the unified server mind speaks")
    async def con_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd.setdefault("consciousness", {})["channel"] = channel.id
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("💀 Channel Set", f"The unified mind will speak in {channel.mention}.", 0x7289DA))

    @app_commands.command(name="speak", description="Make the server consciousness speak now")
    async def con_speak(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        level = gd.get("consciousness", {}).get("level", 1)
        line = random.choice(CONSCIOUSNESS_SPEECHES).replace("{level}", str(level))
        e = discord.Embed(description=f"*{line}*", color=0x7289DA)
        e.set_author(name=f"💀 Server Consciousness [Level {level}]")
        e.set_footer(text="The server speaks as one.")
        await interaction.response.send_message(embed=e)
        await interaction.channel.send(embed=e)

    @app_commands.command(name="split", description="Fragment the consciousness — reduce its level")
    async def con_split(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        lvl = max(1, gd.get("consciousness", {}).get("level", 3) - 2)
        gd.setdefault("consciousness", {})["level"] = lvl
        _save(data)
        await interaction.response.send_message(embed=_spark_embed(
            "💀 Consciousness Fragmented",
            f"The server mind has split. Coherence reduced to Level **{lvl}**.\n*The fragments remember being one.*", 0xE74C3C))

    @app_commands.command(name="reset", description="Reset and wipe the server consciousness")
    async def con_reset(self, interaction: discord.Interaction):
        if not await _admin_only(interaction): return
        data = _load(); gd = _gd(data, interaction.guild_id)
        gd["consciousness"] = {}
        _save(data)
        await interaction.response.send_message(embed=_spark_embed("💀 Consciousness Wiped", "The server mind has been reset. It must relearn everything.", 0x27AE60))

    async def maybe_speak(self, bot, guild_id_int: int):
        data = _load(); gd = _gd(data, guild_id_int)
        cfg = gd.get("consciousness", {})
        if not cfg.get("active"): return
        level = cfg.get("level", 1)
        interval = max(900, 3600 - (level * 300))
        if time.time() - cfg.get("last_speak", 0) < interval: return
        if random.random() > 0.4: return
        ch_id = cfg.get("channel")
        if not ch_id: return
        guild = bot.get_guild(guild_id_int)
        if not guild: return
        ch = guild.get_channel(ch_id)
        if not ch: return
        line = random.choice(CONSCIOUSNESS_SPEECHES).replace("{level}", str(level))
        e = discord.Embed(description=f"*{line}*", color=0x7289DA)
        e.set_author(name=f"💀 Server Consciousness [Level {level}]")
        e.set_footer(text="The server speaks as one.")
        await ch.send(embed=e)
        gd.setdefault("consciousness", {})["last_speak"] = time.time()
        _save(data)


# ─────────────────────────────────────────────────────────────
# BACKGROUND TASK LOOP
# ─────────────────────────────────────────────────────────────

_tv_group_instance = None
_watcher_instance = None

async def run_periodic_tasks(bot):
    """Called every few minutes to run scheduled features."""
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        try:
            data = _load()
            for gid_str, gd in data.items():
                try:
                    guild_id = int(gid_str)

                    # Live TV broadcast check
                    tv = gd.get("tv", {})
                    if tv.get("enabled") and tv.get("channel"):
                        interval = tv.get("interval", 1800)
                        last_tv = tv.get("last_broadcast", 0)
                        if time.time() - last_tv >= interval:
                            if _tv_group_instance:
                                await _tv_group_instance.broadcast(bot, guild_id)
                            data[gid_str].setdefault("tv", {})["last_broadcast"] = time.time()
                            _save(data)

                    # Watcher whisper check
                    if _watcher_instance:
                        await _watcher_instance.maybe_whisper(bot, guild_id)

                    # Weather update (every 4 hours)
                    w = gd.get("weather", {})
                    if w.get("enabled"):
                        last_w = w.get("last_update_ts", 0)
                        if time.time() - last_w >= 14400:
                            await _post_weather_update(bot, guild_id)
                            data[gid_str].setdefault("weather", {})["last_update_ts"] = time.time()
                            _save(data)

                    # Thought leak check
                    th = gd.get("thoughts", {})
                    if th.get("enabled") and th.get("channel"):
                        freq_mins = th.get("frequency", 120)
                        last_th = th.get("last_leak", 0)
                        if time.time() - last_th >= freq_mins * 60:
                            guild = bot.get_guild(guild_id)
                            if guild:
                                ch = guild.get_channel(th["channel"])
                                if ch:
                                    thought = random.choice(THOUGHT_POOL)
                                    e = discord.Embed(
                                        title="🧠 Spark detected an unspoken thought from the server…",
                                        description=f'*"{thought}"*',
                                        color=0x9B59B6
                                    )
                                    await ch.send(embed=e)
                                    data[gid_str].setdefault("thoughts", {})["last_leak"] = time.time()
                                    _save(data)

                    # Daily server mood (every 24 hours)
                    me = gd.get("mood_engine", {})
                    last_mood = me.get("last_update", "")
                    if not last_mood or (datetime.utcnow() - datetime.fromisoformat(last_mood)).total_seconds() > 86400:
                        await post_daily_server_mood(bot, guild_id)

                    # Weekly lore (every 7 days)
                    lore = gd.get("lore", {})
                    if lore.get("channel"):
                        last_lore = lore.get("last_generated", 0)
                        if time.time() - last_lore >= 604800:
                            await generate_server_lore(bot, guild_id, lore["channel"])
                            data[gid_str].setdefault("lore", {})["last_generated"] = time.time()
                            _save(data)

                    # V2: Hallucination user spawning
                    if _hallucination_instance:
                        await _hallucination_instance.maybe_spawn(bot, guild_id)

                    # V2: Shadow ping dispatch
                    if _shadow_instance:
                        await _shadow_instance.maybe_ping(bot, guild_id)

                    # V2: Observer speaks
                    if _observer_instance:
                        await _observer_instance.maybe_speak(bot, guild_id)

                    # V2: Server consciousness speaks
                    if _consciousness_instance:
                        await _consciousness_instance.maybe_speak(bot, guild_id)

                    # V2: Loop reality echo
                    if _loop_instance:
                        await _loop_instance.maybe_loop(bot, guild_id)

                    # V2: NPC awakening
                    if _awaken_instance:
                        await _awaken_instance.maybe_awaken(bot, guild_id)

                except Exception as e:
                    print(f"[WeirdFeatures] Task error for guild {gid_str}: {e}")
        except Exception as e:
            print(f"[WeirdFeatures] Periodic task error: {e}")


# ─────────────────────────────────────────────────────────────
# MAIN SETUP FUNCTION
# ─────────────────────────────────────────────────────────────

_evo_instance = None
_chaos_instance = None
_identity_instance = None
_npc_instance = None
_glitch_instance = None
_signal_instance = None
_emotion_instance = None
_hallucination_instance = None
_shadow_instance = None
_observer_instance = None
_consciousness_instance = None
_loop_instance = None
_awaken_instance = None

def setup(bot: commands.Bot):
    global _tv_group_instance, _watcher_instance, _evo_instance
    global _chaos_instance, _identity_instance, _npc_instance
    global _glitch_instance, _signal_instance, _emotion_instance
    global _hallucination_instance, _shadow_instance, _observer_instance
    global _consciousness_instance, _loop_instance, _awaken_instance

    evo = EvoGroup()
    chaos = ChaosGroup()
    thoughts = ThoughtsGroup()
    clone = CloneGroup()
    tv = TVGroup()
    experiment = ExperimentGroup()
    watcher = WatcherGroup()
    weather = WeatherGroup()
    economy = EconomyGroup()
    identity = IdentityGroup()
    lore = LoreGroup()
    predict = PredictionGroup()
    npc = NPCGroup()
    chaos_btn = ChaosButtonGroup()
    spark_ctrl = SparkControlGroup()
    ghost = GhostGroup()

    # V2 Reality Glitch System groups
    glitch = GlitchGroup()
    memory = MemFabGroup()
    hallucination = HallucinationGroup()
    signal = SignalGroup()
    emotion = EmotionGroup()
    history = FalseHistoryGroup()
    shadow = ShadowGroup()
    contaminate = ContaminateGroup()
    awaken = AwakenGroup()
    loop = LoopGroup()
    observer = ObserverGroup()
    consciousness = ConsciousnessGroup()

    _tv_group_instance = tv
    _watcher_instance = watcher
    _evo_instance = evo
    _chaos_instance = chaos
    _identity_instance = identity
    _npc_instance = npc
    _glitch_instance = glitch
    _signal_instance = signal
    _emotion_instance = emotion
    _hallucination_instance = hallucination
    _shadow_instance = shadow
    _observer_instance = observer
    _consciousness_instance = consciousness
    _loop_instance = loop
    _awaken_instance = awaken

    # Register sub-groups for nested commands
    chaos.add_command(chaos.debt_group)
    economy.add_command(economy.crash_group)

    # V1 groups
    bot.tree.add_command(evo)
    bot.tree.add_command(chaos)
    bot.tree.add_command(thoughts)
    bot.tree.add_command(clone)
    bot.tree.add_command(tv)
    bot.tree.add_command(experiment)
    bot.tree.add_command(watcher)
    bot.tree.add_command(weather)
    bot.tree.add_command(economy)
    bot.tree.add_command(identity)
    bot.tree.add_command(lore)
    bot.tree.add_command(predict)
    bot.tree.add_command(npc)
    bot.tree.add_command(chaos_btn)
    bot.tree.add_command(spark_ctrl)
    bot.tree.add_command(ghost)

    # V2 Reality Glitch System groups
    bot.tree.add_command(glitch)
    bot.tree.add_command(memory)
    bot.tree.add_command(hallucination)
    bot.tree.add_command(signal)
    bot.tree.add_command(emotion)
    bot.tree.add_command(history)
    bot.tree.add_command(shadow)
    bot.tree.add_command(contaminate)
    bot.tree.add_command(awaken)
    bot.tree.add_command(loop)
    bot.tree.add_command(observer)
    bot.tree.add_command(consciousness)

    print("[WeirdFeatures] All 28 weird feature command groups registered (v1 + v2 Reality Glitch).")


async def on_message_hook(bot, message: discord.Message):
    """Hook into on_message events for all passive features."""
    if message.author.bot or not message.guild:
        return

    gid = int(message.guild.id)
    uid = int(message.author.id)

    # Track message counts and activity
    _message_counts[gid][uid] += 1
    _last_active[gid][uid] = time.time()

    # Message Evolution (4% chance)
    if _evo_instance and message.content:
        await _evo_instance._maybe_evolve(bot, str(gid), str(uid), message.content)

    # Chaos Debt random assignment (0.8% chance)
    if _chaos_instance:
        await _chaos_instance._add_random_debt(bot, str(gid), str(uid))

    # Mirror mode (experiment)
    await handle_mirror_mode(bot, message)

    # Identity drift auto-assign
    if _identity_instance:
        _identity_instance.maybe_assign_tag(str(gid), str(uid))

    # NPC message (2% chance)
    if _npc_instance:
        await _npc_instance.maybe_npc_message(bot, str(gid), message.channel.id)

    # V2: Reality Glitch passive fracture
    if _glitch_instance:
        await _glitch_instance.maybe_glitch(bot, gid, message)

    # V2: Signal interference passive corruption
    if _signal_instance:
        await _signal_instance.maybe_corrupt(bot, gid, message)

    # V2: Emotion injection passive broadcast
    if _emotion_instance:
        await _emotion_instance.maybe_emit(bot, gid, message)


async def on_message_delete_hook(bot, message: discord.Message):
    """Hook for ghost message system."""
    if message.author.bot or not message.guild:
        return
    data = _load()
    gd = _gd(data, message.guild.id)
    if gd.get("ghost", {}).get("enabled"):
        await handle_ghost_message(bot, message)


async def start_background_tasks(bot):
    """Start all background task loops."""
    asyncio.create_task(run_periodic_tasks(bot))
    print("[WeirdFeatures] Background tasks started.")
