# Sentinel OS Discord Bot

## Overview
Sentinel OS is a feature-rich AI-powered Discord bot (V2.5.0) with a web-based admin dashboard. Provides moderation, auto-moderation, giveaways, tickets, reaction roles, auto-responder, XP leveling, security, social alerts, and logging for Discord servers.

## Architecture
- **SENTINEL-BOT-Main/main.py** - Discord bot entry point. Starts Flask dashboard in background thread, runs Discord bot.
- **SENTINEL-BOT-Main/dashboard.py** - Flask web dashboard (port 5000). Landing page, UID auth, all API routes.
- **SENTINEL-BOT-Main/wsgi.py** - WSGI entry point for gunicorn (production). Starts bot thread then serves Flask app.
- **SENTINEL-BOT-Main/automod.py** - Auto-moderation logic (profanity, links, invites, mentions, blacklist)
- **SENTINEL-BOT-Main/moderation.py** - Manual moderation (timed mute, unmute, auto-role)
- **SENTINEL-BOT-Main/roles.py** - Role management and hierarchy monitoring
- **SENTINEL-BOT-Main/logs.py** - Discord logging utilities
- **SENTINEL-BOT-Main/social.py** - Social media alert integrations
- **SENTINEL-BOT-Main/security.py** - Security features (anti-raid, 2FA, role monitor, mass-action prevention)
- **SENTINEL-BOT-Main/giveaway.py** - Giveaway system (start, end, reroll)
- **SENTINEL-BOT-Main/tickets.py** - Support ticket system with channel creation and close button
- **SENTINEL-BOT-Main/autoresponder.py** - Keyword auto-responder rules
- **SENTINEL-BOT-Main/reaction_roles.py** - Reaction-to-role mapping
- **SENTINEL-BOT-Main/leveling.py** - XP/leveling system with cooldowns and leaderboard
- **SENTINEL-BOT-Main/server_official.py** - **Server Official** (second Discord bot, separate `SERVER_TOKEN`). Full server-management bot (no moderation). 9 feature categories: (1) Setup automation — `/setup_roles`, `/setup_channels`, `/setup_template` (community/gaming/study); (2) Member mgmt — welcome roles, `/userinfo`, join/leave logs, `/set_welcome_role`, `/set_log_channel`; (3) Analytics — messages-per-day, top chatters, voice-minute tracking; (4) Announcements — `/announce`, `/broadcast`, `/schedule`, templates; (5) Utilities — `/poll`, `/remind`, `/sticky`, `/note`, embed builder; (6) Engagement — server `/goal` tracker only (XP/leveling/daily-checkin removed in V2.6); (7) Logging — channel/role create/delete/rename, bot action history, setup history; (8) Web dashboard with charts & toggles; (9) Automation — auto-pin on N reactions, auto-cleanup of bot's old messages, daily backups; (10) Integrations — custom commands (`!trigger`), incoming webhooks (`/server-official/api/webhook_in/<secret>`), outgoing external API hooks. Web landing `/server-official`, dashboard `/server-official/dashboard` (Discord login required). Storage: hot working copy in `server_official_state.json`, durable mirror in Supabase table `server_official_state` (single JSONB row, id=1) flushed every 15s by a background daemon — on cold start the local file is hydrated from Supabase if missing. Auto-launched by `wsgi.py` if `SERVER_TOKEN` is set.

## Data Files
- **SENTINEL-BOT-Main/settings.json** - Persistent bot configuration
- **SENTINEL-BOT-Main/warnings.json** - Warning records (guild → member → count + history)
- **SENTINEL-BOT-Main/users.json** - Registered users database (UID-based auth)
- **SENTINEL-BOT-Main/giveaways.json** - Giveaway records
- **SENTINEL-BOT-Main/tickets.json** - Ticket records
- **SENTINEL-BOT-Main/autoresponder.json** - Auto-responder rules
- **SENTINEL-BOT-Main/reaction_roles.json** - Reaction role mappings
- **SENTINEL-BOT-Main/xp.json** - XP/leveling data per guild/user

## Templates
- **SENTINEL-BOT-Main/templates/landing.html** - Public landing page (/)
- **SENTINEL-BOT-Main/templates/login.html** - UID-based login page (/login)
- **SENTINEL-BOT-Main/templates/register.html** - Registration request page (/register)
- **SENTINEL-BOT-Main/templates/index.html** - Main dashboard (/dashboard) — 13 tabs

## Dashboard Tabs
General | Moderation | Automod | Security | Social Notifier | Live Warnings | Action Logs | Giveaways | Tickets | Auto-Responder | Reaction Roles | Leveling/XP | Users (admin) | Feedback | Account

## Loading Screen
Both `index.html` (SP4RK) and `server_official_dashboard.html` (Server Official) show a full-screen splash on every load with: Byte Labs branding, an animated progress bar, a rotating tip carousel (changes every 2.6s), and an Alpha-warning banner ("Bot is in Alpha. Expect bugs. — Byte Labs"). The splash auto-hides on `window.load` after a 1.4s minimum (so the warning is actually read), with a 6s safety-net timeout. CSS/JS is inlined per template under the `#bl-splash` id and shares the `bl-` class prefix to avoid collisions.

## User Feedback System
Star-rating + comment system on the SP4RK dashboard's **Feedback** tab.
- 5-star widget with hover preview, keyboard support, and rolling label ("Loved it!", etc.).
- Optional 2000-char comment textarea.
- Submit POSTs to `/api/feedback` (Discord-OAuth gated).
- Server stores each entry in Supabase table `sentinel_feedback` AND in local `feedback.json` as a fallback, then emits a `FEEDBACK` action log.
- The same tab lists the latest 10 ratings via `GET /api/feedback`, showing average score and per-entry stars / author / timestamp.

## Leveling / XP
- Leveling is enabled by the `leveling` config key.
- Members earn 15-25 XP per counted message with a 60 second per-user cooldown.
- Level-up announcements use the configurable `level_up_message` template with `{user}` and `{level}` placeholders.
- The dashboard Leveling / XP tab shows tracked members, total messages, total XP, and leaderboard data from Supabase when configured or `xp.json` fallback.

## Profanity / AI Moderation
- `automod.py` includes an always-on strict Bangla/English profanity filter for severe abusive words and obfuscated variants.
- Gemini moderation is optional via `GEMINI_KEY`; the bot still deletes configured strict profanity without AI credentials.
- When dashboard and bot run as separate Render services, set `DASHBOARD_URL` on the worker to the public dashboard URL so prefix/config changes are fetched by the bot.
- `START_BOT_IN_WEB` defaults to false so the dashboard web service does not start a second Discord bot connection. Only the worker should run the bot unless explicitly running a one-service deployment.

## Slash Commands (V2.5.0)
- `/bot` — Bot stats, uptime, active features (everyone)
- `/dashboard` — Mini admin dashboard embed (admin only)
- `/commands` — Full command list
- `/serverinfo` — Server info
- `/giveaway <duration> <prize> [winners]` — Start giveaway
- `/giveaway-end <message_id>` — End giveaway early
- `/giveaway-reroll <message_id>` — Reroll winner
- `/poll <question> <opt1> <opt2> [opt3] [opt4]` — Create reaction poll
- `/ticket [reason]` — Open support ticket
- `/ticket-close` — Close current ticket
- `/setup-reaction-role <message_id> <emoji> <role>` — Link emoji to role
- `/autorespond add <trigger> <response> [match]` — Add auto-reply
- `/autorespond remove <trigger>` — Remove auto-reply
- `/autorespond list` — List all rules
- `/rank [@user]` — Check XP and level
- `/leaderboard` — Server XP leaderboard

## Prefix Commands (default: !)
warn, warnings, clearwarnings, kick, ban, tempban, mute (timed), unmute, purge, slowmode, lockdown, unlockdown, userinfo, refresh

## Routes
- `/` - Public landing page
- `/login` - UID-based login (format: TBLS-XXXX-XXXX)
- `/register` - Registration request form
- `/dashboard` - Main dashboard (requires login)
- `/logout` - Logout
- `/health` - Health check endpoint
- `/api/config` - Bot config
- `/api/logs` - Action logs
- `/api/giveaways` - Giveaway data
- `/api/tickets` - Ticket data
- `/api/autoresponder` - Auto-responder rules
- `/api/autoresponder/add` - Add rule
- `/api/autoresponder/remove` - Remove rule
- `/api/reaction_roles` - Reaction role mappings
- `/api/reaction_roles/remove` - Remove mapping
- `/api/xp/leaderboard` - XP leaderboard
- `/api/stats` - Overview stats
- `/api/get_warnings` - Warning data
- `/api/users` - User management (admin)

## Auth System
- UID format: `TBLS-{first 4 letters of name}-{4 char unique code}`
- Human-readable session token format: `SENTINEL-{name}-{word}-{code}`
- Admin UID: `TBLS-ADMI-9ZX7`
- Users register → admin approves → user logs in with UID, session token, or email
- Admins can view every user's session token and grant or revoke admin access from the Users tab
- Captcha on login (math), lockout after 3 failed attempts

## Running the App on Replit
**Workflow command:** `cd SENTINEL-BOT-Main && python3 -m gunicorn --bind 0.0.0.0:5000 --workers 1 --timeout 120 wsgi:app`

## Render Deployment
- Web service root directory: `SENTINEL-BOT-Main`
- Web service build command: `pip install -r requirements.txt`
- Web service start command: `gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 wsgi:app`
- Set `START_BOT_IN_WEB=false` on the Render web service when using a separate worker.
- Background worker root directory: `SENTINEL-BOT-Main`
- Background worker build command: `pip install -r requirements.txt`
- Background worker start command: `python bot_worker.py`
- Set `DISCORD_TOKEN` on the worker service. Keep only one service connected to Discord at a time.

## Environment Variables
- `DISCORD_TOKEN` - Discord bot token (required for Discord connection)
- `GEMINI_KEY` - Gemini API key (optional, AI moderation)
- `FLASK_SECRET_KEY` - Flask session secret key (set as shared env var)

## Design System
- Dark navy `#07101f` background, accent `#3a8fff`, text `#e8f0ff`, muted `#7a93b8`
- Sentinel S-shield SVG logo, circuit board SVG patterns, SVG castle art
- Glassmorphism cards, "tech fortress" aesthetic throughout

## Byte Labs Expansion (Apr 2026)
- `/` → Byte Labs landing (templates/bytelabs_landing.html). Hero CTAs: "Our Bots", "Ask for a Bot" (orange flame button → modal), "Learn More".
- `/bots` → Bot card showcase (templates/bots.html). Cards: Server Official (admin-customizable), SP4RK (live), Tempo & Pulse (coming soon).
- `/sp4rk` → Original SP4RK landing (templates/landing.html), unchanged design.
- Server Official card: name + image (URL) + tagline + description stored in `dev_state.json` under key `server_official`. Edit pencil icon shows only when dev session unlocked. Inline editor modal posts to `POST /dev/server-official` (PIN-gated). Public read at `GET /dev/server-official`.

## Dev Menu (now on both Byte Labs landing & SP4RK landing)
- Hidden until PIN-unlocked (PIN env var: `DEV_PIN`, defaults `sp4rk2024`).
- Unlock gestures (all open the PIN modal — none auto-unlock):
  1. Tap the Byte Labs / SP4RK logo 7 times within 3 seconds
  2. Long-press any pillar / bot card / feature card for 7 seconds
  3. Type the secret word `spark`
  4. Press `Ctrl + Shift + D`
- Once unlocked: kebab menu appears in nav with: Edit Server Official, Ask for a Bot preview, Maintenance Mode toggle, Lock dev menu.
- Server endpoints: `POST /dev/unlock` (validate PIN), `POST /dev/lock`, `POST /dev/maintenance`, `GET /dev/state`, `POST /dev/server-official`.

## Developer Tools Panel on Dashboards (Apr 2026)
When a dev session is unlocked (PIN passed), both `/dashboard` and `/server-official` show a floating gear button (bottom-right) that opens a side panel with:
- **Theme**: accent color picker, presets (Midnight, Ocean, Sunset, Forest, Royal, Mono), splash on/off, compact density, reset.
- **Quick actions**: reload, ping bot health, copy session info, clear local cache & reload.
- **System info**: page, viewport, language, platform, UA, current theme.
Theme is persisted in `localStorage.bl_theme` and applied pre-paint via an early `<head>` script in both dashboards (maps `--accent`/`--accent2` and `--bg2`/`--sidebar`, `--bg3`/`--card` so both CSS schemes work). Server passes `dev_unlocked=is_dev_session()` from `dashboard.py` to both render calls.

## /bots Page Beauty Pass (Apr 2026)
Enhanced `templates/bots.html` while keeping all existing IDs/classes intact:
- **Stats strip** below the header (4 stats with gradient count-up: Bots Crafted, Live Now, Uptime Target, Possibilities).
- **Filter chips** (All / Live / In the Lab) with live count badges; toggles `data-status` on each card to hide non-matching entries.
- **Mouse-tracking spotlight** on live cards via `--mx`/`--my` CSS vars updated on `mousemove`.
- **Banner shimmer sweep** on hover; **icon halo glow** with pulse animation on Coming Soon cards.
- **Build-progress bar** on Tempo (65%) and Pulse (40%) with shimmer fill, animated in via IntersectionObserver.
- All new CSS uses `bx-` prefix to avoid colliding with the existing `bot-`/`so*` names used by the dev edit modal.
