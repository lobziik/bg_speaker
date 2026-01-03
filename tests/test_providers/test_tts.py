"""Tests for TTS providers."""

from unittest.mock import MagicMock, patch

import pytest

from src.models.narration import LanguageCode
from src.providers.tts.base import TTSProvider, TTSSettings, Voice
from src.providers.tts.piper import (
    DEFAULT_VOICES,
    LANGUAGE_DEFAULT_VOICES,
    PiperSettings,
    PiperTTSProvider,
)


class TestTTSProviderProtocol:
    """Test TTS provider protocol compliance."""

    def test_piper_implements_protocol(self) -> None:
        """Verify PiperTTSProvider implements TTSProvider protocol."""
        provider = PiperTTSProvider()
        assert isinstance(provider, TTSProvider)

    def test_voice_dataclass(self) -> None:
        """Test Voice dataclass."""
        voice = Voice(
            id="test-voice",
            name="Test Voice",
            language="en",
            preview_url="https://example.com/preview.mp3",
        )
        assert voice.id == "test-voice"
        assert voice.name == "Test Voice"
        assert voice.language == "en"
        assert voice.preview_url == "https://example.com/preview.mp3"

    def test_voice_without_preview(self) -> None:
        """Test Voice dataclass without preview URL."""
        voice = Voice(
            id="test-voice",
            name="Test Voice",
            language="en",
        )
        assert voice.preview_url is None

    def test_tts_settings_defaults(self) -> None:
        """Test TTSSettings default values."""
        settings = TTSSettings()
        assert settings.speed == 1.0
        assert settings.pitch == 1.0
        assert settings.stability == 0.5
        assert settings.similarity == 0.75


class TestPiperSettings:
    """Tests for Piper settings model."""

    def test_default_settings(self) -> None:
        """Test default Piper settings."""
        settings = PiperSettings()
        assert settings.voice == "en_US-lessac-medium"
        assert settings.length_scale == 1.0
        assert settings.noise_scale == 0.667
        assert settings.noise_w == 0.8
        assert settings.model_path is None
        assert settings.speaker_id is None

    def test_custom_settings(self) -> None:
        """Test custom Piper settings."""
        settings = PiperSettings(
            voice="en_GB-alba-medium",
            length_scale=0.8,
            noise_scale=0.5,
        )
        assert settings.voice == "en_GB-alba-medium"
        assert settings.length_scale == 0.8
        assert settings.noise_scale == 0.5


class TestPiperTTSProvider:
    """Tests for Piper TTS provider."""

    def test_initialization_default(self) -> None:
        """Test default provider initialization."""
        provider = PiperTTSProvider()
        assert provider.name == "piper"
        assert provider.supports_streaming is False
        assert provider.supports_cloning is False

    def test_initialization_with_settings(self) -> None:
        """Test provider initialization with custom settings."""
        settings = PiperSettings(voice="en_GB-alba-medium")
        provider = PiperTTSProvider(settings=settings)
        assert provider._settings.voice == "en_GB-alba-medium"

    @pytest.mark.asyncio
    async def test_list_voices(self) -> None:
        """Test listing available voices."""
        provider = PiperTTSProvider()
        voices = await provider.list_voices()

        assert len(voices) == len(DEFAULT_VOICES)
        assert all(isinstance(v, Voice) for v in voices)

        # Check for some expected voices
        voice_ids = [v.id for v in voices]
        assert "en_US-lessac-medium" in voice_ids
        assert "en_GB-alba-medium" in voice_ids

    @pytest.mark.asyncio
    async def test_clone_voice_not_supported(self) -> None:
        """Test that voice cloning raises NotImplementedError."""
        provider = PiperTTSProvider()

        with pytest.raises(NotImplementedError) as exc_info:
            await provider.clone_voice("test", [b"audio"])

        assert "Piper does not support voice cloning" in str(exc_info.value)

    def test_settings_schema(self) -> None:
        """Test settings schema generation."""
        provider = PiperTTSProvider()
        schema = provider.get_settings_schema()

        assert schema["type"] == "object"
        assert "properties" in schema
        assert "voice" in schema["properties"]
        assert "length_scale" in schema["properties"]
        assert "noise_scale" in schema["properties"]

        # Check voice enum contains expected voices
        voice_enum = schema["properties"]["voice"]["enum"]
        assert "en_US-lessac-medium" in voice_enum

    @pytest.mark.asyncio
    async def test_synthesize_success(self) -> None:
        """Test successful synthesis with mocked Piper."""
        provider = PiperTTSProvider()

        # Create mock voice
        mock_voice = MagicMock()
        mock_voice.config.sample_rate = 22050

        # Mock audio chunk with audio_int16_bytes attribute
        mock_chunk = MagicMock()
        mock_chunk.audio_int16_bytes = b"\x00\x01" * 1000
        mock_voice.synthesize.return_value = iter([mock_chunk])

        # Patch the _ensure_voice_loaded to return our mock
        async def mock_ensure_voice_loaded(_voice_id: str) -> MagicMock:
            return mock_voice

        provider._ensure_voice_loaded = mock_ensure_voice_loaded  # type: ignore[method-assign]

        # Patch the piper module import that happens in _synthesize_sync
        with patch("piper.PiperVoice"), patch("piper.config.SynthesisConfig"):
            result = await provider.synthesize("Hello, world!")

        assert isinstance(result, bytes)
        assert len(result) > 0
        # Should be WAV format (starts with RIFF)
        assert result[:4] == b"RIFF"

    @pytest.mark.asyncio
    async def test_synthesize_with_speed_adjustment(self) -> None:
        """Test synthesis with speed adjustment."""
        provider = PiperTTSProvider()

        mock_voice = MagicMock()
        mock_voice.config.sample_rate = 22050

        # Mock audio chunk with audio_int16_bytes attribute
        mock_chunk = MagicMock()
        mock_chunk.audio_int16_bytes = b"\x00\x01" * 100
        mock_voice.synthesize.return_value = iter([mock_chunk])

        async def mock_ensure_voice_loaded(_voice_id: str) -> MagicMock:
            return mock_voice

        provider._ensure_voice_loaded = mock_ensure_voice_loaded  # type: ignore[method-assign]

        settings = TTSSettings(speed=1.5)

        with patch("piper.PiperVoice"), patch(
            "piper.config.SynthesisConfig"
        ) as mock_config_class:
            await provider.synthesize("Test", settings=settings)

        # Verify length_scale was adjusted (speed 1.5 -> length_scale ~0.67)
        call_args = mock_config_class.call_args
        length_scale = call_args.kwargs.get("length_scale")
        assert length_scale is not None
        assert abs(length_scale - (1.0 / 1.5)) < 0.01

    @pytest.mark.asyncio
    async def test_synthesize_stream_yields_full_audio(self) -> None:
        """Test that synthesize_stream yields full audio (no streaming support)."""
        provider = PiperTTSProvider()

        mock_voice = MagicMock()
        mock_voice.config.sample_rate = 22050

        # Mock audio chunk with audio_int16_bytes attribute
        mock_chunk = MagicMock()
        mock_chunk.audio_int16_bytes = b"\x00\x01" * 100
        mock_voice.synthesize.return_value = iter([mock_chunk])

        async def mock_ensure_voice_loaded(_voice_id: str) -> MagicMock:
            return mock_voice

        provider._ensure_voice_loaded = mock_ensure_voice_loaded  # type: ignore[method-assign]

        with patch("piper.PiperVoice"), patch("piper.config.SynthesisConfig"):
            chunks = []
            async for chunk in provider.synthesize_stream("Test"):
                chunks.append(chunk)

        # Should only yield one chunk (full audio)
        assert len(chunks) == 1
        assert chunks[0][:4] == b"RIFF"

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        """Test successful health check."""
        provider = PiperTTSProvider()

        async def mock_ensure_loaded() -> MagicMock:
            return MagicMock()

        provider._ensure_loaded = mock_ensure_loaded  # type: ignore[method-assign]

        result = await provider.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_import_error(self) -> None:
        """Test health check when piper is not installed."""
        provider = PiperTTSProvider()

        async def mock_ensure_loaded() -> None:
            raise ImportError("piper not installed")

        provider._ensure_loaded = mock_ensure_loaded  # type: ignore[method-assign]

        result = await provider.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_model_not_found(self) -> None:
        """Test health check when model file not found."""
        provider = PiperTTSProvider()

        async def mock_ensure_loaded() -> None:
            raise FileNotFoundError("Model not found")

        provider._ensure_loaded = mock_ensure_loaded  # type: ignore[method-assign]

        result = await provider.health_check()
        assert result is False


class TestPiperLanguageVoiceSelection:
    """Tests for language-based voice selection in Piper."""

    def test_language_default_voices_mapping(self) -> None:
        """Test that all supported languages have default voices."""
        # All language codes should have a default voice
        for lang in [LanguageCode.EN, LanguageCode.RU]:
            assert lang in LANGUAGE_DEFAULT_VOICES
            voice_id = LANGUAGE_DEFAULT_VOICES[lang]
            # Voice ID should be valid format
            assert "-" in voice_id
            assert "_" in voice_id

    def test_get_voice_for_language_returns_default(self) -> None:
        """Should return default voice for supported languages."""
        provider = PiperTTSProvider()

        assert provider.get_voice_for_language(LanguageCode.EN) == "en_US-lessac-medium"
        assert provider.get_voice_for_language(LanguageCode.RU) == "ru_RU-ruslan-medium"

    def test_get_voice_for_language_uses_override(self) -> None:
        """Should prefer user override over default."""
        provider = PiperTTSProvider(
            voice_overrides={LanguageCode.EN: "en_GB-alba-medium"}
        )

        # EN should use override
        assert provider.get_voice_for_language(LanguageCode.EN) == "en_GB-alba-medium"
        # RU should still use default
        assert provider.get_voice_for_language(LanguageCode.RU) == "ru_RU-ruslan-medium"

    def test_get_voice_for_language_multiple_overrides(self) -> None:
        """Should handle multiple language overrides."""
        provider = PiperTTSProvider(
            voice_overrides={
                LanguageCode.EN: "en_US-ryan-medium",
                LanguageCode.RU: "ru_RU-irina-medium",
            }
        )

        assert provider.get_voice_for_language(LanguageCode.EN) == "en_US-ryan-medium"
        assert provider.get_voice_for_language(LanguageCode.RU) == "ru_RU-irina-medium"

    def test_get_voices_for_language_filters_correctly(self) -> None:
        """Should return only voices matching the language."""
        provider = PiperTTSProvider()

        en_voices = provider.get_voices_for_language(LanguageCode.EN)
        assert len(en_voices) > 0
        assert all(v.language == "en" for v in en_voices)

        ru_voices = provider.get_voices_for_language(LanguageCode.RU)
        assert len(ru_voices) > 0
        assert all(v.language == "ru" for v in ru_voices)

    def test_provider_initialization_with_voice_overrides(self) -> None:
        """Test provider initialization with voice overrides."""
        overrides = {LanguageCode.EN: "en_GB-alba-medium"}
        provider = PiperTTSProvider(voice_overrides=overrides)

        assert provider._voice_overrides == overrides

    def test_provider_initialization_empty_overrides(self) -> None:
        """Test provider initialization with empty overrides."""
        provider = PiperTTSProvider(voice_overrides={})

        assert provider._voice_overrides == {}

    @pytest.mark.asyncio
    async def test_synthesize_with_language_parameter(self) -> None:
        """Test synthesis uses language to select voice."""
        provider = PiperTTSProvider()

        mock_voice = MagicMock()
        mock_voice.config.sample_rate = 22050

        mock_chunk = MagicMock()
        mock_chunk.audio_int16_bytes = b"\x00\x01" * 100
        mock_voice.synthesize.return_value = iter([mock_chunk])

        # Track which voice was loaded
        loaded_voice_ids: list[str] = []

        async def mock_ensure_voice_loaded(voice_id: str) -> MagicMock:
            loaded_voice_ids.append(voice_id)
            return mock_voice

        provider._ensure_voice_loaded = mock_ensure_voice_loaded  # type: ignore[method-assign]

        with patch("piper.PiperVoice"), patch("piper.config.SynthesisConfig"):
            await provider.synthesize("Test", language=LanguageCode.RU)

        # Should have loaded Russian voice
        assert "ru_RU-ruslan-medium" in loaded_voice_ids

    @pytest.mark.asyncio
    async def test_synthesize_with_language_and_override(self) -> None:
        """Test synthesis uses override when language specified."""
        provider = PiperTTSProvider(
            voice_overrides={LanguageCode.RU: "ru_RU-irina-medium"}
        )

        mock_voice = MagicMock()
        mock_voice.config.sample_rate = 22050

        mock_chunk = MagicMock()
        mock_chunk.audio_int16_bytes = b"\x00\x01" * 100
        mock_voice.synthesize.return_value = iter([mock_chunk])

        loaded_voice_ids: list[str] = []

        async def mock_ensure_voice_loaded(voice_id: str) -> MagicMock:
            loaded_voice_ids.append(voice_id)
            return mock_voice

        provider._ensure_voice_loaded = mock_ensure_voice_loaded  # type: ignore[method-assign]

        with patch("piper.PiperVoice"), patch("piper.config.SynthesisConfig"):
            await provider.synthesize("Test", language=LanguageCode.RU)

        # Should have loaded override voice, not default
        assert "ru_RU-irina-medium" in loaded_voice_ids
        assert "ru_RU-ruslan-medium" not in loaded_voice_ids

    @pytest.mark.asyncio
    async def test_synthesize_voice_id_takes_precedence(self) -> None:
        """Test explicit voice_id takes precedence over language."""
        provider = PiperTTSProvider()

        mock_voice = MagicMock()
        mock_voice.config.sample_rate = 22050

        mock_chunk = MagicMock()
        mock_chunk.audio_int16_bytes = b"\x00\x01" * 100
        mock_voice.synthesize.return_value = iter([mock_chunk])

        loaded_voice_ids: list[str] = []

        async def mock_ensure_voice_loaded(voice_id: str) -> MagicMock:
            loaded_voice_ids.append(voice_id)
            return mock_voice

        provider._ensure_voice_loaded = mock_ensure_voice_loaded  # type: ignore[method-assign]

        with patch("piper.PiperVoice"), patch("piper.config.SynthesisConfig"):
            # Specify both voice_id and language - voice_id should win
            await provider.synthesize(
                "Test",
                voice_id="custom-voice",
                language=LanguageCode.RU
            )

        assert "custom-voice" in loaded_voice_ids
        assert "ru_RU-ruslan-medium" not in loaded_voice_ids
