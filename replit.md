# Sentinel OS Discord Bot

## Overview
Sentinel OS is a feature-rich Discord bot with a web-based admin dashboard. Provides moderation, auto-moderation, role management, security features, social alerts, and logging for Discord servers.

## Architecture
- **SENTINEL-BOT-Main/main.py** - Discord bot entry point. Starts the Flask dashboard in a background thread, then runs the Discord bot.
- **SENTINEL-BOT-Main/dashboard.py** - Flask web dashboard (port 5000). Landing page, UID-based auth, register/approve flow, and all API routes.
- **SENTINEL-BOT-Main/wsgi.py** - WSGI entry point for gunicorn (production). Starts bot thread then serves Flask app.
- **SENTINEL-BOT-Main/automod.py** - Auto-moderation logic
- **SENTINEL-BOT-Main/automod_ai.py** - AI-powered moderation using OpenAI
- **SENTINEL-BOT-Main/moderation.py** - Manual moderation commands
- **SENTINEL-BOT-Main/roles.py** - Role management
- **SENTINEL-BOT-Main/logs.py** - Logging utilities
- **SENTINEL-BOT-Main/social.py** - Social media alert integrations
- **SENTINEL-BOT-Main/security.py** - Security features (anti-raid, 2FA, role monitor)
- **SENTINEL-BOT-Main/settings.json** - Persistent bot configuration
- **SENTINEL-BOT-Main/warnings.json** - Warning records
- **SENTINEL-BOT-Main/users.json** - Registered users database (UID-based auth)
- **SENTINEL-BOT-Main/templates/landing.html** - Public landing page (/)
- **SENTINEL-BOT-Main/templates/login.html** - UID-based login page (/login)
- **SENTINEL-BOT-Main/templates/register.html** - Registration request page (/register)
- **SENTINEL-BOT-Main/templates/index.html** - Main dashboard (/dashboard)

## Routes
- `/` - Public landing page
- `/login` - UID-based login (format: TBLS-XXXX-XXXX)
- `/register` - Registration request form
- `/dashboard` - Main dashboard (requires login)
- `/logout` - Logout
- `/health` - Health check endpoint (for Railway)
- `/api/*` - REST API endpoints

## Auth System
- UID format: `TBLS-{first 4 letters of name}-{4 char unique code}`
- Admin UID: `TBLS-ADMI-9ZX7` (full admin access)
- Users register → admin approves in Users tab → user logs in with UID
- Captcha on login to prevent brute force, lockout after 3 failed attempts

## Running the App
**Development:** `cd SENTINEL-BOT-Main && python main.py`
**Production (Railway):** `cd SENTINEL-BOT-Main && gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 wsgi:app`

## Environment Variables
- `DISCORD_TOKEN` - Discord bot token (required for bot functionality)
- `OPENAI_KEY` - OpenAI API key (optional, for AI moderation)

## Deployment
- `railway.json` at project root configures Railway deployment
- `Procfile` in SENTINEL-BOT-Main for Railway/Render
