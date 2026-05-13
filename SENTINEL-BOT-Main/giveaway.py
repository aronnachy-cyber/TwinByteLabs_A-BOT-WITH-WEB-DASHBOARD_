import discord
import json
import os
import random
import asyncio
from datetime import datetime, timedelta

GIVEAWAY_FILE = "giveaways.json"
_TABLE = "sentinel_giveaways"

# Track scheduled end-tasks so we don't double-schedule the same giveaway
_SCHEDULED = set()


def _get_supabase():
    try:
        from dashboard import supabase
        return supabase
    except Exception:
        return None


# ── Postgres + Local JSON fallback ──────────────────────────────────────────

def _local_load():
    try:
        import db_spark as _ds
        if _ds.is_available():
            val = _ds.get_blob("giveaways")
            if val is not None:
                return val
    except Exception:
        pass
    if os.path.exists(GIVEAWAY_FILE):
        try:
            with open(GIVEAWAY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"giveaways": []}


def _local_save(data):
    try:
        import db_spark as _ds
        if _ds.is_available():
            _ds.set_blob("giveaways", data)
    except Exception:
        pass
    tmp = GIVEAWAY_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, GIVEAWAY_FILE)
    except Exception:
        pass


def _row_to_dict(row):
    """Normalize a Supabase row into the dict shape the rest of the bot/dashboard expects."""
    if not row:
        return None
    out = dict(row)
    # message_id is the primary key — keep both 'id' and 'message_id' populated for templates
    if "message_id" in out:
        try:
            out["message_id"] = int(out["message_id"])
        except (TypeError, ValueError):
            pass
        out.setdefault("id", out["message_id"])
    for k in ("channel_id", "guild_id", "host_id"):
        if out.get(k) is not None:
            try:
                out[k] = int(out[k])
            except (TypeError, ValueError):
                pass
    return out


# ── Public read API ──────────────────────────────────────────────────────────

def load_giveaways():
    """Return {'giveaways': [...]} — Supabase first, JSON fallback."""
    sb = _get_supabase()
    if sb:
        try:
            res = sb.table(_TABLE).select("*").order("created_at", desc=False).execute()
            return {"giveaways": [_row_to_dict(r) for r in (res.data or [])]}
        except Exception as e:
            print(f"[GIVEAWAY] Supabase load error: {e}")
    return _local_load()


def save_giveaways(data):
    """Local-only JSON save (used as fallback when Supabase is unavailable)."""
    _local_save(data)


def get_all_giveaways():
    return load_giveaways().get("giveaways", [])


def _find_one(message_id):
    sb = _get_supabase()
    mid = str(message_id)
    if sb:
        try:
            res = sb.table(_TABLE).select("*").eq("message_id", mid).execute()
            if res.data:
                return _row_to_dict(res.data[0])
        except Exception as e:
            print(f"[GIVEAWAY] Supabase find error: {e}")
    # fallback
    data = _local_load()
    for g in data.get("giveaways", []):
        if str(g.get("message_id")) == mid:
            return g
    return None


def _upsert(record):
    """Insert or update a giveaway in Supabase, falling back to local JSON."""
    sb = _get_supabase()
    payload = {
        "message_id":   str(record["message_id"]),
        "channel_id":   str(record["channel_id"]),
        "guild_id":     str(record["guild_id"]),
        "prize":        record.get("prize", ""),
        "winners":      int(record.get("winners", 1)),
        "host_id":      str(record.get("host_id", "")),
        "host_name":    record.get("host_name", ""),
        "ends_at":      record.get("ends_at"),
        "ended":        bool(record.get("ended", False)),
        "created_at":   record.get("created_at"),
        "ended_at":     record.get("ended_at"),
        "winner_names": record.get("winner_names"),
        "forced_winner": bool(record.get("forced_winner", False)),
    }
    if sb:
        try:
            sb.table(_TABLE).upsert(payload, on_conflict="message_id").execute()
            return
        except Exception as e:
            print(f"[GIVEAWAY] Supabase upsert error: {e}")
    # fallback
    data = _local_load()
    found = False
    for i, g in enumerate(data.get("giveaways", [])):
        if str(g.get("message_id")) == str(record["message_id"]):
            data["giveaways"][i] = record
            found = True
            break
    if not found:
        data.setdefault("giveaways", []).append(record)
    _local_save(data)


# ── Public write API ─────────────────────────────────────────────────────────

async def start_giveaway(bot, channel, duration_seconds, prize, host, winners=1):
    ends_at_dt = datetime.utcnow() + timedelta(seconds=duration_seconds)
    ends_at = ends_at_dt.isoformat()
    import embeds as _emb
    embed = _emb.render(
        "sp4rk.giveaway_active",
        guild_id=getattr(channel.guild, "id", None),
        prize=prize, winners=winners, host=host,
        ends_at_relative=f"<t:{int(ends_at_dt.timestamp())}:R>",
        ends_at_absolute=f"{ends_at_dt.strftime('%Y-%m-%d %H:%M')} UTC",
    )
    msg = await channel.send(embed=embed)
    try:
        await msg.add_reaction("🎉")
    except discord.Forbidden:
        # Bot lacks "Add Reactions" — leave the giveaway alive but warn in console.
        print(f"[giveaway] could not add 🎉 reaction in #{channel.name} — missing Add Reactions permission.")

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
        "created_at": datetime.utcnow().isoformat(),
    }
    _upsert(giveaway)
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
    """Re-schedule (or immediately end) every active giveaway after a bot restart."""
    now = datetime.utcnow()
    resumed = 0
    ended_immediately = 0
    for g in get_all_giveaways():
        if g.get("ended"):
            continue
        ends_at_str = g.get("ends_at")
        if not ends_at_str:
            continue
        try:
            # Supabase may return a TZ-aware ISO string — strip timezone for comparison
            ends_at = datetime.fromisoformat(str(ends_at_str).replace("Z", "+00:00"))
            if ends_at.tzinfo is not None:
                ends_at = ends_at.replace(tzinfo=None)
        except Exception:
            continue
        try:
            mid = int(g.get("message_id"))
            cid = int(g.get("channel_id"))
        except (TypeError, ValueError):
            continue
        delay = (ends_at - now).total_seconds()
        if delay <= 0:
            asyncio.create_task(end_giveaway(bot, mid, cid))
            ended_immediately += 1
        else:
            _schedule(bot, mid, cid, delay)
            resumed += 1
    if resumed or ended_immediately:
        print(f"[GIVEAWAY] Resume on startup: {resumed} re-scheduled, {ended_immediately} ended immediately.")


async def end_giveaway(bot, message_id, channel_id, force=False):
    giveaway = _find_one(message_id)
    if not giveaway or giveaway.get("ended"):
        return None
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        mid = message_id

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
        import embeds as _emb
        gid_for_render = getattr(channel.guild, "id", None)
        if entries:
            winners = random.sample(entries, min(num_winners, len(entries)))
            winner_mentions = ", ".join(w.mention for w in winners)
            winner_names = ", ".join(str(w) for w in winners)
            embed = _emb.render(
                "sp4rk.giveaway_ended",
                guild_id=gid_for_render,
                prize=giveaway["prize"],
                winners_mentions=winner_mentions,
                host=f"<@{giveaway['host_id']}>",
            )
            await message.edit(embed=embed)
            await channel.send(f"🎉 Congratulations {winner_mentions}! You won **{giveaway['prize']}**!")
            giveaway["winner_names"] = winner_names
        else:
            embed = _emb.render(
                "sp4rk.giveaway_no_entries",
                guild_id=gid_for_render,
                prize=giveaway["prize"],
            )
            await message.edit(embed=embed)
            await channel.send("😔 No one entered the giveaway. No winner.")
            giveaway["winner_names"] = "No winner"

        giveaway["ended"] = True
        giveaway["ended_at"] = datetime.utcnow().isoformat()
        _upsert(giveaway)
        return giveaway

    except Exception as e:
        print(f"End giveaway error: {e}")
        giveaway["ended"] = True
        giveaway["ended_at"] = datetime.utcnow().isoformat()
        _upsert(giveaway)
        return None


def force_set_winner(message_id, winner_name):
    """Manually force a winner on an active giveaway. Returns the giveaway dict or None."""
    giveaway = _find_one(message_id)
    if not giveaway or giveaway.get("ended"):
        return None
    giveaway["winner_names"] = winner_name
    giveaway["forced_winner"] = True
    giveaway["ended"] = True
    giveaway["ended_at"] = datetime.utcnow().isoformat()
    _upsert(giveaway)
    return giveaway


async def reroll_giveaway(bot, message_id, channel_id):
    giveaway = _find_one(message_id)
    if not giveaway or not giveaway.get("ended"):
        return None
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        mid = message_id
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
