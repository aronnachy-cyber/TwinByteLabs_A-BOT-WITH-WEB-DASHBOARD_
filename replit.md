# Sentinel OS Discord Bot

## Overview
Sentinel OS is a feature-rich Discord bot with a web-based admin dashboard. It provides moderation, auto-moderation, role management, security features, social alerts, and logging for Discord servers.

## Architecture
- **SENTINEL-BOT-Main/main.py** - Discord bot entry point. Starts the Flask dashboard in a background thread, then runs the Discord bot.
- **SENTINEL-BOT-Main/dashboard.py** - Flask web dashboard (port 5000). Provides an admin UI for bot configuration and live log viewing.
- **SENTINEL-BOT-Main/automod.py** - Auto-moderation logic (link blocking, invite blocking, mention spam, custom blacklist)
- **SENTINEL-BOT-Main/automod_ai.py** - AI-powered moderation using OpenAI
- **SENTINEL-BOT-Main/moderation.py** - Manual moderation commands
- **SENTINEL-BOT-Main/roles.py** - Role management
- **SENTINEL-BOT-Main/logs.py** - Logging utilities
- **SENTINEL-BOT-Main/social.py** - Social media alert integrations
- **SENTINEL-BOT-Main/security.py** - Security features (anti-raid, 2FA, role monitor)
- **SENTINEL-BOT-Main/settings.json** - Persistent bot configuration
- **SENTINEL-BOT-Main/warnings.json** - Warning records
- **SENTINEL-BOT-Main/templates/** - Flask HTML templates (login.html, index.html)

## Running the App
The workflow runs `cd SENTINEL-BOT-Main && python main.py` which:
1. Starts the Flask dashboard on port 5000 (accessible in the webview)
2. Starts the Discord bot (requires `DISCORD_TOKEN` environment variable)

## Environment Variables
- `DISCORD_TOKEN` - Discord bot token (required for bot functionality)
- `OPENAI_KEY` - OpenAI API key (optional, for AI moderation)

## Dashboard Access
- URL: accessible via the Replit webview
- Default credentials: admin / admin123

## Dependencies
Installed via pip: discord.py, flask, gunicorn, openai, requests, aiohttp
