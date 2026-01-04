"""TTL-based cache with automatic cleanup for memory-intensive resources.

This module provides a generic TTL cache that automatically evicts entries
after a configurable period of inactivity. Designed for caching heavy resources
like TTS voice models that should be unloaded when not in use.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger()


@dataclass
class CacheEntry[T]:
    """A cache entry with last access timestamp.

    Attributes:
        value: The cached resource.
        last_accessed: Unix timestamp of last access.
    """

    value: T
    last_accessed: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Update last accessed timestamp to current time."""
        self.last_accessed = time.time()

    def age_seconds(self) -> float:
        """Get age of this entry in seconds since last access.

        Returns:
            Seconds since last access.
        """
        return time.time() - self.last_accessed


class TTLCache[T]:
    """Thread-safe cache with TTL-based automatic cleanup.

    Resources are evicted after `ttl_seconds` of inactivity.
    A background task periodically scans for expired entries.

    Type Parameters:
        T: Type of cached resources.

    Example:
        >>> cache: TTLCache[Model] = TTLCache(
        ...     ttl_seconds=1800,  # 30 minutes
        ...     cleanup_interval_seconds=60,  # check every minute
        ...     on_evict=lambda k, v: logger.info("evicted", key=k),
        ...     name="models",
        ... )
        >>> await cache.start()
        >>> await cache.set("model_a", heavy_model)
        >>> model = await cache.get("model_a")  # resets TTL
        >>> await cache.stop()
    """

    # Default TTL: 30 minutes
    DEFAULT_TTL_SECONDS: float = 1800.0
    # Default cleanup interval: 1 minute
    DEFAULT_CLEANUP_INTERVAL_SECONDS: float = 60.0

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        cleanup_interval_seconds: float = DEFAULT_CLEANUP_INTERVAL_SECONDS,
        on_evict: Callable[[str, T], None] | None = None,
        name: str = "ttl_cache",
    ) -> None:
        """Initialize the TTL cache.

        Args:
            ttl_seconds: Time-to-live for cache entries in seconds.
                Entries not accessed within this time will be evicted.
            cleanup_interval_seconds: How often to run the cleanup task.
                Shorter intervals mean faster eviction but more CPU overhead.
            on_evict: Optional callback when an entry is evicted.
                Called with (key, value) for cleanup or logging.
            name: Name for logging purposes to distinguish multiple caches.
        """
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        if cleanup_interval_seconds <= 0:
            raise ValueError(
                f"cleanup_interval_seconds must be positive, got {cleanup_interval_seconds}"
            )

        self._entries: dict[str, CacheEntry[T]] = {}
        self._lock = asyncio.Lock()
        self._ttl_seconds = ttl_seconds
        self._cleanup_interval = cleanup_interval_seconds
        self._on_evict = on_evict
        self._name = name
        self._cleanup_task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def ttl_seconds(self) -> float:
        """Get configured TTL in seconds."""
        return self._ttl_seconds

    @property
    def cleanup_interval_seconds(self) -> float:
        """Get configured cleanup interval in seconds."""
        return self._cleanup_interval

    async def start(self) -> None:
        """Start the background cleanup task.

        Safe to call multiple times - subsequent calls are no-ops.
        Must be called before using the cache for automatic cleanup.
        """
        if self._running:
            logger.debug(
                "ttl_cache_already_running",
                cache_name=self._name,
            )
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name=f"ttl_cache_cleanup_{self._name}",
        )
        logger.info(
            "ttl_cache_started",
            cache_name=self._name,
            ttl_seconds=self._ttl_seconds,
            cleanup_interval_seconds=self._cleanup_interval,
        )

    async def stop(self) -> None:
        """Stop the background cleanup task.

        Safe to call multiple times - subsequent calls are no-ops.
        Does NOT clear the cache - call clear() separately if needed.
        """
        if not self._running:
            logger.info(
                "ttl_cache_already_stopped",
                cache_name=self._name,
            )
            return

        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

        logger.info(
            "ttl_cache_stopped",
            cache_name=self._name,
            remaining_entries=len(self._entries),
        )

    async def get(self, key: str) -> T | None:
        """Get entry and update its access time.

        Args:
            key: Cache key.

        Returns:
            Cached value or None if not found.
        """
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                old_age = entry.age_seconds()
                entry.touch()
                logger.debug(
                    "ttl_cache_entry_accessed",
                    cache_name=self._name,
                    key=key,
                    previous_age_seconds=round(old_age, 1),
                )
                return entry.value
            return None

    async def set(self, key: str, value: T) -> None:
        """Set or update cache entry.

        If the key already exists, the value is replaced and TTL is reset.

        Args:
            key: Cache key.
            value: Value to cache.
        """
        async with self._lock:
            is_update = key in self._entries
            self._entries[key] = CacheEntry(value=value)
            logger.info(
                "ttl_cache_entry_added",
                cache_name=self._name,
                key=key,
                is_update=is_update,
                total_entries=len(self._entries),
            )

    async def remove(self, key: str) -> T | None:
        """Manually remove entry from cache.

        Calls on_evict callback if configured.

        Args:
            key: Cache key.

        Returns:
            Removed value or None if not found.
        """
        async with self._lock:
            entry = self._entries.pop(key, None)
            if entry is not None:
                logger.info(
                    "ttl_cache_entry_removed",
                    cache_name=self._name,
                    key=key,
                    age_seconds=round(entry.age_seconds(), 1),
                    remaining_entries=len(self._entries),
                )
                if self._on_evict:
                    self._on_evict(key, entry.value)
                return entry.value
            return None

    async def clear(self) -> int:
        """Clear all entries, calling on_evict for each.

        Returns:
            Number of entries cleared.
        """
        async with self._lock:
            count = len(self._entries)
            if count == 0:
                logger.debug(
                    "ttl_cache_clear_empty",
                    cache_name=self._name,
                )
                return 0

            # Call eviction callback for each entry
            for key, entry in list(self._entries.items()):
                logger.debug(
                    "ttl_cache_entry_clearing",
                    cache_name=self._name,
                    key=key,
                    age_seconds=round(entry.age_seconds(), 1),
                )
                if self._on_evict:
                    self._on_evict(key, entry.value)

            self._entries.clear()
            logger.info(
                "ttl_cache_cleared",
                cache_name=self._name,
                entries_cleared=count,
            )
            return count

    def size(self) -> int:
        """Return current number of cached entries.

        Note: This is not async as it's a simple read operation.
        For accurate count during concurrent modifications, use async methods.
        """
        return len(self._entries)

    async def _cleanup_loop(self) -> None:
        """Background task that periodically evicts expired entries."""
        logger.info(
            "ttl_cache_cleanup_loop_started",
            cache_name=self._name,
        )

        while self._running:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                logger.info(
                    "ttl_cache_cleanup_loop_cancelled",
                    cache_name=self._name,
                )
                break
            except Exception as e:
                # Log error but continue running - cleanup is best-effort
                logger.error(
                    "ttl_cache_cleanup_error",
                    cache_name=self._name,
                    error=str(e),
                    error_type=type(e).__name__,
                )

    async def _cleanup_expired(self) -> int:
        """Remove all expired entries.

        Returns:
            Number of entries evicted.
        """
        now = time.time()
        evicted = 0

        async with self._lock:
            total_entries = len(self._entries)

            # Find oldest entry age for logging
            oldest_age: float | None = None
            if self._entries:
                oldest_age = max(
                    now - entry.last_accessed for entry in self._entries.values()
                )

            # Find expired entries
            expired_keys: list[str] = []
            for key, entry in self._entries.items():
                age = now - entry.last_accessed
                if age > self._ttl_seconds:
                    expired_keys.append(key)
                    logger.info(
                        "ttl_cache_entry_expired",
                        cache_name=self._name,
                        key=key,
                        age_seconds=round(age, 1),
                        ttl_seconds=self._ttl_seconds,
                    )

            # Evict expired entries
            for key in expired_keys:
                entry = self._entries.pop(key)
                if self._on_evict:
                    self._on_evict(key, entry.value)
                evicted += 1

            # Log cleanup tick with stats
            logger.debug(
                "ttl_cache_cleanup_tick",
                cache_name=self._name,
                entries_checked=total_entries,
                entries_evicted=evicted,
                entries_remaining=len(self._entries),
                oldest_entry_age_seconds=round(oldest_age, 1) if oldest_age else None,
                ttl_seconds=self._ttl_seconds,
            )

        if evicted > 0:
            logger.info(
                "ttl_cache_cleanup_summary",
                cache_name=self._name,
                evicted_count=evicted,
                remaining_count=len(self._entries),
            )

        return evicted
