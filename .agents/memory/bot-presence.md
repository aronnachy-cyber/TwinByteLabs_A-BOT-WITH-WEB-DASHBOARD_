---
name: Bot Presence (Global)
description: Bot status and activity are global settings, NOT per-server.
---

## Storage
- `config["bot_status"]` — online/idle/dnd/invisible (global)
- `config["bot_activity_type"]` — none/playing/watching/listening/competing/custom (global)
- `config["bot_activity_text"]` — the activity text string (global)
- All three live in DEFAULT_CONFIG in dashboard.py and are saved via `save_config()`

## Bot sync
`sync_bot_settings` in main.py runs every 60s, reads all three keys, builds the correct discord.py Activity object, calls `change_presence(status=..., activity=...)`.

## Dashboard UI
- **Public dashboard** (index.html General tab): read-only display + "Edit in Owner Panel →" link
- **Owner panel** Bot Presence tab: full selector with status, activity type, activity text

**Why:** Bot presence is a single bot instance — per-server status doesn't exist in Discord's API. Previously it was incorrectly shown as a per-server field.
