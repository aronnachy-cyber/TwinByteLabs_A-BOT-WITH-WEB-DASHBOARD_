# Sentinel OS Discord Bot

## Overview
Sentinel OS is a feature-rich Discord bot with a web-based admin dashboard. It provides moderation, auto-moderation, role management, security features, social alerts, and logging for Discord servers.

## Architecture
- **main.py** - Discord bot entry point. Starts the Flask dashboard in a background thread, then runs the Discord bot.
- **dashboard.py** - Flask web dashboard (port 5000). Provides an admin UI for bot configuration and live log viewing.
- **automod.py** - Auto-moderation logic (link blocking, invite blocking, mention spam, custom blacklist)
- **automod_ai.py** - AI-powered moderation using OpenAI
- **moderation.py** - Manual moderation commands
- **roles.py** - Role management
- **logs.py** - Logging utilities
- **social.py** - Social media alert integrations
- **security.py** - Security features (anti-raid, 2FA, role monitor)
- **settings.json** - Persistent bot configuration
- **warnings.json** - Warning records
- **templates/** - Flask HTML templates (login.html, index.html)

## Running the App
The workflow runs `python main.py` which:
1. Starts the Flask dashboard on port 5000 (accessible in the webview)
2. Starts the Discord bot (requires `DISCORD_TOKEN` environment variable)

## Environment Variables
- `DISCORD_TOKEN` - Discord bot token (required for bot functionality)
- `OPENAI_KEY` - OpenAI API key (optional, for AI moderation)

## Dashboard Access
- URL: http://localhost:5000
- Default credentials: admin / admin123

## Deployment

### Render
- `render.yaml` is included for one-click Render deploy
- On Render, set `DISCORD_TOKEN` and `OPENAI_KEY` as environment variables in the dashboard
- Render auto-sets `PORT` — the app reads it automatically
- The `Procfile` uses `web:` so Render exposes the Flask dashboard as a public URL

### Replit
- Configured as a VM deployment (always-running)
