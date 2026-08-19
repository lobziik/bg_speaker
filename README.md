# Twitch Narrator Bot

A Twitch Channel Points integration that reads chat messages in the dramatic voice of a fantasy narrator, with multilingual subtitles overlay for OBS.

## Features

- **Channel Points Integration**: Only redeemed messages are narrated (spam filtering via cost)
- **Dramatic Narration**: LLM transforms casual chat into theatrical fantasy prose
- **Swappable Providers**: Groq or Gemini for the LLM, Piper or Gemini for TTS, switched live from the Web UI
- **Editable Prompts**: Every narration and moderation prompt is stored in the database and edited in the UI
- **Fast Local TTS**: Piper TTS with high-quality pre-trained voices (MIT license), no API key needed
- **Cloud TTS**: Gemini TTS with 30 multilingual voices, steered by a natural-language style prompt
- **Web UI Dashboard**: parchment-themed configuration interface with HTMX
- **Auto-Reconnect**: Twitch services auto-connect on startup if previously authorized
- **Configurable Languages**: Source, narrator, and subtitle languages are independent
- **Provider Abstraction**: Swappable LLM, TTS, and Translation providers
- **Priority Queue**: VIP users processed first, with rate limiting
- **Global Cooldown**: Configurable pause after each narration (default 5 min) to prevent spam
- **OBS Overlay**: WebSocket-based overlay with subtitles and audio
- **Type Safety**: Pydantic v2 strict mode, mypy + ty, typed protocols everywhere
- **Single-Container Deploy**: systemd + Caddy image with automatic TLS, one command on a fresh VM

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

# LLM providers (at least one required)
GROQ_API_KEY=your_groq_key
# Google AI Studio key - powers both the Gemini LLM and the Gemini TTS provider
GEMINI_API_KEY=your_gemini_key

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
- A Groq or Gemini API key
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

The bot includes a parchment-themed web dashboard at `http://localhost:8000`:

- **Dashboard** (`/`): Queue status, rate limit countdown, global cooldown status, worker control
- **Settings** (`/settings`): Providers, prompts, language, narrator, queue, overlay, reward, and global cooldown configuration
- **Queue** (`/queue`): View and manage pending narrations
- **Test** (`/test`): Submit test narrations manually
- **Logs** (`/logs`): View narration history with filtering

## Providers

Which provider runs each pipeline stage is stored in the database and changed at
**Settings → Providers**; the pipeline is rebuilt in place, without a restart.
API keys are read from environment variables only and are never written to the
database, so a provider is only selectable once its key is present.

| Stage | Provider | Notes |
|-------|----------|-------|
| LLM | `groq` | Llama / Mixtral / Gemma, needs `GROQ_API_KEY` |
| LLM | `gemini` | Gemini 2.5 Flash / Flash-Lite / Pro, needs `GEMINI_API_KEY` |
| TTS | `piper` | Local, CPU-only, no key; per-language voices, speed and variation sliders |
| TTS | `gemini` | 30 multilingual voices, needs `GEMINI_API_KEY` |

Each provider's settings form is generated from the provider itself: the model
dropdown is fetched from the provider's API, so it lists what your key can
actually use, and the remaining controls come from the JSON Schema it publishes.
If the API cannot be reached the form falls back to the built-in catalogue
instead of showing an empty dropdown. Adding a provider adds its form automatically. The **TTS
Voice** tab is only about mapping languages to Piper voices; everything else about
a provider lives with that provider on the Providers tab.

Gemini specifics:

- **Structured output**: narration and moderation both use response schemas, so
  the JSON contract is enforced by the API rather than by prompting.
- **Thinking level**: defaults to `MINIMAL`, which keeps narration latency down.
  Current models reject the older numeric thinking budget of `0` outright, so
  the level is the only control.
- **Groq reasoning models**: a reasoning model refuses JSON mode while its
  thinking leaks into the response. That is detected and retried once with the
  reasoning turned off, so such a model works without any configuration.
- **Safety threshold**: defaults to `BLOCK_ONLY_HIGH` so ordinary fantasy combat
  description is not rejected by Google's filter; the bot's own moderation step
  remains the Twitch-policy gate. A message the provider does block is treated
  as a policy rejection (points consumed, not refunded).
- **Style, not sliders**: Gemini TTS has no speed or pitch parameters. Delivery
  is directed in plain language via the style prompt, e.g. *"Read this as a
  hushed, conspiratorial narrator"*.

Test either provider from the CLI without changing what the server uses:

```bash
uv run python -m src.main test "I found a legendary sword!" \
    --llm-provider gemini --tts-provider gemini
```

## Prompts

No prompt text is hardcoded at runtime. The defaults ship in
`src/providers/llm/prompts.py`, get written to the database on first boot, and
are edited at **Settings → Prompts**. Changes apply from the next narration -
no restart, no pipeline rebuild.

The narration system prompt is assembled from these sections, in order:

| Section | Purpose |
|---------|---------|
| Format Contract | The JSON shape the model must return |
| Language | Picked automatically: one template for two languages, one for a single language |
| Style | Flair per narrator style (default / whisper / proclaim / mock) |
| Custom prompt | The free-form text from Narrator settings |
| Formatting | Length and tone guidelines |

Moderation has its own system prompt and a user prompt carrying the message.

Runtime values are injected with `$placeholder` syntax - `$` rather than braces
so a prompt can contain literal JSON like `{"voice_text": "..."}`. Each field
accepts only the placeholders the app can fill for it:

| Field | Placeholders |
|-------|--------------|
| Language - two languages | `$narrator_lang_name`, `$narrator_lang_code`, `$subtitle_lang_name`, `$subtitle_lang_code` |
| Language - single language | `$narrator_lang_name`, `$narrator_lang_code` |
| Moderation user prompt | `$message` (required), `$user` |

Unknown placeholders, missing required ones and stray `$` signs are rejected on
save with a message naming the section - nothing is stored until it validates.
Write `$$` for a literal dollar sign. **Reset to Defaults** restores the shipped
text, which is also how you pick up default improvements from a new release:
seeding never overwrites prompts you have edited.

## Deployment

The release image bundles the app and Caddy under systemd in a single container,
so a fresh VM (built and tested on OCI Ampere / arm64) needs only a container
runtime. Images are published to `ghcr.io/lobziik/bg_speaker` for amd64 and
arm64 on every version tag.

```bash
# On the VM: grab the CLI from the latest release
curl -fsSLO https://github.com/lobziik/bg_speaker/releases/latest/download/narrator
chmod +x narrator

# Interactive setup: prompts for domain, keys and dashboard login,
# pulls the image and starts the container
sudo ./narrator install
```

Requirements:

- A DNS A record pointing at the VM - Caddy issues a Let's Encrypt certificate
  automatically on first start.
- TCP 80 and 443 reachable. On Oracle Cloud, open them in **both** the host
  firewall and the VCN security list.
- Set the Twitch OAuth redirect URL to `https://<your-domain>/auth/callback`.

Day-to-day commands:

```bash
./narrator status         # container + service status
./narrator logs app       # narrator application log
./narrator logs caddy     # TLS / proxy log
./narrator restart
./narrator upgrade        # fetch the newest CLI, then restart
```

State lives in two named volumes: `narrator-data` (SQLite database and cached
Piper voices) and `narrator-caddy` (certificates). Configuration is written to
`~/.config/narrator/env`.

The image is defined in `container/` (Dockerfile, systemd units, init script,
Caddyfile template). The root `Dockerfile` is a separate, simpler build used for
Railway and does not include Caddy.

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
