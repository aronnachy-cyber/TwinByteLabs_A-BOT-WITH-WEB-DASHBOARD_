# NVD Official — Private Discord Bot + Web Dashboard

NVD Official is a privately operated Discord moderation bot with an admin web dashboard. Public bot-directory listings and self-service invitation links are disabled; existing bot functionality remains available to the configured operator. It provides auto-moderation, role management, security, social alerts, AI-powered moderation, and a fully interactive web UI for managing your server.

---

## Features

- **Moderation** — Mute, ban, kick, warn, timeout, and more via slash commands
- **Auto-Moderation** — Block links, invites, mentions, custom blacklists, profanity filter
- **AI Moderation** — Google Gemini-powered content analysis
- **Security** — Anti-raid, role monitor, 2FA enforcement, mass-ban prevention
- **Social Alerts** — YouTube, Discord, and Twitch notification integrations
- **Web Dashboard** — Full admin UI at `http://localhost:5000`
- **Private Operation** — Dashboard access is authenticated; public bot promotion and invitation pages are disabled
- **Logging** — Action logs stored in PostgreSQL with live dashboard view
- **Wave Rooms** — Watch-party / sync streaming rooms

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Bot | Python 3.11, discord.py 2.x |
| Dashboard | Flask 3.x, Gunicorn |
| Database | PostgreSQL (via psycopg2) or JSON flat files |
| Optional DB | Supabase (alternative to raw Postgres) |
| AI | Google Gemini (`google-generativeai`) |
| Email | Resend API or SMTP |
| Container | Docker + Docker Compose |

---

## Prerequisites

- Python **3.11+**
- PostgreSQL (optional — app falls back to JSON files if not set)
- A Discord application at [discord.com/developers](https://discord.com/developers/applications)

---

## Quick Start — Local Development

### 1. Clone and enter the project

```bash
git clone https://github.com/your-org/sentinel-bot.git
cd sentinel-bot
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate      # macOS/Linux
.venv\Scripts\activate         # Windows
```

### 3. Install dependencies

```bash
pip install -r SENTINEL-BOT-Main/requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in at least the required values:

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal |
| `DISCORD_CLIENT_ID` | OAuth2 Client ID — for dashboard login |
| `DISCORD_CLIENT_SECRET` | OAuth2 Client Secret |
| `DISCORD_REDIRECT_URI` | Set to `http://localhost:5000/callback` for local dev |
| `FLASK_SECRET_KEY` | Any random 32-byte hex string |
| `ADMIN_DISCORD_ID` | Your Discord user ID (enables Owner Panel) |

Generate a secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 5. Run the application

```bash
cd SENTINEL-BOT-Main
gunicorn --bind 0.0.0.0:5000 --workers 1 --reload wsgi:app
```

Or directly with Python (starts both bot + dashboard):
```bash
cd SENTINEL-BOT-Main
python main.py
```

Dashboard will be at **http://localhost:5000**.

---

## Docker — Recommended for Local Dev with Postgres

The Docker Compose setup starts a PostgreSQL database and the app in one command.

### Requirements
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) or Docker Engine + Compose

### Steps

```bash
# 1. Create your .env file
cp .env.example .env
# Fill in DISCORD_TOKEN, DISCORD_CLIENT_ID, etc.

# 2. Start everything
docker compose up --build

# Dashboard: http://localhost:5000
# Postgres:  localhost:5432 (user: sp4rk / pass: sp4rkpass / db: sp4rkdb)
```

> **Note:** The `DATABASE_URL` in docker-compose.yml is pre-configured to point at the bundled Postgres container. You do not need to set it manually.

To stop:
```bash
docker compose down
```

To reset the database volume:
```bash
docker compose down -v
```

---

## Environment Variables Reference

See [`.env.example`](.env.example) for the full annotated list.

### Required

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Discord bot token |
| `FLASK_SECRET_KEY` | Flask session signing key (`SESSION_SECRET` also accepted) |

### Dashboard / OAuth

| Variable | Description |
|----------|-------------|
| `DISCORD_CLIENT_ID` | OAuth2 client ID |
| `DISCORD_CLIENT_SECRET` | OAuth2 client secret |
| `DISCORD_REDIRECT_URI` | `http://localhost:5000/callback` (local) or your public URL |
| `ADMIN_DISCORD_ID` | Your Discord user ID — unlocks the Owner Panel |

### Database (optional — falls back to JSON files)

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | `postgresql://user:pass@host:5432/dbname` |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon/service key |

### AI Moderation (optional)

| Variable | Description |
|----------|-------------|
| `GEMINI_KEY` | Google Gemini API key |

### Email (optional)

| Variable | Description |
|----------|-------------|
| `RESEND_API_KEY` | Resend API key |
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP port (default: 587) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASS` | SMTP password |

### Deployment

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5000` | HTTP port |
| `DASHBOARD_URL` | auto-detected | Public URL for keepalive ping |
| `FORCE_HTTPS_COOKIES` | — | Set to `true` when behind HTTPS proxy |
| `START_BOT_IN_WEB` | `true` | Start bot in the same process as gunicorn |
| `DEV_PIN` | `change-me-in-production` | PIN for the `/dev` panel — **change this** |

---

## Discord App Setup

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Create a new application
3. Under **Bot**, click **Reset Token** and copy your `DISCORD_TOKEN`
4. Enable the following **Privileged Gateway Intents**:
   - Server Members Intent
   - Message Content Intent
5. Under **OAuth2 → Redirects**, add your redirect URI:
   - Local: `http://localhost:5000/callback`
   - Production: `https://your-domain.com/callback`
6. Copy **Client ID** and **Client Secret** into your `.env`

---

## Database Setup

### Option A — No database (default)

If `DATABASE_URL` is not set, the app uses JSON flat files in `SENTINEL-BOT-Main/`. This is fine for development but data is lost on container restart.

### Option B — PostgreSQL

Set `DATABASE_URL` and the app will auto-create tables on startup.

```
DATABASE_URL=postgresql://user:password@localhost:5432/sp4rkdb
```

The schema is created automatically. You can also inspect `supabase_schema.sql` for the table definitions.

### Option C — Supabase

Create a project at [supabase.com](https://supabase.com), copy the URL and anon key, then set:

```
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=your-anon-key
```

---

## Deployment

### Render (one-click)

`render.yaml` is included for Render deploys. Set secrets in the Render dashboard under **Environment**.

1. Fork this repository
2. Connect to [render.com](https://render.com)
3. Import the repo — Render detects `render.yaml` automatically
4. Set all required environment variables in the Render dashboard
5. Deploy

### Railway

```bash
railway login
railway init
railway up
```

Set env vars with `railway variables set KEY=VALUE` or via the Railway dashboard.

### Fly.io

```bash
fly launch
fly secrets set DISCORD_TOKEN=... FLASK_SECRET_KEY=...
fly deploy
```

### VPS / Self-Hosted

```bash
pip install -r SENTINEL-BOT-Main/requirements.txt
cd SENTINEL-BOT-Main
gunicorn --bind 0.0.0.0:5000 --workers 1 --timeout 120 wsgi:app
```

Use Nginx or Caddy as a reverse proxy for HTTPS, then set `FORCE_HTTPS_COOKIES=true`.

---

## External Services

| Service | Required | Purpose | Sign-up |
|---------|----------|---------|---------|
| Discord API | **Yes** | Bot + OAuth login | [discord.com/developers](https://discord.com/developers) |
| Google Gemini | No | AI auto-moderation | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| PostgreSQL | No | Persistent storage | Self-host or [Render](https://render.com)/[Supabase](https://supabase.com) |
| Supabase | No | Managed Postgres + storage | [supabase.com](https://supabase.com) |
| Resend | No | Transactional email | [resend.com](https://resend.com) |
| Twitch API | No | Twitch live alerts | [dev.twitch.tv](https://dev.twitch.tv) |

---

## Project Structure

```
.
├── .env.example              ← Template for all env vars
├── Dockerfile                ← Container image
├── docker-compose.yml        ← Local dev stack (app + Postgres)
├── README.md
└── SENTINEL-BOT-Main/
    ├── wsgi.py               ← Gunicorn entry point
    ├── main.py               ← Bot entry point (also starts dashboard)
    ├── dashboard.py          ← Flask web dashboard (~6000 lines)
    ├── config_check.py       ← Startup env validation
    ├── requirements.txt
    ├── render.yaml           ← Render deployment config
    ├── Procfile              ← Procfile for Heroku-compatible hosts
    ├── automod.py            ← Auto-moderation logic
    ├── automod_ai.py         ← Gemini AI moderation
    ├── moderation.py         ← Manual mod commands
    ├── roles.py              ← Role management
    ├── logs.py               ← Logging utilities
    ├── security.py           ← Anti-raid, 2FA, role monitor
    ├── social.py             ← Social media alerts
    ├── premium_features.py   ← Premium feature gating
    ├── db_spark.py           ← PostgreSQL helpers (action logs)
    ├── db_users.py           ← User record helpers
    ├── db_forms.py           ← Form submission helpers
    ├── settings.json         ← Default bot config (no secrets)
    └── templates/            ← Flask HTML templates
```

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Commit your changes: `git commit -m "feat: describe change"`
4. Push and open a pull request

Please make sure `.env` is never committed — it is in `.gitignore`.

---

## License

See [LICENSE](LICENSE) for details.
