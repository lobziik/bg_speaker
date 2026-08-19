"""Tests for the memoised provider catalogue."""

import pytest

from src.providers.catalogue import ApiCatalogue


class TestApiCatalogue:
    """Fetch once, reuse briefly, and never leave the caller empty-handed."""

    @pytest.mark.asyncio
    async def test_fetches_once_within_the_ttl(self) -> None:
        """A second render reuses the memo instead of calling the API again."""
        calls = 0

        async def fetch() -> list[str]:
            nonlocal calls
            calls += 1
            return ["live"]

        catalogue: ApiCatalogue[str] = ApiCatalogue("test", ttl_seconds=60.0)

        assert await catalogue.get(fetch, ["static"]) == ["live"]
        assert await catalogue.get(fetch, ["static"]) == ["live"]
        assert calls == 1

    @pytest.mark.asyncio
    async def test_refetches_once_stale(self) -> None:
        """The TTL is absolute, so a busy page still sees a refreshed list."""
        calls = 0

        async def fetch() -> list[str]:
            nonlocal calls
            calls += 1
            return [f"live-{calls}"]

        catalogue: ApiCatalogue[str] = ApiCatalogue("test", ttl_seconds=0.0)

        assert await catalogue.get(fetch, ["static"]) == ["live-1"]
        assert await catalogue.get(fetch, ["static"]) == ["live-2"]

    @pytest.mark.asyncio
    async def test_falls_back_when_the_api_fails(self) -> None:
        """An unreachable API must not take the settings form down."""

        async def fetch() -> list[str]:
            raise ConnectionError("no route to host")

        catalogue: ApiCatalogue[str] = ApiCatalogue("test")

        assert await catalogue.get(fetch, ["static"]) == ["static"]

    @pytest.mark.asyncio
    async def test_failures_are_not_cached(self) -> None:
        """A transient outage must not pin the fallback for the whole TTL."""
        attempts = 0

        async def fetch() -> list[str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("transient")
            return ["live"]

        catalogue: ApiCatalogue[str] = ApiCatalogue("test", ttl_seconds=60.0)

        assert await catalogue.get(fetch, ["static"]) == ["static"]
        assert await catalogue.get(fetch, ["static"]) == ["live"]

    @pytest.mark.asyncio
    async def test_empty_listing_falls_back(self) -> None:
        """An empty dropdown is worse than a stale one."""

        async def fetch() -> list[str]:
            return []

        catalogue: ApiCatalogue[str] = ApiCatalogue("test")

        assert await catalogue.get(fetch, ["static"]) == ["static"]

    @pytest.mark.asyncio
    async def test_invalidate_forces_a_refetch(self) -> None:
        """Dropping the memo is how a test - or a key change - starts over."""
        calls = 0

        async def fetch() -> list[str]:
            nonlocal calls
            calls += 1
            return ["live"]

        catalogue: ApiCatalogue[str] = ApiCatalogue("test", ttl_seconds=60.0)

        await catalogue.get(fetch, ["static"])
        catalogue.invalidate()
        await catalogue.get(fetch, ["static"])

        assert calls == 2

    @pytest.mark.asyncio
    async def test_caller_cannot_mutate_the_memo(self) -> None:
        """The cached list is handed out as a copy."""

        async def fetch() -> list[str]:
            return ["live"]

        catalogue: ApiCatalogue[str] = ApiCatalogue("test", ttl_seconds=60.0)

        first = await catalogue.get(fetch, ["static"])
        first.append("tampered")

        assert await catalogue.get(fetch, ["static"]) == ["live"]
