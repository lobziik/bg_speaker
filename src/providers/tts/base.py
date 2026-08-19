"""TTS Provider protocol and base types."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from src.models.narration import LanguageCode


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

    async def start(self) -> None:
        """Start any background work the provider needs.

        Called once during application startup, before the first synthesis.
        Implementations must be safe to call multiple times.
        """
        ...

    async def close(self) -> None:
        """Release provider resources (models, HTTP clients, background tasks).

        Called on application shutdown and whenever the active provider is
        swapped at runtime. Implementations must be safe to call multiple times.
        """
        ...

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
        language: "LanguageCode | None" = None,
    ) -> bytes:
        """Synthesize text to audio.

        Args:
            text: Text to synthesize
            voice_id: Voice identifier (optional, uses default)
            settings: TTS settings (optional)
            language: Target language for automatic voice selection (optional)

        Returns:
            Audio bytes (WAV or MP3)
        """
        ...

    def synthesize_stream(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
        language: "LanguageCode | None" = None,
    ) -> AsyncIterator[bytes]:
        """Streaming audio synthesis.

        Note: Implementations should be async generators (async def with yield).
        The return type is AsyncIterator to match async generator behavior.

        Args:
            text: Text to synthesize
            voice_id: Voice identifier (optional, uses default)
            settings: TTS settings (optional)
            language: Target language for automatic voice selection (optional)

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

    def update_settings(
        self,
        *,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
    ) -> None:
        """Update TTS synthesis settings at runtime.

        Provider implementations may support different subsets of these parameters.

        Args:
            length_scale: Speech speed (0.5=fast, 1.0=normal, 2.0=slow).
            noise_scale: Pronunciation variation (0=monotone, 1=varied).
            noise_w: Phoneme duration variation (0=consistent, 1=varied).
        """
        ...
