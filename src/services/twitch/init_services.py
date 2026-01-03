"""Twitch service initialization helper.

Initializes EventSub and TwitchRewardController either at app startup
(if tokens exist in DB) or after OAuth callback completes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from src.db.repositories.settings import SettingsRepository
from src.db.repositories.twitch_state import TwitchStateRepository
from src.models.settings import TwitchRewardSettings
from src.services.queue import NarrationQueue  # noqa: TC001 - used at runtime via queue parameter
from src.services.rate_limiter import RejectionReason
from src.services.twitch.eventsub import (
    EventSubConnectionError,
    RedemptionEvent,
    RedemptionHandler,
    TwitchEventSubService,
)
from src.services.twitch.rewards import (
    RewardConfig,
    RewardOperationError,
    TwitchRewardController,
)

if TYPE_CHECKING:
    from src.api.dependencies import AppState

logger = structlog.get_logger()


class TwitchInitError(Exception):
    """Raised when Twitch service initialization fails."""

    pass


async def initialize_twitch_services(
    state: AppState,
    access_token: str,
    refresh_token: str,
    broadcaster_id: str,
) -> bool:
    """Initialize EventSub and RewardController.

    Creates and configures:
    - TwitchRewardController: Manages Channel Points reward lifecycle
    - TwitchEventSubService: Listens for redemption events

    Args:
        state: Application state to populate with initialized services.
        access_token: Valid OAuth token with required scopes.
        refresh_token: Refresh token for automatic token renewal by twitchio.
        broadcaster_id: Twitch user ID (numeric string).

    Returns:
        True if initialization succeeded, False if it failed.

    Note:
        On failure, logs warning and returns False (does not raise).
        App continues without Twitch features - user can re-authorize via UI.
    """
    logger.info(
        "twitch_services_init_starting",
        broadcaster_id=broadcaster_id,
    )

    try:
        # Step 1: Create and initialize RewardController
        rewards_controller = TwitchRewardController(
            client_id=state.env.twitch_client_id,
            broadcaster_id=broadcaster_id,
        )

        # Load reward settings from DB
        settings_repo = SettingsRepository(state.db.connection)
        reward_settings = await settings_repo.get(
            "reward", TwitchRewardSettings, TwitchRewardSettings()
        ) or TwitchRewardSettings()

        # Create reward config from settings
        reward_config = RewardConfig(
            title=reward_settings.title,
            cost=reward_settings.cost,
            prompt=reward_settings.prompt,
            background_color=reward_settings.background_color,
            is_user_input_required=True,
            should_skip_request_queue=False,
            global_cooldown_seconds=0,  # We manage cooldown via GlobalCooldownManager
        )

        # Initialize reward (finds existing or creates new)
        reward_id = await rewards_controller.initialize(access_token, reward_config)
        logger.info("twitch_reward_initialized", reward_id=reward_id)

        # Save reward_id to DB for persistence
        twitch_repo = TwitchStateRepository(state.db.connection)
        await twitch_repo.save_reward_id(reward_id)

        # Step 2: Create EventSub service with tokens
        logger.debug(
            "eventsub_creating_with_tokens",
            broadcaster_id=broadcaster_id,
            access_token_prefix=access_token[:10] + "...",
            reward_id=reward_id,
        )
        eventsub = TwitchEventSubService(
            client_id=state.env.twitch_client_id,
            client_secret=state.env.twitch_client_secret,
            broadcaster_id=broadcaster_id,
            access_token=access_token,
            refresh_token=refresh_token,
            target_reward_id=reward_id,  # Only listen to our reward
        )

        # Register redemption handler
        handler = _create_redemption_handler(
            queue=state.queue,
            rewards_controller=rewards_controller,
            refund_on_queue_full=reward_settings.refund_on_queue_full,
            refund_on_filtered=reward_settings.refund_on_filtered,
        )
        eventsub.on_redemption(handler)

        # Start EventSub connection
        await eventsub.start()
        logger.info("twitch_eventsub_started")

        # Step 3: Update application state
        state.twitch_rewards = rewards_controller
        state.twitch_eventsub = eventsub

        # Step 4: Wire up GlobalCooldownManager if it exists
        if state.global_cooldown:
            state.global_cooldown.set_rewards_controller(rewards_controller)

        logger.info(
            "twitch_services_initialized",
            reward_id=reward_id,
            eventsub_connected=eventsub.is_connected,
        )
        return True

    except RewardOperationError as e:
        logger.warning(
            "twitch_reward_init_failed",
            error=str(e),
            error_type="RewardOperationError",
        )
        return False

    except EventSubConnectionError as e:
        logger.warning(
            "twitch_eventsub_init_failed",
            error=str(e),
            error_type="EventSubConnectionError",
        )
        # Clean up rewards controller if EventSub failed
        if state.twitch_rewards:
            await state.twitch_rewards.close()
            state.twitch_rewards = None
        return False


def _create_redemption_handler(
    queue: NarrationQueue,
    rewards_controller: TwitchRewardController,
    refund_on_queue_full: bool,
    refund_on_filtered: bool,
) -> RedemptionHandler:
    """Create handler that adds redemptions to the queue.

    Args:
        queue: Narration queue to add items to.
        rewards_controller: Controller for cancelling rejected redemptions.
        refund_on_queue_full: Whether to refund when queue is full.
        refund_on_filtered: Whether to refund when message is filtered.

    Returns:
        Async handler function for redemption events.
    """

    async def handler(event: RedemptionEvent) -> None:
        """Handle a channel points redemption event."""
        logger.info(
            "redemption_received",
            redemption_id=event.id,
            user=event.user,
            message_length=len(event.message),
        )

        # Queue.add() handles rate limiting and validation internally
        result = await queue.add(
            user=event.user,
            message=event.message,
            redemption_id=event.id,
        )

        if result.success:
            logger.info(
                "redemption_queued",
                redemption_id=event.id,
                position=result.queue_position,
            )
        else:
            # Determine if we should refund
            should_refund = False
            if result.rejection_reason == RejectionReason.QUEUE_FULL:
                should_refund = refund_on_queue_full
            elif result.rejection_reason == RejectionReason.MESSAGE_FILTERED:
                should_refund = refund_on_filtered
            elif result.rejection_reason == RejectionReason.USER_COOLDOWN:
                should_refund = True  # Always refund rate-limited users
            elif result.rejection_reason == RejectionReason.TTS_RATE_LIMITED:
                should_refund = True  # Always refund TTS rate limit

            logger.info(
                "redemption_rejected",
                redemption_id=event.id,
                reason=str(result.rejection_reason),
                will_refund=should_refund,
            )

            if should_refund:
                try:
                    await rewards_controller.cancel_redemption(event.id)
                    logger.info(
                        "redemption_refunded",
                        redemption_id=event.id,
                    )
                except RewardOperationError as e:
                    logger.error(
                        "redemption_refund_failed",
                        redemption_id=event.id,
                        error=str(e),
                    )

    return handler
