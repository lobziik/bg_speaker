"""Settings models for the narrator bot."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator

from src.core.types import StrictModel
from src.models.narration import LanguageCode, NarratorStyle

# === Global Settings ===


class LanguageSettings(StrictModel):
    """Language configuration for narrator.

    Attributes:
        narrator_lang: Language for TTS voice output.
        subtitle_lang: Language for overlay subtitles.
    """

    narrator_lang: LanguageCode = LanguageCode.EN
    subtitle_lang: LanguageCode = LanguageCode.RU

    @field_validator("narrator_lang", "subtitle_lang", mode="before")
    @classmethod
    def convert_string_to_language_code(cls, value: str | LanguageCode) -> LanguageCode:
        """Convert string to LanguageCode enum for JSON deserialization."""
        if isinstance(value, str):
            return LanguageCode(value)
        return value


class NarratorSettings(StrictModel):
    """Narrator behavior settings.

    Attributes:
        default_style: Default narration style when not specified.
        system_prompt: Custom prompt to add narrator personality/behavior.
        bypass_llm: If True, skip LLM formatting and use raw message.
        auto_translate: When True and languages differ, LLM produces
            voice_text in narrator_lang and subtitle_text in subtitle_lang.
        enable_moderation: When True, validate messages for Twitch policy
            compliance before narration. Rejected messages consume points
            without refund.
    """

    default_style: NarratorStyle = NarratorStyle.DEFAULT
    system_prompt: str = Field(default="")
    bypass_llm: bool = False
    auto_translate: bool = True
    enable_moderation: bool = True

    @field_validator("default_style", mode="before")
    @classmethod
    def convert_string_to_narrator_style(cls, value: str | NarratorStyle) -> NarratorStyle:
        """Convert string to NarratorStyle enum for JSON deserialization."""
        if isinstance(value, str):
            return NarratorStyle(value)
        return value


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


class TTSVoiceSettings(StrictModel):
    """TTS voice settings with per-language overrides.

    Allows users to customize which Piper voice is used for each language.
    If a language is not specified in overrides, the default voice for that
    language is used (defined in LANGUAGE_DEFAULT_VOICES in piper.py).
    """

    voice_overrides: dict[LanguageCode, str] = Field(
        default_factory=dict,
        description="Custom voice ID per language (overrides defaults)",
    )

    @field_validator("voice_overrides", mode="before")
    @classmethod
    def convert_string_keys_to_enum(
        cls, value: dict[str | LanguageCode, str]
    ) -> dict[LanguageCode, str]:
        """Convert string keys to LanguageCode enum for JSON deserialization."""
        if not isinstance(value, dict):
            return value  # ty: ignore[invalid-return-type]
        return {LanguageCode(k) if isinstance(k, str) else k: v for k, v in value.items()}


# === Provider Settings ===


class LLMProviderName(StrEnum):
    """LLM providers with a working implementation."""

    GROQ = "groq"
    GEMINI = "gemini"


class TTSProviderName(StrEnum):
    """TTS providers with a working implementation."""

    PIPER = "piper"
    GEMINI = "gemini"


class GeminiSafetyThreshold(StrEnum):
    """Gemini safety filter strictness levels.

    Mirrors ``google.genai.types.HarmBlockThreshold``. BLOCK_ONLY_HIGH is the
    project default: fantasy violence passes while high-severity content is
    still blocked, and the bot's own moderation step enforces Twitch policy.
    """

    BLOCK_LOW_AND_ABOVE = "BLOCK_LOW_AND_ABOVE"
    BLOCK_MEDIUM_AND_ABOVE = "BLOCK_MEDIUM_AND_ABOVE"
    BLOCK_ONLY_HIGH = "BLOCK_ONLY_HIGH"
    BLOCK_NONE = "BLOCK_NONE"


class ProviderSettings(StrictModel):
    """Which provider implementation is active for each pipeline stage.

    Stored under the ``providers`` settings key. Changing it rebuilds the
    narration pipeline without restarting the app.

    Attributes:
        llm: Active LLM provider for narration and moderation.
        tts: Active TTS provider for speech synthesis.
    """

    llm: LLMProviderName = LLMProviderName.GROQ
    tts: TTSProviderName = TTSProviderName.PIPER

    @field_validator("llm", mode="before")
    @classmethod
    def convert_string_to_llm_provider(cls, value: str | LLMProviderName) -> LLMProviderName:
        """Convert string to LLMProviderName enum for JSON deserialization."""
        if isinstance(value, str):
            return LLMProviderName(value)
        return value

    @field_validator("tts", mode="before")
    @classmethod
    def convert_string_to_tts_provider(cls, value: str | TTSProviderName) -> TTSProviderName:
        """Convert string to TTSProviderName enum for JSON deserialization."""
        if isinstance(value, str):
            return TTSProviderName(value)
        return value


class GroqLLMSettings(StrictModel):
    """Groq LLM provider settings.

    The API key is intentionally absent - secrets live in environment
    variables only (``GROQ_API_KEY``), never in the database.
    """

    model: str = "llama-3.3-70b-versatile"
    temperature: float = Field(default=0.8, ge=0, le=2)
    max_tokens: int = Field(default=500, ge=50, le=2000)


class GeminiLLMSettings(StrictModel):
    """Gemini LLM provider settings.

    The API key is intentionally absent - secrets live in environment
    variables only (``GEMINI_API_KEY``), never in the database.

    Attributes:
        model: Gemini model ID.
        temperature: Sampling temperature.
        max_output_tokens: Response length cap.
        thinking_budget: Thinking token budget. 0 disables thinking for the
            lowest latency (2.5 Flash / Flash-Lite only), -1 lets the model
            decide, None uses the model default.
        safety_threshold: Gemini safety filter strictness.
    """

    model: str = "gemini-2.5-flash"
    temperature: float = Field(default=0.8, ge=0, le=2)
    max_output_tokens: int = Field(default=500, ge=50, le=2000)
    thinking_budget: int | None = Field(default=0, ge=-1, le=24576)
    safety_threshold: GeminiSafetyThreshold = GeminiSafetyThreshold.BLOCK_ONLY_HIGH

    @field_validator("safety_threshold", mode="before")
    @classmethod
    def convert_string_to_safety_threshold(
        cls, value: str | GeminiSafetyThreshold
    ) -> GeminiSafetyThreshold:
        """Convert string to GeminiSafetyThreshold enum for JSON deserialization."""
        if isinstance(value, str):
            return GeminiSafetyThreshold(value)
        return value


class PiperSettings(StrictModel):
    """Piper TTS provider settings."""

    voice: str = "en_US-lessac-medium"
    length_scale: float = Field(default=1.0, ge=0.5, le=2.0)
    noise_scale: float = Field(default=0.667, ge=0.0, le=1.0)
    noise_w: float = Field(default=0.8, ge=0.0, le=1.0)
