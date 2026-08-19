"""Tests for the Gemini TTS provider."""

import io
import wave

import pytest
from google.genai import types as genai_types
from pydantic import SecretStr

from src.models.narration import LanguageCode
from src.providers.tts.base import TTSProvider, TTSSettings
from src.providers.tts.gemini import (
    GEMINI_VOICES,
    GeminiTTSProvider,
    GeminiTTSSettings,
)


def _audio_response(
    data: bytes | None = b"\x00\x01" * 16,
    mime_type: str | None = "audio/L16;codec=pcm;rate=24000",
) -> genai_types.GenerateContentResponse:
    """Build a TTS-shaped response carrying one inline audio part.

    Args:
        data: Raw PCM payload, or None to omit the blob.
        mime_type: Mime type of the blob, or None to omit it.

    Returns:
        A response shaped like the SDK returns for audio generation.
    """
    blob = genai_types.Blob(data=data, mime_type=mime_type) if data is not None else None
    return genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(
                    parts=[genai_types.Part(inline_data=blob)],
                    role="model",
                ),
            )
        ],
    )


class TestGeminiTTSConstruction:
    """Construction, validation and protocol compliance."""

    def test_implements_protocol(self, mock_api_key: SecretStr) -> None:
        """Verify GeminiTTSProvider implements TTSProvider protocol."""
        provider = GeminiTTSProvider(api_key=mock_api_key)
        assert isinstance(provider, TTSProvider)

    def test_defaults(self, mock_api_key: SecretStr) -> None:
        """Default model, voice and capability flags."""
        provider = GeminiTTSProvider(api_key=mock_api_key)

        assert provider.name == "gemini"
        assert provider.supports_streaming is False
        assert provider.supports_cloning is False
        assert provider._settings.model == "gemini-2.5-flash-preview-tts"
        assert provider._settings.voice_name == "Charon"

    def test_unknown_default_voice_rejected(self, mock_api_key: SecretStr) -> None:
        """A typo'd voice fails at construction, not on the first redemption."""
        with pytest.raises(ValueError, match="Unknown Gemini TTS voice 'Gandalf'"):
            GeminiTTSProvider(
                api_key=mock_api_key,
                settings=GeminiTTSSettings(voice_name="Gandalf"),
            )

    def test_unknown_override_voice_rejected(self, mock_api_key: SecretStr) -> None:
        """Per-language overrides are validated too, naming the language."""
        with pytest.raises(ValueError, match="override for 'ru'"):
            GeminiTTSProvider(
                api_key=mock_api_key,
                voice_overrides={LanguageCode.RU: "Ruslan"},
            )


class TestGeminiTTSVoiceSelection:
    """Voice resolution across defaults and overrides."""

    def test_default_voice_for_language(self, mock_api_key: SecretStr) -> None:
        """Without an override every language uses the default voice."""
        provider = GeminiTTSProvider(api_key=mock_api_key)

        assert provider.get_voice_for_language(LanguageCode.EN) == "Charon"
        assert provider.get_voice_for_language(LanguageCode.RU) == "Charon"

    def test_override_wins(self, mock_api_key: SecretStr) -> None:
        """A per-language override takes precedence for that language only."""
        provider = GeminiTTSProvider(
            api_key=mock_api_key,
            voice_overrides={LanguageCode.RU: "Sulafat"},
        )

        assert provider.get_voice_for_language(LanguageCode.RU) == "Sulafat"
        assert provider.get_voice_for_language(LanguageCode.EN) == "Charon"

    @pytest.mark.asyncio
    async def test_list_voices(self, mock_api_key: SecretStr) -> None:
        """The catalogue is returned as a copy of the known voices."""
        provider = GeminiTTSProvider(api_key=mock_api_key)
        voices = await provider.list_voices()

        assert len(voices) == len(GEMINI_VOICES)
        assert any(v.id == "Charon" for v in voices)


class TestGeminiTTSAudioHandling:
    """PCM parsing and WAV packaging."""

    def test_parses_sample_rate(self) -> None:
        """The rate parameter is read out of the mime type."""
        rate = GeminiTTSProvider._parse_sample_rate("audio/L16;codec=pcm;rate=24000")
        assert rate == 24000

    def test_missing_rate_raises(self) -> None:
        """A mime type without a rate cannot produce a valid WAV header."""
        with pytest.raises(ValueError, match="no rate parameter"):
            GeminiTTSProvider._parse_sample_rate("audio/L16;codec=pcm")

    def test_unparsable_rate_raises(self) -> None:
        """A non-numeric rate is rejected rather than defaulted."""
        with pytest.raises(ValueError, match="unparsable sample rate"):
            GeminiTTSProvider._parse_sample_rate("audio/L16;codec=pcm;rate=fast")

    def test_pcm_wrapped_in_wav(self) -> None:
        """The WAV wrapper describes mono 16-bit audio at the given rate."""
        pcm = b"\x00\x01" * 100

        wav_bytes = GeminiTTSProvider._pcm_to_wav(pcm, 24000)

        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == 24000
            assert wav.getnframes() == len(pcm) // 2

    def test_extract_audio(self) -> None:
        """The inline blob and its mime type are returned together."""
        data, mime_type = GeminiTTSProvider._extract_audio(_audio_response())

        assert data == b"\x00\x01" * 16
        assert mime_type == "audio/L16;codec=pcm;rate=24000"

    def test_extract_audio_without_mime_type_raises(self) -> None:
        """Audio without a mime type has no discoverable sample rate."""
        with pytest.raises(RuntimeError, match="without a mime type"):
            GeminiTTSProvider._extract_audio(_audio_response(mime_type=None))

    def test_extract_audio_without_parts_raises(self) -> None:
        """A response with no parts reports the finish reason."""
        response = genai_types.GenerateContentResponse(
            candidates=[
                genai_types.Candidate(
                    content=genai_types.Content(parts=[], role="model"),
                    finish_reason=genai_types.FinishReason.SAFETY,
                )
            ],
        )

        with pytest.raises(RuntimeError, match="SAFETY"):
            GeminiTTSProvider._extract_audio(response)

    def test_extract_audio_without_candidates_raises(self) -> None:
        """No candidates means no audio."""
        response = genai_types.GenerateContentResponse(candidates=[])

        with pytest.raises(RuntimeError, match="no candidates"):
            GeminiTTSProvider._extract_audio(response)


class TestGeminiTTSUnsupportedFeatures:
    """Knobs Gemini does not have must fail loudly rather than no-op."""

    def test_update_settings_raises(self, mock_api_key: SecretStr) -> None:
        """Piper's synthesis knobs are rejected with a pointer to the style prompt."""
        provider = GeminiTTSProvider(api_key=mock_api_key)

        with pytest.raises(NotImplementedError, match="style prompt"):
            provider.update_settings(length_scale=1.5)

    @pytest.mark.asyncio
    async def test_non_default_settings_rejected(self, mock_api_key: SecretStr) -> None:
        """A caller asking for a speed change is told it cannot be honoured."""
        provider = GeminiTTSProvider(api_key=mock_api_key)

        with pytest.raises(ValueError, match="does not support speed"):
            await provider.synthesize("hello", settings=TTSSettings(speed=1.5))

    @pytest.mark.asyncio
    async def test_clone_voice_raises(self, mock_api_key: SecretStr) -> None:
        """Voice cloning is not offered by Gemini TTS."""
        provider = GeminiTTSProvider(api_key=mock_api_key)

        with pytest.raises(NotImplementedError, match="voice cloning"):
            await provider.clone_voice("custom", [b"audio"])


class TestGeminiTTSPrompt:
    """Style direction handling."""

    def test_style_prompt_prefixes_text(self, mock_api_key: SecretStr) -> None:
        """The style direction is prepended to the text to speak."""
        provider = GeminiTTSProvider(
            api_key=mock_api_key,
            settings=GeminiTTSSettings(style_prompt="Whisper it"),
        )

        assert provider._build_prompt("the door creaks") == "Whisper it: the door creaks"

    def test_empty_style_prompt_passes_text_through(self, mock_api_key: SecretStr) -> None:
        """An empty style direction leaves the text untouched."""
        provider = GeminiTTSProvider(
            api_key=mock_api_key,
            settings=GeminiTTSSettings(style_prompt="  "),
        )

        assert provider._build_prompt("the door creaks") == "the door creaks"
