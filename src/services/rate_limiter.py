"""Rate limiting for TTS and per-user cooldowns."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import structlog

logger = structlog.get_logger()


class RejectionReason(StrEnum):
    """Reasons for rate limit rejection."""

    TTS_RATE_LIMITED = "tts_rate_limited"
    USER_COOLDOWN = "user_cooldown"
    QUEUE_FULL = "queue_full"
    USER_BANNED = "user_banned"
    MESSAGE_FILTERED = "message_filtered"


@dataclass(frozen=True)
class RateLimitResult:
    """Result of rate limit check.

    Attributes:
        allowed: Whether the request is allowed.
        reason: Rejection reason if not allowed.
        retry_after_seconds: Seconds until retry is allowed.
    """

    allowed: bool
    reason: RejectionReason | None = None
    retry_after_seconds: float | None = None


@dataclass(frozen=True)
class RateLimitStatus:
    """Current rate limiter status for UI/API.

    Attributes:
        tts_rate_limit_seconds: Configured TTS rate limit.
        user_cooldown_seconds: Configured per-user cooldown.
        tts_available: Whether TTS is currently available.
        tts_available_in_seconds: Seconds until TTS is available.
        active_user_cooldowns: Number of users currently on cooldown.
    """

    tts_rate_limit_seconds: float
    user_cooldown_seconds: float
    tts_available: bool
    tts_available_in_seconds: float | None
    active_user_cooldowns: int


class RateLimiter:
    """Global TTS rate limiter with per-user cooldowns.

    Two levels of rate limiting:
    1. Global TTS: Minimum interval between ANY TTS generations
    2. Per-user cooldown: Minimum interval for same user

    The global TTS limit prevents overloading the TTS system,
    while per-user cooldown prevents spam from single users.

    Thread-safe via asyncio.Lock.
    """

    def __init__(
        self,
        tts_rate_limit_seconds: float = 10.0,
        user_cooldown_seconds: float = 5.0,
    ) -> None:
        """Initialize rate limiter.

        Args:
            tts_rate_limit_seconds: Minimum seconds between TTS generations.
            user_cooldown_seconds: Minimum seconds between messages from same user.
        """
        self._tts_rate_limit = timedelta(seconds=tts_rate_limit_seconds)
        self._user_cooldown = timedelta(seconds=user_cooldown_seconds)
        self._last_tts_time: datetime | None = None
        self._user_last_message: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def check(self, user: str) -> RateLimitResult:
        """Check if a message from user can be processed.

        This does NOT consume the rate limit - use acquire() for that.
        Use this for queue admission checks.

        Args:
            user: Username (case-insensitive internally).

        Returns:
            RateLimitResult indicating if allowed.
        """
        user_lower = user.lower()
        now = datetime.now()

        # Check user cooldown (more specific check)
        if user_lower in self._user_last_message:
            user_elapsed = now - self._user_last_message[user_lower]
            if user_elapsed < self._user_cooldown:
                retry_after = (self._user_cooldown - user_elapsed).total_seconds()
                return RateLimitResult(
                    allowed=False,
                    reason=RejectionReason.USER_COOLDOWN,
                    retry_after_seconds=retry_after,
                )

        return RateLimitResult(allowed=True)

    async def acquire(self, user: str) -> RateLimitResult:
        """Acquire rate limit slot for TTS generation.

        Call this when actually starting TTS processing,
        not when adding to queue.

        Args:
            user: Username.

        Returns:
            RateLimitResult - if not allowed, contains retry info.
        """
        async with self._lock:
            user_lower = user.lower()
            now = datetime.now()

            # Check user cooldown
            if user_lower in self._user_last_message:
                user_elapsed = now - self._user_last_message[user_lower]
                if user_elapsed < self._user_cooldown:
                    retry_after = (self._user_cooldown - user_elapsed).total_seconds()
                    return RateLimitResult(
                        allowed=False,
                        reason=RejectionReason.USER_COOLDOWN,
                        retry_after_seconds=retry_after,
                    )

            # Check global TTS rate limit
            if self._last_tts_time is not None:
                tts_elapsed = now - self._last_tts_time
                if tts_elapsed < self._tts_rate_limit:
                    retry_after = (self._tts_rate_limit - tts_elapsed).total_seconds()
                    return RateLimitResult(
                        allowed=False,
                        reason=RejectionReason.TTS_RATE_LIMITED,
                        retry_after_seconds=retry_after,
                    )

            # Acquire: update timestamps
            self._last_tts_time = now
            self._user_last_message[user_lower] = now

            logger.debug(
                "rate_limit_acquired",
                user=user,
                active_cooldowns=len(self._user_last_message),
            )

            return RateLimitResult(allowed=True)

    async def update_settings(
        self,
        tts_rate_limit_seconds: float | None = None,
        user_cooldown_seconds: float | None = None,
    ) -> None:
        """Update rate limit settings dynamically.

        Settings take effect immediately for new requests.

        Args:
            tts_rate_limit_seconds: New TTS rate limit (optional).
            user_cooldown_seconds: New per-user cooldown (optional).
        """
        async with self._lock:
            if tts_rate_limit_seconds is not None:
                self._tts_rate_limit = timedelta(seconds=tts_rate_limit_seconds)
            if user_cooldown_seconds is not None:
                self._user_cooldown = timedelta(seconds=user_cooldown_seconds)

            logger.info(
                "rate_limiter_settings_updated",
                tts_rate_limit=self._tts_rate_limit.total_seconds(),
                user_cooldown=self._user_cooldown.total_seconds(),
            )

    def get_status(self) -> RateLimitStatus:
        """Get current rate limiter status for UI/API.

        Returns:
            RateLimitStatus with current state information.
        """
        now = datetime.now()

        tts_available = True
        tts_available_in: float | None = None

        if self._last_tts_time:
            elapsed = now - self._last_tts_time
            remaining = self._tts_rate_limit - elapsed
            if remaining.total_seconds() > 0:
                tts_available = False
                tts_available_in = remaining.total_seconds()

        # Clean expired user cooldowns while we're here
        expired_users = [
            user
            for user, last_time in self._user_last_message.items()
            if (now - last_time) > self._user_cooldown
        ]
        for user in expired_users:
            del self._user_last_message[user]

        return RateLimitStatus(
            tts_rate_limit_seconds=self._tts_rate_limit.total_seconds(),
            user_cooldown_seconds=self._user_cooldown.total_seconds(),
            tts_available=tts_available,
            tts_available_in_seconds=tts_available_in,
            active_user_cooldowns=len(self._user_last_message),
        )

    async def reset(self) -> None:
        """Reset all rate limit state. Use for testing."""
        async with self._lock:
            self._last_tts_time = None
            self._user_last_message.clear()
            logger.info("rate_limiter_reset")
