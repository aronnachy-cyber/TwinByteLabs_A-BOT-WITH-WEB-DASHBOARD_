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

## Public Landing (Byte Labs)
`templates/bytelabs_landing.html` is the marketing page at `/`. All copy & theme are driven by `site_settings` (see `dev_state.json → site_settings`, defaults in `dashboard.py → SITE_SETTINGS_DEFAULTS`). Features:
- **Mobile hamburger menu** (`#navBurger` + `#mobileMenu`) — visible <860px; auto-closes on link tap, outside click, and Escape.
- **Dev kebab menu** (PIN-protected, `DEV_PIN` env, default `sp4rk2024`; session flag `dev_unlocked`). Items: Maintenance toggle, Site Theme, Edit Hero/Text, View Bot Requests.
- **Site Settings modal** (`#settingsOverlay`) — live-edit brand name, 5 theme colors (with paired color picker + hex input → live `:root` CSS-var preview, including regenerated `--grad`), hero badge/title/lede, "What We Build" headline + lede, CTA strip headline + lede, footer tag. Saves to `/dev/site_settings`; reset via `/dev/site_settings/reset`.
- **Public star-rating feedback** (`#feedback` section) — no login required; 5 stars + optional name + comment. POST `/api/public_feedback` (source=`landing`); the existing logged-in dashboard feedback (`/api/feedback`) is unchanged. Recent ratings + average shown live.
- **"Ask for a Bot" registration form** (`#askOverlay`) — full intake: name, email, Discord handle, bot name, purpose, description (required), features list, needs_dashboard checkbox, bot_token (optional, masked in inbox), budget, timeline, notes. POST `/api/bot_request`. Stored in Supabase table `bot_requests` + local `bot_requests.json` fallback.
- **Bot Requests Inbox modal** (`#reqOverlay`) — dev-only; lists submissions with masked bot tokens (`first6…last4`) and per-row delete. Endpoints `/api/bot_requests` (GET) and `/api/bot_requests/<id>` (DELETE) require `dev_unlocked`.

## Templates
- **SENTINEL-BOT-Main/templates/bytelabs_landing.html** - Public Byte Labs landing page (/)
- **SENTINEL-BOT-Main/templates/landing.html** - SP4RK product page (/sp4rk)
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

## V2.8.2 — Dashboard UX Polish (SP4RK + Server Official)
Both admin dashboards (`templates/index.html` and `templates/server_official_dashboard.html`) were refreshed for clarity and visual consistency, with no behavioral changes to backend routes, IDs, or JS handlers.

- **Modernized design tokens** — both dashboards now share gradient accents (`--grad`), softer card depth, focus rings, gradient stat numbers, hover lifts, and a `fadeIn` tab transition. SP4RK keeps its blue identity (#3a8fff); SO keeps its emerald/cyan/purple gradient. SP4RK's `bl_theme` localStorage dev panel still drives the live CSS-var overrides.
- **Help text everywhere** — every configurable input now has a `.help-text` line explaining what it does, what format Discord expects, where to copy IDs from, and what variables are available (e.g. `{user}`, `{server}`, `{title}`, `{url}`). Cards that wrap multiple options got a short intro paragraph.
- **Rewritten cards with how-to docs** — Reaction Roles, Tickets, and Auto-Responder on SP4RK now lead with a "How it works" card (3-step setup, code-styled command snippets, copy-message-ID tip). Tickets uses the new `.stat-grid` for open/closed counts. Auto-Responder rule editor switched from inline-styled inputs to the shared `.btn` / form classes.
- **Server Official cards polished** — Welcome/Leave Configuration, Embed Builder (now with paired hex + native color picker via `syncEmbColor`), Sticky Messages, Custom Commands, Incoming Webhooks, and Outgoing Hooks all gained labeled fields + per-input help text.

Every existing element ID, function name, onclick handler, and Jinja variable was preserved (`showTab`, `saveAll`, `restartBot`, `loadGiveaways`, `embColor`, `cfgWelcomeChan`, `addAutoResponder`, `setSticky`, `addCustomCmd`, `createWebhook`, `addHook`, etc.) so all dashboard JS continues to work unchanged.

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

## Wave📡 — Streaming & Watch Parties
Discord bot (`wavebot.py`) + web watch room. Uses `W_TOKEN` env var. Admin-only commands:
- `/here` — designates the current channel as the Wave announcement channel for the guild (stored in `wave_config.json`)
- `/start [title] [url]` — creates a watch room via `/api/wave/create`, posts the viewer link to the designated channel, sends the host link to the admin via ephemeral reply

**Web watch room (`stream_room.html` at `/watch/<room_id>`):**
- **Host view**: URL bar + Browse button (opens collapsible mini browser panel) + Find Videos (yt-dlp scan) + Stream This button
- **Mini browser**: embedded iframe of any URL; cross-origin blocked sites show an overlay with fallback to Find Videos
- **Stream This Page**: sends the current URL to all viewers as an iframe stream (stream_type=iframe)
- **Find Videos**: POSTs to `/api/wave/scan` — yt-dlp extracts HLS/MP4/direct stream URLs from the page; host picks one → all viewers get direct video playback (stream_type=video) with HLS.js
- **Viewer view**: sees whatever the host is streaming (iframe or synced video player) + live chat
- **Chat**: live chat sidebar, polled every 2s
- **Presence**: viewers send a ping every 6s; viewer count shown live

**Routes**: `GET /wave`, `GET /wave/dashboard` (login required), `GET /watch/<room_id>`, `POST /api/wave/create`, `GET /api/wave/state/<room_id>`, `POST /api/wave/set-stream/<room_id>`, `POST /api/wave/control/<room_id>`, `POST /api/wave/chat/<room_id>`, `POST /api/wave/ping/<room_id>`, `POST /api/wave/scan`, `POST /api/wave/end/<room_id>`, `GET /api/wave/admin/rooms` (login required)

**Wave Dashboard** (`wave_dashboard.html`) — sidebar + main layout matching Server Official style, rose/red theme:
- **Overview**: stat cards (active rooms, viewers online, guilds configured, bot status), live rooms list, quick actions
- **Active Rooms**: full table of running rooms with viewer count, stream type/URL, created time; View, Copy Viewer Link, End Session per row; auto-refreshes every 10s
- **Create Room**: title + optional URL form → POST /api/wave/create → shows viewer link + host link with copy buttons
- **Bot Setup**: W_TOKEN status display, /here and /start command reference
- **Guild Config**: table of guild IDs and their configured stream channel IDs from wave_config.json

**Storage**: `wave_rooms.json` (atomic write, thread-safe lock), `wave_config.json` (guild → stream channel id)

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
- `/bots` → Bot card showcase (templates/bots.html). Cards: Server Official (admin-customizable), SP4RK (live).
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

## V2.8.3 — Embed Editor + Bot Hardening (Apr 2026)
**New: Centralized Embed System (`embeds.py`)** — 22 editable embed templates (17 SP4RK + 5 Server Official) covering welcome/leave, all moderation actions, message logs, voice events, tickets, giveaways, starboard, and SO log/announcement. Stored as default ← global override ← per-guild override in `embed_templates.json` (atomic write via tmp+os.replace, thread-safe). Placeholder substitution for `{user_mention}`, `{server_name}`, `{moderator}`, `{reason}`, `{warn_count}`, `{ticket_id}`, `{prize}`, etc.

**Wired callsites**: `main.py` (warn/kick/ban/timeout/unban + welcome/leave/member_ban + message_edit/message_delete + voice events), `tickets.py` (open/close), `giveaway.py` (active/ended/no_entries), `server_official.py` (welcome/leave + `_post_log` helper), `so_extras.py` (starboard). Bots now render every embed through `embeds.render(key, ...)` or `logs.log_template(bot, config, key, ...)`.

**Dashboard Embed Editor tab** in both `/dashboard` (SP4RK) and `/server-official`:
- Sidebar nav item with paint-brush icon
- Two-pane layout: category-grouped template list (with "edited" pill for customized ones) + editor
- Fields: title, description, color picker, footer, thumbnail URL
- Click-to-insert placeholder chips that target the focused field
- Live Discord-style preview rendered server-side (`embeds.preview()` returns rendered JSON)
- Save / Reset to Default / Preview buttons
- Backend: `GET/POST/DELETE /api/embeds`, `POST /api/embeds/preview` (SP4RK); same under `/server-official/api/embeds` (SO). Save logs an `embed_edit` audit entry via `so.add_bot_action`.

**Bug fixes shipped this round**:
- `automod_ai.py`: switched from blocking `requests.post` to `aiohttp` so AI moderation no longer freezes the bot's event loop on slow Gemini calls.
- `reaction_roles.py`: `_local_save` now writes via tmp + `os.replace` so a crash mid-write can't corrupt the persistence file.
- `main.py` `userinfo`: guards against `discord.User` (vs `Member`) and uses `discord.Color.default()` comparison instead of comparing `.value` against `0`.
- `server_official.py` `daily_backup_tick`: wrapped the body in try/except so a single failed backup can't kill the recurring task.
- `giveaway.py`: added `discord.Forbidden` guard around `add_reaction("🎉")` so a missing "Add Reactions" perm logs a warning instead of crashing the start command.

## V2.8.4 — User DB on Postgres + Per-Server Embeds + Leveling Removed (Apr 2026)
**Postgres user store (`db_users.py`)** — When `DATABASE_URL` is set (Replit Postgres), users are persisted in a real `sentinel_users` table via `psycopg2` connection pool. Auto-creates the schema on first boot, includes one-shot migration of any pre-existing `users.json`, and exposes `load_all`, `upsert`, `find_by_discord_id`, `delete`. `dashboard.py` `load_users` / `save_single_user` / `find_user_by_discord_id` now prefer Postgres → Supabase REST → JSON file (full backwards compat).

**Per-server embed scoping fix** — Server Official's Embed Editor previously sent edits without a `guild_id`, so picking a server in the top bar then editing an embed silently overwrote the global default. Added a "Apply changes to: [This server only / All servers]" radio in the embed editor card that defaults to the selected server, disables the per-server option when no server is picked, and threads `guild_id` through every call (`soEmbLoad`, `soEmbSave`, `soEmbReset`, `soEmbPreview`). Changing the top-bar guild also auto-refreshes the editor list and override pills.

**Server Official analytics empty-state** — `loadAnalytics` now overlays a friendly "No data yet — bot needs to be online" badge on each chart when the API succeeds with empty buckets, and a separate "Analytics unavailable" message when the API call itself fails. New `setChartEmptyState(canvasId, message)` helper handles overlay lifecycle.

**Leveling removed** — Deleted orphaned `leveling.py` + `xp.json`. Cleaned dead reference `state["xp"].pop(gid, None)` in `dashboard.py` `reset_guild`. Updated `wsgi.py` warning message and `server_official.py` module docstring. Removed XP/Leveling chips, descriptions, feature card, and pricing list items from `landing.html`, `bots.html`, and `bytelabs_landing.html`.

## V2.8.5 — Forms moved to dedicated dev page + Postgres-backed (Apr 2026)
**Form submissions now persist in Postgres.** New `db_forms.py` mirrors the `db_users` pattern with a generic `sentinel_form_submissions` table (`id`, `form_type`, `status`, `payload` JSONB, `created_at`, `updated_at`) so additional form types can be added later without a schema change. `dashboard.py` now uses `_save_bot_request(entry)` and `_delete_bot_request(rid)` helpers which prefer Postgres and transparently fall back to the legacy `bot_requests.json` file when `DATABASE_URL` isn't set. One-shot JSON→PG migration runs automatically on boot (idempotent).

**New dedicated "Forms" admin page** (`/dev/forms`) — A full-page, dev-gated submissions inbox lives at its own URL instead of an in-page overlay. Returns 404 to anyone without a dev session (does not even leak existence). Page shows each submission as a card with submitter identity, contact info, bot details, dashboard need, masked token preview (`abc123…wxyz`), and any free-text fields (description / features / notes). Per-card actions: copy email, export single submission as JSON, delete (calls existing `DELETE /api/bot_requests/<id>`). Top toolbar has live search across name/email/bot/purpose/notes plus a status filter (new / replied / archived). Header pill shows the active storage backend ("Postgres" or "Local JSON file").

**Dev menu updates** — Added a "Forms" link to the developer-tools kebab dropdown in both `landing.html` (SP4RK) and `bytelabs_landing.html` (Byte Labs). On Byte Labs the entry replaces the older inline "View Bot Requests" overlay trigger so all developer access goes through the same dedicated page.

## V2.8.6 — Server Official analytics: real names + concurrency-safe counters (Apr 2026)
**Top chatters / Top voice now show real names.** Previously the dashboard rendered placeholder labels like `User …4567` (literally the last four digits of the Discord ID) for every bar in the SO analytics charts because the bot exposed only raw IDs. `server_official.get_analytics()` now resolves each top-N user ID through the live `bot` cache (per-guild member nickname → display name → global username → ID-tail fallback) and ships the resolved name as a third element on each `[uid, value, name]` tuple. Frontend uses a new `labelForTopEntry()` helper that prefers the server-supplied name and only falls back to the last-4-digit placeholder if name resolution failed (e.g. when the bot is offline or the user has left the server).

**Race-condition undercount fix.** The `_bump_messages_for`, `_bump_join`, `_bump_leave`, and `_add_voice_minutes` functions did `state = load_state(); …; save_state(state)` without holding a lock across the whole sequence — so two concurrent events on different threads could both read the same value, each `+1`, and the second `save_state` would overwrite the first, silently undercounting activity. Switched `_state_lock` to `threading.RLock()` (so it can be safely re-entered from inside `load_state`/`save_state`) and wrapped every bump in `with _state_lock:` to make the read-modify-write atomic.

**Bonus**: `joins_per_day` and `leaves_per_day` are now also trimmed to the same 60-day window as `messages_per_day` so the state file can't grow unboundedly.
