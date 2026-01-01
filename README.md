# BG3 Twitch Narrator Bot

A Twitch Channel Points integration that reads chat messages in the dramatic voice style of a D&D narrator, with multilingual subtitles overlay for OBS.

## Features

- **Channel Points Integration**: Only redeemed messages are narrated (spam filtering via cost)
- **D&D Narrator Style**: LLM transforms casual chat into dramatic narrator prose
- **Fast Local TTS**: Piper TTS with high-quality pre-trained voices (MIT license)
- **Configurable Languages**: Source, narrator, and subtitle languages are independent
- **Provider Abstraction**: Swappable LLM, TTS, and Translation providers
- **Type Safety**: Pydantic v2 strict mode, mypy, typed protocols everywhere

## Quick Start

```bash
# Install dependencies
uv sync --extra dev

# Set up environment variables
cp .env.example .env
# Edit .env with your GROQ_API_KEY

# Run the CLI
uv run python -m src.main "Hello everyone!" --user DragonSlayer
```

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- Groq API key (or other LLM provider)

## Development

```bash
# Run tests
uv run pytest

# Type checking
uv run mypy src

# Linting
uv run ruff check .

# Format code
uv run ruff format .
```

## License

MIT
