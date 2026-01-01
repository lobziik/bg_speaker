"""Piper TTS provider implementation."""

import asyncio
import io
import wave
from collections.abc import AsyncIterator
from pathlib import Path

import structlog

from src.core.types import StrictModel
from src.providers.tts.base import TTSProvider, TTSSettings, Voice

logger = structlog.get_logger()


class PiperSettings(StrictModel):
    """Piper TTS configuration."""

    voice: str = "en_US-lessac-medium"  # Voice model name
    model_path: Path | None = None  # Custom model path (optional)
    speaker_id: int | None = None  # For multi-speaker models
    length_scale: float = 1.0  # Speed: <1 faster, >1 slower
    noise_scale: float = 0.667  # Variation in pronunciation
    noise_w: float = 0.8  # Variation in phoneme duration


# Default voices available in Piper
DEFAULT_VOICES = [
    Voice(
        id="en_US-lessac-medium",
        name="Lessac (US English) - Narrator style",
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
        name="Ryan (US English) - Male",
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


class PiperTTSProvider:
    """Piper TTS - Fast, local, MIT licensed neural TTS.

    Features:
    - CPU-friendly (3-11x realtime on modern CPU)
    - Small models (~60-100MB per voice)
    - 30+ languages, 100+ voices
    - No voice cloning (uses pre-trained voices)

    Voices: https://rhasspy.github.io/piper-samples/
    """

    def __init__(self, settings: PiperSettings | None = None) -> None:
        """Initialize Piper TTS provider.

        Args:
            settings: Piper-specific settings
        """
        self._settings = settings or PiperSettings()
        self._voice: object | None = None  # PiperVoice, lazy loaded
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Provider display name."""
        return "piper"

    @property
    def supports_streaming(self) -> bool:
        """Piper generates full audio at once."""
        return False

    @property
    def supports_cloning(self) -> bool:
        """Piper doesn't support voice cloning."""
        return False

    async def _ensure_loaded(self) -> object:
        """Lazy load voice model."""
        if self._voice is None:
            async with self._lock:
                if self._voice is None:
                    loop = asyncio.get_event_loop()
                    self._voice = await loop.run_in_executor(None, self._load_voice)
        return self._voice

    def _load_voice(self) -> object:
        """Load Piper voice model (sync).

        Returns:
            PiperVoice instance
        """
        # Import here to allow the module to load even without piper installed
        from piper import PiperVoice

        if self._settings.model_path:
            return PiperVoice.load(str(self._settings.model_path))
        else:
            # Auto-download from Hugging Face
            return PiperVoice.load(self._settings.voice)

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,  # noqa: ARG002
        settings: TTSSettings | None = None,
    ) -> bytes:
        """Synthesize text to WAV audio.

        Args:
            text: Text to synthesize
            voice_id: Voice identifier (ignored, uses configured voice)
            settings: TTS settings

        Returns:
            WAV audio bytes
        """
        voice = await self._ensure_loaded()

        # Map generic settings to Piper-specific
        length_scale = self._settings.length_scale
        if settings and settings.speed != 1.0:
            length_scale = 1.0 / settings.speed

        loop = asyncio.get_event_loop()
        audio_bytes = await loop.run_in_executor(
            None, lambda: self._synthesize_sync(voice, text, length_scale)
        )

        logger.debug(
            "piper_synthesis_complete",
            text_length=len(text),
            audio_size=len(audio_bytes),
            voice=self._settings.voice,
        )

        return audio_bytes

    def _synthesize_sync(
        self,
        voice: object,
        text: str,
        length_scale: float,
    ) -> bytes:
        """Synchronous synthesis.

        Args:
            voice: PiperVoice instance
            text: Text to synthesize
            length_scale: Speech speed

        Returns:
            WAV audio bytes
        """
        # Type assertion for the voice object
        from piper import PiperVoice  # noqa: TC002

        piper_voice: PiperVoice = voice  # type: ignore[assignment]

        audio_stream = piper_voice.synthesize_stream_raw(
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
            wav.setframerate(piper_voice.config.sample_rate)
            wav.writeframes(raw_audio)

        return buffer.getvalue()

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
    ) -> AsyncIterator[bytes]:
        """Streaming not supported - yields full audio."""
        audio = await self.synthesize(text, voice_id, settings)
        yield audio

    async def list_voices(self) -> list[Voice]:
        """List recommended Piper voices."""
        return DEFAULT_VOICES.copy()

    async def clone_voice(
        self,
        name: str,
        audio_files: list[bytes],
    ) -> Voice:
        """Voice cloning not supported by Piper."""
        raise NotImplementedError(
            "Piper does not support voice cloning. Use ElevenLabs provider for voice cloning."
        )

    async def health_check(self) -> bool:
        """Check if Piper is available."""
        try:
            await self._ensure_loaded()
            return True
        except ImportError:
            logger.warning("piper_not_installed")
            return False
        except FileNotFoundError as e:
            logger.warning("piper_model_not_found", error=str(e))
            return False

    def get_settings_schema(self) -> dict[str, object]:
        """JSON Schema for UI."""
        return {
            "type": "object",
            "properties": {
                "voice": {
                    "type": "string",
                    "title": "Voice Model",
                    "description": "Piper voice model name",
                    "default": "en_US-lessac-medium",
                    "enum": [v.id for v in DEFAULT_VOICES],
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


# Type assertion to verify protocol compliance
def _verify_protocol() -> None:
    """Verify PiperTTSProvider implements TTSProvider protocol."""
    provider: TTSProvider = PiperTTSProvider()  # noqa: F841
