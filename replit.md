# SP4RK OS / SENTINEL BOT

A feature-rich AI-powered Discord bot (V2.5.0) with a web-based admin dashboard for managing Discord servers. Fully portable — runs locally, on Docker, Render, Railway, Fly.io, or any Linux host.

## Run & Operate

- **Local (gunicorn):** `cd SENTINEL-BOT-Main && gunicorn --bind 0.0.0.0:5000 --workers 1 --reload wsgi:app`
- **Local (Docker):** `docker compose up --build`
- **Replit workflow:** runs automatically via `gunicorn --bind 0.0.0.0:5000 --reuse-port --reload main:app`

## Required Environment Variables

Set these in Replit Secrets (cloud) or in a `.env` file (local — copy from `.env.example`):

| Variable | Purpose |
|----------|---------|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal |
| `FLASK_SECRET_KEY` | Random 32-byte hex string for session signing |
| `DISCORD_CLIENT_ID` | OAuth2 client ID — for dashboard login |
| `DISCORD_CLIENT_SECRET` | OAuth2 client secret |
| `DISCORD_REDIRECT_URI` | Must match Discord app settings (e.g. `http://localhost:5000/callback`) |
| `ADMIN_DISCORD_ID` | Your Discord user ID — grants /owner panel access |

## Optional Environment Variables

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL URL for persistent storage |
| `SUPABASE_URL` / `SUPABASE_KEY` | Supabase alternative to raw Postgres |
| `GEMINI_KEY` / `GOOGLE_API_KEY` | Google Gemini for AI moderation |
| `SERVER_TOKEN` | Server Official bot token (second bot) |
| `W_TOKEN` | Wave streaming bot token |
| `RESEND_API_KEY` | Resend email notifications |
| `SMTP_HOST/PORT/USER/PASS` | Classic SMTP email |
| `DEV_PIN` | Dev panel PIN (default: `change-me-in-production`) |
| `DASHBOARD_URL` | Public URL — used by keepalive ping |
| `FORCE_HTTPS_COOKIES` | Set `true` when behind HTTPS proxy |

See `.env.example` for the full list with descriptions.

## Stack

- Python 3.11, Flask 3.x, Gunicorn, discord.py 2.x
- PostgreSQL / Supabase for persistence — falls back to local JSON files when not configured
- Google Generative AI (Gemini) for AI automod
- Discord OAuth2 for dashboard login
- `python-dotenv` for local `.env` loading

## Where things live

- `SENTINEL-BOT-Main/wsgi.py` — WSGI entry point; loads dotenv, runs config check, starts bot threads
- `SENTINEL-BOT-Main/main.py` — Discord bot entry point
- `SENTINEL-BOT-Main/dashboard.py` — Flask web dashboard + all API routes (port 5000)
- `SENTINEL-BOT-Main/config_check.py` — Startup env validation (prints missing vars)
- `SENTINEL-BOT-Main/server_official.py` — Second Discord bot (SERVER_TOKEN)
- `SENTINEL-BOT-Main/templates/` — Jinja2 HTML templates
- `SENTINEL-BOT-Main/settings.json` — Default bot config (no secrets — safe to commit)
- `SENTINEL-BOT-Main/supabase_schema.sql` — DB schema reference
- `.env.example` — Template for all environment variables
- `Dockerfile` + `docker-compose.yml` — Container dev environment
- `README.md` — Full setup guide for external developers

## Architecture

- Three Discord bots (SP4RK, Server Official, Wave) share one Flask process via daemon threads
- Auth is Discord OAuth2 — dashboard login at `/login` redirects to Discord
- Supabase / Postgres is the durable store; local JSON files are hot fallbacks
- A watchdog thread monitors the bot thread and auto-restarts it if it dies silently
- Self-ping keepalive hits `/health` every 2 min (uses `DASHBOARD_URL`, `RENDER_EXTERNAL_URL`, or `RAILWAY_PUBLIC_DOMAIN`)

## Portability Notes

- **No Replit-specific runtime code** — `REPLIT_DEV_DOMAIN` is only read as an optional HTTPS detection hint alongside `RENDER`, `RAILWAY_ENVIRONMENT`, `FLY_APP_NAME`, and `FORCE_HTTPS_COOKIES`
- **dotenv loading** — `wsgi.py` and `dashboard.py` both load `.env` via `python-dotenv` before anything else, so local dev works without setting any host-level secrets
- **No hardcoded secrets** — all tokens, keys, and IDs are environment variables; `settings.json` contains only safe defaults
- **Startup validation** — `config_check.py` prints a clear table of set/missing vars on every boot

## Gotchas

- `psycopg2-binary` must be used (not `psycopg2`) — `pg_config` is not in PATH in most environments
- `SESSION_COOKIE_SECURE=True` requires HTTPS — set `FORCE_HTTPS_COOKIES=true` only when behind a TLS proxy; leave unset for plain `http://localhost`
- Discord OAuth redirect URI must exactly match what's registered in the Discord Developer Portal
- `DEV_PIN` defaults to `change-me-in-production` — always set a strong custom value in production

## User preferences

- Keep all sensitive config in environment variables — never hardcode tokens, IDs, or passwords
- `settings.json` is the safe-to-commit default config file — it should never contain personal data
