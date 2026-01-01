"""TTS Provider protocol and base types."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Voice:
    """TTS voice information."""

    id: str
    name: str
    language: str
    preview_url: str | None = None


@dataclass(frozen=True)
class TTSSettings:
    """Common TTS settings."""

    speed: float = 1.0  # 0.5 - 2.0
    pitch: float = 1.0  # 0.5 - 2.0 (provider-specific)
    stability: float = 0.5  # Provider-specific
    similarity: float = 0.75  # Provider-specific


@runtime_checkable
class TTSProvider(Protocol):
    """Protocol for TTS providers.

    All TTS providers must implement this interface to be used
    in the narration pipeline.
    """

    @property
    def name(self) -> str:
        """Provider display name."""
        ...

    @property
    def supports_streaming(self) -> bool:
        """Whether provider supports audio streaming."""
        ...

    @property
    def supports_cloning(self) -> bool:
        """Whether provider supports voice cloning."""
        ...

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
    ) -> bytes:
        """Synthesize text to audio.

        Args:
            text: Text to synthesize
            voice_id: Voice identifier (optional, uses default)
            settings: TTS settings (optional)

        Returns:
            Audio bytes (WAV or MP3)
        """
        ...

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
    ) -> AsyncIterator[bytes]:
        """Streaming audio synthesis.

        Yields:
            Audio chunks as they're generated
        """
        ...

    async def list_voices(self) -> list[Voice]:
        """List available voices."""
        ...

    async def clone_voice(
        self,
        name: str,
        audio_files: list[bytes],
    ) -> Voice:
        """Clone a voice from audio samples (if supported)."""
        ...

    async def health_check(self) -> bool:
        """Check if provider is available."""
        ...

    def get_settings_schema(self) -> dict[str, object]:
        """JSON Schema for provider settings."""
        ...
