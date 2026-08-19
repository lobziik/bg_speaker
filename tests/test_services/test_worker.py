"""Tests for the QueueWorker service."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.api.websocket import WebSocketManager
from src.db.manager import DatabaseManager
from src.db.repositories.settings import SettingsRepository
from src.models.narration import LanguageCode, NarrationRequest, NarrationResult
from src.models.settings import LanguageSettings, QueueSettings, TTSVoiceSettings
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
        moderation_latency_ms=0,
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
        pipeline=mock_pipeline,
        ws_manager=mock_ws_manager,
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
            moderation_latency_ms=0,
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
            pipeline=fast_pipeline,
            ws_manager=mock_ws_manager,
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
            pipeline=error_pipeline,
            ws_manager=mock_ws_manager,
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
            moderation_latency_ms=0,
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
            pipeline=error_pipeline,
            ws_manager=mock_ws_manager,
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
            moderation_latency_ms=0,
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
            pipeline=fast_pipeline,
            ws_manager=mock_ws_manager,
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
            pipeline=error_pipeline,
            ws_manager=mock_ws_manager,
            rewards_controller=mock_rewards,
        )

        await worker.start()
        await queue.add("TestUser", "Hello!", redemption_id="redemption-456")
        await asyncio.sleep(0.1)
        await worker.stop()

        mock_rewards.cancel_redemption.assert_called_once_with("redemption-456")


class TestWorkerPipelineSwap:
    """Swapping providers at runtime without restarting the worker."""

    @pytest.mark.asyncio
    async def test_set_pipeline_used_for_next_item(
        self,
        worker: QueueWorker,
        queue: NarrationQueue,
        mock_pipeline: MagicMock,
    ) -> None:
        """Items queued after the swap run through the replacement pipeline."""
        replacement = MagicMock(spec=NarrationPipeline)
        replacement.llm_provider_name = "gemini"
        replacement.tts_provider_name = "gemini"
        replacement.process = mock_pipeline.process
        mock_pipeline.llm_provider_name = "groq"
        mock_pipeline.tts_provider_name = "piper"

        await worker.set_pipeline(replacement)
        await worker.start()

        await queue.add(user="TestUser", message="After the swap")
        await asyncio.sleep(0.3)
        await worker.stop()

        assert worker._pipeline is replacement

    @pytest.mark.asyncio
    async def test_set_pipeline_waits_for_in_flight_item(
        self,
        worker: QueueWorker,
        queue: NarrationQueue,
        mock_pipeline: MagicMock,
    ) -> None:
        """A swap blocks until the narration being processed completes.

        This is what makes it safe to close the previous providers afterwards.
        """
        release = asyncio.Event()
        original_process = mock_pipeline.process

        async def slow_process(*args: object, **kwargs: object) -> object:
            await release.wait()
            return await original_process(*args, **kwargs)

        mock_pipeline.process = slow_process
        mock_pipeline.llm_provider_name = "groq"
        mock_pipeline.tts_provider_name = "piper"

        replacement = MagicMock(spec=NarrationPipeline)
        replacement.llm_provider_name = "gemini"
        replacement.tts_provider_name = "gemini"

        await worker.start()
        await queue.add(user="TestUser", message="In flight")
        await asyncio.sleep(0.1)

        swap = asyncio.create_task(worker.set_pipeline(replacement))
        await asyncio.sleep(0.1)
        assert not swap.done(), "swap must wait for the in-flight narration"

        release.set()
        await asyncio.wait_for(swap, timeout=5.0)
        await worker.stop()

        assert worker._pipeline is replacement

    @pytest.mark.asyncio
    async def test_swap_does_not_wait_for_audio_playback(
        self,
        worker: QueueWorker,
        queue: NarrationQueue,
        mock_pipeline: MagicMock,
    ) -> None:
        """The lock covers generation, not the wait for the overlay to finish.

        Broadcasting sleeps for the length of the audio; holding the lock across
        it would block a provider change in the UI for the whole narration.
        """
        long_result, metrics = mock_pipeline.process.return_value
        playing = NarrationResult(
            id=long_result.id,
            user=long_result.user,
            voice_text=long_result.voice_text,
            subtitle_text=long_result.subtitle_text,
            target_lang=long_result.target_lang,
            audio_data=long_result.audio_data,
            duration_ms=5000,
        )
        mock_pipeline.process = AsyncMock(return_value=(playing, metrics))
        mock_pipeline.llm_provider_name = "groq"
        mock_pipeline.tts_provider_name = "piper"

        replacement = MagicMock(spec=NarrationPipeline)
        replacement.llm_provider_name = "gemini"
        replacement.tts_provider_name = "gemini"

        await worker.start()
        await queue.add(user="TestUser", message="A long one")

        for _ in range(50):
            if mock_pipeline.process.await_count:
                break
            await asyncio.sleep(0.02)

        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(worker.set_pipeline(replacement), timeout=4.0)
        elapsed = asyncio.get_running_loop().time() - started

        await worker.stop()

        assert elapsed < 1.0, f"swap waited {elapsed:.1f}s for playback to finish"


class TestVoiceOverrideScoping:
    """Per-language voice overrides belong to Piper and must not leak."""

    @pytest_asyncio.fixture
    async def db(self, tmp_path: Path) -> AsyncIterator[DatabaseManager]:
        """A database holding a Piper voice override for English."""
        manager = DatabaseManager(db_path=tmp_path / "data" / "narrator.db")
        await manager.initialize()
        repo = SettingsRepository(manager.connection)
        await repo.set(
            "tts_voice",
            TTSVoiceSettings(voice_overrides={LanguageCode.EN: "en_US-amy-medium"}),
        )
        await repo.set("language", LanguageSettings(narrator_lang=LanguageCode.EN))
        try:
            yield manager
        finally:
            await manager.close()

    async def _run_one(
        self,
        db: DatabaseManager,
        queue: NarrationQueue,
        mock_pipeline: MagicMock,
        mock_ws_manager: MagicMock,
        tts_provider_name: str,
    ) -> object:
        """Process a single item and return the voice_id handed to the pipeline."""
        mock_pipeline.tts_provider_name = tts_provider_name
        mock_pipeline.llm_provider_name = "groq"

        worker = QueueWorker(
            queue=queue,
            pipeline=mock_pipeline,
            ws_manager=mock_ws_manager,
            db_connection=db.connection,
        )

        await worker.start()
        await queue.add(user="TestUser", message="A sword!")
        await asyncio.sleep(0.3)
        await worker.stop()

        return mock_pipeline.process.call_args.kwargs["voice_id"]

    @pytest.mark.asyncio
    async def test_piper_receives_the_override(
        self,
        db: DatabaseManager,
        queue: NarrationQueue,
        mock_pipeline: MagicMock,
        mock_ws_manager: MagicMock,
    ) -> None:
        """With Piper active the stored override is used."""
        voice_id = await self._run_one(db, queue, mock_pipeline, mock_ws_manager, "piper")

        assert voice_id == "en_US-amy-medium"

    @pytest.mark.asyncio
    async def test_other_provider_does_not_receive_piper_voices(
        self,
        db: DatabaseManager,
        queue: NarrationQueue,
        mock_pipeline: MagicMock,
        mock_ws_manager: MagicMock,
    ) -> None:
        """A Piper voice ID would make every Gemini narration fail validation."""
        voice_id = await self._run_one(db, queue, mock_pipeline, mock_ws_manager, "gemini")

        assert voice_id is None
