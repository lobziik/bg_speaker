"""Settings models for the narrator bot."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, SecretStr

from src.core.types import StrictModel
from src.models.narration import LanguageCode, NarratorStyle


class ProviderType(StrEnum):
    """Types of providers available."""

    LLM = "llm"
    TTS = "tts"
    TRANSLATE = "translate"


# === Global Settings ===


class LanguageSettings(StrictModel):
    """Language configuration for narrator."""

    source_lang: LanguageCode = LanguageCode.RU  # Chat message language
    narrator_lang: LanguageCode = LanguageCode.EN  # TTS output language
    subtitle_lang: LanguageCode = LanguageCode.RU  # Overlay subtitle language
    auto_translate: bool = True  # Enable translation

    @property
    def needs_translation(self) -> bool:
        """Check if translation is needed based on settings."""
        return self.auto_translate and self.source_lang != self.narrator_lang


class NarratorSettings(StrictModel):
    """Narrator behavior settings."""

    default_style: NarratorStyle = NarratorStyle.DEFAULT
    system_prompt: str = Field(default="")
    bypass_llm: bool = False  # Skip LLM, read message as-is


class QueueSettings(StrictModel):
    """Message queue settings."""

    max_size: int = Field(default=50, ge=1, le=200)
    message_min_length: int = Field(default=1, ge=1)
    message_max_length: int = Field(default=300, ge=10, le=500)
    cooldown_seconds: int = Field(default=5, ge=0)
    tts_rate_limit_seconds: int = Field(default=10, ge=1)
    priority_users: list[str] = Field(default_factory=list)


class OverlaySettings(StrictModel):
    """OBS overlay settings."""

    font_family: str = "IM Fell English SC"
    font_size: int = Field(default=28, ge=12, le=72)
    text_color: str = "#F4E4BC"
    background_color: str = "rgba(20, 15, 10, 0.85)"
    animation_duration_ms: int = Field(default=500, ge=0)
    position: Literal["top", "center", "bottom"] = "bottom"


class TwitchRewardSettings(StrictModel):
    """Twitch Channel Points reward configuration."""

    title: str = "Narrator TTS"
    cost: int = Field(default=500, ge=1, le=1_000_000)
    prompt: str = "Enter your message for the narrator"
    background_color: str = Field(default="#6441A4", pattern=r"^#[0-9A-Fa-f]{6}$")
    sync_cooldown_with_rate_limit: bool = True
    refund_on_queue_full: bool = True
    refund_on_filtered: bool = True
    refund_on_banned_user: bool = False
    global_cooldown_seconds: int = Field(
        default=300,
        ge=0,
        le=3600,
        description="Global cooldown after each narration (pauses Twitch reward for all users)",
    )


class AppSettings(StrictModel):
    """Root settings object combining all settings."""

    language: LanguageSettings = Field(default_factory=LanguageSettings)
    narrator: NarratorSettings = Field(default_factory=NarratorSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    overlay: OverlaySettings = Field(default_factory=OverlaySettings)
    reward: TwitchRewardSettings = Field(default_factory=TwitchRewardSettings)


# === Provider Settings ===


class GroqSettings(StrictModel):
    """Groq LLM provider settings."""

    api_key: SecretStr
    model: str = "llama-3.3-70b-versatile"
    temperature: float = Field(default=0.8, ge=0, le=2)
    max_tokens: int = Field(default=500, ge=50, le=2000)


class OpenAISettings(StrictModel):
    """OpenAI LLM provider settings."""

    api_key: SecretStr
    model: str = "gpt-4o-mini"
    temperature: float = Field(default=0.8, ge=0, le=2)


class PiperSettings(StrictModel):
    """Piper TTS provider settings."""

    voice: str = "en_US-lessac-medium"
    length_scale: float = Field(default=1.0, ge=0.5, le=2.0)
    noise_scale: float = Field(default=0.667, ge=0.0, le=1.0)
    noise_w: float = Field(default=0.8, ge=0.0, le=1.0)


class ElevenLabsSettings(StrictModel):
    """ElevenLabs TTS provider settings."""

    api_key: SecretStr
    voice_id: str
    model_id: str = "eleven_multilingual_v2"
    stability: float = Field(default=0.5, ge=0, le=1)
    similarity_boost: float = Field(default=0.75, ge=0, le=1)
