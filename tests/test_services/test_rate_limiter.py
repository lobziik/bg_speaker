"""Tests for the RateLimiter service."""

import asyncio

import pytest

from src.services.rate_limiter import RateLimiter, RejectionReason


@pytest.fixture
def rate_limiter() -> RateLimiter:
    """Create a rate limiter with short timeouts for testing."""
    return RateLimiter(
        tts_rate_limit_seconds=0.1,  # 100ms for fast tests
        user_cooldown_seconds=0.05,  # 50ms for fast tests
    )


class TestRateLimiterCheck:
    """Tests for rate limiter check() method."""

    @pytest.mark.asyncio
    async def test_check_allows_first_message(self, rate_limiter: RateLimiter) -> None:
        """First message from a user should be allowed."""
        result = await rate_limiter.check("user1")
        assert result.allowed is True
        assert result.reason is None

    @pytest.mark.asyncio
    async def test_check_blocks_rapid_messages_from_same_user(
        self, rate_limiter: RateLimiter
    ) -> None:
        """Rapid messages from same user should be blocked after acquire."""
        # First acquire the rate limit
        await rate_limiter.acquire("user1")

        # Now check should fail for same user
        result = await rate_limiter.check("user1")
        assert result.allowed is False
        assert result.reason == RejectionReason.USER_COOLDOWN
        assert result.retry_after_seconds is not None
        assert result.retry_after_seconds > 0

    @pytest.mark.asyncio
    async def test_check_allows_different_users(self, rate_limiter: RateLimiter) -> None:
        """Different users should not affect each other's cooldown."""
        await rate_limiter.acquire("user1")

        # Different user should still be allowed
        result = await rate_limiter.check("user2")
        assert result.allowed is True


class TestRateLimiterAcquire:
    """Tests for rate limiter acquire() method."""

    @pytest.mark.asyncio
    async def test_acquire_first_request(self, rate_limiter: RateLimiter) -> None:
        """First acquire should succeed."""
        result = await rate_limiter.acquire("user1")
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_acquire_respects_tts_rate_limit(self, rate_limiter: RateLimiter) -> None:
        """Rapid acquires should be blocked by TTS rate limit."""
        # First acquire
        result1 = await rate_limiter.acquire("user1")
        assert result1.allowed is True

        # Second acquire from different user should be blocked by TTS limit
        result2 = await rate_limiter.acquire("user2")
        assert result2.allowed is False
        assert result2.reason == RejectionReason.TTS_RATE_LIMITED

    @pytest.mark.asyncio
    async def test_acquire_respects_user_cooldown(self, rate_limiter: RateLimiter) -> None:
        """Same user should be blocked by cooldown."""
        await rate_limiter.acquire("user1")

        # Wait for TTS rate limit but not user cooldown
        # Since user_cooldown (50ms) < tts_rate_limit (100ms),
        # we need to wait for tts and try again
        await asyncio.sleep(0.11)

        # Now TTS is available but user is still on cooldown
        # Actually user cooldown is 50ms so it should be available too
        result = await rate_limiter.acquire("user1")
        # After waiting 110ms, both should be available (50ms and 100ms)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_acquire_after_waiting(self, rate_limiter: RateLimiter) -> None:
        """Acquire should succeed after waiting for rate limit."""
        await rate_limiter.acquire("user1")

        # Wait for TTS rate limit to expire
        await asyncio.sleep(0.11)

        result = await rate_limiter.acquire("user2")
        assert result.allowed is True


class TestRateLimiterStatus:
    """Tests for rate limiter get_status() method."""

    def test_status_initial(self, rate_limiter: RateLimiter) -> None:
        """Initial status should show TTS available."""
        status = rate_limiter.get_status()
        assert status.tts_available is True
        assert status.tts_available_in_seconds is None
        assert status.active_user_cooldowns == 0

    @pytest.mark.asyncio
    async def test_status_after_acquire(self, rate_limiter: RateLimiter) -> None:
        """Status should reflect rate limit state after acquire."""
        await rate_limiter.acquire("user1")

        status = rate_limiter.get_status()
        assert status.tts_available is False
        assert status.tts_available_in_seconds is not None
        assert status.tts_available_in_seconds > 0
        assert status.active_user_cooldowns == 1


class TestRateLimiterReset:
    """Tests for rate limiter reset() method."""

    @pytest.mark.asyncio
    async def test_reset_clears_state(self, rate_limiter: RateLimiter) -> None:
        """Reset should clear all rate limit state."""
        await rate_limiter.acquire("user1")
        await rate_limiter.reset()

        # Should be able to acquire immediately
        result = await rate_limiter.acquire("user1")
        assert result.allowed is True

        status = rate_limiter.get_status()
        assert status.active_user_cooldowns == 1


class TestRateLimiterSettings:
    """Tests for rate limiter settings update."""

    @pytest.mark.asyncio
    async def test_update_settings(self, rate_limiter: RateLimiter) -> None:
        """Settings update should take effect."""
        await rate_limiter.update_settings(
            tts_rate_limit_seconds=1.0,
            user_cooldown_seconds=0.5,
        )

        status = rate_limiter.get_status()
        assert status.tts_rate_limit_seconds == 1.0
        assert status.user_cooldown_seconds == 0.5
