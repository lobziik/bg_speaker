# BG3 Twitch Narrator Bot

A Twitch Channel Points integration that reads chat messages in the dramatic voice style of a D&D narrator, with multilingual subtitles overlay for OBS.

## Features

- **Channel Points Integration**: Only redeemed messages are narrated (spam filtering via cost)
- **D&D Narrator Style**: LLM transforms casual chat into dramatic narrator prose
- **Fast Local TTS**: Piper TTS with high-quality pre-trained voices (MIT license)
- **Web UI Dashboard**: BG3-themed configuration interface with HTMX
- **Auto-Reconnect**: Twitch services auto-connect on startup if previously authorized
- **Configurable Languages**: Source, narrator, and subtitle languages are independent
- **Provider Abstraction**: Swappable LLM, TTS, and Translation providers
- **Priority Queue**: VIP users processed first, with rate limiting
- **Global Cooldown**: Configurable pause after each narration (default 5 min) to prevent spam
- **OBS Overlay**: WebSocket-based overlay with subtitles and audio
- **Type Safety**: Pydantic v2 strict mode, mypy + ty, typed protocols everywhere

## Quick Start

```bash
# Install dependencies
uv sync --extra dev

# Set up environment variables
cp .env.example .env
# Edit .env with your credentials (see Configuration below)

# Start the server
uv run python -m src.main serve --port 8000

# Or test CLI pipeline directly
uv run python -m src.main test "Hello everyone!" --user DragonSlayer
```

## Configuration

Create a `.env` file with:

```bash
# Twitch Application (from https://dev.twitch.tv/console)
TWITCH_CLIENT_ID=your_client_id
TWITCH_CLIENT_SECRET=your_client_secret
TWITCH_CHANNEL=your_channel_name

# LLM Provider (at least one required)
GROQ_API_KEY=your_groq_key

# App
SECRET_KEY=your_secret_key
DATABASE_URL=sqlite:///data/narrator.db

# Logging (optional)
LOG_LEVEL=info          # debug, info, warning, error
LOG_FORMAT=console      # console (human-readable) or json (for Railway/production)
```

## Twitch OAuth Setup

1. Create an application at https://dev.twitch.tv/console
2. Set OAuth Redirect URL to `http://localhost:8000/auth/callback`
3. Copy Client ID and Client Secret to `.env`
4. Start server: `uv run python -m src.main serve`
5. Visit `http://localhost:8000/auth/login` to authorize
6. Check status at `http://localhost:8000/auth/status`

**Auto-connect**: Once authorized, Twitch services auto-reconnect on server restart. Use the Logout button in the dashboard to disconnect.

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- Groq API key (or other LLM provider)
- Twitch Developer Application

## Development

```bash
# Run tests
uv run pytest

# Type checking (both mypy and ty)
uv run mypy src
uv run ty check src

# Linting
uv run ruff check .

# Format code
uv run ruff format .
```

## Web UI

The bot includes a BG3-themed web dashboard at `http://localhost:8000`:

- **Dashboard** (`/`): Queue status, rate limit countdown, global cooldown status, worker control
- **Settings** (`/settings`): Language, narrator, queue, overlay, reward, and global cooldown configuration
- **Queue** (`/queue`): View and manage pending narrations
- **Test** (`/test`): Submit test narrations manually
- **Logs** (`/logs`): View narration history with filtering

## OBS Overlay

Add a Browser Source in OBS pointing to `http://localhost:8000/overlay`:
- Width: 1920, Height: 1080 (or match your canvas)
- Enable "Control audio via OBS"
- Debug mode: Add `?debug=1` for connection status

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/auth/login` | GET | Start Twitch OAuth flow |
| `/auth/callback` | GET | OAuth callback handler |
| `/auth/logout` | POST | Clear stored tokens |
| `/auth/status` | GET | Check auth status |
| `/api/test/narrate` | POST | Test narration (requires `user`, `message`) |
| `/api/test/queue` | GET | Get current queue |
| `/api/test/queue/{id}` | DELETE | Skip queue item |
| `/ws/overlay` | WS | WebSocket for OBS overlay |

## Architecture

```
Twitch EventSub → Queue → Rate Limiter → Pipeline (LLM → TTS) → WebSocket → OBS
                    ↑                                              ↓
                 Web UI ←──────────────────────────────────────────┘
```

See `ai/progress_phase_*.md` for detailed implementation notes:
- Phase 1: Core providers (LLM, TTS)
- Phase 2: Twitch integration, queue, rate limiting
- Phase 3: WebSocket, OBS overlay
- Phase 4: Web UI dashboard

## License

MIT
