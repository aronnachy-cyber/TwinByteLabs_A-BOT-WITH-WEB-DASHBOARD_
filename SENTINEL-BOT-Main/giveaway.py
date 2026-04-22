import discord
import json
import os
import random
import asyncio
from datetime import datetime, timedelta

GIVEAWAY_FILE = "giveaways.json"

# Track scheduled end-tasks so we don't double-schedule the same giveaway
_SCHEDULED = set()

def load_giveaways():
    if os.path.exists(GIVEAWAY_FILE):
        with open(GIVEAWAY_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {"giveaways": []}
    return {"giveaways": []}

def save_giveaways(data):
    # Atomic write so a crash mid-write can't corrupt the JSON
    tmp = GIVEAWAY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, GIVEAWAY_FILE)

def get_all_giveaways():
    return load_giveaways().get("giveaways", [])

async def start_giveaway(bot, channel, duration_seconds, prize, host, winners=1):
    ends_at = (datetime.utcnow() + timedelta(seconds=duration_seconds)).isoformat()
    embed = discord.Embed(
        title="🎉 GIVEAWAY 🎉",
        description=(
            f"**Prize:** {prize}\n"
            f"**Winners:** {winners}\n"
            f"**Hosted by:** {host.mention}\n"
            f"**Ends:** <t:{int((datetime.utcnow() + timedelta(seconds=duration_seconds)).timestamp())}:R>\n\n"
            f"React with 🎉 to enter!"
        ),
        color=0x3a8fff
    )
    embed.set_footer(text=f"Ends at {(datetime.utcnow() + timedelta(seconds=duration_seconds)).strftime('%Y-%m-%d %H:%M')} UTC")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("🎉")

    data = load_giveaways()
    giveaway = {
        "id": msg.id,
        "channel_id": channel.id,
        "guild_id": channel.guild.id,
        "message_id": msg.id,
        "prize": prize,
        "winners": winners,
        "host_id": host.id,
        "host_name": str(host),
        "ends_at": ends_at,
        "ended": False,
        "created_at": datetime.utcnow().isoformat()
    }
    data["giveaways"].append(giveaway)
    save_giveaways(data)

    _schedule(bot, msg.id, channel.id, duration_seconds)
    return giveaway

def _schedule(bot, message_id, channel_id, delay):
    """Schedule a giveaway to end after `delay` seconds. Idempotent per message_id."""
    if message_id in _SCHEDULED:
        return
    _SCHEDULED.add(message_id)
    asyncio.create_task(_schedule_end(bot, message_id, channel_id, max(0, delay)))

async def _schedule_end(bot, message_id, channel_id, delay):
    try:
        await asyncio.sleep(delay)
        await end_giveaway(bot, message_id, channel_id)
    finally:
        _SCHEDULED.discard(message_id)

async def resume_pending(bot):
    """
    Re-schedule (or immediately end) every giveaway that was active before the bot restarted.
    Without this, `asyncio.create_task(_schedule_end)` is lost on every restart and giveaways
    stay marked active forever, so completed-counts on the dashboard never go up.
    """
    data = load_giveaways()
    now = datetime.utcnow()
    resumed = 0
    ended_immediately = 0
    for g in data.get("giveaways", []):
        if g.get("ended"):
            continue
        ends_at_str = g.get("ends_at")
        if not ends_at_str:
            continue
        try:
            ends_at = datetime.fromisoformat(ends_at_str)
        except Exception:
            continue
        delay = (ends_at - now).total_seconds()
        try:
            mid = int(g.get("message_id"))
            cid = int(g.get("channel_id"))
        except (TypeError, ValueError):
            continue
        if delay <= 0:
            # Was supposed to end while bot was offline — end it now
            asyncio.create_task(end_giveaway(bot, mid, cid))
            ended_immediately += 1
        else:
            _schedule(bot, mid, cid, delay)
            resumed += 1
    if resumed or ended_immediately:
        print(f"[GIVEAWAY] Resume on startup: {resumed} re-scheduled, {ended_immediately} ended immediately.")

async def end_giveaway(bot, message_id, channel_id, force=False):
    data = load_giveaways()
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        mid = message_id
    giveaway = next(
        (g for g in data["giveaways"]
         if str(g.get("message_id")) == str(mid)),
        None
    )
    if not giveaway or giveaway.get("ended"):
        return None

    try:
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(mid))
        reaction = discord.utils.get(message.reactions, emoji="🎉")
        entries = []
        if reaction:
            async for user in reaction.users():
                if not user.bot:
                    entries.append(user)

        num_winners = giveaway.get("winners", 1)
        if entries:
            winners = random.sample(entries, min(num_winners, len(entries)))
            winner_mentions = ", ".join(w.mention for w in winners)
            winner_names = ", ".join(str(w) for w in winners)
            embed = discord.Embed(
                title="🎉 Giveaway Ended!",
                description=(
                    f"**Prize:** {giveaway['prize']}\n"
                    f"**Winner(s):** {winner_mentions}\n"
                    f"**Hosted by:** <@{giveaway['host_id']}>"
                ),
                color=0x2ecc71
            )
            await message.edit(embed=embed)
            await channel.send(f"🎉 Congratulations {winner_mentions}! You won **{giveaway['prize']}**!")
            giveaway["winner_names"] = winner_names
        else:
            embed = discord.Embed(
                title="🎉 Giveaway Ended",
                description=f"**Prize:** {giveaway['prize']}\nNo valid entries. No winner.",
                color=0xe74c3c
            )
            await message.edit(embed=embed)
            await channel.send("😔 No one entered the giveaway. No winner.")
            giveaway["winner_names"] = "No winner"

        giveaway["ended"] = True
        giveaway["ended_at"] = datetime.utcnow().isoformat()
        save_giveaways(data)
        return giveaway

    except Exception as e:
        print(f"End giveaway error: {e}")
        giveaway["ended"] = True
        save_giveaways(data)
        return None

def force_set_winner(message_id, winner_name):
    """Manually force a winner on an active giveaway. Returns the giveaway dict or None."""
    data = load_giveaways()
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        mid = message_id
    giveaway = next(
        (g for g in data["giveaways"] if g.get("message_id") == mid and not g.get("ended")),
        None
    )
    if not giveaway:
        return None
    giveaway["winner_names"] = winner_name
    giveaway["forced_winner"] = True
    giveaway["ended"] = True
    giveaway["ended_at"] = datetime.utcnow().isoformat()
    save_giveaways(data)
    return giveaway

async def reroll_giveaway(bot, message_id, channel_id):
    data = load_giveaways()
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        mid = message_id
    giveaway = next(
        (g for g in data["giveaways"]
         if str(g.get("message_id")) == str(mid)),
        None
    )
    if not giveaway or not giveaway.get("ended"):
        return None
    try:
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(mid))
        reaction = discord.utils.get(message.reactions, emoji="🎉")
        entries = []
        if reaction:
            async for user in reaction.users():
                if not user.bot:
                    entries.append(user)
        if entries:
            winner = random.choice(entries)
            await channel.send(f"🔄 Reroll! New winner: {winner.mention}! Congrats on **{giveaway['prize']}**!")
            return winner
    except Exception as e:
        print(f"Reroll error: {e}")
    return None
