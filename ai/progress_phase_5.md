# Phase 5 Progress: Gemini Providers & Self-Hosted Release

## Overview

Phase 5 adds Google Gemini as both an LLM and a TTS provider, makes the provider
choice a runtime setting instead of a hardcoded constant, and turns the project
into something deployable on a single VM (built and verified for OCI Ampere /
arm64) with one command.

**Deliverable:** Gemini LLM + Gemini TTS selectable from the Web UI, and a
release image (systemd + Caddy, automatic TLS) published to GHCR for amd64 and
arm64 with a `narrator` installer CLI.

## Completed Tasks

### 1. Gemini LLM Provider

- [x] `src/providers/llm/gemini.py` - `GeminiLLMProvider` on the `google-genai` SDK:
  - `generate()` / `generate_raw()` / `moderate()` / `generate_stream()`
  - Explicit `types.Schema` response schemas for narration and moderation.
    Hand-written because `StrictModel` emits `additionalProperties`, which the
    Gemini API rejects.
  - `thinking_budget` (default `0` for latency) validated against the model at
    construction: 2.5 Pro cannot disable thinking, 2.0 Flash takes no budget.
  - `safety_threshold` (default `BLOCK_ONLY_HIGH`) validated against real enum
    members - the SDK's enums accept unknown values with only a `UserWarning`.
  - Models: 2.5 Flash, 2.5 Flash-Lite, 2.5 Pro, 2.0 Flash.
- [x] `LLMContentBlockedError` in `src/providers/llm/base.py` - a provider-side
  safety refusal, distinct from a parse failure.

### 2. Gemini TTS Provider

- [x] `src/providers/tts/gemini.py` - `GeminiTTSProvider`:
  - `gemini-2.5-flash-preview-tts` / `gemini-2.5-pro-preview-tts`
  - 30 prebuilt multilingual voices, validated before a request is made.
  - Raw PCM response wrapped in WAV; sample rate parsed from the part mime type
    (`audio/L16;codec=pcm;rate=24000`), never assumed.
  - No speed/pitch knobs: `update_settings()` raises `NotImplementedError` and a
    non-default `TTSSettings` is rejected. Delivery is directed by `style_prompt`.

### 3. Provider Selection

- [x] `src/models/settings.py` - `ProviderSettings`, `GroqLLMSettings`,
  `GeminiLLMSettings`, `GeminiSafetyThreshold`, `LLMProviderName`,
  `TTSProviderName`. Removed the unused `GroqSettings` / `OpenAISettings` /
  `ElevenLabsSettings` models, which stored `api_key` in a DB-backed model.
- [x] `src/providers/factory.py` - the single place selection (database) meets
  credentials (environment). `ProviderConfigurationError` names the missing
  variable; `default_provider_settings(env)` keeps a fresh Gemini-only install
  from starting with a dead worker.
- [x] `AppState.rebuild_pipeline()` - used for both startup wiring and runtime
  swaps; creates the worker if absent.
- [x] `QueueWorker.set_pipeline()` - swaps between items under a processing lock,
  so the previous providers can be closed safely.
- [x] `start()` / `close()` added to the TTS protocol, `close()` to the LLM
  protocol, so the lifecycle is provider-agnostic.

### 4. Web UI

- [x] Settings -> **Providers** tab: active LLM/TTS selection plus per-provider
  forms (Groq, Gemini LLM, Gemini TTS). Saving rebuilds the pipeline live; a
  misconfiguration comes back as a toast rather than a 500.
- [x] Piper's speed/variation form and voice test are now provider-aware and say
  so when Piper is not active.
- [x] `PiperTTSProvider.current_settings()` replaces the settings view reaching
  into `_settings` privates.

### 5. CLI

- [x] `python -m src.main test` now builds providers through the factory using the
  stored language and narrator settings, with `--llm-provider` / `--tts-provider`
  overrides that apply to that run only.

### 6. Editable Prompts

- [x] `PromptSettings` in `src/providers/llm/prompts.py` - ten editable sections
  (format contract, two language templates, four styles, formatting, two moderation
  prompts) stored under the `prompts` settings key.
- [x] `string.Template` (`$placeholder`) instead of `str.format`, because prompts
  contain literal JSON that brace formatting would choke on. Placeholders are
  whitelisted per field and validated on save *and* on load: unknown names,
  missing required ones and bad `$` syntax all raise with the section named.
- [x] `SettingsRepository.set_if_absent()` seeds the defaults on first boot and
  never overwrites an operator's edits afterwards.
- [x] Moderation prompts left the providers: `LLMProvider.moderate()` now takes
  `system_prompt` and `user_prompt`, so prompts are configuration rather than
  provider internals. Gemini keeps its structured moderation schema.
- [x] Settings -> **Prompts** tab with per-section help, validation toasts and
  "Reset to Defaults" (re-renders the form with the restored text).
- [x] The worker loads prompts per queue item, so edits apply from the next
  narration without a restart or pipeline rebuild.

### 7. Release & Deployment

- [x] `container/` - UBI10-init image running three systemd units:
  `narrator-init` (renders `/etc/narrator/env` and the Caddyfile, fails fast on a
  missing variable), `narrator-app` (uvicorn on loopback as the `narrator` user),
  `narrator-caddy` (TLS termination with automatic Let's Encrypt).
- [x] `.github/workflows/release.yml` - buildah builds on native amd64 and arm64
  runners, pushed to GHCR and joined into a manifest; the `narrator` CLI is
  attached to the GitHub release with its version stamped in.
- [x] `narrator` - installer/operator CLI (podman first, docker fallback):
  `install`, `start`, `stop`, `restart`, `logs [app|caddy|init]`, `status`,
  `upgrade`.

### 7. Making the Provider Protocol Load-Bearing

Half the provider protocol had no caller. Rather than keep it as decoration, the
usable parts were wired up and the rest removed.

- [x] `src/views/schema_form.py` parses a provider's JSON Schema into typed
  `SettingsField` objects; `partials/settings_field.html` renders them. The three
  hand-written provider forms are gone, and `get_settings_schema()` is now what
  actually draws the UI.
- [x] `list_models()` and `list_voices()` fill the dropdowns, including the
  per-language Piper voice pickers, so a catalogue has one source of truth.
- [x] Piper's synthesis settings moved to the Providers tab next to every other
  provider's; the TTS Voice tab is now only language-to-voice mapping. `noise_w`
  is editable instead of being a hidden input.
- [x] The voice preview builds a throwaway provider from the submitted form for
  *both* providers. It used to mutate the live Piper instance and restore it
  afterwards, and ignored the form entirely for Gemini.
- [x] Removed with no honest use: `generate_stream()`, `synthesize_stream()`,
  `clone_voice()`, `supports_streaming`, `supports_cloning` and
  `Model.supports_streaming`.
- [x] Provider settings routes report validation failures as toasts; an
  out-of-range value used to raise a 500.

### 9. Live Model Catalogues

- [x] `list_models()` now queries the provider's API, so the dropdown lists the
  models the configured key can use instead of a hardcoded set. Gemini filters on
  `supported_actions`; Groq's SDK reports only an ID, so speech models are
  excluded by substring.
- [x] `src/providers/catalogue.py` memoises the listing for five minutes with an
  absolute TTL, because the settings view builds a throwaway provider per render.
- [x] A failed or empty listing logs a warning and falls back to the built-in
  catalogue, which is what keeps `AVAILABLE_MODELS` meaningful.

### 10. First Contact With the Live API

Running a real narration surfaced three things the offline work could not.

- [x] `thinking_budget=0`, the shipped default, is rejected by the current model
  with a bare `400 INVALID_ARGUMENT` naming no field. Bisecting the request
  showed the budget was the only offending argument. Reasoning effort is now set
  with `thinking_level` (MINIMAL by default), verified against the live API; the
  numeric budget remains for models that want it, and the two are mutually
  exclusive.
- [x] The per-model thinking table named `gemini-2.5-pro` and `gemini-2.0-flash`,
  which this key now gets a 404 for. It is gone - the API is the authority, and a
  400 now logs the thinking and safety settings that were sent so the cause is
  visible.
- [x] Filtering the listing on `supported_actions` alone was too permissive: the
  same call returns speech, image, music, robotics and agent models, so the
  dropdown offered "Nano Banana Pro" as a narrator. Those are excluded by marker.

## Bugs Found and Fixed Along the Way

- `NarratorSettings.system_prompt` was never reaching the LLM: the pipeline's
  `custom_prompt` was only a constructor argument nobody passed. It is now a
  per-request parameter supplied by the worker, so the setting takes effect
  immediately.
- `DatabaseManager._run_migrations()` returned silently when the migrations
  directory was missing, which in the container (working directory = data volume)
  produced `no such table: settings` at startup. The default is now anchored to
  the source tree and a missing directory raises `MigrationError`.
- Narration history logged `llm_provider="groq"` / `tts_provider="piper"` as
  hardcoded strings; they now come from the pipeline.
- A blank `GROQ_API_KEY=` in an env file produced `SecretStr("")`, which the
  factory's `is None` check accepted - it would have failed later as a 401. Blank
  keys now count as unset everywhere.
- `EnvSettings.get_available_llm_providers()` advertised openai/anthropic/
  openrouter/ollama, none of which are implemented; selecting one would have
  broken the factory. It now reports only what can actually be built.
- A per-language Piper voice override was passed to whichever TTS provider was
  active. After switching to Gemini TTS, every narration in that language failed
  `_validate_voice` and refunded the viewer's points. The worker now applies the
  overrides only while Piper is synthesizing. (Found by review.)
- `build_providers()` left the LLM provider's HTTP pool open when the TTS
  provider failed to build, leaking one pool per retry from the settings form.
  (Found by review.)

### 8. Second Review Pass

- [x] Provider settings are proved buildable before they are stored. A model and
  thinking budget the provider refuses used to be written to the database first
  and only rejected by the rebuild, which bricked the settings page on the next
  render and left the worker unstarted after a restart.
- [x] The settings page survives a stored combination the provider rejects: the
  section explains the problem and still renders the form from a default-built
  instance, so the offending value can be corrected in the UI instead of in
  SQLite.
- [x] The worker's processing lock now covers generation only. It used to be
  held across the broadcast, which waits out the audio playback, so saving a
  provider change during a narration blocked the request for its whole length.
  The provider names in the history are pinned to the pipeline that ran.
- [x] `rebuild_pipeline()` and the CLI close freshly built providers when a
  later startup step fails.

## Verification

- 271 tests pass (124 new: Gemini LLM, Gemini TTS, factory, worker pipeline swap,
  database migrations, prompt templates, settings repository, schema-driven form
  rendering, and regression tests for the voice-override scoping bug, the
  settings page surviving a rejected stored row, and the provider swap no longer
  waiting for audio playback - the last one verified to fail against the old
  code).
- `ruff check .`, `mypy .` and `ty check .` all clean.
- Container built for `linux/arm64` with podman and booted end to end: systemd
  units active, migrations applied, worker started on the env-derived provider,
  dashboard behind Basic Auth, overlay served, health reachable through Caddy TLS.
- The `podman run` flag set used by `narrator start` was exercised directly,
  including named volumes and `--health-cmd`.

## Not Covered

The Gemini providers have not been exercised against the live Google API - there
was no `GEMINI_API_KEY` available in this environment. Request assembly, response
parsing, error paths and audio packaging are unit-tested against SDK-shaped
objects, but the first real call should be smoke-tested with
`python -m src.main test "..." --llm-provider gemini --tts-provider gemini`.
