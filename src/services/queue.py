"""Message queue with priority and rate limiting support."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

from src.services.rate_limiter import RateLimiter, RejectionReason

if TYPE_CHECKING:
    from src.models.settings import QueueSettings

logger = structlog.get_logger()


class QueueEventType(StrEnum):
    """Queue state change event types."""

    ITEM_ADDED = "item_added"
    ITEM_REMOVED = "item_removed"
    ITEM_PROCESSING = "item_processing"
    ITEM_COMPLETED = "item_completed"
    QUEUE_FULL = "queue_full"


@dataclass
class QueueItem:
    """Item in the narration queue.

    Attributes:
        id: Unique identifier for this queue item.
        user: Username who sent the message.
        message: The message text.
        redemption_id: Twitch redemption ID for fulfill/cancel (optional).
        created_at: When the item was added to the queue.
        priority: Higher priority items are processed first.
    """

    id: str
    user: str
    message: str
    redemption_id: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    priority: int = 0  # Higher = processed first

    def __hash__(self) -> int:
        """Hash by ID for set operations."""
        return hash(self.id)


@dataclass(frozen=True)
class QueueAddResult:
    """Result of attempting to add item to queue.

    Attributes:
        success: Whether the item was added.
        item_id: The queue item ID if successful.
        queue_position: Position in queue (1-indexed) if successful.
        rejection_reason: Why the item was rejected.
        retry_after_seconds: When to retry if rate limited.
    """

    success: bool
    item_id: str | None = None
    queue_position: int | None = None
    rejection_reason: RejectionReason | None = None
    retry_after_seconds: float | None = None


# Type alias for event handlers
QueueEventHandler = Callable[[QueueEventType, QueueItem | None], Awaitable[None]]


class NarrationQueue:
    """Async message queue with priority support and rate limiting.

    Features:
    - Priority users (VIPs, subscribers) get processed first
    - Integrates with RateLimiter for per-user cooldowns
    - Queue size limit with configurable max
    - Message length validation
    - Event emission for UI updates

    Thread-safe via asyncio.Lock.
    """

    def __init__(
        self,
        settings: QueueSettings,
        rate_limiter: RateLimiter,
    ) -> None:
        """Initialize the narration queue.

        Args:
            settings: Queue configuration settings.
            rate_limiter: Rate limiter for cooldown checks.
        """
        self._settings = settings
        self._rate_limiter = rate_limiter
        self._queue: deque[QueueItem] = deque()
        self._lock = asyncio.Lock()
        self._event_handlers: list[QueueEventHandler] = []
        self._processing_event = asyncio.Event()
        self._shutdown = False

    @property
    def settings(self) -> QueueSettings:
        """Get current queue settings."""
        return self._settings

    def on_event(self, handler: QueueEventHandler) -> None:
        """Register queue event handler for UI updates.

        Handlers are called for queue state changes.

        Args:
            handler: Async function called with event type and item.
        """
        self._event_handlers.append(handler)

    async def add(
        self,
        user: str,
        message: str,
        redemption_id: str | None = None,
    ) -> QueueAddResult:
        """Add message to queue if validation passes.

        Validates:
        - Queue not full
        - Message length within bounds
        - User not on cooldown

        Args:
            user: Username sending the message.
            message: Message text to narrate.
            redemption_id: Twitch redemption ID for tracking (optional).

        Returns:
            QueueAddResult with success status and position/rejection info.
        """
        async with self._lock:
            # Check queue capacity
            if len(self._queue) >= self._settings.max_size:
                logger.warning(
                    "queue_full",
                    current_size=len(self._queue),
                    max_size=self._settings.max_size,
                )
                await self._emit_event(QueueEventType.QUEUE_FULL, None)
                return QueueAddResult(
                    success=False,
                    rejection_reason=RejectionReason.QUEUE_FULL,
                )

            # Validate message length
            msg_len = len(message)
            if not (
                self._settings.message_min_length <= msg_len <= self._settings.message_max_length
            ):
                logger.info(
                    "message_length_invalid",
                    length=msg_len,
                    min_length=self._settings.message_min_length,
                    max_length=self._settings.message_max_length,
                )
                return QueueAddResult(
                    success=False,
                    rejection_reason=RejectionReason.MESSAGE_FILTERED,
                )

            # Check user cooldown (but don't acquire - just check)
            rate_result = await self._rate_limiter.check(user)
            if not rate_result.allowed and rate_result.reason == RejectionReason.USER_COOLDOWN:
                return QueueAddResult(
                    success=False,
                    rejection_reason=rate_result.reason,
                    retry_after_seconds=rate_result.retry_after_seconds,
                )

            # Create queue item
            item = QueueItem(
                id=str(uuid.uuid4())[:8],
                user=user,
                message=message,
                redemption_id=redemption_id,
                priority=1 if user in self._settings.priority_users else 0,
            )

            # Add and sort
            self._queue.append(item)
            self._sort_queue()

            # Calculate position
            position = list(self._queue).index(item) + 1

            logger.info(
                "queue_item_added",
                item_id=item.id,
                user=user,
                position=position,
                queue_size=len(self._queue),
            )

            await self._emit_event(QueueEventType.ITEM_ADDED, item)

            # Signal that there's work to process
            self._processing_event.set()

            return QueueAddResult(
                success=True,
                item_id=item.id,
                queue_position=position,
            )

    async def get_next(self) -> QueueItem | None:
        """Get next item for processing (waits for rate limit).

        This method:
        1. Waits for items if queue is empty
        2. Checks TTS rate limit
        3. Acquires rate limit slot
        4. Returns item for processing

        Returns:
            Next QueueItem or None if queue is shutting down.
        """
        while not self._shutdown:
            # Wait for items
            if len(self._queue) == 0:
                self._processing_event.clear()
                try:
                    await asyncio.wait_for(
                        self._processing_event.wait(),
                        timeout=1.0,
                    )
                except TimeoutError:
                    continue

            async with self._lock:
                if not self._queue:
                    continue

                item = self._queue[0]

                # Try to acquire rate limit
                result = await self._rate_limiter.acquire(item.user)

                if result.allowed:
                    self._queue.popleft()
                    logger.info(
                        "queue_item_processing",
                        item_id=item.id,
                        user=item.user,
                    )
                    await self._emit_event(QueueEventType.ITEM_PROCESSING, item)
                    return item

            # Rate limited - wait and retry
            if result.retry_after_seconds:
                await asyncio.sleep(result.retry_after_seconds)

        return None

    async def mark_completed(self, item: QueueItem) -> None:
        """Mark item as completed after successful processing.

        Args:
            item: The queue item that was processed.
        """
        logger.info("queue_item_completed", item_id=item.id)
        await self._emit_event(QueueEventType.ITEM_COMPLETED, item)

    async def skip(self, item_id: str) -> bool:
        """Remove item from queue by ID.

        Args:
            item_id: The queue item ID to remove.

        Returns:
            True if item was found and removed.
        """
        async with self._lock:
            for i, item in enumerate(self._queue):
                if item.id == item_id:
                    del self._queue[i]
                    logger.info("queue_item_skipped", item_id=item_id)
                    await self._emit_event(QueueEventType.ITEM_REMOVED, item)
                    return True
            return False

    async def get_items(self) -> list[QueueItem]:
        """Get current queue items (for UI display).

        Returns:
            List of current queue items in order.
        """
        async with self._lock:
            return list(self._queue)

    async def clear(self) -> int:
        """Clear all items from queue.

        Returns:
            Number of items cleared.
        """
        async with self._lock:
            count = len(self._queue)
            self._queue.clear()
            logger.info("queue_cleared", count=count)
            return count

    async def update_settings(self, settings: QueueSettings) -> None:
        """Update queue settings dynamically.

        Args:
            settings: New queue settings.
        """
        async with self._lock:
            self._settings = settings
            logger.info("queue_settings_updated")

    async def shutdown(self) -> None:
        """Shutdown the queue, stopping the get_next loop."""
        self._shutdown = True
        self._processing_event.set()  # Wake up any waiters

    def _sort_queue(self) -> None:
        """Sort by priority (desc), then by created_at (asc)."""
        items = list(self._queue)
        items.sort(key=lambda x: (-x.priority, x.created_at))
        self._queue = deque(items)

    async def _emit_event(
        self,
        event_type: QueueEventType,
        item: QueueItem | None,
    ) -> None:
        """Emit event to all registered handlers."""
        for handler in self._event_handlers:
            try:
                await handler(event_type, item)
            except Exception as e:
                logger.error(
                    "queue_event_handler_error",
                    event=event_type,
                    error=str(e),
                )

    def __len__(self) -> int:
        """Get current queue size."""
        return len(self._queue)
