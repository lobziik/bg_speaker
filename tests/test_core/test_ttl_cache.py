"""Tests for TTL cache implementation."""

import asyncio
import time

import pytest

from src.core.ttl_cache import CacheEntry, TTLCache


class TestCacheEntry:
    """Tests for CacheEntry dataclass."""

    def test_entry_stores_value(self) -> None:
        """Entry should store the provided value."""
        entry: CacheEntry[str] = CacheEntry(value="test_value")
        assert entry.value == "test_value"

    def test_entry_sets_initial_timestamp(self) -> None:
        """Entry should set last_accessed to current time on creation."""
        before = time.time()
        entry: CacheEntry[str] = CacheEntry(value="test")
        after = time.time()

        assert before <= entry.last_accessed <= after

    def test_touch_updates_timestamp(self) -> None:
        """touch() should update last_accessed to current time."""
        entry: CacheEntry[str] = CacheEntry(value="test")
        original_time = entry.last_accessed

        # Small delay to ensure timestamp changes
        time.sleep(0.01)
        entry.touch()

        assert entry.last_accessed > original_time

    def test_age_seconds_returns_elapsed_time(self) -> None:
        """age_seconds() should return time since last access."""
        entry: CacheEntry[str] = CacheEntry(value="test")

        time.sleep(0.05)
        age = entry.age_seconds()

        assert 0.04 < age < 0.1  # Allow some tolerance


class TestTTLCacheInit:
    """Tests for TTLCache initialization."""

    def test_init_with_defaults(self) -> None:
        """Cache should initialize with default values."""
        cache: TTLCache[str] = TTLCache(name="test")

        assert cache.ttl_seconds == TTLCache.DEFAULT_TTL_SECONDS
        assert cache.cleanup_interval_seconds == TTLCache.DEFAULT_CLEANUP_INTERVAL_SECONDS
        assert cache.size() == 0

    def test_init_with_custom_values(self) -> None:
        """Cache should accept custom TTL and interval values."""
        cache: TTLCache[str] = TTLCache(
            ttl_seconds=300.0,
            cleanup_interval_seconds=30.0,
            name="custom",
        )

        assert cache.ttl_seconds == 300.0
        assert cache.cleanup_interval_seconds == 30.0

    def test_init_rejects_zero_ttl(self) -> None:
        """Cache should reject TTL <= 0."""
        with pytest.raises(ValueError, match="ttl_seconds must be positive"):
            TTLCache[str](ttl_seconds=0)

    def test_init_rejects_negative_ttl(self) -> None:
        """Cache should reject negative TTL."""
        with pytest.raises(ValueError, match="ttl_seconds must be positive"):
            TTLCache[str](ttl_seconds=-10)

    def test_init_rejects_zero_cleanup_interval(self) -> None:
        """Cache should reject cleanup_interval <= 0."""
        with pytest.raises(ValueError, match="cleanup_interval_seconds must be positive"):
            TTLCache[str](cleanup_interval_seconds=0)


class TestTTLCacheBasicOperations:
    """Tests for basic get/set operations."""

    @pytest.mark.asyncio
    async def test_set_and_get_value(self) -> None:
        """Should store and retrieve a value."""
        cache: TTLCache[str] = TTLCache(name="test")

        await cache.set("key1", "value1")
        result = await cache.get("key1")

        assert result == "value1"
        assert cache.size() == 1

    @pytest.mark.asyncio
    async def test_get_missing_key_returns_none(self) -> None:
        """get() should return None for missing key."""
        cache: TTLCache[str] = TTLCache(name="test")

        result = await cache.get("missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_set_overwrites_existing_value(self) -> None:
        """set() should overwrite existing value and reset TTL."""
        cache: TTLCache[str] = TTLCache(name="test")

        await cache.set("key1", "value1")
        await cache.set("key1", "value2")
        result = await cache.get("key1")

        assert result == "value2"
        assert cache.size() == 1

    @pytest.mark.asyncio
    async def test_multiple_keys(self) -> None:
        """Should handle multiple keys independently."""
        cache: TTLCache[str] = TTLCache(name="test")

        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")

        assert await cache.get("key1") == "value1"
        assert await cache.get("key2") == "value2"
        assert await cache.get("key3") == "value3"
        assert cache.size() == 3


class TestTTLCacheRemove:
    """Tests for manual removal."""

    @pytest.mark.asyncio
    async def test_remove_existing_key(self) -> None:
        """remove() should return and delete existing entry."""
        cache: TTLCache[str] = TTLCache(name="test")
        await cache.set("key1", "value1")

        result = await cache.remove("key1")

        assert result == "value1"
        assert cache.size() == 0
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_remove_missing_key(self) -> None:
        """remove() should return None for missing key."""
        cache: TTLCache[str] = TTLCache(name="test")

        result = await cache.remove("missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_remove_calls_on_evict(self) -> None:
        """remove() should call on_evict callback."""
        evicted: list[tuple[str, str]] = []

        def on_evict(key: str, value: str) -> None:
            evicted.append((key, value))

        cache: TTLCache[str] = TTLCache(name="test", on_evict=on_evict)
        await cache.set("key1", "value1")

        await cache.remove("key1")

        assert evicted == [("key1", "value1")]


class TestTTLCacheClear:
    """Tests for cache clearing."""

    @pytest.mark.asyncio
    async def test_clear_removes_all_entries(self) -> None:
        """clear() should remove all entries."""
        cache: TTLCache[str] = TTLCache(name="test")
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")

        count = await cache.clear()

        assert count == 2
        assert cache.size() == 0

    @pytest.mark.asyncio
    async def test_clear_empty_cache(self) -> None:
        """clear() on empty cache should return 0."""
        cache: TTLCache[str] = TTLCache(name="test")

        count = await cache.clear()

        assert count == 0

    @pytest.mark.asyncio
    async def test_clear_calls_on_evict_for_all(self) -> None:
        """clear() should call on_evict for each entry."""
        evicted: list[str] = []

        def on_evict(key: str, _value: str) -> None:
            evicted.append(key)

        cache: TTLCache[str] = TTLCache(name="test", on_evict=on_evict)
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")

        await cache.clear()

        assert sorted(evicted) == ["key1", "key2", "key3"]


class TestTTLCacheAccessResetsTTL:
    """Tests for TTL reset on access."""

    @pytest.mark.asyncio
    async def test_get_updates_last_accessed(self) -> None:
        """get() should update entry's last_accessed timestamp."""
        cache: TTLCache[str] = TTLCache(
            ttl_seconds=0.15,
            cleanup_interval_seconds=0.05,
            name="test",
        )

        await cache.start()
        try:
            await cache.set("key1", "value1")

            # Access entry to reset TTL before it expires
            await asyncio.sleep(0.1)
            result = await cache.get("key1")
            assert result == "value1"

            # Wait a bit more - entry should still exist because we reset TTL
            await asyncio.sleep(0.1)
            result = await cache.get("key1")
            assert result == "value1"
        finally:
            await cache.stop()


class TestTTLCacheExpiration:
    """Tests for TTL-based expiration."""

    @pytest.mark.asyncio
    async def test_entry_evicted_after_ttl(self) -> None:
        """Entry should be evicted after TTL expires."""
        evicted: list[str] = []

        def on_evict(key: str, _value: str) -> None:
            evicted.append(key)

        cache: TTLCache[str] = TTLCache(
            ttl_seconds=0.1,  # 100ms TTL
            cleanup_interval_seconds=0.05,  # 50ms cleanup
            on_evict=on_evict,
            name="test",
        )

        await cache.start()
        try:
            await cache.set("key1", "value1")

            # Wait for entry to expire and cleanup to run
            await asyncio.sleep(0.2)

            assert "key1" in evicted
            assert cache.size() == 0
            assert await cache.get("key1") is None
        finally:
            await cache.stop()

    @pytest.mark.asyncio
    async def test_only_expired_entries_evicted(self) -> None:
        """Only expired entries should be evicted."""
        cache: TTLCache[str] = TTLCache(
            ttl_seconds=0.15,
            cleanup_interval_seconds=0.05,
            name="test",
        )

        await cache.start()
        try:
            await cache.set("key1", "value1")
            await asyncio.sleep(0.1)
            await cache.set("key2", "value2")  # Added later, won't expire yet

            # Wait for key1 to expire but not key2
            await asyncio.sleep(0.1)

            assert await cache.get("key1") is None  # Expired
            assert await cache.get("key2") == "value2"  # Still valid
        finally:
            await cache.stop()


class TestTTLCacheLifecycle:
    """Tests for start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_cleanup_task(self) -> None:
        """start() should create the cleanup background task."""
        cache: TTLCache[str] = TTLCache(name="test")

        await cache.start()
        try:
            # Task should be running
            assert cache._cleanup_task is not None
            assert not cache._cleanup_task.done()
        finally:
            await cache.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_cleanup_task(self) -> None:
        """stop() should cancel the cleanup task."""
        cache: TTLCache[str] = TTLCache(name="test")

        await cache.start()
        await cache.stop()

        assert cache._cleanup_task is None
        assert cache._running is False

    @pytest.mark.asyncio
    async def test_double_start_is_safe(self) -> None:
        """Calling start() twice should be safe (no-op)."""
        cache: TTLCache[str] = TTLCache(name="test")

        await cache.start()
        task1 = cache._cleanup_task

        await cache.start()  # Should be no-op
        task2 = cache._cleanup_task

        assert task1 is task2
        await cache.stop()

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self) -> None:
        """Calling stop() twice should be safe (no-op)."""
        cache: TTLCache[str] = TTLCache(name="test")

        await cache.start()
        await cache.stop()
        await cache.stop()  # Should be no-op, not raise

        assert cache._cleanup_task is None

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self) -> None:
        """Calling stop() without start() should be safe."""
        cache: TTLCache[str] = TTLCache(name="test")

        await cache.stop()  # Should not raise

        assert cache._cleanup_task is None


class TestTTLCacheThreadSafety:
    """Tests for concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_set_and_get(self) -> None:
        """Concurrent set and get should be safe."""
        cache: TTLCache[int] = TTLCache(name="test")

        async def writer(key: str, value: int) -> None:
            for i in range(10):
                await cache.set(f"{key}_{i}", value + i)

        async def reader(key: str) -> list[int | None]:
            results = []
            for i in range(10):
                results.append(await cache.get(f"{key}_{i}"))
            return results

        # Run multiple writers concurrently
        await asyncio.gather(
            writer("a", 0),
            writer("b", 100),
            writer("c", 200),
        )

        # Verify all entries exist
        assert cache.size() == 30

    @pytest.mark.asyncio
    async def test_concurrent_set_same_key(self) -> None:
        """Concurrent sets to same key should not corrupt data."""
        cache: TTLCache[int] = TTLCache(name="test")

        async def setter(value: int) -> None:
            for _ in range(50):
                await cache.set("shared_key", value)
                await asyncio.sleep(0)  # Yield to other tasks

        await asyncio.gather(
            setter(1),
            setter(2),
            setter(3),
        )

        # Key should exist with some valid value
        result = await cache.get("shared_key")
        assert result in (1, 2, 3)
        assert cache.size() == 1
