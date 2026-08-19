"""TwitchIO 3.x EventSub WebSocket service for Channel Points."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import structlog
import twitchio
from twitchio import ChannelPointsRedemptionAdd
from twitchio import eventsub as eventsub_subscriptions

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic import SecretStr

logger = structlog.get_logger()


class RedemptionHandler(Protocol):
    """Protocol for redemption event handlers."""

    async def __call__(self, event: RedemptionEvent) -> None:
        """Handle a channel points redemption event."""
        ...


@dataclass(frozen=True)
class RedemptionEvent:
    """Parsed channel point redemption event.

    Immutable dataclass representing a redemption that can be
    passed to handlers for processing.

    Attributes:
        id: Redemption ID (for fulfill/cancel).
        user: Display name of the user.
        user_id: Twitch user ID.
        user_login: Lowercase login name.
        message: User input text (may be empty).
        reward_id: Channel Points reward ID.
        reward_title: Reward title.
        reward_cost: Points spent.
        redeemed_at: ISO timestamp of redemption.
    """

    id: str  # Redemption ID (for fulfill/cancel)
    user: str  # Display name
    user_id: str
    user_login: str  # Lowercase login name
    message: str  # User input text (may be empty)
    reward_id: str
    reward_title: str
    reward_cost: int
    redeemed_at: str  # ISO timestamp


class EventSubConnectionError(Exception):
    """Raised when EventSub connection fails."""

    pass


class EventSubNotConnectedError(Exception):
    """Raised when trying to operate on disconnected EventSub."""

    pass


class _EventSubClient(twitchio.Client):
    """Internal TwitchIO Client subclass for handling EventSub events.

    Stores access and refresh tokens for use when adding tokens
    to the managed HTTP client.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        access_token: str,
        refresh_token: str,
        redemption_callback: Callable[[ChannelPointsRedemptionAdd], Awaitable[None]],
    ) -> None:
        super().__init__(
            client_id=client_id,
            client_secret=client_secret.get_secret_value(),
        )
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._redemption_callback = redemption_callback

    async def event_custom_redemption_add(
        self,
        event: ChannelPointsRedemptionAdd,
    ) -> None:
        """Handle channel points custom reward redemption events."""
        await self._redemption_callback(event)


class TwitchEventSubService:
    """TwitchIO 3.x EventSub WebSocket integration for Channel Points.

    Handles:
    - EventSub WebSocket connection lifecycle
    - Channel point redemption subscription
    - Event routing to registered handlers
    - Automatic reconnection on disconnect

    Required OAuth scopes:
    - channel:read:redemptions

    Usage:
        service = TwitchEventSubService(
            client_id, client_secret, broadcaster_id,
            access_token, refresh_token,
        )
        service.on_redemption(my_handler)
        await service.start()
        # ... later
        await service.stop()
    """

    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        broadcaster_id: str,
        access_token: str,
        refresh_token: str,
        target_reward_id: str | None = None,
    ) -> None:
        """Initialize EventSub service.

        Args:
            client_id: Twitch application client ID.
            client_secret: Twitch application client secret.
            broadcaster_id: Channel's user ID (numeric string).
            access_token: Valid OAuth token with required scopes.
            refresh_token: Refresh token for automatic token renewal.
            target_reward_id: If set, only handle this reward's redemptions.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._broadcaster_id = broadcaster_id
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._target_reward_id = target_reward_id

        self._client: _EventSubClient | None = None
        self._is_connected = False

        self._redemption_handlers: list[RedemptionHandler] = []

    @property
    def is_connected(self) -> bool:
        """Check if EventSub is currently connected."""
        return self._is_connected

    def on_redemption(self, handler: RedemptionHandler) -> None:
        """Register a handler for redemption events.

        Handlers are called in order of registration.
        If a handler raises an exception, it's logged but
        other handlers still execute.

        Args:
            handler: Async callable that handles redemption events.
        """
        self._redemption_handlers.append(handler)

    async def start(self) -> None:
        """Start EventSub WebSocket connection.

        Uses tokens provided during initialization.

        Raises:
            EventSubConnectionError: If connection fails.
        """
        if self._is_connected:
            logger.warning("eventsub_already_connected")
            return

        logger.info(
            "eventsub_starting",
            broadcaster_id=self._broadcaster_id,
            target_reward_id=self._target_reward_id,
        )

        try:
            # Create TwitchIO client with tokens and event handler
            logger.debug("eventsub_creating_client")
            self._client = _EventSubClient(
                client_id=self._client_id,
                client_secret=self._client_secret,
                access_token=self._access_token,
                refresh_token=self._refresh_token,
                redemption_callback=self._handle_redemption,
            )

            # CRITICAL: Must call login() to initialize app token.
            # TwitchIO's _find_token() has early return when both header token
            # and _app_token are None (None == None → True), which skips
            # the token_for lookup entirely, causing 401 errors.
            await self._client.login(load_tokens=False, save_tokens=False)
            logger.debug("eventsub_client_logged_in")

            # Add the user token to twitchio's HTTP manager for API calls
            # TwitchIO stores tokens keyed by user_id from its validation response
            token_payload = await self._client.add_token(self._access_token, self._refresh_token)
            logger.info(
                "eventsub_token_added",
                broadcaster_id=self._broadcaster_id,
                token_user_id=token_payload.user_id,
                token_login=token_payload.login,
                user_id_matches_broadcaster=token_payload.user_id == self._broadcaster_id,
            )

            # Create subscription payload
            subscription = eventsub_subscriptions.ChannelPointsRedeemAddSubscription(
                broadcaster_user_id=self._broadcaster_id,
            )

            # Subscribe to channel point redemptions via WebSocket
            # CRITICAL: Use token_payload.user_id (not broadcaster_id) because
            # TwitchIO stores and looks up tokens by the user_id from add_token()
            logger.debug(
                "eventsub_subscribing",
                token_for=token_payload.user_id,
                broadcaster_id=self._broadcaster_id,
                subscription_type="channel.channel_points_custom_reward_redemption.add",
            )
            await self._client.subscribe_websocket(
                subscription,
                token_for=token_payload.user_id,
            )
            logger.info(
                "eventsub_subscription_created",
                subscription_type="channel.channel_points_custom_reward_redemption.add",
            )

            self._is_connected = True
            logger.info("eventsub_connected")

        except twitchio.HTTPException as e:
            logger.error("eventsub_connection_failed", error=str(e))
            await self._cleanup()
            raise EventSubConnectionError(f"Failed to connect: {e}") from e

    async def stop(self) -> None:
        """Stop EventSub connection and cleanup."""
        if not self._is_connected:
            return

        logger.info("eventsub_stopping")
        await self._cleanup()
        logger.info("eventsub_stopped")

    async def _cleanup(self) -> None:
        """Internal cleanup of resources."""
        self._is_connected = False

        if self._client:
            try:
                await self._client.close()
            except twitchio.HTTPException as e:
                logger.warning("client_close_error", error=str(e))
            self._client = None

    async def _handle_redemption(
        self,
        event: ChannelPointsRedemptionAdd,
    ) -> None:
        """Internal handler for TwitchIO redemption events."""
        # Filter by reward ID if configured
        if self._target_reward_id and event.reward.id != self._target_reward_id:
            logger.debug(
                "eventsub_redemption_filtered",
                reward_id=event.reward.id,
                target_reward_id=self._target_reward_id,
            )
            return

        # Parse to our clean dataclass
        user_display = event.user.display_name or event.user.name or "Unknown"
        user_login = event.user.name or "unknown"
        redemption = RedemptionEvent(
            id=event.id,
            user=user_display,
            user_id=str(event.user.id),
            user_login=user_login,
            message=event.user_input or "",
            reward_id=event.reward.id,
            reward_title=event.reward.title,
            reward_cost=event.reward.cost,
            redeemed_at=event.redeemed_at.isoformat() if event.redeemed_at else "",
        )

        logger.info(
            "eventsub_redemption_received",
            redemption_id=redemption.id,
            user=redemption.user,
            reward=redemption.reward_title,
            message_length=len(redemption.message),
        )

        # Call all registered handlers
        for handler in self._redemption_handlers:
            handler_name = handler.__class__.__name__
            logger.debug(
                "eventsub_handler_start",
                handler=handler_name,
                redemption_id=redemption.id,
            )
            handler_start = time.monotonic()
            try:
                await handler(redemption)
                handler_latency_ms = int((time.monotonic() - handler_start) * 1000)
                logger.debug(
                    "eventsub_handler_complete",
                    handler=handler_name,
                    redemption_id=redemption.id,
                    latency_ms=handler_latency_ms,
                )
            except Exception as e:
                handler_latency_ms = int((time.monotonic() - handler_start) * 1000)
                logger.error(
                    "redemption_handler_error",
                    handler=handler_name,
                    error=str(e),
                    redemption_id=redemption.id,
                    latency_ms=handler_latency_ms,
                )
