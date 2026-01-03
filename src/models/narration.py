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


class NarrationRequest(StrictModel):
    """Request for narration processing.

    Attributes:
        user: Twitch username.
        message: Original chat message.
        style: Narrator style to apply.
    """

    user: str = Field(min_length=1, max_length=50)
    message: str = Field(min_length=1, max_length=300)
    style: NarratorStyle = NarratorStyle.DEFAULT


class NarrationResult(StrictModel):
    """Result of narration processing.

    Attributes:
        id: Unique narration ID.
        user: Username who triggered narration.
        voice_text: Text used for TTS synthesis (in narrator_lang).
        subtitle_text: Text for subtitles (in subtitle_lang).
        target_lang: Language used for TTS.
        audio_data: Synthesized audio bytes.
        duration_ms: Audio duration in milliseconds.
    """

    id: str
    user: str
    voice_text: str
    subtitle_text: str
    target_lang: LanguageCode
    audio_data: bytes
    duration_ms: int
