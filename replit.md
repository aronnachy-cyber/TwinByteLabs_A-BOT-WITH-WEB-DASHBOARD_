# Sentinel OS Discord Bot

A feature-rich AI-powered Discord bot (V2.5.0) with a web-based admin dashboard (Byte Labs) for managing Discord servers.

## Run & Operate

- **Dev:** `cd SENTINEL-BOT-Main && python3 -m gunicorn --bind 0.0.0.0:5000 --workers 1 --timeout 120 wsgi:app`
- **Prod deploy:** `gunicorn --bind 0.0.0.0:5000 main:app`
- **Required secrets** (add via Secrets tab): `DISCORD_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `SUPABASE_URL`, `SUPABASE_KEY`, `GEMINI_KEY` (or `GOOGLE_API_KEY`), `RESEND_API_KEY`
- **Optional bot tokens:** `SERVER_TOKEN` (Server Official bot), `W_TOKEN` (Wave streaming bot)

## Stack

- Python 3.11, Flask 3.x, Gunicorn, discord.py 2.x
- Supabase (Postgres) for persistence — falls back to local JSON files when not configured
- Google Generative AI (Gemini) for AI automod
- Discord OAuth2 for dashboard login

## Where things live

- `SENTINEL-BOT-Main/main.py` — Discord bot entry point
- `SENTINEL-BOT-Main/dashboard.py` — Flask web dashboard + all API routes (port 5000)
- `SENTINEL-BOT-Main/wsgi.py` — WSGI entry point; starts bot threads then serves Flask
- `SENTINEL-BOT-Main/server_official.py` — Second Discord bot (SERVER_TOKEN)
- `SENTINEL-BOT-Main/wavebot.py` — Wave streaming bot (W_TOKEN)
- `SENTINEL-BOT-Main/templates/` — Jinja2 HTML templates
- `SENTINEL-BOT-Main/settings.json` — Persistent bot config (hot copy)
- `SENTINEL-BOT-Main/supabase_schema.sql` — DB schema reference

## Architecture decisions

- Three separate Discord bots (SP4RK, Server Official, Wave) share one Flask process via daemon threads, all started from `wsgi.py`
- Auth is Discord OAuth2 (not Replit Auth) — dashboard login at `/login` redirects to Discord
- Supabase is the durable store; local JSON files are hot fallbacks for all data
- A watchdog thread monitors the bot thread and auto-restarts it if it dies silently
- Self-ping keepalive hits `/health` every 10 min using `REPLIT_DEV_DOMAIN` env var

## Product

- Public landing page (`/`) — Byte Labs marketing site with bot-request intake form
- SP4RK dashboard (`/dashboard`) — 13-tab admin panel: General, Moderation, Automod, Security, Social Notifier, Live Warnings, Action Logs, Giveaways, Tickets, Auto-Responder, Reaction Roles, Leveling/XP, Users, Feedback, Account
- Server Official dashboard (`/server-official/dashboard`) — second bot management

## Gotchas

- `psycopg2-binary` must be used (not `psycopg2`) — `pg_config` is not in PATH
- Packages are installed via `pip` (the nix pip wrapper), not `uv`/`pyproject.toml` directly
- `SESSION_COOKIE_SECURE=True` requires HTTPS — works on Replit's proxy but breaks on plain `http://localhost`
- Discord OAuth redirect URI must exactly match what's registered in the Discord Developer Portal
