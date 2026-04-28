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
- **SENTINEL-BOT-Main/server_official.py** - **Server Official** (second Discord bot, separate `SERVER_TOKEN`). Full server-management bot (no moderation). 9 feature categories: (1) Setup automation — `/setup_roles`, `/setup_channels`, `/setup_template` (community/gaming/study); (2) Member mgmt — welcome roles, join/leave logs, `/set_welcome_role`, `/set_log_channel` (general info commands `/userinfo` and `/serverinfo` were removed in V2.7 — those live on SP4RK only so each bot has a clear, non-overlapping role); (3) Analytics — messages-per-day, top chatters, voice-minute tracking; (4) Announcements — `/announce`, `/broadcast`, `/schedule`, templates; (5) Utilities — `/poll`, `/remind`, `/sticky`, `/note`, embed builder; (6) Engagement — server `/goal` tracker only (XP/leveling/daily-checkin removed in V2.6); (7) Logging — channel/role create/delete/rename, bot action history, setup history; (8) Web dashboard with charts & toggles; (9) Automation — auto-pin on N reactions, auto-cleanup of bot's old messages, daily backups; (10) Integrations — custom commands (`!trigger`), incoming webhooks (`/server-official/api/webhook_in/<secret>`), outgoing external API hooks. Web landing `/server-official`, dashboard `/server-official/dashboard` (Discord login required). Storage: hot working copy in `server_official_state.json`, durable mirror in Supabase table `server_official_state` (single JSONB row, id=1) flushed every 15s by a background daemon — on cold start the local file is hydrated from Supabase if missing. Auto-launched by `wsgi.py` if `SERVER_TOKEN` is set.

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

## Slash Commands (V2.7 — full slash parity for moderation)
**Info & Utility**
- `/bot` — Bot stats, uptime, active features
- `/dashboard` — Mini admin dashboard embed (admin only)
- `/commands` — Full command list
- `/serverinfo` — **Big** server info embed (icon, banner, owner, presence breakdown, channel/role/boost/feature stats)
- `/userinfo [member]` — **Big** user profile (avatar, banner, status, activity, top role, join position, acks, warnings progress bar)
- `/avatar [member]` — Full-size avatar with PNG/JPG/WEBP/GIF download links
- `/banner [member]` — Show profile banner (if set)
- `/roleinfo <role>` — Role color, position, member count, hoist/mentionable/managed flags, key permissions

**Moderation (V2.7 — new slash counterparts of every prefix command)**
- `/warn <member> [reason]` — Warn (calls same `add_warning` helper as `!warn`)
- `/warnings <member>` — Warning history embed
- `/clearwarnings <member>` — Reset warnings (admin)
- `/kick <member> [reason]`
- `/ban <member> [reason]`
- `/unban <user_id> [reason]`
- `/timeout <member> <duration> [reason]` — Discord native timeout (max 28d)
- `/untimeout <member>`
- `/purge <amount>` — Bulk delete (1–200)
All slash mod commands enforce both `app_commands.checks.has_permissions` AND the same role-hierarchy guard as `_hierarchy_block`. A global `@bot.tree.error` handler now turns missing-permission / cooldown / check-failure exceptions into friendly ephemeral replies instead of "The application did not respond."

**Engagement / Tickets / Giveaways** (unchanged)
- `/giveaway`, `/giveaway-end`, `/giveaway-reroll`, `/poll`, `/ticket`, `/ticket-close`
- `/setup-reaction-role`, `/remove-reaction-role`, `/list-reaction-roles`
- `/autorespond add|remove|list`, `/rank`, `/leaderboard`

## Prefix Commands (default: !)
warn, warnings, clearwarnings, kick, ban, tempban, mute (timed), unmute, timeout, untimeout, unban, purge, purgeuser, slowmode, lockdown, unlockdown, lock, unlock, role, nick, voicekick, snipe, massban, note, notes, modlogs, userinfo (alias whois/ui), serverinfo (alias si), refresh

## V2.8.1 — Server Official Landing Page Refresh
The Server Official public landing page (`/server-official`) has been rebuilt to match SP4RK's depth and richness while keeping its own emerald/cyan identity (vs SP4RK's blue/orange):

- **Animated mission-control hero** — a radar scope with rotating sweep, four pulsing "blip" feature dots, a satellite-dish core with a `ping` ring, and three floating "transmission" pills ("Announcement queued", "Giveaway live", "Starboard +1"). Replaces SP4RK's construction crane + ember motif with a thematic equivalent.
- **Hero CTAs match SP4RK's pattern** — Open Dashboard (gradient primary), Add to Discord (when invite URL is configured), See Features (ghost). Online/offline status pill plus a live "Managing N servers · M members" mini-stat when the bot is connected.
- **By-the-numbers stat strip** — six tiles (Servers, Scheduled Posts, Active Giveaways, Live Polls, Reminders, Sticky Messages) populated from `get_full_overview()` and `_so_live_state()`. Conditionally rendered so the page isn't ugly when SO isn't running.
- **12 feature cards** (was 6) — covers every shipped module, with NEW badges on Starboard and Embed Builder.
- **Expanded slash command grid** — 20 commands now listed (was 11), grouped to flow naturally from announcements → giveaways → polls/reminders → embeds → starboard → invites → setup.
- **3-tier pricing section** — Starter / Pro (featured) / Enterprise, mirroring SP4RK's miniO / Simple / Advance layout. Featured tier uses gold/amber gradient instead of SP4RK's orange so the two bots stay visually distinct.
- **Rich footer** — social icon row + copyright with a live-state indicator.
- **Route now passes `live`, `overview`, and `invite_url`** to the template. Invite URL is built from `SERVER_OFFICIAL_CLIENT_ID` (or falls back to `DISCORD_CLIENT_ID`) plus optional `SERVER_OFFICIAL_PERMISSIONS` env var so a single-OAuth-app deployment Just Works.

File grew from 452 → ~1000 lines. Same overall shape as SP4RK's landing (1454 lines) — the difference is mostly that SP4RK has the `dev menu` PIN-protected drawer for the maintenance toggle, which is intentionally SP4RK-only.

## V2.8 — Dyno / Carl-bot Feature Pack
Two new self-contained modules add the signature features people expect from modern community bots. Both keep `main.py` / `server_official.py` clean by exposing a single `setup()` entry point.

**`extras.py` (loaded by SP4RK):**
- **🏷️ Tags** — Carl-bot/Dyno style user-created text snippets. `/tag show|create|edit|delete|list|info` plus the prefix shortcut `!tag <name>`. Stored in `tags.json` with atomic writes; sorted-by-usage list view; per-tag author + use-counter + edit timestamp; only the author or a moderator can edit/delete.
- **🧹 Softban** — `/softban <member> [reason] [delete_days 1-7]`. Bans then immediately unbans to wipe a member's recent messages without a permanent removal. Uses modern `delete_message_seconds` (discord.py 2.7+) and the same role-hierarchy guard as `/ban`.
- **💤 AFK** — `/afk [reason]`. Bot replies when the AFK user gets pinged (with a `<t:since:R>` relative timestamp) and clears the AFK flag automatically on the user's next message. Stored in `afk.json`.
- **🔔 Highlights** — Carl-bot's killer feature. `/highlight add|remove|list|clear`. Get DM'd when a keyword you choose appears in any channel you can read. Word-boundary matching (so "art" doesn't fire on "start"), respects channel read perms, 60-second per-(guild,user) DM cooldown to prevent spam, capped at 10 highlights per server per user. Stored in `highlights.json`.

**`so_extras.py` (loaded by Server Official):**
- **⭐ Starboard** — `/setup-starboard <channel> <threshold>`, `/disable-starboard`, `/starboard-stats`. Listens on `on_raw_reaction_add` with a fast emoji prefilter so non-⭐ reactions add zero overhead. Reposts qualifying messages with author, jump link, first image attachment, and live ⭐ count footer. Tracks already-posted message IDs in per-guild config so a re-enable can't double-post old stars; ignores bot messages and stars from inside the starboard channel itself (no mirror loops).
- **🎨 Embed Builder** — `/embed` slash command. Build & post rich embeds in any channel without the dashboard. Supports title, description (with `\n` escapes), channel target, color (hex / int / 12 named colors like `blurple`/`gold`), image, thumbnail, author name+icon, footer, and clickable title URL. Catches Discord's HTTP rejects (bad image URL / oversized payload) and reports them as friendly ephemeral errors.

Storage for Server Official extras lives inside the existing per-guild `server_configs` block, so it rides the Supabase mirror automatically. A small `_so_set_guild_config` passthrough writer was added because the regular `update_guild_config` filters keys to `DEFAULT_GUILD_CONFIG` and would otherwise silently drop the new starboard keys.

## V2.7 Reliability Hardening
- `save_warnings()` now writes atomically (`tmp + os.replace`) so a crash mid-write can never leave a 0-byte/corrupted `warnings.json`.
- All blocking `requests.post` calls inside async event handlers replaced with `aiohttp` so a slow webhook/Gemini/dashboard endpoint can no longer freeze the bot heartbeat (was up to 8s per message). Affected: `check_warning_limit`, `handle_ai_moderation`, `automod.send_webhook_log`.
- `server_official.fire_external_hooks` now dispatches each external webhook in a daemon thread (was: serial blocking POSTs that could stall a slash command or Flask request for `5s × hook_count`).
- All `tasks.loop` background loops (`sync_bot_settings`, `daily_report_task`, `scheduler_tick`, `reminder_tick`) wrapped in top-level try/except so a single bad iteration can never silently kill the loop and leave the bot frozen on stale state.
- Scheduler/reminder duplicate-fire race fixed: items are marked `posted=True` / `sent=True` and persisted **before** the network send, so a crash mid-send can never replay an announcement on next boot.

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
