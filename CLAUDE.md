# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Twitch Narrator Bot - A Twitch Channel Points integration that reads chat messages in the voice of a theatrical fantasy narrator, with multilingual subtitles overlay for OBS. Transforms casual chat into dramatic narrator prose using LLM, then synthesizes speech with TTS.

**Core Flow:** Twitch Channel Points Redemption → LLM (narrator formatting) → Translation (if needed) → TTS (Piper) → WebSocket → OBS Browser Source (audio + subtitles)

## Build & Development Commands

```bash
# Install dependencies (uses uv)
uv sync

# Install with dev tools
uv sync --extra dev

# Start the web server (Twitch integration)
uv run python -m src.main serve --port 8000

# Test CLI pipeline (add --llm-provider / --tts-provider to override the stored selection)
uv run python -m src.main test "Hello everyone!" --user DragonSlayer --style whisper

# Run tests
uv run pytest

# Run single test file
uv run pytest tests/test_providers/test_llm.py

# Run with coverage
uv run pytest --cov=src --cov-report=term-missing

# Type checking
uv run mypy .
uv run ty check .

# Linting
uv run ruff check .

# Auto-fix lint issues
uv run ruff check --fix .

# Format code (CI enforces `ruff format --check .`)
uv run ruff format .
```

## Architecture

### Provider Pattern
All external services (LLM, TTS) use Protocol-based abstraction in `src/providers/`:
- `LLMProvider` protocol: Groq (`GROQ_API_KEY`), Gemini (`GEMINI_API_KEY`)
- `TTSProvider` protocol: Piper (local, MIT licensed, CPU-friendly), Gemini (`GEMINI_API_KEY`)

Every method on the protocols is called by the application - there is no speculative surface.
Streaming, voice cloning and the `supports_*` flags were removed because nothing could use them:
the narration JSON contract makes partial LLM output unusable, and streaming audio to the overlay
needs a chunked WebSocket protocol that does not exist yet.

- `get_settings_schema()` renders the provider's form on the Providers tab (see below).
- `list_models()` / `list_voices()` fill the model and voice dropdowns, so a provider's catalogue
  has one source of truth.

### Groq Reasoning Models
A reasoning model refuses JSON mode while its thinking ends up in the content, and Groq reports
that as `json_validate_failed` with an empty `failed_generation`. `_complete_json()` retries once
with `reasoning_effort="none"`, which is what makes such a model work without a hardcoded list of
which models reason. `reasoning_effort` is also exposed as a setting but left unset by default: a
model that does not reason rejects the parameter outright.

### Live Model Catalogues
`list_models()` asks the provider's API which models the configured key can actually use, so the
dropdown reflects the account rather than a list baked into the source.

- Gemini filters on `supported_actions` containing `generateContent`, which is authoritative.
- Groq's SDK exposes only an ID, so speech models are filtered out by substring
  (`NON_CHAT_MODEL_MARKERS`) - a heuristic, and the reason the built-in list still matters.
- `AVAILABLE_MODELS` remains the fallback: a failed or empty listing logs a warning and the form
  renders the built-in catalogue rather than an empty dropdown.
- `src/providers/catalogue.py` memoises the result for five minutes, keyed per provider module,
  because the settings view builds a throwaway provider on every render. The TTL is absolute, so
  a frequently viewed page still refreshes. Tests must call `invalidate()` - `tests/conftest.py`
  does it automatically.
- Both protocols include `close()`; `TTSProvider` also includes `start()`, so the app lifecycle can
  treat every provider the same when swapping them at runtime.

### Schema-Driven Settings Forms
`src/views/schema_form.py` is the only place that interprets a provider schema. It parses the
JSON Schema into typed `SettingsField` objects, which `partials/settings_field.html` renders; the
templates never inspect the schema themselves. A new provider therefore gets a settings form for
free - implement `get_settings_schema()` and add it to `LLM_FORM_SPECS` / `TTS_FORM_SPECS`.

- The supported subset is small on purpose (string, string+enum, number, integer, `X | null`, and
  `"format": "textarea"`). Anything else raises `SchemaError` rather than rendering a wrong widget.
- The settings view builds one instance per configured provider to read its schema and catalogue,
  then closes the throwaways. Constructors perform no network I/O.
- Bounds declared in the schema are enforced twice: the browser gets `min`/`max`, and the Pydantic
  settings model rejects anything that still arrives, reported as a toast rather than a 500.

### Per-Language Voice Overrides Are Piper-Only
`TTSVoiceSettings.voice_overrides` holds Piper voice IDs. The worker only applies them while
`pipeline.tts_provider_name` is `piper` - handing `en_US-amy-medium` to Gemini TTS would fail
voice validation on every narration in that language and refund the points.

Only implemented providers are ever offered: `EnvSettings.get_available_llm_providers()` /
`get_available_tts_providers()` report exactly what the factory can build.

### Provider Selection & Factory
`src/providers/factory.py` is the single place where provider *selection* (database) meets provider
*credentials* (environment). Never construct providers directly outside it.

- Selection is stored under the `providers` settings key (`ProviderSettings`), edited at
  Settings -> Providers.
- Per-provider settings live under `groq_llm`, `gemini_llm`, `piper`, `tts_voice`, `gemini_tts`.
- API keys are environment-only. A blank value (`GROQ_API_KEY=` in an env file) counts as unset.
- On a fresh install with no stored selection, `default_provider_settings(env)` picks the first
  configured LLM provider plus Piper, so a Gemini-only deployment starts a working worker.
- A provider that cannot be built raises `ProviderConfigurationError` naming the missing variable.
  Startup logs it and continues (dashboard stays up); the settings view turns it into a toast.

`AppState.rebuild_pipeline()` performs both the initial wiring at startup and every runtime swap:
it builds new providers, hands them to the worker via `QueueWorker.set_pipeline()` (which waits on
the processing lock so the in-flight narration finishes on the old pipeline), then closes the
previous providers. It also creates and starts the worker if it does not exist yet.

### Gemini Providers
`src/providers/llm/gemini.py` and `src/providers/tts/gemini.py`, both on the `google-genai` SDK.

- Narration and moderation use explicit `types.Schema` response schemas. They are hand-written
  rather than derived from the Pydantic models: `StrictModel` emits JSON Schema keywords
  (`additionalProperties`) the Gemini API rejects.
- The SDK's enums accept unknown values with only a `UserWarning`, so `safety_threshold` is
  validated against actual members before use.
- Reasoning effort is set with `thinking_level` (MINIMAL by default, for narration latency). The
  numeric `thinking_budget` is gone: current models reject a budget of `0` with a bare
  `400 INVALID_ARGUMENT`, and two mutually exclusive fields cannot share a form that submits every
  field on save. A 400 now logs the thinking and safety settings that were sent.
- There is no hardcoded table of per-model thinking rules any more: it went stale (it named models
  the API now 404s for) and the API is the authority.
- A safety-filter refusal raises `LLMContentBlockedError`, which the pipeline maps onto
  `ModerationRejectedError` - points are consumed, not refunded, same as a policy violation.
- Gemini TTS returns raw PCM; the sample rate is parsed from the part's mime type
  (`audio/L16;codec=pcm;rate=24000`) and never assumed, then wrapped in a WAV container.
- Gemini TTS has no speed/pitch knobs, so `update_settings()` raises `NotImplementedError` and a
  non-default `TTSSettings` is rejected - delivery is directed by `style_prompt` instead.
- `style_prompt` must read as *how to speak*, never as *what to produce*. Gemini TTS is a
  generative model: given "Transform the user's message into concise narrative prose" it improvises,
  speaking the direction itself aloud in the direction's own language and ignoring the payload -
  while the subtitles, which come from the LLM, stay correct, so the overlay looks fine and only the
  audio is wrong. `VERBATIM_GUARD` is appended to the operator's direction in `_build_prompt()` and
  is deliberately not editable in settings; do not make it configurable.

### Prompt Templates
Nothing about the narration or moderation prompt is hardcoded at runtime. The texts in
`src/providers/llm/prompts.py` are *defaults*; the effective values live under the `prompts`
settings key (`PromptSettings`) and are edited at Settings -> Prompts.

- Seeded on first boot with `SettingsRepository.set_if_absent("prompts", ...)`, so the form opens
  with real text. Later boots never overwrite an operator's edits - which also means new defaults
  in code do not reach existing installs; that is what the Reset button is for.
- The system prompt is assembled in this order: `base_system`, language section, style section,
  the operator's `NarratorSettings.system_prompt`, then `formatting`.
- Sections that vary at runtime are `string.Template` strings using `$placeholder`. `$` rather than
  `str.format` because prompts contain literal JSON (`{"voice_text": ...}`) that `format` would
  choke on. Only `language_dual`, `language_single` and `moderation_user` are substituted; the rest
  are passed through verbatim, so a `$` in prose is harmless there.
- Placeholders are whitelisted per field and validated on save *and* on load (`validate_template`):
  unknown names, missing required ones and bad `$` syntax all raise. The settings view turns that
  into a toast and stores nothing.
- Moderation prompts are no longer provider internals: `LLMProvider.moderate()` takes
  `system_prompt` and `user_prompt` from the caller. The worker loads `PromptSettings` per item, so
  edits apply from the next narration without a restart.

### Piper TTS Voice Selection
Piper automatically selects the appropriate voice based on the configured `narrator_lang`:
- `LANGUAGE_DEFAULT_VOICES` in `src/providers/tts/piper.py` maps each `LanguageCode` to a default voice
- Users can override default voices per language via Settings → TTS Voice tab
- Voice overrides stored in `TTSVoiceSettings.voice_overrides` (SQLite, key: `tts_voice`)
- Voice models are lazy-loaded and cached with TTL-based cleanup (see below)
- The `language` parameter flows: Worker → Pipeline → TTS `synthesize()`

### Piper TTS Memory Management
Voice models (~60-100MB each) are automatically unloaded after inactivity to reduce memory usage:
- `TTLCache` in `src/core/ttl_cache.py` provides generic TTL-based caching with background cleanup
- `PiperTTSProvider._voices` uses TTLCache to manage voice model lifecycle
- Default TTL: 30 minutes (`PiperTTSProvider.DEFAULT_VOICE_TTL_SECONDS`)
- Cleanup interval: 1 minute (`PiperTTSProvider.DEFAULT_CLEANUP_INTERVAL_SECONDS`)
- Provider lifecycle: `start()` begins cleanup task, `close()` stops and clears cache
- AppState tracks `tts_provider` and calls `close()` on shutdown

### Service Layer (`src/services/`)
- `twitch/init_services.py`: Initializes EventSub + RewardController (at startup or after OAuth)
- `twitch/eventsub.py`: TwitchIO 3.x EventSub WebSocket for Channel Points redemptions
- `twitch/rewards.py`: Manages Channel Points reward lifecycle (create/pause/fulfill/cancel)
- `pipeline.py`: Orchestrates LLM → TTS flow
- `queue.py`: Message queue with priority and rate limiting
- `rate_limiter.py`: Global TTS rate limit + per-user cooldowns
- `global_cooldown.py`: Global Twitch reward cooldown (pauses reward after each narration)
- `worker.py`: Background queue processor that runs pipeline and broadcasts via WebSocket

### Audio Length Is Bounded, And The Worker Blocks On Playback
`_broadcast_narration()` sleeps for the audio's full duration before the worker takes the next
item, so one runaway synthesis stalls the whole queue for as long as it plays. That happened: a
generative TTS style prompt produced 655 seconds of audio for an 84-character line and the queue
sat for eleven minutes.

- `NarrationPipeline._reject_runaway_audio()` fails such a narration with `AudioLengthError` before
  it reaches the overlay. The allowance is `len(voice_text) / SPEECH_CHARS_PER_SECOND`, times
  `AUDIO_DURATION_TOLERANCE`, clamped between `MIN_PLAUSIBLE_AUDIO_SECONDS` and
  `MAX_AUDIO_DURATION_SECONDS`. The worker's generic failure path refunds the points.
- The success row is written to `narration_log` *before* broadcasting, not after. Logging after the
  sleep hid finished narrations from the history for the length of their audio, which made a
  failure logged during that window look like the most recent event.

### Twitch Service Initialization
Twitch services (EventSub + RewardController) are initialized in two scenarios:
1. **At app startup**: If OAuth tokens exist in DB, auto-connects to Twitch
2. **After OAuth callback**: Immediately after user authorizes, services start

The initialization flow (`src/services/twitch/init_services.py`):
1. Create `TwitchRewardController` and find/create the Channel Points reward
2. Save `reward_id` to DB for persistence across restarts
3. Create `TwitchEventSubService` with handler that queues redemptions
4. Wire up `GlobalCooldownManager` to pause reward after narrations

On failure: logs warning and continues without Twitch (dashboard shows "disconnected").

### Data Layer
- SQLite database at `data/narrator.db`
- Migrations in `migrations/` folder (auto-applied on startup). `DEFAULT_MIGRATIONS_DIR` is anchored
  to the source tree, not the working directory - the container runs the app from the data volume.
  A missing migrations directory raises `MigrationError` rather than silently skipping.
- `src/db/repositories/`: Typed repository pattern for settings, providers, logs.
  `SettingsRepository.get_or_default()` is the non-optional variant of `get()`.

### Web Components
- `src/api/`: FastAPI routes (JSON API + WebSocket)
- `src/api/websocket.py`: WebSocket connection manager for overlay broadcasts
- `src/api/ws_types.py`: TypedDict message types for WebSocket protocol
- `src/api/routes/overlay.py`: WebSocket endpoint (`/ws/overlay`) and static file serving
- `overlay/`: OBS Browser Source (HTML/CSS/JS) connecting via WebSocket

### Web UI (Phase 4)
- `src/views/`: HTMX view routes returning HTML pages/partials
  - `dashboard.py`: Main dashboard with queue status, worker control
  - `settings.py`: Settings forms (language, TTS voice, narrator, queue, overlay, reward)
  - `queue.py`: Queue management (view, skip, clear)
  - `test.py`: Manual narration testing
  - `logs.py`: Narration history with filtering/pagination
- `src/templates/`: Jinja2 templates
  - `base.html`: Base layout with parchment-themed sidebar
  - `pages/`: Full page templates (dashboard, settings, queue, test, logs)
  - `partials/`: HTMX partial templates for live updates
- `src/static/`: Static assets
  - `css/main.css`: Parchment theme (dark background, gold accents)
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
- No `# type: ignore` without explicit asking and extended comment explaining why
- Use `# ty: ignore[rule-name]` for ty-specific suppressions (doesn't trigger mypy unused-ignore)

## Fail fast and LOUD
Never do `except Exception:`. Always narrow down the exception type and handle it properly.
Better fail than swallow an error.

## Queue Event Handlers
Queue event handlers (registered via `queue.on_event()`) are called while the queue lock is held. **Never call `queue.get_items()` or other queue methods from an event handler** - this will cause a deadlock. Use `asyncio.create_task()` to defer any queue access.

## Key Design Decisions

- **Piper TTS for MVP**: CPU-friendly (3-11x realtime), MIT licensed, no GPU required. ElevenLabs optional for premium voices.
- **Dynamic Voice Selection**: Piper voice automatically selected based on `narrator_lang` setting. Each language has a default voice (e.g., `ru_RU-ruslan-medium` for Russian), with per-language overrides configurable via UI.
- **Single OBS Source**: Audio + subtitles delivered via one WebSocket connection
- **Language Independence**: `source_lang`, `narrator_lang`, `subtitle_lang` are separately configurable
- **Translation Skip**: When `source_lang == narrator_lang`, translation step is bypassed
- **SecretStr**: All API keys use Pydantic SecretStr (env vars only, never in DB)
- **Global Cooldown**: After each narration (success or failure), the Twitch reward is paused for a configurable duration (default 5 minutes) to prevent rapid redemptions. State persists across restarts.

## Configuration

- **Secrets**: Environment variables only (`.env` file, never committed)
- **Everything else**: SQLite database - modified via Web UI, seeded with defaults
  from the Pydantic models on first run

Required env vars: `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_CHANNEL`, `SECRET_KEY`, plus at
least one LLM API key (`GROQ_API_KEY` or `GEMINI_API_KEY`). `GEMINI_API_KEY` powers both the Gemini
LLM and the Gemini TTS provider.

## Deployment

Two container builds exist, deliberately:

- `Dockerfile` (repo root): plain app image used by Railway (`railway.toml`). No TLS, no systemd.
- `container/Dockerfile`: the release image for self-hosting. UBI10-init base running systemd with
  three units - `narrator-init` (renders `/etc/narrator/env` and the Caddyfile, fails fast on a
  missing variable), `narrator-app` (uvicorn on 127.0.0.1:8000 as the `narrator` user) and
  `narrator-caddy` (TLS termination, automatic Let's Encrypt).

Oracle Cloud filters inbound traffic twice, and both layers must allow 80 and 443: the VCN
security list (or NSG) in the console, and the instance's own iptables, whose stock ruleset ends
in a blanket REJECT. `./narrator install` offers to open the host side - a rule appended after
that REJECT never matches, so it is inserted above it - but the VCN side can only be done in the
console. A blocked port 80 shows up as Let's Encrypt reporting
`Timeout during connect (likely firewall problem)`.

Gotchas that are easy to reintroduce:

- systemd does not inherit the container environment. Every variable `narrator-init.sh` reads must
  be listed in `PassEnvironment=` in `narrator-init.service`.
- `COPY` preserves host file modes; the Dockerfile normalises them with `chmod -R a+rX` so the
  unprivileged `narrator` user can read the code.
- buildah produces OCI images, which drop the Dockerfile `HEALTHCHECK`. The `narrator` CLI therefore
  passes `--health-cmd` at `podman run` time.

`./narrator doctor` walks the request path end to end - container, units, app on loopback, bound
ports, host firewall, DNS, reachability on the public address, TLS - so a broken deployment is
diagnosed on the machine rather than by reading logs. Extend it when a new failure mode costs
more than one round trip to diagnose.

`.github/workflows/release.yml` builds amd64 and arm64 on native runners with buildah, pushes both to
GHCR, joins them into a manifest, and attaches the `narrator` CLI to the GitHub release (with
`VERSION="dev"` rewritten to the tag). Deployment on the VM is `./narrator install`.

## Testing

Tests in `tests/` directory mirror `src/` structure. Use `pytest-asyncio` for async tests. Coverage target: 80%.

## Web UI

The web dashboard is served at `http://localhost:8000` with the following pages:

| Route | Description |
|-------|-------------|
| `/` | Dashboard with queue status, worker control |
| `/settings` | Configuration for providers, prompts, language, TTS voice, narrator, queue, overlay, rewards |
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
