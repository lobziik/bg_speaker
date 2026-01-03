"""Global cooldown manager for Twitch reward pausing.

Manages a global cooldown that pauses the Twitch Channel Points reward
after each narration completes (success or failure), preventing rapid
redemptions by all users.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from src.core.types import StrictModel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import aiosqlite

    from src.services.twitch.rewards import TwitchRewardController

logger = structlog.get_logger()


class GlobalCooldownState(StrictModel):
    """Persisted cooldown state for restart recovery.

    Attributes:
        cooldown_ends_at: When the current cooldown expires (if active).
        is_paused: Whether the reward is currently paused by this manager.
    """

    cooldown_ends_at: datetime | None = None
    is_paused: bool = False


@dataclass(frozen=True)
class CooldownStatus:
    """Current cooldown status for UI/API display.

    Attributes:
        is_active: Whether a cooldown is currently active.
        remaining_seconds: Seconds until cooldown ends (if active).
        total_seconds: Configured cooldown duration.
    """

    is_active: bool
    remaining_seconds: float | None
    total_seconds: int


class GlobalCooldownManager:
    """Manages global Twitch reward cooldown after narration completion.

    Responsibilities:
    - Pause reward after narration completes (success or failure)
    - Auto-unpause after configurable cooldown duration
    - Persist state for app restart recovery
    - Broadcast status updates via callback

    Thread-safe via asyncio Lock.
    """

    SETTINGS_KEY = "global_cooldown_state"

    def __init__(
        self,
        rewards_controller: TwitchRewardController | None,
        db_connection: aiosqlite.Connection | None,
        cooldown_seconds: int = 300,
        on_status_change: Callable[[CooldownStatus], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize the global cooldown manager.

        Args:
            rewards_controller: Twitch reward controller for pause/unpause.
            db_connection: Database connection for state persistence.
            cooldown_seconds: Default cooldown duration in seconds.
            on_status_change: Async callback for status broadcasts.
        """
        self._rewards = rewards_controller
        self._db = db_connection
        self._cooldown_seconds = cooldown_seconds
        self._on_status_change = on_status_change

        self._unpause_task: asyncio.Task[None] | None = None
        self._is_active = False
        self._cooldown_ends_at: datetime | None = None
        self._lock = asyncio.Lock()

    def set_rewards_controller(self, controller: TwitchRewardController) -> None:
        """Set the rewards controller after Twitch initialization.

        This allows the cooldown manager to be created before Twitch services
        are initialized (e.g., at app startup before OAuth is complete).

        Args:
            controller: Initialized TwitchRewardController instance.
        """
        self._rewards = controller
        logger.info("global_cooldown_rewards_controller_set")

    async def initialize(self) -> None:
        """Initialize and restore state from database.

        Should be called on app startup to resume any active cooldown
        that was interrupted by app restart.
        """
        if not self._db:
            logger.debug("global_cooldown_init_skipped", reason="no_db_connection")
            return

        state = await self._load_state()
        if state and state.is_paused and state.cooldown_ends_at:
            now = datetime.now(UTC)
            # Handle timezone-naive datetime from DB
            ends_at = state.cooldown_ends_at
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=UTC)

            if ends_at > now:
                remaining = (ends_at - now).total_seconds()
                logger.info(
                    "global_cooldown_restored",
                    remaining_seconds=remaining,
                    ends_at=ends_at.isoformat(),
                )
                await self._start_cooldown_internal(remaining)
            else:
                logger.info("global_cooldown_expired_during_restart")
                await self._do_unpause()

    async def on_narration_complete(self) -> None:
        """Called when any narration completes (success or failure).

        Starts the global cooldown timer, pausing the Twitch reward.
        """
        if not self._rewards:
            logger.debug("global_cooldown_skipped", reason="no_rewards_controller")
            return

        if self._cooldown_seconds <= 0:
            logger.debug("global_cooldown_skipped", reason="disabled")
            return

        await self._start_cooldown_internal(float(self._cooldown_seconds))

    async def update_cooldown_duration(self, seconds: int) -> None:
        """Update cooldown duration setting.

        Takes effect on next narration completion (doesn't affect current cooldown).

        Args:
            seconds: New cooldown duration in seconds (0 to disable).
        """
        async with self._lock:
            self._cooldown_seconds = seconds
            logger.info("global_cooldown_duration_updated", seconds=seconds)

    def get_status(self) -> CooldownStatus:
        """Get current cooldown status for UI display.

        Returns:
            Current cooldown status with remaining time if active.
        """
        remaining: float | None = None
        if self._is_active and self._cooldown_ends_at:
            now = datetime.now(UTC)
            ends_at = self._cooldown_ends_at
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=UTC)
            remaining = max(0.0, (ends_at - now).total_seconds())

        return CooldownStatus(
            is_active=self._is_active,
            remaining_seconds=remaining,
            total_seconds=self._cooldown_seconds,
        )

    async def force_unpause(self) -> None:
        """Manually force unpause (admin override).

        Cancels any active cooldown timer and immediately unpauses the reward.
        """
        async with self._lock:
            if self._unpause_task and not self._unpause_task.done():
                self._unpause_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._unpause_task
            await self._do_unpause()
            logger.info("global_cooldown_force_unpause")

    async def shutdown(self) -> None:
        """Clean shutdown, cancels pending tasks.

        Should be called on app shutdown.
        """
        if self._unpause_task and not self._unpause_task.done():
            self._unpause_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._unpause_task
        logger.debug("global_cooldown_shutdown")

    async def _start_cooldown_internal(self, duration_seconds: float) -> None:
        """Start or restart the cooldown timer.

        Args:
            duration_seconds: Duration of the cooldown in seconds.
        """
        async with self._lock:
            # Cancel existing timer if running
            if self._unpause_task and not self._unpause_task.done():
                self._unpause_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._unpause_task

            # Pause the reward
            if self._rewards:
                try:
                    await self._rewards.pause()
                except Exception as e:
                    logger.error(
                        "global_cooldown_pause_failed",
                        error=str(e),
                        error_type=type(e).__name__,
                    )
                    # Continue anyway - reward might already be paused

            self._is_active = True
            self._cooldown_ends_at = datetime.now(UTC) + timedelta(
                seconds=duration_seconds
            )

            # Persist state for restart recovery
            await self._save_state()

            logger.info(
                "global_cooldown_started",
                duration_seconds=duration_seconds,
                ends_at=self._cooldown_ends_at.isoformat(),
            )

            # Broadcast status update
            if self._on_status_change:
                try:
                    await self._on_status_change(self.get_status())
                except Exception as e:
                    logger.warning(
                        "global_cooldown_status_broadcast_failed",
                        error=str(e),
                    )

            # Schedule unpause task
            self._unpause_task = asyncio.create_task(
                self._unpause_after_delay(duration_seconds)
            )

    async def _unpause_after_delay(self, seconds: float) -> None:
        """Wait then unpause the reward.

        Args:
            seconds: Seconds to wait before unpausing.
        """
        try:
            await asyncio.sleep(seconds)
            async with self._lock:
                await self._do_unpause()
        except asyncio.CancelledError:
            logger.debug("global_cooldown_timer_cancelled")
            raise

    async def _do_unpause(self) -> None:
        """Execute the unpause operation.

        Internal method - caller must hold the lock.
        """
        if self._rewards:
            try:
                await self._rewards.unpause()
            except Exception as e:
                logger.error(
                    "global_cooldown_unpause_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                # State will still be cleared - on next narration it will try again

        self._is_active = False
        self._cooldown_ends_at = None

        # Clear persisted state
        await self._save_state()

        logger.info("global_cooldown_ended")

        # Broadcast status update
        if self._on_status_change:
            try:
                await self._on_status_change(self.get_status())
            except Exception as e:
                logger.warning(
                    "global_cooldown_status_broadcast_failed",
                    error=str(e),
                )

    async def _load_state(self) -> GlobalCooldownState | None:
        """Load persisted state from database.

        Returns:
            Loaded state or None if not found.
        """
        if not self._db:
            return None

        from src.db.repositories.settings import SettingsRepository

        repo = SettingsRepository(self._db)
        return await repo.get(self.SETTINGS_KEY, GlobalCooldownState, None)

    async def _save_state(self) -> None:
        """Persist current state to database."""
        if not self._db:
            return

        from src.db.repositories.settings import SettingsRepository

        repo = SettingsRepository(self._db)
        state = GlobalCooldownState(
            cooldown_ends_at=self._cooldown_ends_at,
            is_paused=self._is_active,
        )
        try:
            await repo.set(self.SETTINGS_KEY, state)
        except Exception as e:
            logger.warning(
                "global_cooldown_state_save_failed",
                error=str(e),
            )
