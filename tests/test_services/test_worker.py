"""Tests for the QueueWorker service."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.websocket import WebSocketManager
from src.models.narration import LanguageCode, NarrationRequest, NarrationResult
from src.models.settings import QueueSettings
from src.services.pipeline import NarrationPipeline, PipelineMetrics
from src.services.queue import NarrationQueue
from src.services.rate_limiter import RateLimiter
from src.services.worker import QueueWorker


@pytest.fixture
def rate_limiter() -> RateLimiter:
    """Create a rate limiter with short timeouts for testing."""
    return RateLimiter(
        tts_rate_limit_seconds=0.01,
        user_cooldown_seconds=0.01,
    )


@pytest.fixture
def queue(rate_limiter: RateLimiter) -> NarrationQueue:
    """Create a narration queue for testing."""
    settings = QueueSettings(
        max_size=10,
        message_min_length=1,
        message_max_length=300,
    )
    return NarrationQueue(settings=settings, rate_limiter=rate_limiter)


@pytest.fixture
def mock_pipeline() -> MagicMock:
    """Create a mock pipeline."""
    pipeline = MagicMock(spec=NarrationPipeline)

    # Create sample result
    sample_result = NarrationResult(
        id="test-result-123",
        user="TestUser",
        voice_text="The adventurer speaks with great enthusiasm!",
        subtitle_text="The adventurer speaks with great enthusiasm!",
        target_lang=LanguageCode.EN,
        audio_data=b"RIFF\x00\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x22\x56\x00\x00D\xac\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00",
        duration_ms=1000,
    )
    sample_metrics = PipelineMetrics(
        llm_latency_ms=100,
        tts_latency_ms=200,
        total_latency_ms=300,
        text_length=42,
        audio_size_bytes=100,
    )

    pipeline.process = AsyncMock(return_value=(sample_result, sample_metrics))
    return pipeline


@pytest.fixture
def mock_ws_manager() -> MagicMock:
    """Create a mock WebSocket manager."""
    manager = MagicMock(spec=WebSocketManager)
    manager.broadcast_narration_start = AsyncMock()
    manager.broadcast_audio_data = AsyncMock()
    manager.broadcast_narration_end = AsyncMock()
    manager.broadcast_narration_error = AsyncMock()
    return manager


@pytest.fixture
def worker(
    queue: NarrationQueue,
    mock_pipeline: MagicMock,
    mock_ws_manager: MagicMock,
) -> QueueWorker:
    """Create a queue worker for testing."""
    return QueueWorker(
        queue=queue,
        pipeline=mock_pipeline,  # type: ignore[arg-type]
        ws_manager=mock_ws_manager,  # type: ignore[arg-type]
        rewards_controller=None,
    )


class TestWorkerLifecycle:
    """Tests for worker start/stop."""

    @pytest.mark.asyncio
    async def test_start_sets_running(self, worker: QueueWorker) -> None:
        """Start should set is_running to True."""
        await worker.start()
        assert worker.is_running is True
        await worker.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self, worker: QueueWorker) -> None:
        """Stop should set is_running to False."""
        await worker.start()
        await worker.stop()
        assert worker.is_running is False

    @pytest.mark.asyncio
    async def test_multiple_start_calls_ignored(self, worker: QueueWorker) -> None:
        """Multiple start calls should be idempotent."""
        await worker.start()
        await worker.start()  # Should not raise
        assert worker.is_running is True
        await worker.stop()


class TestWorkerProcessing:
    """Tests for queue item processing."""

    @pytest.mark.asyncio
    async def test_process_item_calls_pipeline(
        self,
        worker: QueueWorker,
        queue: NarrationQueue,
        mock_pipeline: MagicMock,
    ) -> None:
        """Processing should call pipeline.process."""
        await worker.start()

        # Add item to queue
        await queue.add("TestUser", "Hello world!")

        # Wait for processing
        await asyncio.sleep(0.1)

        await worker.stop()

        # Pipeline should have been called
        mock_pipeline.process.assert_called_once()
        call_args = mock_pipeline.process.call_args
        request: NarrationRequest = call_args[0][0]
        assert request.user == "TestUser"
        assert request.message == "Hello world!"

    @pytest.mark.asyncio
    async def test_process_item_passes_target_lang(
        self,
        worker: QueueWorker,
        queue: NarrationQueue,
        mock_pipeline: MagicMock,
    ) -> None:
        """Processing should pass target_lang to pipeline.

        Without DB connection, worker uses default (EN).
        """
        await worker.start()

        await queue.add("TestUser", "Hello world!")
        await asyncio.sleep(0.1)

        await worker.stop()

        # Pipeline should have been called with narrator_lang
        mock_pipeline.process.assert_called_once()
        call_args = mock_pipeline.process.call_args
        # Default language when no DB is EN
        assert call_args.kwargs.get("narrator_lang") == LanguageCode.EN

    @pytest.mark.asyncio
    async def test_process_broadcasts_narration_start(
        self,
        worker: QueueWorker,
        queue: NarrationQueue,
        mock_ws_manager: MagicMock,
    ) -> None:
        """Processing should broadcast narration_start."""
        await worker.start()
        await queue.add("TestUser", "Hello!")
        await asyncio.sleep(0.1)
        await worker.stop()

        mock_ws_manager.broadcast_narration_start.assert_called_once()
        call_args = mock_ws_manager.broadcast_narration_start.call_args
        assert call_args.kwargs["user"] == "TestUser"

    @pytest.mark.asyncio
    async def test_process_broadcasts_audio_data(
        self,
        worker: QueueWorker,
        queue: NarrationQueue,
        mock_ws_manager: MagicMock,
    ) -> None:
        """Processing should broadcast audio_data."""
        await worker.start()
        await queue.add("TestUser", "Hello!")
        await asyncio.sleep(0.1)
        await worker.stop()

        mock_ws_manager.broadcast_audio_data.assert_called_once()
        call_args = mock_ws_manager.broadcast_audio_data.call_args
        assert call_args.kwargs["duration_ms"] == 1000

    @pytest.mark.asyncio
    async def test_process_multiple_items(
        self,
        queue: NarrationQueue,
        mock_ws_manager: MagicMock,
    ) -> None:
        """Should process multiple items in order."""
        # Create pipeline with short duration for faster tests
        fast_result = NarrationResult(
            id="test-result",
            user="User",
            voice_text="Text",
            subtitle_text="Text",
            target_lang=LanguageCode.EN,
            audio_data=b"audio",
            duration_ms=10,  # Very short duration
        )
        fast_metrics = PipelineMetrics(
            llm_latency_ms=1,
            tts_latency_ms=1,
            total_latency_ms=2,
            text_length=4,
            audio_size_bytes=5,
        )
        fast_pipeline = MagicMock(spec=NarrationPipeline)
        fast_pipeline.process = AsyncMock(return_value=(fast_result, fast_metrics))

        worker = QueueWorker(
            queue=queue,
            pipeline=fast_pipeline,  # type: ignore[arg-type]
            ws_manager=mock_ws_manager,  # type: ignore[arg-type]
            rewards_controller=None,
        )

        await worker.start()

        # Add multiple items
        await queue.add("User1", "Message 1")
        await queue.add("User2", "Message 2")

        # Wait for processing (needs longer since worker waits for duration)
        await asyncio.sleep(0.2)

        await worker.stop()

        # Both should be processed
        assert fast_pipeline.process.call_count == 2


class TestWorkerErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_pipeline_error_broadcasts_error(
        self,
        queue: NarrationQueue,
        mock_ws_manager: MagicMock,
    ) -> None:
        """Pipeline errors should broadcast narration_error."""
        # Create pipeline that raises
        error_pipeline = MagicMock(spec=NarrationPipeline)
        error_pipeline.process = AsyncMock(side_effect=RuntimeError("Test error"))

        worker = QueueWorker(
            queue=queue,
            pipeline=error_pipeline,  # type: ignore[arg-type]
            ws_manager=mock_ws_manager,  # type: ignore[arg-type]
            rewards_controller=None,
        )

        await worker.start()
        await queue.add("TestUser", "Hello!")
        await asyncio.sleep(0.1)
        await worker.stop()

        mock_ws_manager.broadcast_narration_error.assert_called_once()
        call_args = mock_ws_manager.broadcast_narration_error.call_args
        assert "Test error" in call_args.kwargs["error"]

    @pytest.mark.asyncio
    async def test_error_does_not_stop_worker(
        self,
        queue: NarrationQueue,
        mock_ws_manager: MagicMock,
    ) -> None:
        """Worker should continue after an error."""
        # Create pipeline that fails once then succeeds
        call_count = 0
        sample_result = NarrationResult(
            id="result-123",
            user="User",
            voice_text="Text",
            subtitle_text="Text",
            target_lang=LanguageCode.EN,
            audio_data=b"audio",
            duration_ms=100,
        )
        sample_metrics = PipelineMetrics(
            llm_latency_ms=10,
            tts_latency_ms=10,
            total_latency_ms=20,
            text_length=4,
            audio_size_bytes=5,
        )

        async def process_with_error(
            _request: NarrationRequest,
            **_kwargs: object,
        ) -> tuple[NarrationResult, PipelineMetrics]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("First call fails")
            return (sample_result, sample_metrics)

        error_pipeline = MagicMock(spec=NarrationPipeline)
        error_pipeline.process = AsyncMock(side_effect=process_with_error)

        worker = QueueWorker(
            queue=queue,
            pipeline=error_pipeline,  # type: ignore[arg-type]
            ws_manager=mock_ws_manager,  # type: ignore[arg-type]
            rewards_controller=None,
        )

        await worker.start()

        # Add two items
        await queue.add("User1", "First message")
        await asyncio.sleep(0.1)
        await queue.add("User2", "Second message")
        await asyncio.sleep(0.2)

        await worker.stop()

        # Both should be attempted
        assert call_count >= 2


class TestWorkerWithRewards:
    """Tests for Twitch rewards integration."""

    @pytest.mark.asyncio
    async def test_fulfill_redemption_on_success(
        self,
        queue: NarrationQueue,
        mock_ws_manager: MagicMock,
    ) -> None:
        """Should fulfill redemption on successful processing."""
        # Create pipeline with short duration
        fast_result = NarrationResult(
            id="test-result",
            user="User",
            voice_text="Text",
            subtitle_text="Text",
            target_lang=LanguageCode.EN,
            audio_data=b"audio",
            duration_ms=10,
        )
        fast_metrics = PipelineMetrics(
            llm_latency_ms=1,
            tts_latency_ms=1,
            total_latency_ms=2,
            text_length=4,
            audio_size_bytes=5,
        )
        fast_pipeline = MagicMock(spec=NarrationPipeline)
        fast_pipeline.process = AsyncMock(return_value=(fast_result, fast_metrics))

        mock_rewards = MagicMock()
        mock_rewards.fulfill_redemption = AsyncMock()

        worker = QueueWorker(
            queue=queue,
            pipeline=fast_pipeline,  # type: ignore[arg-type]
            ws_manager=mock_ws_manager,  # type: ignore[arg-type]
            rewards_controller=mock_rewards,
        )

        await worker.start()
        await queue.add("TestUser", "Hello!", redemption_id="redemption-123")
        await asyncio.sleep(0.15)
        await worker.stop()

        mock_rewards.fulfill_redemption.assert_called_once_with("redemption-123")

    @pytest.mark.asyncio
    async def test_cancel_redemption_on_error(
        self,
        queue: NarrationQueue,
        mock_ws_manager: MagicMock,
    ) -> None:
        """Should cancel redemption on processing error."""
        mock_rewards = MagicMock()
        mock_rewards.cancel_redemption = AsyncMock()

        error_pipeline = MagicMock(spec=NarrationPipeline)
        error_pipeline.process = AsyncMock(side_effect=RuntimeError("Error"))

        worker = QueueWorker(
            queue=queue,
            pipeline=error_pipeline,  # type: ignore[arg-type]
            ws_manager=mock_ws_manager,  # type: ignore[arg-type]
            rewards_controller=mock_rewards,
        )

        await worker.start()
        await queue.add("TestUser", "Hello!", redemption_id="redemption-456")
        await asyncio.sleep(0.1)
        await worker.stop()

        mock_rewards.cancel_redemption.assert_called_once_with("redemption-456")
