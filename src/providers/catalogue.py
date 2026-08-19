"""Short-lived memo for catalogues fetched from a provider's API.

The settings form asks every configured provider for its model or voice
catalogue on each render, and builds a throwaway provider instance to do it. A
module-level memo keeps that from turning every page load into an API round
trip, while still letting the list refresh on its own.

The TTL is absolute rather than an idle timeout: viewing the settings page often
must not pin a stale catalogue forever.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger()

DEFAULT_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class _Cached[T]:
    """A catalogue and the moment it was fetched.

    Attributes:
        items: The catalogue as returned by the provider.
        fetched_at: Monotonic timestamp of the fetch.
    """

    items: list[T]
    fetched_at: float


class ApiCatalogue[T]:
    """Caches one API-fetched catalogue, falling back to a static list.

    A provider that cannot reach its API still has to render a usable form, so
    a failed fetch is logged and answered from the caller's built-in catalogue
    rather than raised.
    """

    def __init__(self, name: str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        """Initialize the memo.

        Args:
            name: Identifier used in log lines.
            ttl_seconds: How long a fetched catalogue stays fresh.
        """
        self._name = name
        self._ttl = ttl_seconds
        self._cached: _Cached[T] | None = None
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Drop the memo, so the next call fetches again."""
        self._cached = None

    async def get(
        self,
        fetch: Callable[[], Awaitable[list[T]]],
        fallback: list[T],
    ) -> list[T]:
        """Return the catalogue, fetching it if the memo is cold or stale.

        Args:
            fetch: Coroutine function that queries the provider's API. It may
                raise; the failure is logged and the fallback returned.
            fallback: Built-in catalogue to use when the API cannot be reached
                or returns nothing usable.

        Returns:
            The freshest catalogue available, never empty as long as the
            fallback is not.
        """
        async with self._lock:
            cached = self._cached
            if cached is not None and (time.monotonic() - cached.fetched_at) < self._ttl:
                return list(cached.items)

            try:
                items = await fetch()
            except Exception as e:
                # Deliberately broad: every provider SDK raises its own error
                # type, and a settings page that 500s because a catalogue call
                # failed is worse than one showing the built-in list.
                logger.warning(
                    "catalogue_fetch_failed",
                    catalogue=self._name,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return list(fallback)

            if not items:
                logger.warning("catalogue_fetch_empty", catalogue=self._name)
                return list(fallback)

            self._cached = _Cached(items=items, fetched_at=time.monotonic())
            logger.info("catalogue_fetched", catalogue=self._name, count=len(items))
            return list(items)
