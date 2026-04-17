import discord
import json
import os
import random
import asyncio
from datetime import datetime, timedelta

GIVEAWAY_FILE = "giveaways.json"

def load_giveaways():
    if os.path.exists(GIVEAWAY_FILE):
        with open(GIVEAWAY_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {"giveaways": []}
    return {"giveaways": []}

def save_giveaways(data):
    with open(GIVEAWAY_FILE, "w") as f:
        json.dump(data, f, indent=4)

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

    asyncio.create_task(_schedule_end(bot, msg.id, channel.id, duration_seconds))
    return giveaway

async def _schedule_end(bot, message_id, channel_id, delay):
    await asyncio.sleep(delay)
    await end_giveaway(bot, message_id, channel_id)

async def end_giveaway(bot, message_id, channel_id, force=False):
    data = load_giveaways()
    giveaway = next((g for g in data["giveaways"] if g["message_id"] == message_id), None)
    if not giveaway or giveaway.get("ended"):
        return None

    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
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
    giveaway = next((g for g in data["giveaways"] if g["message_id"] == message_id), None)
    if not giveaway or not giveaway.get("ended"):
        return None
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
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
