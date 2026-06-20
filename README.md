# TG Stream Server — Multi-Source Video Streaming

Stream videos from Telegram with **NO 20MB limit**. Supports multi-language, multi-quality switching, admin panel, and embeddable player.

## Features

- **MTProto Streaming** — Files up to 4GB (NO Bot API `getFile`)
- **Multi-Language** — Hindi, English, Japanese, etc. in one player
- **Multi-Quality** — 480p, 720p, 1080p switching
- **Smart Switching** — Changing quality keeps same language and vice versa
- **Custom Player** — Dark themed, responsive, anti-download
- **Pre-roll Ads** — VAST or custom HTML support
- **Admin Panel** — Content, users, ads, and logs management
- **Embed System** — Single iframe for any website
- **Bot Commands** — Create content and add sources via Telegram

## Architecture

```
Browser → /watch/{slug} → Player JS fetches /api/sources/{slug}
                        → Gets signed MTProto stream URLs
                        → <video> plays from /stream/{file_id}
                        → FastAPI → Pyrogram MTProto → Telegram DC
                        → 1MB chunks, Range support, seeking
```

## Quick Start

### 1. Get Credentials

- **API_ID + API_HASH**: https://my.telegram.org
- **BOT_TOKEN**: @BotFather on Telegram
- **ADMIN_TELEGRAM_ID**: @userinfobot on Telegram

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your values
```

### 3. Deploy

**Render:**
```bash
# Push to GitHub → Render → New Web Service
# Build: pip install -r requirements.txt
# Start: uvicorn server:app --host 0.0.0.0 --port $PORT
# Add all .env variables
```

**Docker:**
```bash
docker build -t tg-stream .
docker run -p 8080:8080 --env-file .env tg-stream
```

**Local:**
```bash
pip install -r requirements.txt
python server.py
```

### 4. Connect PHP Website

```php
define('STREAM_SERVER_URL', 'https://your-app.onrender.com');
define('STREAM_SERVER_SECRET', 'same-as-STREAM_SECRET');
```

## Bot Commands

| Command | Description |
|---|---|
| `/new Title` | Create content group |
| `/add slug Language Quality` | Add video to content (send video first) |
| `/links slug` | Get player/embed/stream links |
| `/list` | List all content |
| `/delete slug` | Delete content and sources |
| `/status` | Server status |

### Workflow

```
1. /new Naruto Episode 1        → Creates slug: naruto-episode-1
2. Send Hindi 480p video        → Bot receives it
3. /add naruto-episode-1 Hindi 480p
4. Send Hindi 720p video
5. /add naruto-episode-1 Hindi 720p
6. Send English 720p video
7. /add naruto-episode-1 English 720p
8. /links naruto-episode-1      → Get watch/embed URLs
```

## API Routes

| Route | Purpose |
|---|---|
| `GET /watch/{slug}` | Full player page |
| `GET /embed/{slug}` | iFrame player |
| `GET /api/sources/{slug}` | JSON: signed stream URLs |
| `GET /api/ads` | JSON: active ads |
| `GET /stream/{file_id}` | Raw MTProto stream |
| `GET /health` | Server status |
| `GET /admin` | Admin panel |

## Admin Panel

Access at `https://your-server/admin`

- **Dashboard** — Stats overview
- **Content** — Create/delete content, manage sources
- **Users** — API key management, plan tiers
- **Ads** — VAST/custom pre-roll ads
- **Logs** — View streaming logs

## Database Tables

| Table | Purpose |
|---|---|
| `tg_content` | Content groups (1 movie/episode = 1 row) |
| `tg_sources` | Video files (language + quality per row) |
| `tg_api_users` | API keys and plan limits |
| `tg_ads` | Ad configuration |
| `tg_videos` | Legacy bot-detected videos |
| `tg_view_logs` | Streaming analytics |

## Files

| File | Lines | Purpose |
|---|---|---|
| `server.py` | 995 | FastAPI + Bot + Admin |
| `player.py` | 609 | Player HTML/CSS/JS templates |
| `database.py` | 460 | MySQL CRUD operations |
| `streamer.py` | 316 | MTProto streaming engine |
| `config.py` | 61 | Environment config |

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `API_ID` | ✅ | From my.telegram.org |
| `API_HASH` | ✅ | From my.telegram.org |
| `BOT_TOKEN` | ✅ | From @BotFather |
| `CHANNEL_ID` | ✅ | Your channel (-100...) |
| `DB_HOST/NAME/USER/PASS` | ✅ | MySQL credentials |
| `STREAM_SECRET` | ✅ | Shared secret (64 hex chars) |
| `STREAM_BASE_URL` | ✅ | Public server URL |
| `ADMIN_PASSWORD` | ✅ | Admin panel password |
| `ADMIN_TELEGRAM_ID` | ✅ | Your Telegram user ID |
