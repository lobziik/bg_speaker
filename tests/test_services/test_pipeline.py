"""Tests for the narration pipeline service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.narration import LanguageCode, NarrationRequest, NarratorStyle
from src.providers.llm.base import LLMResponse, LLMResponseParseError, ModerationResult
from src.providers.llm.prompts import PromptSettings
from src.services.pipeline import ModerationRejectedError, NarrationPipeline, PipelineMetrics


@pytest.fixture
def mock_llm_provider() -> MagicMock:
    """Create a mock LLM provider."""
    provider = MagicMock()
    provider.name = "mock_llm"
    provider.generate = AsyncMock(
        return_value=LLMResponse(
            voice_text="The adventurer speaks with great enthusiasm!",
            subtitle_text="The adventurer speaks with great enthusiasm!",
            raw_response='{"voice_text": "...", "subtitle_text": "..."}',
        )
    )
    provider.health_check = AsyncMock(return_value=True)
    return provider


@pytest.fixture
def mock_tts_provider() -> MagicMock:
    """Create a mock TTS provider."""
    provider = MagicMock()
    provider.name = "mock_tts"

    # Create a minimal valid WAV file
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x00\x00" * 22050)  # 1 second of silence

    provider.synthesize = AsyncMock(return_value=buffer.getvalue())
    provider.health_check = AsyncMock(return_value=True)
    return provider


@pytest.fixture
def pipeline(mock_llm_provider: MagicMock, mock_tts_provider: MagicMock) -> NarrationPipeline:
    """Create a pipeline with mock providers."""
    return NarrationPipeline(
        llm_provider=mock_llm_provider,
        tts_provider=mock_tts_provider,
    )


class TestNarrationPipeline:
    """Tests for NarrationPipeline."""

    @pytest.mark.asyncio
    async def test_process_basic(
        self,
        pipeline: NarrationPipeline,
        mock_llm_provider: MagicMock,
        mock_tts_provider: MagicMock,
    ) -> None:
        """Test basic pipeline processing."""
        request = NarrationRequest(
            user="TestUser",
            message="Hello everyone!",
        )

        result, metrics = await pipeline.process(request, enable_moderation=False)

        # Verify LLM was called
        mock_llm_provider.generate.assert_called_once()
        call_args = mock_llm_provider.generate.call_args
        assert call_args.kwargs["user"] == "TestUser"
        assert call_args.kwargs["message"] == "Hello everyone!"

        # Verify TTS was called with LLM output and language
        mock_tts_provider.synthesize.assert_called_once_with(
            "The adventurer speaks with great enthusiasm!",
            voice_id=None,
            language=LanguageCode.EN,
        )

        # Verify result
        assert result.user == "TestUser"
        assert result.voice_text == "The adventurer speaks with great enthusiasm!"
        assert result.subtitle_text == "The adventurer speaks with great enthusiasm!"
        assert isinstance(result.audio_data, bytes)
        assert result.duration_ms > 0

        # Verify metrics
        assert isinstance(metrics, PipelineMetrics)
        assert metrics.llm_latency_ms >= 0
        assert metrics.tts_latency_ms >= 0
        assert metrics.total_latency_ms >= 0

    @pytest.mark.asyncio
    async def test_process_with_style(
        self,
        pipeline: NarrationPipeline,
        mock_llm_provider: MagicMock,
    ) -> None:
        """Test pipeline processing with custom style."""
        request = NarrationRequest(
            user="TestUser",
            message="A secret...",
            style=NarratorStyle.WHISPER,
        )

        await pipeline.process(request, enable_moderation=False)

        # Verify style was passed to LLM
        call_args = mock_llm_provider.generate.call_args
        assert call_args.kwargs["style"] == "whisper"

    @pytest.mark.asyncio
    async def test_process_with_narrator_language(
        self,
        pipeline: NarrationPipeline,
    ) -> None:
        """Test pipeline processing with narrator language."""
        request = NarrationRequest(
            user="TestUser",
            message="Hello!",
        )

        result, _ = await pipeline.process(
            request, narrator_lang=LanguageCode.RU, enable_moderation=False
        )

        assert result.target_lang == LanguageCode.RU

    @pytest.mark.asyncio
    async def test_process_generates_unique_id(
        self,
        pipeline: NarrationPipeline,
    ) -> None:
        """Test that each process call generates a unique ID."""
        request = NarrationRequest(
            user="TestUser",
            message="Hello!",
        )

        result1, _ = await pipeline.process(request, enable_moderation=False)
        result2, _ = await pipeline.process(request, enable_moderation=False)

        assert result1.id != result2.id

    @pytest.mark.asyncio
    async def test_health_check_all_healthy(
        self,
        pipeline: NarrationPipeline,
    ) -> None:
        """Test health check when all components are healthy."""
        health = await pipeline.health_check()

        assert health["llm"] is True
        assert health["tts"] is True
        assert health["pipeline"] is True

    @pytest.mark.asyncio
    async def test_health_check_llm_unhealthy(
        self,
        pipeline: NarrationPipeline,
        mock_llm_provider: MagicMock,
    ) -> None:
        """Test health check when LLM is unhealthy."""
        mock_llm_provider.health_check.return_value = False

        health = await pipeline.health_check()

        assert health["llm"] is False
        assert health["tts"] is True
        assert health["pipeline"] is False

    @pytest.mark.asyncio
    async def test_health_check_tts_unhealthy(
        self,
        pipeline: NarrationPipeline,
        mock_tts_provider: MagicMock,
    ) -> None:
        """Test health check when TTS is unhealthy."""
        mock_tts_provider.health_check.return_value = False

        health = await pipeline.health_check()

        assert health["llm"] is True
        assert health["tts"] is False
        assert health["pipeline"] is False

    @pytest.mark.asyncio
    async def test_custom_prompt(
        self,
        mock_llm_provider: MagicMock,
        mock_tts_provider: MagicMock,
    ) -> None:
        """Test pipeline with custom narrator prompt."""
        custom_prompt = "You are a pirate narrator. Arrr!"

        pipeline = NarrationPipeline(
            llm_provider=mock_llm_provider,
            tts_provider=mock_tts_provider,
            custom_prompt=custom_prompt,
        )

        request = NarrationRequest(
            user="TestUser",
            message="Hello!",
        )

        await pipeline.process(request, enable_moderation=False)

        # Verify custom prompt was included in the system prompt
        call_args = mock_llm_provider.generate.call_args
        system_prompt = call_args.kwargs["system_prompt"]
        assert custom_prompt in system_prompt

    def test_estimate_audio_duration_wav(
        self,
        pipeline: NarrationPipeline,
    ) -> None:
        """Test audio duration estimation for WAV."""
        import io
        import wave

        # Create a 2-second WAV file
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x00\x00" * 44100)  # 2 seconds

        duration = pipeline._estimate_audio_duration(buffer.getvalue())

        # Should be approximately 2000ms
        assert 1900 <= duration <= 2100


class TestPipelineMetrics:
    """Tests for PipelineMetrics."""

    def test_metrics_creation(self) -> None:
        """Test metrics dataclass creation."""
        metrics = PipelineMetrics(
            moderation_latency_ms=50,
            llm_latency_ms=100,
            tts_latency_ms=200,
            total_latency_ms=350,
            text_length=50,
            audio_size_bytes=44100,
        )

        assert metrics.moderation_latency_ms == 50
        assert metrics.llm_latency_ms == 100
        assert metrics.tts_latency_ms == 200
        assert metrics.total_latency_ms == 350
        assert metrics.text_length == 50
        assert metrics.audio_size_bytes == 44100


class TestModerationCheck:
    """Tests for content moderation."""

    @pytest.fixture
    def mock_llm_for_moderation(self) -> MagicMock:
        """Create a mock LLM that handles both moderation and narration calls."""
        provider = MagicMock()
        provider.name = "mock_llm"
        provider.health_check = AsyncMock(return_value=True)
        return provider

    @pytest.fixture
    def mock_tts_for_moderation(self) -> MagicMock:
        """Create a mock TTS provider."""
        import io
        import wave

        provider = MagicMock()
        provider.name = "mock_tts"

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x00\x00" * 22050)

        provider.synthesize = AsyncMock(return_value=buffer.getvalue())
        provider.health_check = AsyncMock(return_value=True)
        return provider

    @pytest.mark.asyncio
    async def test_moderation_allows_clean_message(
        self,
        mock_llm_for_moderation: MagicMock,
        mock_tts_for_moderation: MagicMock,
    ) -> None:
        """Clean messages should pass moderation and proceed to narration."""
        # Set up moderate for moderation (returns ModerationResult)
        mock_llm_for_moderation.moderate = AsyncMock(
            return_value=ModerationResult(allowed=True, reason="", category="")
        )
        # Set up generate for narration
        mock_llm_for_moderation.generate = AsyncMock(
            return_value=LLMResponse(
                voice_text="The adventurer greets everyone!",
                subtitle_text="The adventurer greets everyone!",
                raw_response='{"voice_text": "...", "subtitle_text": "..."}',
            )
        )

        pipeline = NarrationPipeline(
            llm_provider=mock_llm_for_moderation,
            tts_provider=mock_tts_for_moderation,
        )

        request = NarrationRequest(user="TestUser", message="Hello everyone!")
        result, metrics = await pipeline.process(request, enable_moderation=True)

        # Moderation (moderate) and narration (generate) should have been called
        mock_llm_for_moderation.moderate.assert_called_once()
        mock_llm_for_moderation.generate.assert_called_once()
        assert result.voice_text == "The adventurer greets everyone!"
        assert metrics.moderation_latency_ms >= 0

    @pytest.mark.asyncio
    async def test_moderation_rejects_policy_violation(
        self,
        mock_llm_for_moderation: MagicMock,
        mock_tts_for_moderation: MagicMock,
    ) -> None:
        """Policy violations should raise ModerationRejectedError."""
        mock_llm_for_moderation.moderate = AsyncMock(
            return_value=ModerationResult(
                allowed=False,
                reason="Contains hate speech",
                category="hate_speech",
            )
        )

        pipeline = NarrationPipeline(
            llm_provider=mock_llm_for_moderation,
            tts_provider=mock_tts_for_moderation,
        )

        request = NarrationRequest(user="TestUser", message="[offensive content]")

        with pytest.raises(ModerationRejectedError) as exc_info:
            await pipeline.process(request, enable_moderation=True)

        assert exc_info.value.category == "hate_speech"
        assert "hate speech" in exc_info.value.reason.lower()
        # Only moderation call should have been made (narration skipped)
        mock_llm_for_moderation.moderate.assert_called_once()

    @pytest.mark.asyncio
    async def test_moderation_skipped_when_disabled(
        self,
        mock_llm_for_moderation: MagicMock,
        mock_tts_for_moderation: MagicMock,
    ) -> None:
        """When moderation is disabled, only narration LLM call is made."""
        mock_llm_for_moderation.generate = AsyncMock(
            return_value=LLMResponse(
                voice_text="Narrated text",
                subtitle_text="Narrated text",
                raw_response='{"voice_text": "Narrated text", "subtitle_text": "Narrated text"}',
            )
        )

        pipeline = NarrationPipeline(
            llm_provider=mock_llm_for_moderation,
            tts_provider=mock_tts_for_moderation,
        )

        request = NarrationRequest(user="TestUser", message="Hello!")
        result, metrics = await pipeline.process(request, enable_moderation=False)

        # Only one LLM call (narration), no moderation
        assert mock_llm_for_moderation.generate.call_count == 1
        assert result.voice_text == "Narrated text"
        # Moderation latency should be 0 when disabled
        assert metrics.moderation_latency_ms == 0

    @pytest.mark.asyncio
    async def test_moderation_parse_error_triggers_rejection(
        self,
        mock_llm_for_moderation: MagicMock,
        mock_tts_for_moderation: MagicMock,
    ) -> None:
        """Parse errors in moderation response should fail fast and reject."""
        mock_llm_for_moderation.moderate = AsyncMock(
            side_effect=LLMResponseParseError(
                raw_response="This is not valid JSON",
                parse_error="Invalid JSON",
            )
        )

        pipeline = NarrationPipeline(
            llm_provider=mock_llm_for_moderation,
            tts_provider=mock_tts_for_moderation,
        )

        request = NarrationRequest(user="TestUser", message="Hello!")

        with pytest.raises(ModerationRejectedError) as exc_info:
            await pipeline.process(request, enable_moderation=True)

        assert exc_info.value.category == "parse_error"
        assert "cannot verify content safety" in exc_info.value.reason.lower()


class TestEditablePrompts:
    """The stored prompt sections are what actually reach the providers."""

    @pytest.mark.asyncio
    async def test_narration_uses_stored_sections(
        self,
        pipeline: NarrationPipeline,
        mock_llm_provider: MagicMock,
    ) -> None:
        """An edited section shows up in the system prompt sent to the LLM."""
        prompts = PromptSettings(
            base_system="Answer in JSON with voice_text and subtitle_text.",
            formatting="Formatting: one word only.",
        )

        await pipeline.process(
            NarrationRequest(user="TestUser", message="hello"),
            enable_moderation=False,
            prompts=prompts,
        )

        system_prompt = mock_llm_provider.generate.call_args.kwargs["system_prompt"]
        assert system_prompt.startswith("Answer in JSON with voice_text and subtitle_text.")
        assert "Formatting: one word only." in system_prompt

    @pytest.mark.asyncio
    async def test_moderation_uses_stored_prompts(
        self,
        pipeline: NarrationPipeline,
        mock_llm_provider: MagicMock,
    ) -> None:
        """Moderation prompts come from settings, not from provider internals."""
        mock_llm_provider.moderate = AsyncMock(
            return_value=ModerationResult(allowed=True, reason="", category="")
        )
        prompts = PromptSettings(
            moderation_system="Only block real threats. Reply with JSON.",
            moderation_user="CHECK[$user]: $message",
        )

        await pipeline.process(
            NarrationRequest(user="DragonSlayer", message="I found a sword"),
            enable_moderation=True,
            prompts=prompts,
        )

        kwargs = mock_llm_provider.moderate.call_args.kwargs
        assert kwargs["system_prompt"] == "Only block real threats. Reply with JSON."
        assert kwargs["user_prompt"] == "CHECK[DragonSlayer]: I found a sword"

    @pytest.mark.asyncio
    async def test_defaults_used_when_not_supplied(
        self,
        pipeline: NarrationPipeline,
        mock_llm_provider: MagicMock,
    ) -> None:
        """Callers that pass no prompts get the built-in defaults."""
        await pipeline.process(
            NarrationRequest(user="TestUser", message="hello"),
            enable_moderation=False,
        )

        system_prompt = mock_llm_provider.generate.call_args.kwargs["system_prompt"]
        assert "You are a theatrical fantasy narrator" in system_prompt
