# Phase 5 Progress: Gemini Providers & Editable Prompts

## Overview

Phase 5 adds Google Gemini as both an LLM and a TTS provider, makes the provider
choice a runtime setting instead of a hardcoded constant, and moves every prompt
out of the source and into editable settings.

**Deliverable:** Gemini LLM + Gemini TTS selectable from the Web UI, with all
narration and moderation prompts stored in the database and edited there too.

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

## Verification

- 230 tests pass (83 new: Gemini LLM, Gemini TTS, factory, worker pipeline swap,
  database migrations, prompt templates, settings repository).
- `ruff check .`, `mypy .` and `ty check .` all clean.
- The Providers and Prompts tabs were exercised against a live app: provider swap
  with a pipeline rebuild, a missing API key reported as a toast rather than a
  500, prompt validation failures leaving the stored value untouched, and the
  defaults seeded once on first boot and preserved across restarts.

## Not Covered

The Gemini providers have not been exercised against the live Google API - there
was no `GEMINI_API_KEY` available in this environment. Request assembly, response
parsing, error paths and audio packaging are unit-tested against SDK-shaped
objects, but the first real call should be smoke-tested with
`python -m src.main test "..." --llm-provider gemini --tts-provider gemini`.
