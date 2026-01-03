"""Queue worker service for processing narrations.

Runs as a background task, pulling items from the queue,
processing them through the pipeline, and broadcasting
results via WebSocket.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import aiosqlite
import structlog

from src.api.websocket import WebSocketManager
from src.db.repositories.narration_log import NarrationLogRepository
from src.models.narration import NarrationRequest, NarrationResult
from src.services.pipeline import NarrationPipeline
from src.services.queue import NarrationQueue, QueueItem

if TYPE_CHECKING:
    from src.services.twitch.rewards import TwitchRewardController

logger = structlog.get_logger()


class QueueWorker:
    """Background worker that processes the narration queue.

    Responsibilities:
    - Pull items from queue (respecting rate limits)
    - Process through pipeline (LLM → TTS)
    - Broadcast results via WebSocket
    - Handle errors and redemption status updates
    """

    def __init__(
        self,
        queue: NarrationQueue,
        pipeline: NarrationPipeline,
        ws_manager: WebSocketManager,
        rewards_controller: TwitchRewardController | None = None,
        db_connection: aiosqlite.Connection | None = None,
    ) -> None:
        """Initialize the queue worker.

        Args:
            queue: The narration queue to process.
            pipeline: The narration pipeline for LLM → TTS.
            ws_manager: WebSocket manager for broadcasting.
            rewards_controller: Optional Twitch rewards for fulfill/cancel.
            db_connection: Optional database connection for logging.
        """
        self._queue = queue
        self._pipeline = pipeline
        self._ws_manager = ws_manager
        self._rewards = rewards_controller
        self._db_connection = db_connection
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Check if worker is running."""
        return self._running

    async def start(self) -> None:
        """Start the worker background task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("queue_worker_started")

    async def stop(self) -> None:
        """Stop the worker and wait for completion."""
        self._running = False

        if self._task:
            # Cancel and wait
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        logger.info("queue_worker_stopped")

    async def _run_loop(self) -> None:
        """Main processing loop."""
        while self._running:
            try:
                # Get next item (blocks until available or shutdown)
                item = await self._queue.get_next()

                if item is None:
                    # Queue is shutting down
                    break

                await self._process_item(item)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("queue_worker_error", error=str(e))
                # Continue processing after error
                await asyncio.sleep(1.0)

    async def _process_item(self, item: QueueItem) -> None:
        """Process a single queue item.

        Args:
            item: The queue item to process.
        """
        logger.info(
            "processing_queue_item",
            item_id=item.id,
            user=item.user,
            message_length=len(item.message),
        )

        try:
            # Create narration request
            request = NarrationRequest(
                user=item.user,
                message=item.message,
            )

            # Process through pipeline
            result, metrics = await self._pipeline.process(request)

            # Broadcast to WebSocket clients
            await self._broadcast_narration(result)

            # Mark completed in queue
            await self._queue.mark_completed(item)

            # Log successful narration
            await self._log_narration(
                item=item,
                result=result,
                status="success",
                llm_latency_ms=metrics.llm_latency_ms,
                tts_latency_ms=metrics.tts_latency_ms,
                total_latency_ms=metrics.llm_latency_ms + metrics.tts_latency_ms,
            )

            # Fulfill Twitch redemption if applicable
            if item.redemption_id and self._rewards:
                try:
                    await self._rewards.fulfill_redemption(item.redemption_id)
                except Exception as e:
                    logger.warning(
                        "redemption_fulfill_failed",
                        redemption_id=item.redemption_id,
                        error=str(e),
                    )

            logger.info(
                "queue_item_processed",
                item_id=item.id,
                narration_id=result.id,
                duration_ms=result.duration_ms,
                llm_latency_ms=metrics.llm_latency_ms,
                tts_latency_ms=metrics.tts_latency_ms,
            )

        except Exception as e:
            logger.error(
                "queue_item_failed",
                item_id=item.id,
                user=item.user,
                error=str(e),
            )

            # Log failed narration
            await self._log_narration(
                item=item,
                result=None,
                status="error",
                error_message=str(e),
            )

            # Broadcast error to clients
            await self._ws_manager.broadcast_narration_error(
                narration_id=item.id,
                error=str(e),
                code="processing_error",
            )

            # Cancel Twitch redemption if applicable (refund points)
            if item.redemption_id and self._rewards:
                try:
                    await self._rewards.cancel_redemption(item.redemption_id)
                except Exception as cancel_error:
                    logger.warning(
                        "redemption_cancel_failed",
                        redemption_id=item.redemption_id,
                        error=str(cancel_error),
                    )

    async def _log_narration(
        self,
        *,
        item: QueueItem,
        result: NarrationResult | None,
        status: str,
        llm_latency_ms: int | None = None,
        tts_latency_ms: int | None = None,
        total_latency_ms: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Log narration to database.

        Args:
            item: The queue item being processed.
            result: The narration result (if successful).
            status: Status of the narration (success, error, etc.).
            llm_latency_ms: LLM processing time.
            tts_latency_ms: TTS processing time.
            total_latency_ms: Total processing time.
            error_message: Error message if failed.
        """
        if not self._db_connection:
            return

        try:
            log_repo = NarrationLogRepository(self._db_connection)
            await log_repo.log_narration(
                id=result.id if result else item.id,
                user=item.user,
                message_original=item.message,
                text_formatted=result.text_original if result else None,
                text_translated=result.text_translated if result else None,
                was_translated=result.was_translated if result else False,
                status=status,
                source_lang="en",  # TODO: Get from settings
                target_lang="en",  # TODO: Get from settings
                llm_provider="groq",  # TODO: Get from pipeline
                tts_provider="piper",  # TODO: Get from pipeline
                latency_llm_ms=llm_latency_ms,
                latency_tts_ms=tts_latency_ms,
                latency_total_ms=total_latency_ms,
                error_message=error_message,
            )
        except Exception as log_error:
            # Don't fail the narration if logging fails
            logger.warning("narration_log_failed", error=str(log_error))

    async def _broadcast_narration(
        self,
        result: NarrationResult,
    ) -> None:
        """Broadcast narration to WebSocket clients.

        Sends in order:
        1. narration_start (with subtitle text)
        2. audio_data (base64 WAV)
        3. narration_end (after duration)

        Args:
            result: The narration result to broadcast.
        """

        # 1. Send narration start
        await self._ws_manager.broadcast_narration_start(
            narration_id=result.id,
            user=result.user,
            text=result.text_original,
        )

        # 2. Send audio data
        await self._ws_manager.broadcast_audio_data(
            narration_id=result.id,
            audio_data=result.audio_data,
            duration_ms=result.duration_ms,
        )

        # 3. Wait for audio duration, then send end
        # The client handles actual playback timing, but we send end
        # after a delay to signal the narration is complete
        await asyncio.sleep(result.duration_ms / 1000.0)

        await self._ws_manager.broadcast_narration_end(
            narration_id=result.id,
            duration_ms=result.duration_ms,
        )
