"""Narration request and result models."""

from enum import StrEnum

from pydantic import Field

from src.core.types import StrictModel


class NarratorStyle(StrEnum):
    """Available narrator styles for message formatting."""

    WHISPER = "whisper"
    PROCLAIM = "proclaim"
    MOCK = "mock"
    DEFAULT = "default"


class LanguageCode(StrEnum):
    """Supported language codes."""

    RU = "ru"
    EN = "en"
    DE = "de"
    FR = "fr"
    ES = "es"


class NarrationRequest(StrictModel):
    """Request for narration processing."""

    user: str = Field(min_length=1, max_length=50)
    message: str = Field(min_length=1, max_length=300)
    style: NarratorStyle = NarratorStyle.DEFAULT
    source_lang: LanguageCode = LanguageCode.RU


class NarrationResult(StrictModel):
    """Result of narration processing."""

    id: str
    user: str
    text_original: str  # Original formatted text
    text_translated: str  # Translated (or same if no translation needed)
    target_lang: LanguageCode
    audio_data: bytes
    duration_ms: int
    was_translated: bool  # False if source == target language
