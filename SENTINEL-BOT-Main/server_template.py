import discord
import json
import os
import asyncio
from datetime import datetime

TEMPLATE_FILE  = "server_template.json"
_PASTE_LOG_DIR = "paste_logs"   # one JSON file per guild stores IDs of pasted items

def _paste_log_path(guild_id):
    os.makedirs(_PASTE_LOG_DIR, exist_ok=True)
    return os.path.join(_PASTE_LOG_DIR, f"paste_{guild_id}.json")

def save_paste_log(guild_id: int, log: dict):
    """Persist a record of what IDs were created by /paste for this guild."""
    with open(_paste_log_path(guild_id), "w") as f:
        json.dump(log, f, indent=4)

def load_paste_log(guild_id: int) -> dict | None:
    """Return the paste log for this guild, or None if /paste was never run here."""
    path = _paste_log_path(guild_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def clear_paste_log(guild_id: int):
    path = _paste_log_path(guild_id)
    if os.path.exists(path):
        os.remove(path)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _overwrite_to_dict(overwrite: discord.PermissionOverwrite) -> dict:
    """Serialize a PermissionOverwrite to a plain dict of {perm: True/False/None}."""
    return {perm: value for perm, value in overwrite}

def _dict_to_overwrite(data: dict) -> discord.PermissionOverwrite:
    """Deserialize a plain dict back to a PermissionOverwrite."""
    ow = discord.PermissionOverwrite()
    for perm, value in data.items():
        try:
            setattr(ow, perm, value)
        except AttributeError:
            pass
    return ow

def _serialize_overwrites(channel, guild) -> list:
    """
    Return a list of overwrite records, keyed by role/member name so they
    can be re-applied by name in a different server.
    """
    result = []
    for target, overwrite in channel.overwrites.items():
        if isinstance(target, discord.Role):
            result.append({
                "type": "role",
                "name": target.name,
                "permissions": _overwrite_to_dict(overwrite)
            })
        elif isinstance(target, discord.Member):
            # Member-specific overwrites are skipped — they won't exist in another server
            pass
    return result

# ---------------------------------------------------------------------------
# COPY
# ---------------------------------------------------------------------------

async def copy_server(guild: discord.Guild) -> dict:
    """
    Snapshot the full structure of a guild and return it as a serialisable dict.
    """
    template = {
        "source_guild_name": guild.name,
        "copied_at": datetime.utcnow().isoformat(),
        "roles": [],
        "categories": [],
        "text_channels": [],
        "voice_channels": [],
        "stage_channels": [],
    }

    # ---- Roles (skip @everyone and managed/bot roles) ----
    for role in sorted(guild.roles, key=lambda r: r.position):
        if role.is_default():
            continue
        template["roles"].append({
            "name":        role.name,
            "color":       role.color.value,
            "hoist":       role.hoist,
            "mentionable": role.mentionable,
            "permissions": role.permissions.value,
            "position":    role.position,
        })

    # ---- Categories ----
    for cat in sorted(guild.categories, key=lambda c: c.position):
        template["categories"].append({
            "name":       cat.name,
            "position":   cat.position,
            "overwrites": _serialize_overwrites(cat, guild),
        })

    # ---- Text channels ----
    for ch in guild.text_channels:
        template["text_channels"].append({
            "name":        ch.name,
            "topic":       ch.topic or "",
            "slowmode":    ch.slowmode_delay,
            "nsfw":        ch.is_nsfw(),
            "position":    ch.position,
            "category":    ch.category.name if ch.category else None,
            "overwrites":  _serialize_overwrites(ch, guild),
        })

    # ---- Voice channels ----
    for ch in guild.voice_channels:
        template["voice_channels"].append({
            "name":       ch.name,
            "bitrate":    ch.bitrate,
            "user_limit": ch.user_limit,
            "position":   ch.position,
            "category":   ch.category.name if ch.category else None,
            "overwrites": _serialize_overwrites(ch, guild),
        })

    # ---- Stage channels ----
    for ch in guild.stage_channels:
        template["stage_channels"].append({
            "name":     ch.name,
            "position": ch.position,
            "category": ch.category.name if ch.category else None,
        })

    # Persist to file
    with open(TEMPLATE_FILE, "w") as f:
        json.dump(template, f, indent=4)

    return template

# ---------------------------------------------------------------------------
# PASTE
# ---------------------------------------------------------------------------

async def paste_server(guild: discord.Guild, progress_callback=None) -> dict:
    """
    Recreate the saved template into guild.
    Returns a summary dict with counts of what was created.
    All created item IDs are saved to a paste log so /delete-template can undo them.
    """
    if not os.path.exists(TEMPLATE_FILE):
        raise FileNotFoundError("No server template found. Run /copy first.")

    with open(TEMPLATE_FILE) as f:
        template = json.load(f)

    summary = {"roles": 0, "categories": 0, "text_channels": 0, "voice_channels": 0, "skipped": 0}

    # Paste log tracks IDs of everything we create so /delete-template can reverse it
    paste_log = {
        "source":        template.get("source_guild_name", "Unknown"),
        "pasted_at":     datetime.utcnow().isoformat(),
        "role_ids":      [],
        "category_ids":  [],
        "text_channel_ids":  [],
        "voice_channel_ids": [],
    }

    async def _progress(msg):
        if progress_callback:
            await progress_callback(msg)

    # ------------------------------------------------------------------ Roles
    await _progress("🎭 Creating roles…")
    role_map: dict[str, discord.Role] = {r.name: r for r in guild.roles}

    for role_data in template.get("roles", []):
        name = role_data["name"]
        if name in role_map:
            summary["skipped"] += 1
            continue
        try:
            new_role = await guild.create_role(
                name=name,
                color=discord.Color(role_data["color"]),
                hoist=role_data["hoist"],
                mentionable=role_data["mentionable"],
                permissions=discord.Permissions(role_data["permissions"]),
                reason="SP4RK /paste"
            )
            role_map[name] = new_role
            paste_log["role_ids"].append(new_role.id)
            summary["roles"] += 1
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Role create error ({name}): {e}")
            summary["skipped"] += 1

    # ------------------------------------------------------------ Helper: overwrites
    def _build_overwrites(ow_list: list) -> dict:
        result = {}
        for item in ow_list:
            if item["type"] == "role":
                role_name = item["name"]
                target = None
                if role_name == "@everyone":
                    target = guild.default_role
                else:
                    target = role_map.get(role_name)
                if target:
                    result[target] = _dict_to_overwrite(item["permissions"])
        return result

    # --------------------------------------------------------------- Categories
    await _progress("📁 Creating categories…")
    cat_map: dict[str, discord.CategoryChannel] = {c.name: c for c in guild.categories}

    for cat_data in sorted(template.get("categories", []), key=lambda x: x["position"]):
        name = cat_data["name"]
        if name in cat_map:
            summary["skipped"] += 1
            continue
        try:
            overwrites = _build_overwrites(cat_data.get("overwrites", []))
            new_cat = await guild.create_category(
                name=name,
                overwrites=overwrites,
                reason="SP4RK /paste"
            )
            cat_map[name] = new_cat
            paste_log["category_ids"].append(new_cat.id)
            summary["categories"] += 1
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Category create error ({name}): {e}")
            summary["skipped"] += 1

    # ---------------------------------------------------------- Text channels
    await _progress("💬 Creating text channels…")
    existing_text = {ch.name for ch in guild.text_channels}

    for ch_data in sorted(template.get("text_channels", []), key=lambda x: x["position"]):
        name = ch_data["name"]
        if name in existing_text:
            summary["skipped"] += 1
            continue
        try:
            category  = cat_map.get(ch_data["category"]) if ch_data.get("category") else None
            overwrites = _build_overwrites(ch_data.get("overwrites", []))
            new_ch = await guild.create_text_channel(
                name=name,
                topic=ch_data.get("topic") or None,
                slowmode_delay=ch_data.get("slowmode", 0),
                nsfw=ch_data.get("nsfw", False),
                category=category,
                overwrites=overwrites,
                reason="SP4RK /paste"
            )
            paste_log["text_channel_ids"].append(new_ch.id)
            summary["text_channels"] += 1
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Text channel create error ({name}): {e}")
            summary["skipped"] += 1

    # ---------------------------------------------------------- Voice channels
    await _progress("🔊 Creating voice channels…")
    existing_voice = {ch.name for ch in guild.voice_channels}

    for ch_data in sorted(template.get("voice_channels", []), key=lambda x: x["position"]):
        name = ch_data["name"]
        if name in existing_voice:
            summary["skipped"] += 1
            continue
        try:
            category  = cat_map.get(ch_data["category"]) if ch_data.get("category") else None
            overwrites = _build_overwrites(ch_data.get("overwrites", []))
            bitrate   = min(ch_data.get("bitrate", 64000), guild.bitrate_limit)
            new_ch = await guild.create_voice_channel(
                name=name,
                bitrate=bitrate,
                user_limit=ch_data.get("user_limit", 0),
                category=category,
                overwrites=overwrites,
                reason="SP4RK /paste"
            )
            paste_log["voice_channel_ids"].append(new_ch.id)
            summary["voice_channels"] += 1
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Voice channel create error ({name}): {e}")
            summary["skipped"] += 1

    # Save the paste log so /delete-template can undo this paste
    save_paste_log(guild.id, paste_log)

    return summary

# ---------------------------------------------------------------------------
# DELETE PASTED
# ---------------------------------------------------------------------------

async def delete_pasted(guild: discord.Guild, progress_callback=None) -> dict:
    """
    Delete every role, category, and channel that was created by the last /paste
    in this guild. Uses the saved paste log to identify items by ID — nothing else
    is touched.
    Returns a summary dict.
    """
    log = load_paste_log(guild.id)
    if not log:
        raise FileNotFoundError("No paste log found for this server. Run /paste first.")

    summary = {"roles": 0, "categories": 0, "text_channels": 0, "voice_channels": 0, "failed": 0}

    async def _progress(msg):
        if progress_callback:
            await progress_callback(msg)

    # ---- Delete text channels first (must be empty before deleting categories) ----
    await _progress("🗑️ Deleting text channels…")
    for ch_id in log.get("text_channel_ids", []):
        ch = guild.get_channel(ch_id)
        if ch:
            try:
                await ch.delete(reason="SP4RK /delete-template")
                summary["text_channels"] += 1
                await asyncio.sleep(0.4)
            except Exception as e:
                print(f"Delete text channel {ch_id} error: {e}")
                summary["failed"] += 1

    # ---- Delete voice channels ----
    await _progress("🗑️ Deleting voice channels…")
    for ch_id in log.get("voice_channel_ids", []):
        ch = guild.get_channel(ch_id)
        if ch:
            try:
                await ch.delete(reason="SP4RK /delete-template")
                summary["voice_channels"] += 1
                await asyncio.sleep(0.4)
            except Exception as e:
                print(f"Delete voice channel {ch_id} error: {e}")
                summary["failed"] += 1

    # ---- Delete categories (now empty) ----
    await _progress("🗑️ Deleting categories…")
    for cat_id in log.get("category_ids", []):
        cat = guild.get_channel(cat_id)
        if cat:
            try:
                await cat.delete(reason="SP4RK /delete-template")
                summary["categories"] += 1
                await asyncio.sleep(0.4)
            except Exception as e:
                print(f"Delete category {cat_id} error: {e}")
                summary["failed"] += 1

    # ---- Delete roles ----
    await _progress("🗑️ Deleting roles…")
    for role_id in log.get("role_ids", []):
        role = guild.get_role(role_id)
        if role:
            try:
                await role.delete(reason="SP4RK /delete-template")
                summary["roles"] += 1
                await asyncio.sleep(0.4)
            except Exception as e:
                print(f"Delete role {role_id} error: {e}")
                summary["failed"] += 1

    # Clear the paste log — nothing left to undo
    clear_paste_log(guild.id)
    return summary


# ---------------------------------------------------------------------------
# INFO
# ---------------------------------------------------------------------------

def get_template_info() -> dict | None:
    """Return basic info about the saved template, or None if none exists."""
    if not os.path.exists(TEMPLATE_FILE):
        return None
    try:
        with open(TEMPLATE_FILE) as f:
            t = json.load(f)
        return {
            "source":       t.get("source_guild_name", "Unknown"),
            "copied_at":    t.get("copied_at", "Unknown"),
            "roles":        len(t.get("roles", [])),
            "categories":   len(t.get("categories", [])),
            "text_channels":  len(t.get("text_channels", [])),
            "voice_channels": len(t.get("voice_channels", [])),
        }
    except Exception:
        return None
