# BG3 Twitch Narrator Bot

A Twitch Channel Points integration that reads chat messages in the dramatic voice style of a D&D narrator, with multilingual subtitles overlay for OBS.

## Overview

### Core Flow

```
Twitch Channel Points Redemption (message in source_lang)
    ↓
LLM (Groq) - D&D style formatting
    ↓
[If source_lang ≠ narrator_lang] → Translation
    ↓
TTS (Piper) - Generate audio in narrator_lang
    ↓
WebSocket → OBS Browser Source
    ↓
Audio playback + subtitles in subtitle_lang
```

### Key Features

- **Channel Points Integration**: Only redeemed messages are narrated (spam filtering via cost)
- **D&D Narrator Style**: LLM transforms casual chat into dramatic narrator prose
- **Fast Local TTS**: Piper TTS with high-quality pre-trained voices (MIT license)
- **Configurable Languages**: Source, narrator, and subtitle languages are independent
- **Smart Translation**: Skips translation when source and narrator languages match
- **Single OBS Source**: Audio and subtitles delivered via WebSocket to one Browser Source
- **Provider Abstraction**: Swappable LLM, TTS, and Translation providers
- **Type Safety**: Pydantic v2 strict mode, mypy, typed protocols everywhere
- **SQLite Settings**: Persistent configuration with typed repositories
- **Web UI**: HTMX-based settings panel for real-time configuration
- **CPU-Friendly**: Runs on Railway Hobby plan (~$5/month)

---

## Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            Railway Deployment                            │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │   Twitch     │  │     LLM      │  │  Translator  │  │     TTS      │ │
│  │   Service    │  │   Service    │  │   Service    │  │   Service    │ │
│  │              │  │              │  │              │  │              │ │
│  │ - EventSub   │  │ - Groq       │  │ - LLM-based  │  │ - Piper      │ │
│  │ - Auth       │  │ - OpenAI     │  │ - DeepL      │  │ - ElevenLabs │ │
│  │ - Redemption │  │ - Anthropic  │  │ - Google     │  │ - Silero     │ │
│  │   validation │  │ - Ollama     │  │ - Argos      │  │              │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘ │
│         │                 │                 │                 │          │
│         └─────────────────┴─────────────────┴─────────────────┘          │
│                                    │                                     │
│                           ┌────────▼────────┐                            │
│                           │  Message Queue  │                            │
│                           │  (asyncio)      │                            │
│                           └────────┬────────┘                            │
│                                    │                                     │
│         ┌──────────────────────────┴──────────────────────────┐          │
│         │                                                      │          │
│  ┌──────▼───────┐                                      ┌───────▼───────┐ │
│  │   Web UI     │                                      │   WebSocket   │ │
│  │              │                                      │    Server     │ │
│  │ - Settings   │                                      │               │ │
│  │ - Queue view │                                      │ - Audio       │ │
│  │ - Test voice │                                      │ - Subtitles   │ │
│  │ - Logs       │                                      │ - Events      │ │
│  └──────────────┘                                      └───────┬───────┘ │
│                                                                │          │
└────────────────────────────────────────────────────────────────┼──────────┘
                                                                 │
                                                        ┌────────▼────────┐
                                                        │  OBS Browser    │
                                                        │  Source         │
                                                        │                 │
                                                        │ - Audio player  │
                                                        │ - Subtitle UI   │
                                                        │ - BG3 styling   │
                                                        └─────────────────┘
```

### Directory Structure

```
narrator-bot/
├── README.md
├── PROJECT_OUTLINE.md
├── pyproject.toml
├── railway.toml
├── Dockerfile
│
├── data/
│   └── narrator.db           # SQLite database (gitignored)
│
├── models/
│   └── piper/                # Piper voice models (auto-downloaded)
│       ├── en_US-lessac-medium.onnx
│       └── en_US-lessac-medium.onnx.json
│
├── migrations/
│   ├── 001_initial.sql
│   └── 002_indexes.sql
│
├── config/
│   ├── default.yaml          # Default configuration
│   ├── config.schema.json    # JSON Schema for validation
│   └── prompts/
│       ├── narrator.txt      # D&D narrator system prompt
│       └── templates/        # Message format templates
│           ├── whisper.txt
│           ├── proclaim.txt
│           └── mock.txt
│
├── src/
│   ├── __init__.py
│   ├── main.py               # Application entry point
│   ├── config.py             # Configuration loading & validation
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   └── types.py          # Base types, StrictModel
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── narration.py      # NarrationRequest, NarrationResult
│   │   └── settings.py       # All settings models
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── manager.py        # DatabaseManager, migrations
│   │   └── repositories/
│   │       ├── __init__.py
│   │       ├── settings.py   # SettingsRepository
│   │       ├── providers.py  # ProviderSettingsRepository
│   │       └── logs.py       # NarrationLogRepository
│   │
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py           # Protocol definitions
│   │   │
│   │   ├── llm/
│   │   │   ├── __init__.py
│   │   │   ├── base.py       # LLMProvider protocol
│   │   │   ├── groq.py       # Groq implementation (MVP)
│   │   │   ├── openai.py
│   │   │   ├── anthropic.py
│   │   │   ├── openrouter.py
│   │   │   └── ollama.py
│   │   │
│   │   ├── tts/
│   │   │   ├── __init__.py
│   │   │   ├── base.py       # TTSProvider protocol
│   │   │   ├── piper.py      # Piper TTS (MVP - MIT license)
│   │   │   ├── elevenlabs.py # ElevenLabs API (premium)
│   │   │   └── silero.py     # Silero TTS (alternative)
│   │   │
│   │   └── translate/
│   │       ├── __init__.py
│   │       ├── base.py       # TranslateProvider protocol
│   │       ├── llm.py        # Use LLM for translation (MVP)
│   │       ├── deepl.py
│   │       ├── google.py
│   │       └── argos.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── twitch/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py           # OAuth flow
│   │   │   ├── eventsub.py       # EventSub WebSocket (TwitchIO 3.x)
│   │   │   ├── rewards.py        # Channel Points reward management
│   │   │   └── models.py         # Twitch API models
│   │   ├── queue.py              # Message queue management
│   │   ├── rate_limiter.py       # TTS rate limiting
│   │   ├── pipeline.py           # Orchestrates LLM → TTS flow
│   │   ├── prompt_builder.py     # Dynamic prompt generation
│   │   └── audio.py              # Audio processing utilities
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── app.py            # FastAPI application factory
│   │   ├── dependencies.py   # Dependency injection
│   │   │
│   │   ├── routes/           # JSON API endpoints
│   │   │   ├── __init__.py
│   │   │   ├── twitch.py     # Twitch OAuth callbacks
│   │   │   ├── test.py       # Test narration endpoint (JSON)
│   │   │   └── health.py     # Health check
│   │   │
│   │   └── websocket.py      # WebSocket handler for overlay
│   │
│   ├── views/                 # HTMX endpoints (return HTML)
│   │   ├── __init__.py
│   │   ├── router.py         # View router
│   │   ├── dashboard.py      # GET / — main dashboard
│   │   ├── settings.py       # GET/POST /settings/*
│   │   ├── queue.py          # GET/POST /queue/*
│   │   ├── logs.py           # GET /logs/*
│   │   └── test.py           # GET/POST /test/*
│   │
│   ├── templates/
│   │   ├── base.html         # Base layout with HTMX
│   │   ├── dashboard.html
│   │   ├── settings.html
│   │   ├── queue.html
│   │   ├── logs.html
│   │   ├── test.html
│   │   └── partials/         # HTMX partial responses
│   │       ├── queue_list.html
│   │       ├── queue_item.html
│   │       ├── queue_status.html
│   │       ├── settings_form.html
│   │       ├── provider_fields.html
│   │       ├── language_fields.html
│   │       ├── reward_fields.html
│   │       ├── queue_settings_fields.html
│   │       ├── log_table.html
│   │       ├── log_row.html
│   │       ├── toast.html
│   │       └── stats.html
│   │
│   └── static/
│       ├── css/
│       │   └── style.css
│       └── js/
│           └── app.js        # Minimal JS (HTMX extensions)
│
├── overlay/
│   ├── index.html            # OBS Browser Source
│   ├── style.css             # BG3-themed styling
│   └── overlay.js            # WebSocket client & audio player
│
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_providers/
    │   ├── test_llm.py
    │   ├── test_tts.py
    │   └── test_translate.py
    ├── test_services/
    │   ├── test_twitch.py
    │   ├── test_pipeline.py
    │   └── test_queue.py
    └── test_api/
        └── test_routes.py
```

---

## Provider Interfaces

### LLM Provider

```python
# src/providers/llm/base.py

from typing import Protocol, AsyncIterator, runtime_checkable
from dataclasses import dataclass

@dataclass
class Model:
    id: str
    name: str
    context_length: int
    supports_streaming: bool

@dataclass 
class LLMResponse:
    text_original: str    # Formatted text in source language (for subtitles)
    text_translated: str  # Translated text for TTS (or same if no translation)
    raw_response: str     # Full LLM response for debugging

@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers"""
    
    @property
    def name(self) -> str:
        """Provider display name"""
        ...
    
    async def generate(
        self, 
        user: str, 
        message: str,
        style: str = "default"
    ) -> LLMResponse:
        """
        Generate narrator text from user message.
        
        Args:
            user: Twitch username
            message: Original message
            style: Narrator style template name
            
        Returns:
            LLMResponse with original and translated text
        """
        ...
    
    async def generate_stream(
        self, 
        user: str, 
        message: str,
        style: str = "default"
    ) -> AsyncIterator[str]:
        """Streaming generation (optional)"""
        ...
    
    async def list_models(self) -> list[Model]:
        """List available models"""
        ...
    
    async def health_check(self) -> bool:
        """Check if provider is available"""
        ...
    
    def get_settings_schema(self) -> dict:
        """
        Return JSON Schema for provider-specific settings.
        Used by Web UI to render dynamic settings form.
        """
        ...
```

### TTS Provider

```python
# src/providers/tts/base.py

from typing import Protocol, AsyncIterator, runtime_checkable
from dataclasses import dataclass

@dataclass
class Voice:
    id: str
    name: str
    language: str
    preview_url: str | None = None

@dataclass
class TTSSettings:
    speed: float = 1.0        # 0.5 - 2.0
    pitch: float = 1.0        # 0.5 - 2.0 (provider-specific)
    stability: float = 0.5    # Provider-specific
    similarity: float = 0.75  # Provider-specific

@runtime_checkable
class TTSProvider(Protocol):
    """Protocol for TTS providers"""
    
    @property
    def name(self) -> str:
        """Provider display name"""
        ...
    
    @property
    def supports_streaming(self) -> bool:
        """Whether provider supports audio streaming"""
        ...
    
    @property
    def supports_cloning(self) -> bool:
        """Whether provider supports voice cloning"""
        ...
    
    async def synthesize(
        self, 
        text: str,
        voice_id: str,
        settings: TTSSettings | None = None
    ) -> bytes:
        """
        Synthesize text to audio.
        
        Args:
            text: Text to synthesize
            voice_id: Voice identifier
            settings: TTS settings
            
        Returns:
            Audio bytes (WAV or MP3)
        """
        ...
    
    async def synthesize_stream(
        self, 
        text: str,
        voice_id: str,
        settings: TTSSettings | None = None
    ) -> AsyncIterator[bytes]:
        """
        Streaming audio synthesis.
        
        Yields:
            Audio chunks as they're generated
        """
        ...
    
    async def list_voices(self) -> list[Voice]:
        """List available voices"""
        ...
    
    async def clone_voice(
        self, 
        name: str, 
        audio_files: list[bytes]
    ) -> Voice:
        """Clone a voice from audio samples (if supported)"""
        ...
    
    async def health_check(self) -> bool:
        """Check if provider is available"""
        ...
    
    def get_settings_schema(self) -> dict:
        """JSON Schema for provider settings"""
        ...
```

### Translation Provider

```python
# src/providers/translate/base.py

from typing import Protocol, runtime_checkable

@runtime_checkable
class TranslateProvider(Protocol):
    """Protocol for translation providers"""
    
    @property
    def name(self) -> str:
        ...
    
    async def translate(
        self, 
        text: str, 
        source_lang: str,
        target_lang: str
    ) -> str:
        """
        Translate text between languages.
        
        Args:
            text: Text to translate
            source_lang: Source language code (e.g., "ru")
            target_lang: Target language code (e.g., "en")
            
        Returns:
            Translated text
        """
        ...
    
    async def health_check(self) -> bool:
        ...
    
    def get_settings_schema(self) -> dict:
        ...
```

---

## TTS Implementation: Piper

### Why Piper?

| Feature | Piper | XTTS v2 | ElevenLabs |
|---------|-------|---------|------------|
| **License** | MIT ✅ | Non-commercial ❌ | Commercial API |
| **GPU Required** | No | Yes (8GB+) | Cloud |
| **Speed (CPU)** | 3-11x realtime | 0.1-0.3x realtime | N/A |
| **Model Size** | ~60-100 MB | ~2 GB | N/A |
| **Voice Cloning** | No | Yes | Yes |
| **Quality** | Good | Excellent | Excellent |
| **Railway Deploy** | ✅ Works | ❌ Too slow | ✅ Works |

### Piper TTS Provider

```python
# src/providers/tts/piper.py

from dataclasses import dataclass
from typing import AsyncIterator
import asyncio
from pathlib import Path
import io
import wave

from piper import PiperVoice

from src.core.types import StrictModel
from src.providers.tts.base import TTSProvider, Voice, TTSSettings


class PiperSettings(StrictModel):
    """Piper TTS configuration"""
    voice: str = "en_US-lessac-medium"  # Voice model name
    model_path: Path | None = None       # Custom model path (optional)
    speaker_id: int | None = None        # For multi-speaker models
    length_scale: float = 1.0            # Speed: <1 faster, >1 slower
    noise_scale: float = 0.667           # Variation in pronunciation
    noise_w: float = 0.8                 # Variation in phoneme duration


class PiperTTSProvider:
    """
    Piper TTS — Fast, local, MIT licensed neural TTS.
    
    Features:
    - CPU-friendly (3-11x realtime on modern CPU)
    - Small models (~60-100MB per voice)
    - 30+ languages, 100+ voices
    - No voice cloning (uses pre-trained voices)
    
    Voices: https://rhasspy.github.io/piper-samples/
    """
    
    def __init__(self, settings: PiperSettings) -> None:
        self._settings = settings
        self._voice: PiperVoice | None = None
        self._lock = asyncio.Lock()
    
    @property
    def name(self) -> str:
        return "piper"
    
    @property
    def supports_streaming(self) -> bool:
        return False  # Piper generates full audio at once
    
    @property
    def supports_cloning(self) -> bool:
        return False
    
    async def _ensure_loaded(self) -> PiperVoice:
        """Lazy load voice model"""
        if self._voice is None:
            async with self._lock:
                if self._voice is None:
                    loop = asyncio.get_event_loop()
                    self._voice = await loop.run_in_executor(
                        None, self._load_voice
                    )
        return self._voice
    
    def _load_voice(self) -> PiperVoice:
        """Load Piper voice model (sync)"""
        if self._settings.model_path:
            return PiperVoice.load(str(self._settings.model_path))
        else:
            # Auto-download from Hugging Face
            return PiperVoice.load(self._settings.voice)
    
    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
    ) -> bytes:
        """Synthesize text to WAV audio."""
        voice = await self._ensure_loaded()
        
        # Map generic settings to Piper-specific
        length_scale = self._settings.length_scale
        if settings and settings.speed != 1.0:
            length_scale = 1.0 / settings.speed
        
        loop = asyncio.get_event_loop()
        audio_bytes = await loop.run_in_executor(
            None,
            lambda: self._synthesize_sync(voice, text, length_scale)
        )
        
        return audio_bytes
    
    def _synthesize_sync(
        self, 
        voice: PiperVoice, 
        text: str, 
        length_scale: float
    ) -> bytes:
        """Synchronous synthesis"""
        audio_stream = voice.synthesize_stream_raw(
            text,
            speaker_id=self._settings.speaker_id,
            length_scale=length_scale,
            noise_scale=self._settings.noise_scale,
            noise_w=self._settings.noise_w,
        )
        
        audio_chunks: list[bytes] = []
        for chunk in audio_stream:
            audio_chunks.append(chunk)
        
        raw_audio = b"".join(audio_chunks)
        
        # Wrap in WAV format
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16-bit
            wav.setframerate(voice.config.sample_rate)
            wav.writeframes(raw_audio)
        
        return buffer.getvalue()
    
    async def synthesize_stream(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
    ) -> AsyncIterator[bytes]:
        """Streaming not supported — yields full audio"""
        audio = await self.synthesize(text, voice_id, settings)
        yield audio
    
    async def list_voices(self) -> list[Voice]:
        """List recommended Piper voices"""
        return [
            Voice(
                id="en_US-lessac-medium",
                name="Lessac (US English) — Narrator style",
                language="en",
                preview_url="https://rhasspy.github.io/piper-samples/samples/en/en_US/lessac/medium/sample.mp3",
            ),
            Voice(
                id="en_GB-alba-medium", 
                name="Alba (British English)",
                language="en",
                preview_url="https://rhasspy.github.io/piper-samples/samples/en/en_GB/alba/medium/sample.mp3",
            ),
            Voice(
                id="en_US-amy-medium",
                name="Amy (US English)",
                language="en",
                preview_url="https://rhasspy.github.io/piper-samples/samples/en/en_US/amy/medium/sample.mp3",
            ),
            Voice(
                id="en_US-ryan-medium",
                name="Ryan (US English) — Male",
                language="en",
                preview_url="https://rhasspy.github.io/piper-samples/samples/en/en_US/ryan/medium/sample.mp3",
            ),
            Voice(
                id="ru_RU-ruslan-medium",
                name="Ruslan (Russian)",
                language="ru",
                preview_url="https://rhasspy.github.io/piper-samples/samples/ru/ru_RU/ruslan/medium/sample.mp3",
            ),
            Voice(
                id="ru_RU-irina-medium",
                name="Irina (Russian)",
                language="ru",
                preview_url="https://rhasspy.github.io/piper-samples/samples/ru/ru_RU/irina/medium/sample.mp3",
            ),
            Voice(
                id="de_DE-thorsten-medium",
                name="Thorsten (German)",
                language="de",
                preview_url="https://rhasspy.github.io/piper-samples/samples/de/de_DE/thorsten/medium/sample.mp3",
            ),
            Voice(
                id="fr_FR-siwis-medium",
                name="Siwis (French)",
                language="fr",
                preview_url="https://rhasspy.github.io/piper-samples/samples/fr/fr_FR/siwis/medium/sample.mp3",
            ),
            Voice(
                id="es_ES-davefx-medium",
                name="Davefx (Spanish)",
                language="es",
                preview_url="https://rhasspy.github.io/piper-samples/samples/es/es_ES/davefx/medium/sample.mp3",
            ),
        ]
    
    async def clone_voice(
        self,
        name: str,
        audio_files: list[bytes],
    ) -> Voice:
        """Voice cloning not supported by Piper"""
        raise NotImplementedError(
            "Piper does not support voice cloning. "
            "Use ElevenLabs provider for voice cloning."
        )
    
    async def health_check(self) -> bool:
        """Check if Piper is available"""
        try:
            await self._ensure_loaded()
            return True
        except Exception:
            return False
    
    def get_settings_schema(self) -> dict:
        """JSON Schema for UI"""
        return {
            "type": "object",
            "properties": {
                "voice": {
                    "type": "string",
                    "title": "Voice Model",
                    "description": "Piper voice model name",
                    "default": "en_US-lessac-medium",
                    "enum": [
                        "en_US-lessac-medium",
                        "en_US-amy-medium",
                        "en_US-ryan-medium",
                        "en_GB-alba-medium",
                        "ru_RU-ruslan-medium",
                        "ru_RU-irina-medium",
                        "de_DE-thorsten-medium",
                        "fr_FR-siwis-medium",
                        "es_ES-davefx-medium",
                    ],
                },
                "length_scale": {
                    "type": "number",
                    "title": "Speed",
                    "description": "Speech speed (0.5=fast, 1.0=normal, 1.5=slow)",
                    "default": 1.0,
                    "minimum": 0.5,
                    "maximum": 2.0,
                },
                "noise_scale": {
                    "type": "number",
                    "title": "Variation",
                    "description": "Pronunciation variation (0=monotone, 1=varied)",
                    "default": 0.667,
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
        }
```

### ElevenLabs Provider (Premium Option)

```python
# src/providers/tts/elevenlabs.py

from typing import AsyncIterator
import httpx

from src.core.types import StrictModel
from src.providers.tts.base import TTSProvider, Voice, TTSSettings


class ElevenLabsSettings(StrictModel):
    """ElevenLabs TTS configuration"""
    api_key: str
    voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel
    model_id: str = "eleven_multilingual_v2"
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    use_speaker_boost: bool = True


class ElevenLabsTTSProvider:
    """
    ElevenLabs — Premium cloud TTS with voice cloning.
    
    Features:
    - High quality voices
    - Voice cloning from samples
    - Streaming audio
    - Multiple languages
    
    Pricing: ~$0.30 per 1000 characters
    """
    
    BASE_URL = "https://api.elevenlabs.io/v1"
    
    def __init__(self, settings: ElevenLabsSettings) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            headers={"xi-api-key": settings.api_key},
            timeout=60.0,
        )
    
    @property
    def name(self) -> str:
        return "elevenlabs"
    
    @property
    def supports_streaming(self) -> bool:
        return True
    
    @property
    def supports_cloning(self) -> bool:
        return True
    
    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
    ) -> bytes:
        """Synthesize text to MP3 audio."""
        voice = voice_id or self._settings.voice_id
        
        response = await self._http.post(
            f"{self.BASE_URL}/text-to-speech/{voice}",
            json={
                "text": text,
                "model_id": self._settings.model_id,
                "voice_settings": {
                    "stability": settings.stability if settings else self._settings.stability,
                    "similarity_boost": self._settings.similarity_boost,
                    "style": self._settings.style,
                    "use_speaker_boost": self._settings.use_speaker_boost,
                },
            },
        )
        response.raise_for_status()
        return response.content
    
    async def synthesize_stream(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
    ) -> AsyncIterator[bytes]:
        """Streaming audio synthesis."""
        voice = voice_id or self._settings.voice_id
        
        async with self._http.stream(
            "POST",
            f"{self.BASE_URL}/text-to-speech/{voice}/stream",
            json={
                "text": text,
                "model_id": self._settings.model_id,
                "voice_settings": {
                    "stability": self._settings.stability,
                    "similarity_boost": self._settings.similarity_boost,
                },
            },
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=1024):
                yield chunk
    
    async def list_voices(self) -> list[Voice]:
        """List available voices from API."""
        response = await self._http.get(f"{self.BASE_URL}/voices")
        response.raise_for_status()
        data = response.json()
        
        return [
            Voice(
                id=v["voice_id"],
                name=v["name"],
                language=v.get("labels", {}).get("language", "en"),
                preview_url=v.get("preview_url"),
            )
            for v in data["voices"]
        ]
    
    async def clone_voice(
        self,
        name: str,
        audio_files: list[bytes],
    ) -> Voice:
        """Clone a voice from audio samples."""
        files = [
            ("files", (f"sample_{i}.wav", audio, "audio/wav"))
            for i, audio in enumerate(audio_files)
        ]
        
        response = await self._http.post(
            f"{self.BASE_URL}/voices/add",
            data={"name": name},
            files=files,
        )
        response.raise_for_status()
        data = response.json()
        
        return Voice(
            id=data["voice_id"],
            name=name,
            language="en",
        )
    
    async def health_check(self) -> bool:
        """Check API availability."""
        try:
            response = await self._http.get(f"{self.BASE_URL}/user")
            return response.status_code == 200
        except Exception:
            return False
    
    async def close(self) -> None:
        await self._http.aclose()
    
    def get_settings_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "voice_id": {
                    "type": "string",
                    "title": "Voice ID",
                    "description": "ElevenLabs voice ID",
                },
                "stability": {
                    "type": "number",
                    "title": "Stability",
                    "description": "Voice stability (0=varied, 1=stable)",
                    "default": 0.5,
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "similarity_boost": {
                    "type": "number",
                    "title": "Clarity",
                    "description": "Clarity + similarity enhancement",
                    "default": 0.75,
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
            "required": ["voice_id"],
        }
```

---

## Type Safety

### Core Principles

- **Pydantic v2** for all data models and settings
- **Strict mode** enabled globally
- **Protocol classes** with `@runtime_checkable` for providers
- **TypedDict** for JSON structures (WebSocket messages)
- **Generic types** where applicable (Queue[NarrationJob])
- **No `Any`** — explicit types everywhere
- **mypy strict mode** in CI

### Pydantic Configuration

```python
# src/core/types.py

from pydantic import BaseModel, ConfigDict

class StrictModel(BaseModel):
    """Base model with strict validation"""
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
    )
```

### Example Typed Models

```python
# src/models/narration.py

from enum import StrEnum
from pydantic import Field
from src.core.types import StrictModel

class NarratorStyle(StrEnum):
    WHISPER = "whisper"
    PROCLAIM = "proclaim"
    MOCK = "mock"

class LanguageCode(StrEnum):
    RU = "ru"
    EN = "en"
    DE = "de"
    FR = "fr"
    ES = "es"

class NarrationRequest(StrictModel):
    user: str = Field(min_length=1, max_length=50)
    message: str = Field(min_length=1, max_length=300)
    style: NarratorStyle = NarratorStyle.WHISPER
    source_lang: LanguageCode = LanguageCode.RU
    
class NarrationResult(StrictModel):
    id: str
    user: str
    text_original: str      # Original formatted text
    text_translated: str    # Translated (or same if no translation needed)
    target_lang: LanguageCode
    audio_data: bytes
    duration_ms: int
    was_translated: bool    # False if source == target language
```

### WebSocket Message Types

```python
# src/api/ws_types.py

from typing import Literal, TypedDict

class NarrationStartMessage(TypedDict):
    type: Literal["narration_start"]
    id: str
    user: str
    text: str
    timestamp: int

class AudioChunkMessage(TypedDict):
    type: Literal["audio_chunk"]
    id: str
    data: str  # base64
    chunk_index: int
    is_final: bool

class NarrationEndMessage(TypedDict):
    type: Literal["narration_end"]
    id: str
    duration_ms: int

class NarrationErrorMessage(TypedDict):
    type: Literal["narration_error"]
    id: str
    error: str

class RateLimitStatusMessage(TypedDict):
    type: Literal["rate_limit_status"]
    tts_available: bool
    tts_available_in_seconds: float | None
    queue_length: int

type ServerMessage = (
    NarrationStartMessage 
    | AudioChunkMessage 
    | NarrationEndMessage 
    | NarrationErrorMessage
    | RateLimitStatusMessage
)
```

---

## Settings Storage (SQLite)

### Database Schema

```sql
-- migrations/001_initial.sql

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,  -- JSON serialized
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE provider_settings (
    provider_type TEXT NOT NULL,  -- 'llm', 'tts', 'translate'
    provider_name TEXT NOT NULL,  -- 'groq', 'piper', 'elevenlabs', etc.
    settings TEXT NOT NULL,       -- JSON serialized
    is_active BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider_type, provider_name)
);

CREATE TABLE twitch_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Stores: reward_id, access_token, refresh_token, broadcaster_id

CREATE TABLE narration_log (
    id TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    message_original TEXT NOT NULL,
    text_formatted TEXT,
    text_translated TEXT,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    was_translated BOOLEAN,
    llm_provider TEXT,
    tts_provider TEXT,
    latency_llm_ms INTEGER,
    latency_tts_ms INTEGER,
    latency_total_ms INTEGER,
    queue_wait_ms INTEGER,
    status TEXT NOT NULL,  -- 'completed', 'error', 'skipped', 'rate_limited'
    rejection_reason TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE banned_users (
    username TEXT PRIMARY KEY,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE banned_words (
    word TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

```sql
-- migrations/002_indexes.sql

CREATE INDEX idx_narration_log_created ON narration_log(created_at);
CREATE INDEX idx_narration_log_user ON narration_log(user);
CREATE INDEX idx_narration_log_status ON narration_log(status);
```

### Settings Repository

```python
# src/db/repositories/settings.py

from typing import TypeVar, Type
from pydantic import BaseModel
import aiosqlite
from src.core.types import StrictModel

T = TypeVar("T", bound=BaseModel)

class SettingsRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db
    
    async def get[T: BaseModel](
        self, 
        key: str, 
        model: Type[T],
        default: T | None = None
    ) -> T | None:
        """Get typed setting by key"""
        async with self._db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return default
            return model.model_validate_json(row[0])
    
    async def set[T: BaseModel](self, key: str, value: T) -> None:
        """Save typed setting"""
        await self._db.execute(
            """
            INSERT INTO settings (key, value, updated_at) 
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET 
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value.model_dump_json())
        )
        await self._db.commit()
    
    async def get_active_provider(
        self, 
        provider_type: str
    ) -> tuple[str, dict] | None:
        """Get active provider name and settings"""
        async with self._db.execute(
            """
            SELECT provider_name, settings FROM provider_settings 
            WHERE provider_type = ? AND is_active = TRUE
            """,
            (provider_type,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            import json
            return row[0], json.loads(row[1])
    
    async def set_provider_settings(
        self,
        provider_type: str,
        provider_name: str,
        settings: dict,
        is_active: bool = False
    ) -> None:
        """Save provider settings"""
        import json
        
        # If setting as active, deactivate others
        if is_active:
            await self._db.execute(
                "UPDATE provider_settings SET is_active = FALSE WHERE provider_type = ?",
                (provider_type,)
            )
        
        await self._db.execute(
            """
            INSERT INTO provider_settings (provider_type, provider_name, settings, is_active, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(provider_type, provider_name) DO UPDATE SET
                settings = excluded.settings,
                is_active = excluded.is_active,
                updated_at = CURRENT_TIMESTAMP
            """,
            (provider_type, provider_name, json.dumps(settings), is_active)
        )
        await self._db.commit()
```

### Settings Models

```python
# src/models/settings.py

from enum import StrEnum
from typing import Literal
from pydantic import Field, SecretStr
from src.core.types import StrictModel
from src.models.narration import LanguageCode, NarratorStyle

class ProviderType(StrEnum):
    LLM = "llm"
    TTS = "tts"
    TRANSLATE = "translate"

# === Global Settings ===

class LanguageSettings(StrictModel):
    """Language configuration for narrator"""
    source_lang: LanguageCode = LanguageCode.RU       # Chat message language
    narrator_lang: LanguageCode = LanguageCode.EN     # TTS output language
    subtitle_lang: LanguageCode = LanguageCode.RU     # Overlay subtitle language
    auto_translate: bool = True                        # Enable translation
    
    @property
    def needs_translation(self) -> bool:
        """Check if translation is needed based on settings"""
        return self.auto_translate and self.source_lang != self.narrator_lang

class NarratorSettings(StrictModel):
    default_style: NarratorStyle = NarratorStyle.WHISPER
    system_prompt: str = Field(default="")
    
class QueueSettings(StrictModel):
    max_size: int = Field(default=50, ge=1, le=200)
    message_min_length: int = Field(default=1, ge=1)
    message_max_length: int = Field(default=300, ge=10, le=500)
    cooldown_seconds: int = Field(default=5, ge=0)
    tts_rate_limit_seconds: int = Field(default=10, ge=1)
    priority_users: list[str] = Field(default_factory=list)

class OverlaySettings(StrictModel):
    font_family: str = "IM Fell English SC"
    font_size: int = Field(default=28, ge=12, le=72)
    text_color: str = "#F4E4BC"
    background_color: str = "rgba(20, 15, 10, 0.85)"
    animation_duration_ms: int = Field(default=500, ge=0)
    position: Literal["top", "center", "bottom"] = "bottom"

class TwitchRewardSettings(StrictModel):
    """Twitch Channel Points reward configuration"""
    title: str = "🎭 Narrator TTS"
    cost: int = Field(default=500, ge=1, le=1_000_000)
    prompt: str = "Enter your message for the narrator"
    background_color: str = Field(default="#6441A4", pattern=r"^#[0-9A-Fa-f]{6}$")
    sync_cooldown_with_rate_limit: bool = True
    refund_on_queue_full: bool = True
    refund_on_filtered: bool = True
    refund_on_banned_user: bool = False

class AppSettings(StrictModel):
    """Root settings object"""
    language: LanguageSettings = Field(default_factory=LanguageSettings)
    narrator: NarratorSettings = Field(default_factory=NarratorSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    overlay: OverlaySettings = Field(default_factory=OverlaySettings)
    reward: TwitchRewardSettings = Field(default_factory=TwitchRewardSettings)

# === Provider Settings ===

class GroqSettings(StrictModel):
    api_key: SecretStr
    model: str = "llama-3.3-70b-versatile"
    temperature: float = Field(default=0.8, ge=0, le=2)
    max_tokens: int = Field(default=500, ge=50, le=2000)

class OpenAISettings(StrictModel):
    api_key: SecretStr
    model: str = "gpt-4o-mini"
    temperature: float = Field(default=0.8, ge=0, le=2)

class PiperSettings(StrictModel):
    voice: str = "en_US-lessac-medium"
    length_scale: float = Field(default=1.0, ge=0.5, le=2.0)
    noise_scale: float = Field(default=0.667, ge=0.0, le=1.0)
    noise_w: float = Field(default=0.8, ge=0.0, le=1.0)
    
class ElevenLabsSettings(StrictModel):
    api_key: SecretStr
    voice_id: str
    model_id: str = "eleven_multilingual_v2"
    stability: float = Field(default=0.5, ge=0, le=1)
    similarity_boost: float = Field(default=0.75, ge=0, le=1)
```

### Database Manager

```python
# src/db/manager.py

from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncIterator
import aiosqlite

class DatabaseManager:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._connection: aiosqlite.Connection | None = None
    
    async def initialize(self) -> None:
        """Create tables and run migrations"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._db_path)
        await self._run_migrations()
    
    async def close(self) -> None:
        if self._connection:
            await self._connection.close()
    
    @property
    def connection(self) -> aiosqlite.Connection:
        assert self._connection is not None
        return self._connection
    
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Transaction context manager"""
        assert self._connection is not None
        try:
            yield self._connection
            await self._connection.commit()
        except Exception:
            await self._connection.rollback()
            raise
    
    async def _run_migrations(self) -> None:
        """Run SQL migrations from migrations/ folder"""
        migrations_dir = Path("migrations")
        if not migrations_dir.exists():
            return
        
        # Create migrations tracking table
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Get applied migrations
        async with self._connection.execute(
            "SELECT name FROM _migrations"
        ) as cursor:
            applied = {row[0] for row in await cursor.fetchall()}
        
        # Apply new migrations
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            if migration_file.name not in applied:
                sql = migration_file.read_text()
                await self._connection.executescript(sql)
                await self._connection.execute(
                    "INSERT INTO _migrations (name) VALUES (?)",
                    (migration_file.name,)
                )
        
        await self._connection.commit()
```

---

## Configuration

### Configuration Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Configuration Sources                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Environment Variables (.env)     Static Config (YAML)       │
│  ─────────────────────────────    ────────────────────────   │
│  • API Keys (secrets)             • App name, debug mode     │
│  • Twitch credentials             • Server host/port         │
│  • Database path                  • Default prompts path     │
│  • External service URLs          • Model paths              │
│                                                              │
│  ────────────────────────────────────────────────────────    │
│                                                              │
│              SQLite Database (Dynamic Settings)              │
│  ────────────────────────────────────────────────────────    │
│  • Language settings (source, narrator, subtitle)            │
│  • Active providers (LLM, TTS, Translate)                    │
│  • Provider-specific settings (model, temperature, etc.)     │
│  • Queue settings (max size, cooldowns)                      │
│  • Overlay settings (font, colors, position)                 │
│  • Banned users/words                                        │
│  • Narration history                                         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Environment Variables (Secrets Only)

```bash
# .env — NEVER commit to git

# === Required ===
TWITCH_CLIENT_ID=your_client_id
TWITCH_CLIENT_SECRET=your_client_secret
TWITCH_CHANNEL=your_channel_name

# === LLM Providers (at least one) ===
GROQ_API_KEY=gsk_...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...

# === TTS Providers (optional — Piper works without API key) ===
ELEVENLABS_API_KEY=...

# === Translation (optional) ===
DEEPL_API_KEY=...

# === App ===
SECRET_KEY=random_secret_for_sessions
DATABASE_URL=sqlite:///data/narrator.db
```

### Static Configuration (YAML)

```yaml
# config/default.yaml — Safe to commit

app:
  name: "BG3 Narrator Bot"
  debug: false
  log_level: "INFO"

server:
  host: "0.0.0.0"
  port: 8000
  cors_origins:
    - "*"

paths:
  database: "${DATABASE_URL:-sqlite:///data/narrator.db}"
  prompts: "./config/prompts"
  models: "./models"

# Default values for SQLite settings (used on first run)
defaults:
  language:
    source_lang: "ru"
    narrator_lang: "en"
    subtitle_lang: "ru"
    auto_translate: true
  
  providers:
    llm: "groq"
    tts: "piper"
    translate: "llm"
  
  piper:
    voice: "en_US-lessac-medium"
    length_scale: 1.0
    noise_scale: 0.667
    
  queue:
    max_size: 50
    message_min_length: 1
    message_max_length: 300
    cooldown_seconds: 5
    tts_rate_limit_seconds: 10

  reward:
    title: "🎭 Narrator TTS"
    cost: 500
    prompt: "Enter your message for the narrator"
    background_color: "#6441A4"
    sync_cooldown_with_rate_limit: true
    refund_on_queue_full: true
    refund_on_filtered: true
```

### Pydantic Settings Model

```python
# src/config.py

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class EnvSettings(BaseSettings):
    """Environment variables — secrets only"""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # Twitch
    twitch_client_id: str
    twitch_client_secret: SecretStr
    twitch_channel: str
    
    # LLM Providers
    groq_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    
    # TTS Providers (Piper doesn't need API key)
    elevenlabs_api_key: SecretStr | None = None
    
    # Translation
    deepl_api_key: SecretStr | None = None
    
    # App
    secret_key: SecretStr
    database_url: str = "sqlite:///data/narrator.db"
    
    def get_available_llm_providers(self) -> list[str]:
        """Return list of configured LLM providers"""
        providers: list[str] = []
        if self.groq_api_key:
            providers.append("groq")
        if self.openai_api_key:
            providers.append("openai")
        if self.anthropic_api_key:
            providers.append("anthropic")
        if self.openrouter_api_key:
            providers.append("openrouter")
        providers.append("ollama")  # Always available (local)
        return providers
    
    def get_available_tts_providers(self) -> list[str]:
        """Return list of configured TTS providers"""
        providers: list[str] = ["piper"]  # Always available
        if self.elevenlabs_api_key:
            providers.append("elevenlabs")
        return providers
```

---

## Twitch Integration (TwitchIO 3.x)

### EventSub WebSocket Client

```python
# src/services/twitch/eventsub.py

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, Awaitable
from dataclasses import dataclass

import twitchio
from twitchio.ext import eventsub

if TYPE_CHECKING:
    from src.services.queue import NarrationQueue


@dataclass(frozen=True)
class RedemptionEvent:
    """Parsed channel point redemption"""
    id: str                    # Redemption ID (for fulfill/cancel)
    user: str                  # Username who redeemed
    user_id: str               # User ID
    message: str               # User input text
    reward_id: str             # Reward ID
    reward_title: str          # Reward title
    reward_cost: int           # Points spent
    redeemed_at: str           # ISO timestamp


class TwitchEventSubService:
    """
    TwitchIO 3.x EventSub integration for Channel Points.
    
    Handles:
    - OAuth token management
    - EventSub WebSocket subscription
    - Redemption event routing
    
    Required scopes:
    - channel:read:redemptions
    - channel:manage:redemptions
    """
    
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        broadcaster_id: str,
        redirect_uri: str = "http://localhost:8000/auth/callback",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._broadcaster_id = broadcaster_id
        self._redirect_uri = redirect_uri
        
        self._client: twitchio.Client | None = None
        self._eventsub: eventsub.EventSubWSClient | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        
        self._redemption_handlers: list[
            Callable[[RedemptionEvent], Awaitable[None]]
        ] = []
    
    async def start(
        self, 
        access_token: str, 
        refresh_token: str
    ) -> None:
        """Start EventSub WebSocket connection"""
        self._access_token = access_token
        self._refresh_token = refresh_token
        
        # Create TwitchIO client
        self._client = twitchio.Client(
            token=access_token,
            client_secret=self._client_secret,
        )
        
        # Create EventSub WebSocket client
        self._eventsub = eventsub.EventSubWSClient(self._client)
        
        # Subscribe to channel point redemptions
        await self._eventsub.subscribe_channel_points_redeemed(
            broadcaster=int(self._broadcaster_id),
            token=access_token,
        )
        
        # Register internal handler
        self._eventsub.event_channel_points_redeemed = self._on_redemption
        
        # Start listening
        asyncio.create_task(self._eventsub.listen())
    
    async def stop(self) -> None:
        """Stop EventSub and cleanup"""
        if self._eventsub:
            await self._eventsub.close()
        if self._client:
            await self._client.close()
    
    def on_redemption(
        self, 
        handler: Callable[[RedemptionEvent], Awaitable[None]]
    ) -> None:
        """Register redemption handler"""
        self._redemption_handlers.append(handler)
    
    async def _on_redemption(
        self, 
        event: eventsub.ChannelPointsRedemptionAddUpdateEvent
    ) -> None:
        """Internal handler for redemption events"""
        redemption = RedemptionEvent(
            id=event.id,
            user=event.user.name,
            user_id=str(event.user.id),
            message=event.input or "",
            reward_id=event.reward.id,
            reward_title=event.reward.title,
            reward_cost=event.reward.cost,
            redeemed_at=event.redeemed_at.isoformat(),
        )
        
        for handler in self._redemption_handlers:
            try:
                await handler(redemption)
            except Exception as e:
                import structlog
                logger = structlog.get_logger()
                logger.error(
                    "redemption_handler_error",
                    error=str(e),
                    redemption_id=redemption.id,
                )
    
    def get_oauth_url(self, state: str) -> str:
        """Generate OAuth authorization URL"""
        scopes = [
            "channel:read:redemptions",
            "channel:manage:redemptions",
        ]
        return (
            f"https://id.twitch.tv/oauth2/authorize"
            f"?client_id={self._client_id}"
            f"&redirect_uri={self._redirect_uri}"
            f"&response_type=code"
            f"&scope={'+'.join(scopes)}"
            f"&state={state}"
        )
    
    async def exchange_code(self, code: str) -> tuple[str, str]:
        """Exchange OAuth code for tokens"""
        import httpx
        
        async with httpx.AsyncClient() as http:
            response = await http.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self._redirect_uri,
                },
            )
            response.raise_for_status()
            data = response.json()
            
            return data["access_token"], data["refresh_token"]
    
    async def refresh_tokens(self) -> tuple[str, str]:
        """Refresh expired tokens"""
        import httpx
        
        if not self._refresh_token:
            raise ValueError("No refresh token available")
        
        async with httpx.AsyncClient() as http:
            response = await http.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            data = response.json()
            
            self._access_token = data["access_token"]
            self._refresh_token = data["refresh_token"]
            
            return self._access_token, self._refresh_token
```

### Twitch Rewards Controller

```python
# src/services/twitch/rewards.py

from dataclasses import dataclass
from enum import StrEnum
import httpx


class RewardState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


@dataclass(frozen=True)
class RewardConfig:
    title: str = "🎭 Narrator TTS"
    cost: int = 500
    prompt: str = "Enter your message for the narrator"
    background_color: str = "#6441A4"
    is_user_input_required: bool = True
    should_redemptions_skip_request_queue: bool = False


@dataclass(frozen=True)
class Reward:
    id: str
    title: str
    cost: int
    is_enabled: bool
    is_paused: bool
    is_in_stock: bool
    cooldown_seconds: int


class TwitchRewardController:
    """
    Manages Channel Points reward lifecycle.
    
    Creates reward on startup, pauses during rate limiting,
    and syncs cooldown with rate limiter settings.
    
    Important: Only rewards created by this app (same client_id)
    can be managed programmatically. Rewards created in Twitch
    Dashboard cannot be controlled via API.
    """
    
    BASE_URL = "https://api.twitch.tv/helix"
    
    def __init__(
        self,
        client_id: str,
        access_token: str,
        broadcaster_id: str,
    ) -> None:
        self._client_id = client_id
        self._access_token = access_token
        self._broadcaster_id = broadcaster_id
        self._reward_id: str | None = None
        self._http = httpx.AsyncClient(
            headers={
                "Client-Id": client_id,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
        )
    
    async def initialize(self, config: RewardConfig) -> str:
        """
        Initialize reward: find existing or create new.
        Returns reward_id.
        """
        existing = await self._find_reward_by_title(config.title)
        
        if existing:
            self._reward_id = existing.id
            await self.set_state(RewardState.ACTIVE)
        else:
            self._reward_id = await self._create_reward(config)
        
        return self._reward_id
    
    async def set_state(self, state: RewardState) -> None:
        """Set reward state (active/paused/disabled)"""
        assert self._reward_id is not None
        
        body: dict[str, bool] = {}
        
        match state:
            case RewardState.ACTIVE:
                body = {"is_enabled": True, "is_paused": False}
            case RewardState.PAUSED:
                body = {"is_enabled": True, "is_paused": True}
            case RewardState.DISABLED:
                body = {"is_enabled": False, "is_paused": False}
        
        await self._http.patch(
            f"{self.BASE_URL}/channel_points/custom_rewards",
            params={
                "broadcaster_id": self._broadcaster_id,
                "id": self._reward_id,
            },
            json=body,
        )
    
    async def pause(self) -> None:
        """Pause reward (users can see but not redeem)"""
        await self.set_state(RewardState.PAUSED)
    
    async def unpause(self) -> None:
        """Unpause reward"""
        await self.set_state(RewardState.ACTIVE)
    
    async def update_cooldown(self, seconds: int) -> None:
        """Sync Twitch's built-in cooldown with our rate limiter"""
        assert self._reward_id is not None
        
        await self._http.patch(
            f"{self.BASE_URL}/channel_points/custom_rewards",
            params={
                "broadcaster_id": self._broadcaster_id,
                "id": self._reward_id,
            },
            json={
                "global_cooldown_setting": {
                    "is_enabled": seconds > 0,
                    "global_cooldown_seconds": seconds,
                }
            },
        )
    
    async def fulfill_redemption(self, redemption_id: str) -> None:
        """Mark redemption as fulfilled (completed)"""
        await self._update_redemption_status(redemption_id, "FULFILLED")
    
    async def cancel_redemption(self, redemption_id: str) -> None:
        """Cancel redemption and refund points"""
        await self._update_redemption_status(redemption_id, "CANCELED")
    
    async def _update_redemption_status(
        self, 
        redemption_id: str, 
        status: str
    ) -> None:
        """Update redemption status (FULFILLED or CANCELED)"""
        assert self._reward_id is not None
        
        await self._http.patch(
            f"{self.BASE_URL}/channel_points/custom_rewards/redemptions",
            params={
                "broadcaster_id": self._broadcaster_id,
                "reward_id": self._reward_id,
                "id": redemption_id,
            },
            json={"status": status},
        )
    
    async def _find_reward_by_title(self, title: str) -> Reward | None:
        """Find our reward by title (only manageable rewards)"""
        response = await self._http.get(
            f"{self.BASE_URL}/channel_points/custom_rewards",
            params={
                "broadcaster_id": self._broadcaster_id,
                "only_manageable_rewards": "true",
            },
        )
        data = response.json()
        
        for reward in data.get("data", []):
            if reward["title"] == title:
                return Reward(
                    id=reward["id"],
                    title=reward["title"],
                    cost=reward["cost"],
                    is_enabled=reward["is_enabled"],
                    is_paused=reward["is_paused"],
                    is_in_stock=reward["is_in_stock"],
                    cooldown_seconds=reward["global_cooldown_setting"].get(
                        "global_cooldown_seconds", 0
                    ),
                )
        return None
    
    async def _create_reward(self, config: RewardConfig) -> str:
        """Create new Channel Points reward"""
        response = await self._http.post(
            f"{self.BASE_URL}/channel_points/custom_rewards",
            params={"broadcaster_id": self._broadcaster_id},
            json={
                "title": config.title,
                "cost": config.cost,
                "prompt": config.prompt,
                "background_color": config.background_color,
                "is_user_input_required": config.is_user_input_required,
                "should_redemptions_skip_request_queue": (
                    config.should_redemptions_skip_request_queue
                ),
                "is_enabled": True,
                "is_paused": False,
            },
        )
        data = response.json()
        return data["data"][0]["id"]
    
    async def close(self) -> None:
        await self._http.aclose()
```

---

## Rate Limiting

### Rate Limiter Service

```python
# src/services/rate_limiter.py

from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import StrEnum
import asyncio


class RejectionReason(StrEnum):
    TTS_RATE_LIMITED = "tts_rate_limited"
    USER_COOLDOWN = "user_cooldown"
    QUEUE_FULL = "queue_full"
    USER_BANNED = "user_banned"
    MESSAGE_FILTERED = "message_filtered"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    reason: RejectionReason | None = None
    retry_after_seconds: float | None = None
    queue_position: int | None = None


class RateLimiter:
    """
    Global TTS rate limiter with per-user cooldowns.
    
    Ensures smooth TTS generation with configurable
    minimum interval between generations.
    """
    
    def __init__(
        self,
        tts_rate_limit_seconds: float = 10.0,
        user_cooldown_seconds: float = 5.0,
    ) -> None:
        self._tts_rate_limit = timedelta(seconds=tts_rate_limit_seconds)
        self._user_cooldown = timedelta(seconds=user_cooldown_seconds)
        self._last_tts_time: datetime | None = None
        self._user_last_message: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
    
    async def check(self, user: str) -> RateLimitResult:
        """
        Check if a message from user can be processed.
        Does not consume the rate limit — call acquire() for that.
        """
        now = datetime.now()
        
        # Check user cooldown
        if user in self._user_last_message:
            user_elapsed = now - self._user_last_message[user]
            if user_elapsed < self._user_cooldown:
                retry_after = (self._user_cooldown - user_elapsed).total_seconds()
                return RateLimitResult(
                    allowed=False,
                    reason=RejectionReason.USER_COOLDOWN,
                    retry_after_seconds=retry_after,
                )
        
        # Check global TTS rate limit
        if self._last_tts_time is not None:
            tts_elapsed = now - self._last_tts_time
            if tts_elapsed < self._tts_rate_limit:
                retry_after = (self._tts_rate_limit - tts_elapsed).total_seconds()
                return RateLimitResult(
                    allowed=False,
                    reason=RejectionReason.TTS_RATE_LIMITED,
                    retry_after_seconds=retry_after,
                )
        
        return RateLimitResult(allowed=True)
    
    async def acquire(self, user: str) -> RateLimitResult:
        """
        Acquire rate limit slot for TTS generation.
        Call this when actually starting TTS, not when queuing.
        """
        async with self._lock:
            result = await self.check(user)
            if not result.allowed:
                return result
            
            now = datetime.now()
            self._last_tts_time = now
            self._user_last_message[user] = now
            
            return RateLimitResult(allowed=True)
    
    async def update_settings(
        self,
        tts_rate_limit_seconds: float | None = None,
        user_cooldown_seconds: float | None = None,
    ) -> None:
        """Update rate limit settings dynamically"""
        async with self._lock:
            if tts_rate_limit_seconds is not None:
                self._tts_rate_limit = timedelta(seconds=tts_rate_limit_seconds)
            if user_cooldown_seconds is not None:
                self._user_cooldown = timedelta(seconds=user_cooldown_seconds)
    
    def get_status(self) -> dict[str, float | None]:
        """Get current rate limiter status for UI"""
        now = datetime.now()
        
        tts_available_in: float | None = None
        if self._last_tts_time:
            elapsed = now - self._last_tts_time
            remaining = self._tts_rate_limit - elapsed
            if remaining.total_seconds() > 0:
                tts_available_in = remaining.total_seconds()
        
        return {
            "tts_rate_limit_seconds": self._tts_rate_limit.total_seconds(),
            "user_cooldown_seconds": self._user_cooldown.total_seconds(),
            "tts_available_in_seconds": tts_available_in,
        }
```

### Queue with Rate Limiting

```python
# src/services/queue.py

from dataclasses import dataclass, field
from datetime import datetime
from collections import deque
import asyncio
import uuid

from src.models.settings import QueueSettings
from src.services.rate_limiter import RateLimiter, RateLimitResult, RejectionReason


@dataclass
class QueueItem:
    id: str
    user: str
    message: str
    redemption_id: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    priority: int = 0  # Higher = processed first


class NarrationQueue:
    def __init__(
        self, 
        settings: QueueSettings,
        rate_limiter: RateLimiter,
    ) -> None:
        self._settings = settings
        self._rate_limiter = rate_limiter
        self._queue: deque[QueueItem] = deque()
        self._lock = asyncio.Lock()
    
    async def add(
        self, 
        user: str, 
        message: str,
        redemption_id: str | None = None,
    ) -> RateLimitResult:
        """Add message to queue if allowed."""
        # Check queue size
        if len(self._queue) >= self._settings.max_size:
            return RateLimitResult(
                allowed=False, 
                reason=RejectionReason.QUEUE_FULL
            )
        
        # Check message length
        if not (self._settings.message_min_length 
                <= len(message) 
                <= self._settings.message_max_length):
            return RateLimitResult(
                allowed=False, 
                reason=RejectionReason.MESSAGE_FILTERED
            )
        
        # Check user cooldown
        result = await self._rate_limiter.check(user)
        if not result.allowed and result.reason == RejectionReason.USER_COOLDOWN:
            return result
        
        # Add to queue
        async with self._lock:
            item = QueueItem(
                id=str(uuid.uuid4())[:8],
                user=user,
                message=message,
                redemption_id=redemption_id,
                priority=1 if user in self._settings.priority_users else 0,
            )
            self._queue.append(item)
            self._sort_queue()
            
            position = list(self._queue).index(item) + 1
            return RateLimitResult(allowed=True, queue_position=position)
    
    async def process_next(self) -> QueueItem | None:
        """Get next item, respecting TTS rate limit."""
        while True:
            async with self._lock:
                if not self._queue:
                    return None
                
                item = self._queue[0]
                result = await self._rate_limiter.acquire(item.user)
                
                if result.allowed:
                    self._queue.popleft()
                    return item
            
            if result.retry_after_seconds:
                await asyncio.sleep(result.retry_after_seconds)
    
    async def items(self) -> list[QueueItem]:
        """Get current queue items for UI"""
        async with self._lock:
            return list(self._queue)
    
    async def skip(self, item_id: str) -> bool:
        """Remove item from queue"""
        async with self._lock:
            for i, item in enumerate(self._queue):
                if item.id == item_id:
                    del self._queue[i]
                    return True
            return False
    
    def _sort_queue(self) -> None:
        """Sort by priority (desc), then by created_at (asc)"""
        items = list(self._queue)
        items.sort(key=lambda x: (-x.priority, x.created_at))
        self._queue = deque(items)
    
    def __len__(self) -> int:
        return len(self._queue)
```

---

## Narrator Prompt

### Dynamic Prompt Generation

```python
# src/services/prompt_builder.py

from src.models.settings import LanguageSettings, NarratorStyle
from src.models.narration import LanguageCode


class PromptBuilder:
    def build_narrator_prompt(
        self,
        user: str,
        message: str,
        style: NarratorStyle,
        language_settings: LanguageSettings,
    ) -> str:
        """Build prompt based on language configuration"""
        
        needs_translation = language_settings.needs_translation
        
        if needs_translation:
            return self._build_translation_prompt(
                user, message, style, 
                language_settings.source_lang,
                language_settings.narrator_lang
            )
        else:
            return self._build_single_lang_prompt(
                user, message, style, 
                language_settings.narrator_lang
            )
    
    def _build_translation_prompt(
        self,
        user: str,
        message: str,
        style: NarratorStyle,
        source_lang: LanguageCode,
        target_lang: LanguageCode,
    ) -> str:
        style_desc = self._get_style_description(style)
        
        return f"""You are the Narrator from Baldur's Gate 3. Your voice is rich, dramatic, and captivating.

A viewer named "{user}" has sent a message: "{message}"

Your task:
1. Transform this casual message into dramatic narrator prose
2. Keep the original meaning but add theatrical flair
3. The narration should be 1-3 sentences maximum

Style: {style_desc}

Respond in JSON format:
{{
  "formatted": "Dramatic narration in {source_lang}",
  "translated": "Same narration translated to {target_lang}"
}}

IMPORTANT:
- "formatted" is the dramatic narration in {source_lang} (for subtitles)
- "translated" is the same narration in {target_lang} (for TTS)
- Do NOT translate the username
- Keep it concise - this will be spoken aloud"""
    
    def _build_single_lang_prompt(
        self,
        user: str,
        message: str,
        style: NarratorStyle,
        language: LanguageCode,
    ) -> str:
        style_desc = self._get_style_description(style)
        
        return f"""You are the Narrator from Baldur's Gate 3. Your voice is rich, dramatic, and captivating.

A viewer named "{user}" has sent a message: "{message}"

Your task:
1. Transform this casual message into dramatic narrator prose in {language}
2. Keep the original meaning but add theatrical flair
3. The narration should be 1-3 sentences maximum

Style: {style_desc}

Respond in JSON format:
{{
  "text": "Dramatic narration in {language}"
}}

IMPORTANT:
- Write the dramatic narration in {language}
- Do NOT translate the username
- Keep it concise - this will be spoken aloud"""
    
    def _get_style_description(self, style: NarratorStyle) -> str:
        match style:
            case NarratorStyle.WHISPER:
                return "Intimate and mysterious, as if sharing a secret"
            case NarratorStyle.PROCLAIM:
                return "Bold and commanding, announcing great deeds"
            case NarratorStyle.MOCK:
                return "Sardonic and amused, gently teasing the subject"
```

### LLM Response Models

```python
# src/models/llm_response.py

from src.core.types import StrictModel


class TranslatedNarration(StrictModel):
    """Response when translation is needed"""
    formatted: str    # Original language formatted text
    translated: str   # Translated text for TTS


class SingleLangNarration(StrictModel):
    """Response when no translation needed"""
    text: str


type LLMNarrationResponse = TranslatedNarration | SingleLangNarration
```

---

## WebSocket Protocol

### Message Types

```typescript
// Client → Server

interface ClientMessage {
  type: "ping" | "subscribe" | "unsubscribe";
  channel?: string;
}

// Server → Client

interface NarrationStart {
  type: "narration_start";
  id: string;
  user: string;
  text: string;       // Subtitle text
  timestamp: number;
}

interface AudioChunk {
  type: "audio_chunk";
  id: string;
  data: string;        // Base64 encoded audio
  chunk_index: number;
  is_final: boolean;
}

interface NarrationEnd {
  type: "narration_end";
  id: string;
  duration_ms: number;
}

interface NarrationError {
  type: "narration_error";
  id: string;
  error: string;
}

interface QueueUpdate {
  type: "queue_update";
  queue_length: number;
  current_position: number | null;
}

interface RateLimitStatus {
  type: "rate_limit_status";
  tts_available: boolean;
  tts_available_in_seconds: number | null;
  queue_length: number;
}

interface Pong {
  type: "pong";
  timestamp: number;
}
```

### Connection Flow

```
1. Client connects to ws://server/ws/overlay
2. Server sends initial state
3. On redemption:
   a. Server sends narration_start (client shows subtitles)
   b. Server sends audio_chunk messages (client buffers & plays)
   c. Server sends narration_end (client hides subtitles after delay)
4. Client sends ping every 30s to keep alive
```

---

## OBS Overlay

### HTML Structure

```html
<!-- overlay/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <link rel="stylesheet" href="style.css">
  <link href="https://fonts.googleapis.com/css2?family=IM+Fell+English+SC&display=swap" rel="stylesheet">
</head>
<body>
  <div id="narrator-container" class="hidden">
    <div id="narrator-frame">
      <div id="narrator-text"></div>
    </div>
  </div>
  <script src="overlay.js"></script>
</body>
</html>
```

### Styling (BG3 Theme)

```css
/* overlay/style.css */

:root {
  --bg-color: rgba(20, 15, 10, 0.85);
  --text-color: #F4E4BC;
  --border-color: #8B7355;
  --glow-color: rgba(244, 228, 188, 0.3);
}

body {
  margin: 0;
  padding: 0;
  background: transparent;
  overflow: hidden;
}

#narrator-container {
  position: fixed;
  bottom: 100px;
  left: 50%;
  transform: translateX(-50%);
  transition: opacity 0.5s ease, transform 0.5s ease;
}

#narrator-container.hidden {
  opacity: 0;
  transform: translateX(-50%) translateY(20px);
}

#narrator-frame {
  background: var(--bg-color);
  border: 2px solid var(--border-color);
  border-radius: 4px;
  padding: 20px 40px;
  max-width: 800px;
  box-shadow: 
    0 0 20px var(--glow-color),
    inset 0 0 20px rgba(0, 0, 0, 0.5);
}

#narrator-text {
  font-family: 'IM Fell English SC', serif;
  font-size: 28px;
  color: var(--text-color);
  text-align: center;
  line-height: 1.4;
  text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.8);
}
```

### JavaScript Client

```javascript
// overlay/overlay.js

class NarratorOverlay {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.audioContext = null;
    this.audioQueue = [];
    this.isPlaying = false;
    this.connect();
  }
  
  connect() {
    this.ws = new WebSocket(this.wsUrl);
    this.ws.onmessage = (event) => this.handleMessage(JSON.parse(event.data));
    this.ws.onclose = () => setTimeout(() => this.connect(), 5000);
    this.ws.onopen = () => console.log("Connected to narrator server");
  }
  
  async ensureAudioContext() {
    if (!this.audioContext) {
      this.audioContext = new AudioContext();
    }
    if (this.audioContext.state === "suspended") {
      await this.audioContext.resume();
    }
  }
  
  handleMessage(msg) {
    switch (msg.type) {
      case "narration_start":
        this.showSubtitles(msg.text);
        break;
      case "audio_chunk":
        this.queueAudio(msg.data, msg.is_final);
        break;
      case "narration_end":
        setTimeout(() => this.hideSubtitles(), 1000);
        break;
      case "narration_error":
        console.error("Narration error:", msg.error);
        this.hideSubtitles();
        break;
      case "pong":
        // Heartbeat acknowledged
        break;
    }
  }
  
  showSubtitles(text) {
    const container = document.getElementById("narrator-container");
    const textEl = document.getElementById("narrator-text");
    textEl.textContent = text;
    container.classList.remove("hidden");
  }
  
  hideSubtitles() {
    document.getElementById("narrator-container").classList.add("hidden");
  }
  
  async queueAudio(base64Data, isFinal) {
    await this.ensureAudioContext();
    
    const binaryStr = atob(base64Data);
    const bytes = new Uint8Array(binaryStr.length);
    for (let i = 0; i < binaryStr.length; i++) {
      bytes[i] = binaryStr.charCodeAt(i);
    }
    
    try {
      const audioBuffer = await this.audioContext.decodeAudioData(bytes.buffer);
      this.audioQueue.push(audioBuffer);
      
      if (!this.isPlaying) {
        this.playNext();
      }
    } catch (e) {
      console.error("Failed to decode audio:", e);
    }
  }
  
  playNext() {
    if (this.audioQueue.length === 0) {
      this.isPlaying = false;
      return;
    }
    
    this.isPlaying = true;
    const buffer = this.audioQueue.shift();
    const source = this.audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(this.audioContext.destination);
    source.onended = () => this.playNext();
    source.start();
  }
  
  startPingInterval() {
    setInterval(() => {
      if (this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 30000);
  }
}

// Initialize on load
const wsUrl = new URLSearchParams(window.location.search).get("ws") 
  || `ws://${window.location.host}/ws/overlay`;

const overlay = new NarratorOverlay(wsUrl);
overlay.startPingInterval();
```

---

## Web UI

### Pages

1. **Dashboard** (`/`)
   - Current queue status with live TTS rate limit countdown
   - Recent narrations log
   - Quick stats (messages today, average latency)
   - Start/Stop toggle

2. **Settings** (`/settings`)
   - **Language Settings**: Source, narrator, subtitle languages
   - **Twitch Reward**: Title, cost, prompt, color
   - **Rate Limiting**: TTS rate limit, user cooldown, max queue
   - Provider selection (LLM, TTS, Translate)
   - Dynamic provider-specific settings
   - Narrator style selection

3. **Queue** (`/queue`)
   - Current queue items with live updates
   - Rate limit status indicator
   - Skip/Priority buttons

4. **Test** (`/test`)
   - Manual text input
   - Language override for testing
   - Voice preview

5. **Logs** (`/logs`)
   - Narration history from SQLite
   - Filter by user, status, date
   - Latency stats

### HTMX Patterns

```html
<!-- templates/base.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}BG3 Narrator Bot{% endblock %}</title>
    <link rel="stylesheet" href="/static/css/style.css">
    <script src="https://unpkg.com/htmx.org@2.0.0"></script>
    <script src="https://unpkg.com/htmx-ext-ws@2.0.0/ws.js"></script>
</head>
<body hx-boost="true">
    <nav>
        <a href="/">Dashboard</a>
        <a href="/settings">Settings</a>
        <a href="/queue">Queue</a>
        <a href="/logs">Logs</a>
        <a href="/test">Test</a>
    </nav>
    
    <main>
        {% block content %}{% endblock %}
    </main>
    
    <div id="toast-container"></div>
    
    <script>
        document.body.addEventListener("showToast", (e) => {
            const toast = document.createElement("div");
            toast.className = "toast success";
            toast.innerHTML = `<span>${e.detail.value}</span>`;
            document.getElementById("toast-container").appendChild(toast);
            setTimeout(() => toast.remove(), 3000);
        });
    </script>
</body>
</html>
```

```html
<!-- templates/partials/queue_status.html -->
<div id="queue-status" 
     hx-get="/queue/status" 
     hx-trigger="every 1s"
     hx-swap="innerHTML">
    
    <div class="status-item">
        <span class="label">Queue</span>
        <span class="value">{{ queue_length }} / {{ max_size }}</span>
    </div>
    
    <div class="status-item">
        <span class="label">TTS</span>
        {% if tts_available %}
        <span class="value ready">Ready</span>
        {% else %}
        <span class="value waiting">{{ tts_available_in | round(1) }}s</span>
        {% endif %}
    </div>
</div>
```

---

## MVP Implementation Plan

### Phase 1: Core Pipeline (Week 1)

**Tasks:**
1. [x] Project setup (pyproject.toml, directory structure)
2. [x] Configuration loading with Pydantic
3. [x] Groq LLM provider implementation
4. [x] Piper TTS provider implementation
5. [x] Basic pipeline: message → LLM → TTS → audio file
6. [x] Unit tests for providers

**Deliverable:** CLI tool that converts text to narrated audio

### Phase 2: Twitch Integration (Week 2)

**Tasks:**
1. [x] TwitchIO 3.x EventSub setup
2. [x] Twitch OAuth flow
3. [x] Redemption validation and parsing
4. [x] Message queue with asyncio
5. [x] User cooldown tracking
6. [x] Integration tests

**Deliverable:** Bot connects to Twitch and queues redemptions

### Phase 3: WebSocket & Overlay (Week 3)

**Tasks:**
1. [x] FastAPI WebSocket endpoint
2. [x] Audio streaming over WebSocket
3. [x] OBS overlay HTML/CSS/JS
4. [x] Subtitle synchronization
5. [x] Reconnection handling
6. [x] End-to-end tests

**Deliverable:** Working overlay in OBS with audio + subtitles

### Phase 4: Web UI (Week 4)

**Tasks:**
1. [ ] FastAPI + Jinja2 setup
2. [ ] HTMX integration
3. [ ] Settings page with dynamic forms
4. [ ] Queue management page
5. [ ] Test/preview functionality
6. [ ] Styling

**Deliverable:** Functional web UI for configuration

### Phase 5: Deployment & Polish (Week 5)

**Tasks:**
1. [ ] Dockerfile optimization
2. [ ] Railway configuration
3. [ ] Environment variable handling
4. [ ] Error handling and logging
5. [ ] Documentation

**Deliverable:** Deployed and running on Railway

---

## Railway Deployment

### Configuration

```toml
# railway.toml

[build]
builder = "dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "on-failure"
restartPolicyMaxRetries = 3
```

### Dockerfile

```dockerfile
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    libespeak-ng1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Pre-download default Piper voice
RUN python -c "from piper import PiperVoice; PiperVoice.load('en_US-lessac-medium')" || true

# Copy application
COPY . .

# Create directories
RUN mkdir -p /app/data /app/models/piper

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "src.main"]
```

### Resource Requirements

| Resource | Requirement |
|----------|-------------|
| **RAM** | 512 MB - 1 GB |
| **CPU** | 1 vCPU (minimum) |
| **GPU** | Not required |
| **Disk** | ~500 MB (app + 1 voice model) |
| **Plan** | Railway Hobby ($5/month) ✅ |

---

## Development Tooling

### pyproject.toml

```toml
[project]
name = "narrator-bot"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    # Web framework
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "python-multipart>=0.0.12",
    "jinja2>=3.1.0",
    "websockets>=13.0",
    
    # Data validation
    "pydantic>=2.9.0",
    "pydantic-settings>=2.5.0",
    
    # Database
    "aiosqlite>=0.20.0",
    
    # Twitch
    "twitchio>=3.0.0",
    
    # HTTP client
    "httpx>=0.27.0",
    
    # TTS
    "piper-tts>=1.2.0",
    
    # LLM clients
    "groq>=0.11.0",
    "openai>=1.50.0",
    "anthropic>=0.34.0",
    
    # Utilities
    "structlog>=24.0.0",
    "tenacity>=9.0.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
elevenlabs = [
    "elevenlabs>=1.0.0",
]

dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=5.0.0",
    "mypy>=1.11.0",
    "ruff>=0.6.0",
    "pre-commit>=3.8.0",
]

[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_ignores = true
disallow_untyped_defs = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = ["piper.*", "twitchio.*", "groq.*"]
ignore_missing_imports = true

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "C4", "UP", "ARG", "SIM", "TCH", "PTH", "RUF"]

[tool.ruff.lint.isort]
known-first-party = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "--cov=src --cov-report=term-missing"
```

### Pre-commit Hooks

```yaml
# .pre-commit-config.yaml

repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.11.0
    hooks:
      - id: mypy
        additional_dependencies:
          - pydantic>=2.9.0
        args: [--strict]
```

### CI Workflow

```yaml
# .github/workflows/ci.yml

name: CI

on: [push, pull_request]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          
      - name: Install dependencies
        run: pip install -e .[dev]
          
      - name: Lint with ruff
        run: ruff check .
        
      - name: Type check with mypy
        run: mypy src
        
      - name: Test with pytest
        run: pytest --cov-fail-under=80
```

---

## Error Handling

### Retry Strategy

```python
# src/providers/base.py

from tenacity import retry, stop_after_attempt, wait_exponential

class BaseProvider:
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True
    )
    async def _call_with_retry(self, func, *args, **kwargs):
        return await func(*args, **kwargs)
```

### Fallback Logic

```python
# src/services/pipeline.py

async def process(self, user: str, message: str) -> NarrationResult:
    try:
        tts_result = await self.tts_provider.synthesize(text)
    except ProviderError:
        if self.fallback_tts:
            logger.warning("Primary TTS failed, using fallback")
            tts_result = await self.fallback_tts.synthesize(text)
        else:
            raise
```

### Error Messages to Overlay

```python
# On error, send friendly message to overlay
await ws.send_json({
    "type": "narration_error",
    "id": narration_id,
    "error": "The narrator's voice fades into silence... (TTS error)"
})
```

---

## Monitoring & Logging

### Structured Logging

```python
# src/main.py

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger()

# Usage
logger.info("narration_complete", 
    user=user, 
    latency_ms=latency,
    tts_provider="piper"
)
```

### Metrics

Track in Web UI dashboard:
- Messages processed (today/total)
- Average latency (LLM + TTS)
- Error rate by provider
- Queue depth over time

---

## Security Considerations

1. **Twitch OAuth tokens**: Store encrypted in SQLite, refresh automatically
2. **API keys**: Environment variables only, never in database or config files
3. **Web UI access**: Consider basic auth or localhost-only for sensitive operations
4. **Input sanitization**: Filter message content before LLM/TTS
5. **Rate limiting**: Prevent abuse via queue limits and cooldowns
6. **SecretStr**: Use Pydantic SecretStr for all sensitive fields

---

## License Information

| Component | License | Commercial Use |
|-----------|---------|----------------|
| **Piper TTS** | MIT | ✅ Allowed |
| **Piper Voices** | Varies | ⚠️ Check each voice |
| `en_US-lessac` | CC BY 4.0 | ✅ With attribution |
| `ru_RU-ruslan` | CC BY 4.0 | ✅ With attribution |
| **TwitchIO** | MIT | ✅ Allowed |
| **FastAPI** | MIT | ✅ Allowed |
| **Groq API** | Proprietary | ✅ Per ToS |

For a non-commercial Twitch bot, all licenses are compatible.

---

## Future Enhancements

- [ ] **Voice Cloning**: ElevenLabs integration for custom voices
- [ ] **Multiple Narrator Styles**: Different voices for different emotions
- [ ] **Sentiment Analysis**: Auto-detect message tone for style selection
- [ ] **Auto-detect Language**: Detect input language automatically
- [ ] **Donation Integration**: Bits, subs trigger special narrations
- [ ] **Sound Effects**: Ambient sounds layered with narration
- [ ] **Mobile-friendly UI**: Responsive web design
- [ ] **Discord Bot**: Variant for Discord servers
- [ ] **Clip Integration**: Auto-clip narrations to Twitch clips
