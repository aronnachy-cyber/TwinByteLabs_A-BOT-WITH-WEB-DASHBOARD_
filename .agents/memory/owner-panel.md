---
name: Owner Panel
description: Hidden /owner dev panel — how to access it, what it does, how it's protected.
---

## Access
- URL: `/owner` — returns 404 if not unlocked (intentional, to hide existence)
- Unlock via DevTools console: `sp4rkDev('your-pin')` (injected in landing.html)
- Or directly: POST `/owner/unlock` with `{"key": "PIN"}`
- Lock: GET `/owner/lock` or sidebar button
- PIN: `DEV_PIN` env var (default `sp4rk2024`)

## Protection
- `_owner_required` decorator in dashboard.py checks `session.get('owner_unlocked')`
- Returns `abort(404)` if not set — does NOT reveal the panel exists

## Tabs
Overview, Bot Presence, Servers (ALL guilds, no user filter), Users, Global Config, Dev Tools

## Key API routes
- GET/POST `/api/owner/bot_presence` — global status + activity
- GET `/api/owner/servers` — ALL bot guilds (live or bot_guilds.json fallback)
- GET/POST `/api/owner/global_config`
- GET `/api/owner/overview` — stats, health, recent logs
- POST `/api/owner/restart` — bot restart
- GET/POST `/api/owner/maintenance` — maintenance mode
- GET `/api/owner/users`

**Why:** Owner panel is not linked anywhere in the public UI. Security through obscurity + session PIN. Public users have no way to discover `/owner` exists.
