# Phase 1 Progress: Core Pipeline

## Overview

Phase 1 establishes the foundational architecture for the Twitch Narrator Bot. The goal is to create a working CLI tool that converts text to narrated audio using the LLM → TTS pipeline.

## Completed Tasks

### 1. Project Setup (pyproject.toml, directory structure)

- [x] Created `pyproject.toml` with all dependencies
- [x] Configured for `uv` package manager
- [x] Set up build system with hatchling
- [x] Configured mypy strict mode
- [x] Configured ruff linting and formatting
- [x] Configured pytest with asyncio support

### 2. Directory Structure

```
narrator-bot/
├── pyproject.toml          # Project configuration
├── .env.example            # Environment variable template
├── config/
│   ├── default.yaml        # Default configuration
│   └── prompts/
│       ├── narrator.txt    # Main narrator prompt
│       └── templates/      # Style templates
├── src/
│   ├── core/
│   │   └── types.py        # StrictModel, MutableStrictModel
│   ├── models/
│   │   ├── narration.py    # NarrationRequest, NarrationResult
│   │   └── settings.py     # All settings models
│   ├── config.py           # EnvSettings with pydantic-settings
│   ├── providers/
│   │   ├── llm/
│   │   │   ├── base.py     # LLMProvider protocol
│   │   │   └── groq.py     # Groq implementation
│   │   └── tts/
│   │       ├── base.py     # TTSProvider protocol
│   │       └── piper.py    # Piper implementation
│   ├── services/
│   │   └── pipeline.py     # NarrationPipeline orchestrator
│   └── main.py             # CLI entry point
└── tests/
    ├── conftest.py         # Shared fixtures
    ├── test_providers/
    │   ├── test_llm.py     # LLM provider tests
    │   └── test_tts.py     # TTS provider tests
    └── test_services/
        └── test_pipeline.py # Pipeline tests
```

### 3. Configuration Loading with Pydantic

- [x] `EnvSettings` class for environment variables
- [x] SecretStr for API keys (never exposed in logs)
- [x] Helper methods for available providers
- [x] Settings models for all components:
  - `LanguageSettings`
  - `NarratorSettings`
  - `QueueSettings`
  - `OverlaySettings`
  - `TwitchRewardSettings`
  - `AppSettings`
  - Provider-specific settings (Groq, Piper, etc.)

### 4. Groq LLM Provider Implementation

- [x] `LLMProvider` protocol with `@runtime_checkable`
- [x] `GroqLLMProvider` class implementing the protocol
- [x] Support for multiple models (Llama 3.3, Mixtral, etc.)
- [x] Async `generate()` method
- [x] Async `generate_stream()` for streaming
- [x] `health_check()` method
- [x] `get_settings_schema()` for dynamic UI

### 5. Piper TTS Provider Implementation

- [x] `TTSProvider` protocol with `@runtime_checkable`
- [x] `PiperTTSProvider` class implementing the protocol
- [x] Lazy model loading with asyncio lock
- [x] WAV output format
- [x] Configurable voice, speed, and variation
- [x] List of 9 recommended voices (en, ru, de, fr, es)
- [x] `health_check()` method
- [x] `get_settings_schema()` for dynamic UI

### 6. Basic Pipeline Service

- [x] `NarrationPipeline` class orchestrating LLM → TTS
- [x] `process()` method that:
  1. Formats message using LLM
  2. Synthesizes speech using TTS
  3. Returns `NarrationResult` with audio data
- [x] `PipelineMetrics` dataclass for latency tracking
- [x] Audio duration estimation
- [x] Custom system prompt support
- [x] `health_check()` for all components

### 7. CLI Entry Point

- [x] `python -m src.main` entry point
- [x] argparse with --user, --style, --output options
- [x] Structured logging with structlog
- [x] Health check before processing
- [x] Audio file output

### 8. Unit Tests

- [x] LLM provider tests (protocol, initialization, mocked API calls)
- [x] TTS provider tests (protocol, settings, mocked synthesis)
- [x] Pipeline tests (processing, metrics, health checks)
- [x] pytest-asyncio for async tests

## Usage

```bash
# Install dependencies with uv
uv sync

# Install with dev tools
uv sync --extra dev

# Run the CLI
uv run python -m src.main "Hello everyone!" --user DragonSlayer

# With style
uv run python -m src.main "A secret message..." --style whisper --output secret.wav

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=src --cov-report=term-missing

# Type checking
uv run mypy src

# Linting
uv run ruff check .

# Format code
uv run ruff format .
```

## Requirements

- Python 3.12+
- `GROQ_API_KEY` environment variable (for LLM)
- Piper TTS will auto-download voice models on first use

## Next Steps (Phase 2)

1. TwitchIO 3.x EventSub setup
2. Twitch OAuth flow
3. Redemption validation and parsing
4. Message queue with asyncio
5. User cooldown tracking
6. Integration tests

## Known Limitations

- Translation is not yet implemented (Phase 2+)
- No database persistence yet (Phase 2+)
- No web UI yet (Phase 4)
- Single LLM provider (Groq) - more in future phases
