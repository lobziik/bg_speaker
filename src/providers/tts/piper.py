"""Piper TTS provider implementation."""

import asyncio
import io
import sys
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.request import urlopen

import structlog

from src.core.ttl_cache import TTLCache
from src.core.types import StrictModel
from src.models.narration import LanguageCode
from src.providers.tts.base import TTSProvider, TTSSettings, Voice

logger = structlog.get_logger()


# Default voice for each supported language.
# Users can override these via TTSVoiceSettings.
LANGUAGE_DEFAULT_VOICES: dict[LanguageCode, str] = {
    LanguageCode.EN: "en_US-lessac-medium",
    LanguageCode.RU: "ru_RU-ruslan-medium"
}


# Piper voice model download configuration
PIPER_VOICES_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def get_piper_cache_dir() -> Path:
    """Get the Piper model cache directory.

    Returns:
        Path to cache directory (created if doesn't exist)
    """
    # Store cache in data/ folder next to the database
    cache_dir = Path("data/piper-tts")
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def parse_voice_name(voice: str) -> dict[str, str]:
    """Parse Piper voice name into components.

    Args:
        voice: Voice name like "en_US-lessac-medium"

    Returns:
        Dictionary with lang_family, lang_code, voice_name, quality

    Raises:
        ValueError: If voice name format is invalid
    """
    parts = voice.split("-")
    if len(parts) != 3:
        raise ValueError(
            f"Invalid voice name format: '{voice}'. Expected format: 'lang_code-name-quality' "
            f"(e.g., 'en_US-lessac-medium')"
        )

    lang_code = parts[0]  # e.g., "en_US"
    voice_name = parts[1]  # e.g., "lessac"
    quality = parts[2]  # e.g., "medium"

    if "_" not in lang_code:
        raise ValueError(
            f"Invalid language code format: '{lang_code}'. Expected format: 'xx_XX' "
            f"(e.g., 'en_US')"
        )

    lang_family = lang_code.split("_")[0]  # e.g., "en"

    return {
        "lang_family": lang_family,
        "lang_code": lang_code,
        "voice_name": voice_name,
        "quality": quality,
    }


def get_voice_urls(voice: str) -> tuple[str, str]:
    """Get download URLs for voice model and config.

    Args:
        voice: Voice name like "en_US-lessac-medium"

    Returns:
        Tuple of (model_url, config_url)
    """
    parts = parse_voice_name(voice)
    base_path = (
        f"{PIPER_VOICES_BASE_URL}/{parts['lang_family']}/{parts['lang_code']}/"
        f"{parts['voice_name']}/{parts['quality']}"
    )
    filename = f"{parts['lang_code']}-{parts['voice_name']}-{parts['quality']}"

    model_url = f"{base_path}/{filename}.onnx?download=true"
    config_url = f"{base_path}/{filename}.onnx.json?download=true"

    return model_url, config_url


def download_with_progress(url: str, dest_path: Path, description: str) -> None:
    """Download a file with progress output.

    Args:
        url: URL to download
        dest_path: Destination file path
        description: Description for progress display
    """
    with urlopen(url) as response:
        total_size = response.headers.get("Content-Length")
        total_size_int = int(total_size) if total_size else None

        if total_size_int:
            total_mb = total_size_int / (1024 * 1024)
            print(f"Downloading {description} ({total_mb:.1f} MB)...")
        else:
            print(f"Downloading {description}...")

        downloaded = 0
        chunk_size = 8192
        last_percent = -1

        with dest_path.open("wb") as f:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)

                if total_size_int:
                    percent = int((downloaded / total_size_int) * 100)
                    if percent != last_percent and percent % 10 == 0:
                        downloaded_mb = downloaded / (1024 * 1024)
                        sys.stdout.write(f"\r  Progress: {percent}% ({downloaded_mb:.1f} MB)")
                        sys.stdout.flush()
                        last_percent = percent

        if total_size_int:
            print()  # Newline after progress


def ensure_voice_downloaded(voice: str) -> Path:
    """Ensure voice model is downloaded, downloading if necessary.

    Args:
        voice: Voice name like "en_US-lessac-medium"

    Returns:
        Path to the downloaded .onnx model file

    Raises:
        ValueError: If voice name format is invalid
        ConnectionError: If download fails
    """
    cache_dir = get_piper_cache_dir()
    parts = parse_voice_name(voice)
    filename = f"{parts['lang_code']}-{parts['voice_name']}-{parts['quality']}"

    model_path = cache_dir / f"{filename}.onnx"
    config_path = cache_dir / f"{filename}.onnx.json"

    # Check if already downloaded
    if model_path.exists() and config_path.exists():
        logger.info(
            "piper_voice_cached",
            voice=voice,
            model_path=str(model_path),
        )
        return model_path

    # Download with progress
    logger.info(
        "piper_voice_download_starting",
        voice=voice,
        cache_dir=str(cache_dir),
    )
    print(f"\nFirst-time setup: downloading Piper voice model '{voice}'")
    print(f"Models will be stored in: {cache_dir}\n")

    model_url, config_url = get_voice_urls(voice)

    try:
        # Download config first (small file)
        download_with_progress(config_url, config_path, f"{filename}.onnx.json")

        # Download model (large file)
        download_with_progress(model_url, model_path, f"{filename}.onnx")

    except Exception as e:
        # Clean up partial downloads
        if config_path.exists():
            config_path.unlink()
        if model_path.exists():
            model_path.unlink()
        raise ConnectionError(f"Failed to download voice model '{voice}': {e}") from e

    logger.info(
        "piper_voice_downloaded",
        voice=voice,
        model_path=str(model_path),
    )
    print(f"\nVoice model downloaded successfully to: {model_path}\n")

    return model_path


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
    )
]


class PiperTTSProvider:
    """Piper TTS - Fast, local, MIT licensed neural TTS.

    Features:
    - CPU-friendly (3-11x realtime on modern CPU)
    - Small models (~60-100MB per voice)
    - 30+ languages, 100+ voices
    - No voice cloning (uses pre-trained voices)
    - Dynamic voice selection based on language
    - TTL-based automatic voice model cleanup after inactivity

    Voices: https://rhasspy.github.io/piper-samples/
    """

    # Default voice model TTL: 30 minutes
    DEFAULT_VOICE_TTL_SECONDS: float = 1800.0
    # Cleanup check interval: 1 minute
    DEFAULT_CLEANUP_INTERVAL_SECONDS: float = 60.0

    def __init__(
        self,
        settings: PiperSettings | None = None,
        voice_overrides: dict[LanguageCode, str] | None = None,
        voice_ttl_seconds: float | None = None,
        cleanup_interval_seconds: float | None = None,
    ) -> None:
        """Initialize Piper TTS provider.

        Args:
            settings: Piper-specific settings (used as fallback for non-language calls)
            voice_overrides: Per-language voice overrides from user settings
            voice_ttl_seconds: TTL for cached voice models in seconds.
                Models not used within this time will be unloaded.
                Defaults to 30 minutes.
            cleanup_interval_seconds: How often to check for expired voice models.
                Defaults to 1 minute.
        """
        self._settings = settings or PiperSettings()
        self._voice_overrides = voice_overrides or {}
        self._voice_ttl = voice_ttl_seconds or self.DEFAULT_VOICE_TTL_SECONDS
        self._cleanup_interval = cleanup_interval_seconds or self.DEFAULT_CLEANUP_INTERVAL_SECONDS

        # TTL cache for voice models with cleanup callback
        self._voices: TTLCache[object] = TTLCache(
            ttl_seconds=self._voice_ttl,
            cleanup_interval_seconds=self._cleanup_interval,
            on_evict=self._on_voice_evicted,
            name="piper_voices",
        )
        self._lock = asyncio.Lock()
        self._started = False

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

    def _on_voice_evicted(self, voice_id: str, _voice: object) -> None:
        """Callback when voice model is evicted from cache.

        Called by TTLCache when a voice model expires due to inactivity.

        Args:
            voice_id: The voice ID being evicted (e.g., "en_US-lessac-medium").
            _voice: The PiperVoice instance being unloaded (unused, kept for callback signature).
        """
        logger.info(
            "piper_voice_unloaded",
            voice=voice_id,
            reason="ttl_expired",
        )
        # Note: PiperVoice doesn't have explicit close() method.
        # Python GC will handle onnxruntime session cleanup when dereferenced.

    async def start(self) -> None:
        """Start the background cleanup task for voice model recycling.

        Should be called after provider creation, before first use.
        Safe to call multiple times.
        """
        if self._started:
            logger.debug("piper_provider_already_started")
            return

        await self._voices.start()
        self._started = True
        logger.info(
            "piper_provider_started",
            voice_ttl_seconds=self._voice_ttl,
            cleanup_interval_seconds=self._cleanup_interval,
        )

    async def close(self) -> None:
        """Stop cleanup task and unload all cached voice models.

        Should be called on application shutdown to free memory.
        Safe to call multiple times.
        """
        if not self._started:
            logger.debug("piper_provider_already_stopped")
            return

        await self._voices.stop()
        cleared_count = await self._voices.clear()
        self._started = False
        logger.info(
            "piper_provider_closed",
            voices_unloaded=cleared_count,
        )

    def update_settings(
        self,
        *,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
    ) -> None:
        """Update TTS synthesis settings at runtime.

        Updates the internal settings without recreating the provider.
        Changed settings will apply to subsequent synthesize() calls.

        Args:
            length_scale: Speech speed (0.5=fast, 1.0=normal, 2.0=slow).
            noise_scale: Pronunciation variation (0=monotone, 1=varied).
            noise_w: Phoneme duration variation (0=consistent, 1=varied).
        """
        self._settings = PiperSettings(
            voice=self._settings.voice,
            model_path=self._settings.model_path,
            speaker_id=self._settings.speaker_id,
            length_scale=length_scale if length_scale is not None else self._settings.length_scale,
            noise_scale=noise_scale if noise_scale is not None else self._settings.noise_scale,
            noise_w=noise_w if noise_w is not None else self._settings.noise_w,
        )
        logger.info(
            "piper_settings_updated",
            length_scale=self._settings.length_scale,
            noise_scale=self._settings.noise_scale,
            noise_w=self._settings.noise_w,
        )

    def get_voice_for_language(self, lang: LanguageCode) -> str:
        """Get the voice ID for a given language.

        Checks user overrides first, then falls back to defaults.

        Args:
            lang: Target language code

        Returns:
            Piper voice ID (e.g., "en_US-lessac-medium")

        Raises:
            ValueError: If no voice is configured for the language
        """
        # Check user override first
        if lang in self._voice_overrides:
            return self._voice_overrides[lang]

        # Fall back to default
        if lang not in LANGUAGE_DEFAULT_VOICES:
            supported = [code.value for code in LANGUAGE_DEFAULT_VOICES]
            raise ValueError(
                f"No default voice configured for language '{lang.value}'. "
                f"Supported languages: {supported}"
            )
        return LANGUAGE_DEFAULT_VOICES[lang]

    def get_voices_for_language(self, lang: LanguageCode) -> list[Voice]:
        """Get available voices for a specific language.

        Args:
            lang: Language code to filter by

        Returns:
            List of voices matching the language
        """
        return [v for v in DEFAULT_VOICES if v.language == lang.value]

    async def _ensure_voice_loaded(self, voice_id: str) -> object:
        """Ensure a specific voice model is loaded, loading if necessary.

        Uses TTLCache for automatic cleanup of unused voice models.
        Double-checked locking pattern for thread safety.

        Args:
            voice_id: Voice ID to load (e.g., "en_US-lessac-medium")

        Returns:
            PiperVoice instance
        """
        # Check cache first (also updates access time for TTL)
        voice = await self._voices.get(voice_id)
        if voice is not None:
            return voice

        async with self._lock:
            # Double-check after acquiring lock
            voice = await self._voices.get(voice_id)
            if voice is not None:
                return voice

            # Load voice in executor (blocking I/O)
            loop = asyncio.get_event_loop()
            voice = await loop.run_in_executor(None, self._load_voice, voice_id)
            await self._voices.set(voice_id, voice)
            return voice

    async def _ensure_loaded(self) -> object:
        """Lazy load default voice model (backward compatibility).

        Uses the voice from settings as the default.
        """
        return await self._ensure_voice_loaded(self._settings.voice)

    def _load_voice(self, voice_id: str) -> object:
        """Load Piper voice model (sync).

        Args:
            voice_id: Voice ID to load (e.g., "en_US-lessac-medium")

        Returns:
            PiperVoice instance
        """
        # Import here to allow the module to load even without piper installed
        from piper import PiperVoice

        # Ensure model is downloaded (with progress), then get the path
        model_path = ensure_voice_downloaded(voice_id)
        logger.info(
            "piper_voice_loading",
            voice=voice_id,
            model_path=str(model_path),
        )

        voice = PiperVoice.load(str(model_path))

        logger.info(
            "piper_voice_loaded",
            voice=voice_id,
            model_path=str(model_path),
        )
        return voice

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
        language: LanguageCode | None = None,
    ) -> bytes:
        """Synthesize text to WAV audio.

        Args:
            text: Text to synthesize
            voice_id: Explicit voice identifier (takes precedence)
            settings: TTS settings
            language: Target language for automatic voice selection

        Returns:
            WAV audio bytes

        Raises:
            ValueError: If language has no configured voice
        """
        # Determine which voice to use (priority: voice_id > language > default)
        if voice_id:
            selected_voice_id = voice_id
        elif language:
            selected_voice_id = self.get_voice_for_language(language)
        else:
            selected_voice_id = self._settings.voice

        logger.debug(
            "piper_synthesis_start",
            text_length=len(text),
            voice=selected_voice_id,
            language=language.value if language else None,
        )

        voice = await self._ensure_voice_loaded(selected_voice_id)

        # Map generic settings to Piper-specific
        length_scale = self._settings.length_scale
        if settings and settings.speed != 1.0:
            length_scale = 1.0 / settings.speed

        loop = asyncio.get_event_loop()
        try:
            audio_bytes = await loop.run_in_executor(
                None, lambda: self._synthesize_sync(voice, text, length_scale)
            )
        except Exception as e:
            logger.error(
                "piper_synthesis_failed",
                text_length=len(text),
                voice=selected_voice_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

        logger.debug(
            "piper_synthesis_complete",
            text_length=len(text),
            audio_size=len(audio_bytes),
            voice=selected_voice_id,
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
        from piper.config import SynthesisConfig

        piper_voice: PiperVoice = voice  # type: ignore[assignment]

        # Create synthesis config
        syn_config = SynthesisConfig(
            speaker_id=self._settings.speaker_id,
            length_scale=length_scale,
            noise_scale=self._settings.noise_scale,
            noise_w_scale=self._settings.noise_w,
        )

        # Synthesize and collect audio chunks
        audio_chunks: list[bytes] = []
        for chunk in piper_voice.synthesize(text, syn_config):
            audio_chunks.append(chunk.audio_int16_bytes)

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
        language: LanguageCode | None = None,
    ) -> AsyncIterator[bytes]:
        """Streaming not supported - yields full audio."""
        audio = await self.synthesize(text, voice_id, settings, language)
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
