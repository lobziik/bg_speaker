# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BG3 Twitch Narrator Bot - A Twitch Channel Points integration that reads chat messages in Baldur's Gate 3 narrator voice style, with multilingual subtitles overlay for OBS. Transforms casual chat into dramatic D&D narrator prose using LLM, then synthesizes speech with TTS.

**Core Flow:** Twitch Channel Points Redemption → LLM (D&D formatting) → Translation (if needed) → TTS (Piper) → WebSocket → OBS Browser Source (audio + subtitles)

## Build & Development Commands

```bash
# Install dependencies (uses uv)
uv sync

# Install with dev tools
uv sync --extra dev

# Install with ElevenLabs support
uv sync --extra elevenlabs

# Start the web server (Twitch integration)
uv run python -m src.main serve --port 8000

# Test CLI pipeline
uv run python -m src.main test "Hello everyone!" --user DragonSlayer --style whisper

# Run tests
uv run pytest

# Run single test file
uv run pytest tests/test_providers/test_llm.py

# Run with coverage
uv run pytest --cov=src --cov-report=term-missing

# Type checking
uv run mypy src

# Linting
uv run ruff check .

# Auto-fix lint issues
uv run ruff check --fix .

# Format code
uv run ruff format .
```

## Architecture

### Provider Pattern
All external services (LLM, TTS, Translation) use Protocol-based abstraction in `src/providers/`:
- `LLMProvider` protocol: Groq (MVP), OpenAI, Anthropic, OpenRouter, Ollama
- `TTSProvider` protocol: Piper (MVP, MIT licensed, CPU-friendly), ElevenLabs (premium)
- `TranslateProvider` protocol: LLM-based (MVP), DeepL, Google, Argos

Providers implement `get_settings_schema()` returning JSON Schema for dynamic Web UI form generation.

### Service Layer (`src/services/`)
- `twitch/eventsub.py`: TwitchIO 3.x EventSub WebSocket for Channel Points redemptions
- `twitch/rewards.py`: Manages Channel Points reward lifecycle (create/pause/fulfill/cancel)
- `pipeline.py`: Orchestrates LLM → TTS flow
- `queue.py`: Message queue with priority and rate limiting
- `rate_limiter.py`: Global TTS rate limit + per-user cooldowns
- `global_cooldown.py`: Global Twitch reward cooldown (pauses reward after each narration)
- `worker.py`: Background queue processor that runs pipeline and broadcasts via WebSocket

### Data Layer
- SQLite database at `data/narrator.db`
- Migrations in `migrations/` folder (auto-applied on startup)
- `src/db/repositories/`: Typed repository pattern for settings, providers, logs

### Web Components
- `src/api/`: FastAPI routes (JSON API + WebSocket)
- `src/api/websocket.py`: WebSocket connection manager for overlay broadcasts
- `src/api/ws_types.py`: TypedDict message types for WebSocket protocol
- `src/api/routes/overlay.py`: WebSocket endpoint (`/ws/overlay`) and static file serving
- `overlay/`: OBS Browser Source (HTML/CSS/JS) connecting via WebSocket

### Web UI (Phase 4)
- `src/views/`: HTMX view routes returning HTML pages/partials
  - `dashboard.py`: Main dashboard with queue status, worker control
  - `settings.py`: Settings forms (language, narrator, queue, overlay, reward)
  - `queue.py`: Queue management (view, skip, clear)
  - `test.py`: Manual narration testing
  - `logs.py`: Narration history with filtering/pagination
- `src/templates/`: Jinja2 templates
  - `base.html`: Base layout with BG3-themed sidebar
  - `pages/`: Full page templates (dashboard, settings, queue, test, logs)
  - `partials/`: HTMX partial templates for live updates
- `src/static/`: Static assets
  - `css/main.css`: BG3 theme (dark parchment, gold accents)
  - `css/components.css`: Reusable component styles
  - `js/htmx.min.js`: HTMX 2.0.4 bundled locally
  - `js/app.js`: WebSocket integration for live updates
- `src/db/repositories/narration_log.py`: Narration history logging

## Type Safety Requirements

- Pydantic v2 with strict mode (`StrictModel` base class in `src/core/types.py`)
- `@runtime_checkable` Protocol classes for providers
- TypedDict for WebSocket message types
- No `Any` types - explicit types everywhere
- mypy strict mode enforced in CI
- ty (Ruff's type checker) additionally for stricter checks
- Use `# ty: ignore[rule-name]` for ty-specific suppressions (doesn't trigger mypy unused-ignore)
- No `# type: ignore` without explicit asking and extended comment explaining why

## Fail fast and LOUD
Never do `except Exception:`. Always narrow down the exception type and handle it properly.
Better fail than swallow an error.

## Queue Event Handlers
Queue event handlers (registered via `queue.on_event()`) are called while the queue lock is held. **Never call `queue.get_items()` or other queue methods from an event handler** - this will cause a deadlock. Use `asyncio.create_task()` to defer any queue access.

## Key Design Decisions

- **Piper TTS for MVP**: CPU-friendly (3-11x realtime), MIT licensed, no GPU required. ElevenLabs optional for premium voices.
- **Single OBS Source**: Audio + subtitles delivered via one WebSocket connection
- **Language Independence**: `source_lang`, `narrator_lang`, `subtitle_lang` are separately configurable
- **Translation Skip**: When `source_lang == narrator_lang`, translation step is bypassed
- **SecretStr**: All API keys use Pydantic SecretStr (env vars only, never in DB)
- **Global Cooldown**: After each narration (success or failure), the Twitch reward is paused for a configurable duration (default 5 minutes) to prevent rapid redemptions. State persists across restarts.

## Configuration

- **Secrets**: Environment variables only (`.env` file, never committed)
- **Static config**: `config/default.yaml` - safe to commit
- **Dynamic settings**: SQLite database - modified via Web UI

Required env vars: `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_CHANNEL`, `SECRET_KEY`, plus at least one LLM API key.

## Testing

Tests in `tests/` directory mirror `src/` structure. Use `pytest-asyncio` for async tests. Coverage target: 80%.

## Web UI

The web dashboard is served at `http://localhost:8000` with the following pages:

| Route | Description |
|-------|-------------|
| `/` | Dashboard with queue status, worker control |
| `/settings` | Configuration for language, narrator, queue, overlay, rewards |
| `/queue` | Queue management (view, skip, clear items) |
| `/test` | Manual narration testing |
| `/logs` | Narration history with filtering |

HTMX patterns used:
- `hx-post` for form submissions with toast responses
- `hx-get` with `hx-trigger="every 1s"` for live polling
- `hx-trigger="refresh from:body"` for WebSocket-triggered updates

## OBS Overlay

The overlay is served at `/overlay` and connects via WebSocket at `/ws/overlay`.

```bash
# Test the overlay
curl -X POST http://localhost:8000/api/test/narrate \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!", "user": "TestUser"}'
```

Debug mode: Add `?debug=1` to show connection status and queue indicator.
