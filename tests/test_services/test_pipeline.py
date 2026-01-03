"""Tests for the narration pipeline service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.narration import LanguageCode, NarrationRequest, NarratorStyle
from src.providers.llm.base import LLMResponse
from src.services.pipeline import NarrationPipeline, PipelineMetrics


@pytest.fixture
def mock_llm_provider() -> MagicMock:
    """Create a mock LLM provider."""
    provider = MagicMock()
    provider.name = "mock_llm"
    provider.generate = AsyncMock(
        return_value=LLMResponse(
            text="The adventurer speaks with great enthusiasm!",
            raw_response='{"mock": true}',
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

        result, metrics = await pipeline.process(request)

        # Verify LLM was called
        mock_llm_provider.generate.assert_called_once()
        call_args = mock_llm_provider.generate.call_args
        assert call_args.kwargs["user"] == "TestUser"
        assert call_args.kwargs["message"] == "Hello everyone!"

        # Verify TTS was called with LLM output and language
        mock_tts_provider.synthesize.assert_called_once_with(
            "The adventurer speaks with great enthusiasm!",
            language=LanguageCode.EN,
        )

        # Verify result
        assert result.user == "TestUser"
        assert result.text_original == "The adventurer speaks with great enthusiasm!"
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

        await pipeline.process(request)

        # Verify style was passed to LLM
        call_args = mock_llm_provider.generate.call_args
        assert call_args.kwargs["style"] == "whisper"

    @pytest.mark.asyncio
    async def test_process_with_target_language(
        self,
        pipeline: NarrationPipeline,
    ) -> None:
        """Test pipeline processing with target language."""
        request = NarrationRequest(
            user="TestUser",
            message="Hello!",
        )

        result, _ = await pipeline.process(request, target_lang=LanguageCode.DE)

        assert result.target_lang == LanguageCode.DE

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

        result1, _ = await pipeline.process(request)
        result2, _ = await pipeline.process(request)

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
    async def test_custom_system_prompt(
        self,
        mock_llm_provider: MagicMock,
        mock_tts_provider: MagicMock,
    ) -> None:
        """Test pipeline with custom system prompt."""
        custom_prompt = "You are a pirate narrator. Arrr!"

        pipeline = NarrationPipeline(
            llm_provider=mock_llm_provider,
            tts_provider=mock_tts_provider,
            system_prompt=custom_prompt,
        )

        request = NarrationRequest(
            user="TestUser",
            message="Hello!",
        )

        await pipeline.process(request)

        call_args = mock_llm_provider.generate.call_args
        assert call_args.kwargs["system_prompt"] == custom_prompt

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
            llm_latency_ms=100,
            tts_latency_ms=200,
            total_latency_ms=350,
            text_length=50,
            audio_size_bytes=44100,
        )

        assert metrics.llm_latency_ms == 100
        assert metrics.tts_latency_ms == 200
        assert metrics.total_latency_ms == 350
        assert metrics.text_length == 50
        assert metrics.audio_size_bytes == 44100
