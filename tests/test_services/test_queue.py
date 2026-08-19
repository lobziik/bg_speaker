"""Tests for the NarrationQueue service."""

import pytest

from src.models.settings import QueueSettings
from src.services.queue import (
    NarrationQueue,
    QueueEventType,
    QueueItem,
)
from src.services.rate_limiter import RateLimiter, RejectionReason


@pytest.fixture
def rate_limiter() -> RateLimiter:
    """Create a rate limiter with short timeouts for testing."""
    return RateLimiter(
        tts_rate_limit_seconds=0.1,
        user_cooldown_seconds=0.05,
    )


@pytest.fixture
def queue_settings() -> QueueSettings:
    """Create queue settings for testing."""
    return QueueSettings(
        max_size=10,
        message_min_length=1,
        message_max_length=100,
        cooldown_seconds=5,
        tts_rate_limit_seconds=10,
        priority_users=["vip_user"],
    )


@pytest.fixture
def queue(queue_settings: QueueSettings, rate_limiter: RateLimiter) -> NarrationQueue:
    """Create a narration queue for testing."""
    return NarrationQueue(settings=queue_settings, rate_limiter=rate_limiter)


class TestQueueAdd:
    """Tests for queue add() method."""

    @pytest.mark.asyncio
    async def test_add_valid_message(self, queue: NarrationQueue) -> None:
        """Valid message should be added to queue."""
        result = await queue.add("user1", "Hello world", redemption_id="red-123")

        assert result.success is True
        assert result.item_id is not None
        assert result.queue_position == 1
        assert len(queue) == 1

    @pytest.mark.asyncio
    async def test_add_message_too_short(self, queue: NarrationQueue) -> None:
        """Message shorter than min length should be rejected."""
        result = await queue.add("user1", "")

        assert result.success is False
        assert result.rejection_reason == RejectionReason.MESSAGE_FILTERED

    @pytest.mark.asyncio
    async def test_add_message_too_long(self, queue: NarrationQueue) -> None:
        """Message longer than max length should be rejected."""
        long_message = "x" * 101  # Exceeds max_length of 100

        result = await queue.add("user1", long_message)

        assert result.success is False
        assert result.rejection_reason == RejectionReason.MESSAGE_FILTERED

    @pytest.mark.asyncio
    async def test_add_queue_full(self, rate_limiter: RateLimiter) -> None:
        """Should reject when queue is full."""
        # Create queue with max_size of 2
        small_settings = QueueSettings(max_size=2)
        small_queue = NarrationQueue(settings=small_settings, rate_limiter=rate_limiter)

        # Fill the queue
        await small_queue.add("user1", "msg1")
        await small_queue.add("user2", "msg2")

        # Third should be rejected
        result = await small_queue.add("user3", "msg3")
        assert result.success is False
        assert result.rejection_reason == RejectionReason.QUEUE_FULL

    @pytest.mark.asyncio
    async def test_add_priority_ordering(self, queue: NarrationQueue) -> None:
        """VIP users should be placed before regular users."""
        # Add regular user first
        await queue.add("regular_user", "message1")

        # Add VIP user
        await queue.add("vip_user", "message2")

        items = await queue.get_items()
        assert len(items) == 2
        # VIP should be first
        assert items[0].user == "vip_user"
        assert items[1].user == "regular_user"


class TestQueueSkip:
    """Tests for queue skip() method."""

    @pytest.mark.asyncio
    async def test_skip_existing_item(self, queue: NarrationQueue) -> None:
        """Should successfully skip an existing item."""
        result = await queue.add("user1", "message")
        item_id = result.item_id

        removed = await queue.skip(item_id)  # type: ignore
        assert removed is True
        assert len(queue) == 0

    @pytest.mark.asyncio
    async def test_skip_nonexistent_item(self, queue: NarrationQueue) -> None:
        """Should return False for non-existent item."""
        removed = await queue.skip("nonexistent-id")
        assert removed is False


class TestQueueClear:
    """Tests for queue clear() method."""

    @pytest.mark.asyncio
    async def test_clear_empty_queue(self, queue: NarrationQueue) -> None:
        """Clearing empty queue should return 0."""
        count = await queue.clear()
        assert count == 0

    @pytest.mark.asyncio
    async def test_clear_with_items(self, queue: NarrationQueue) -> None:
        """Should clear all items and return count."""
        await queue.add("user1", "msg1")
        await queue.add("user2", "msg2")

        count = await queue.clear()
        assert count == 2
        assert len(queue) == 0


class TestQueueEvents:
    """Tests for queue event emission."""

    @pytest.mark.asyncio
    async def test_item_added_event(self, queue: NarrationQueue) -> None:
        """Should emit ITEM_ADDED event when adding item."""
        events: list[tuple[QueueEventType, QueueItem | None]] = []

        async def handler(event_type: QueueEventType, item: QueueItem | None) -> None:
            events.append((event_type, item))

        queue.on_event(handler)

        await queue.add("user1", "message")

        assert len(events) == 1
        assert events[0][0] == QueueEventType.ITEM_ADDED
        assert events[0][1] is not None
        assert events[0][1].user == "user1"

    @pytest.mark.asyncio
    async def test_queue_full_event(self, rate_limiter: RateLimiter) -> None:
        """Should emit QUEUE_FULL event when queue is full."""
        small_settings = QueueSettings(max_size=1)
        small_queue = NarrationQueue(settings=small_settings, rate_limiter=rate_limiter)

        events: list[tuple[QueueEventType, QueueItem | None]] = []

        async def handler(event_type: QueueEventType, item: QueueItem | None) -> None:
            events.append((event_type, item))

        small_queue.on_event(handler)

        await small_queue.add("user1", "msg1")
        await small_queue.add("user2", "msg2")  # Should trigger QUEUE_FULL

        assert any(e[0] == QueueEventType.QUEUE_FULL for e in events)


class TestQueueSettings:
    """Tests for queue settings update."""

    @pytest.mark.asyncio
    async def test_update_settings(self, queue: NarrationQueue) -> None:
        """Settings update should take effect."""
        new_settings = QueueSettings(
            max_size=5,
            message_max_length=50,
        )

        await queue.update_settings(new_settings)

        assert queue.settings.max_size == 5
        assert queue.settings.message_max_length == 50
