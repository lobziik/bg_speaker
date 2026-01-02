"""Twitch Channel Points reward management."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import httpx
import structlog

logger = structlog.get_logger()


class RewardState(StrEnum):
    """Channel Points reward states."""

    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class RewardNotFoundError(Exception):
    """Raised when reward is not found or not manageable."""

    pass


class RewardOperationError(Exception):
    """Raised when reward operation fails."""

    pass


@dataclass(frozen=True)
class RewardConfig:
    """Configuration for creating/updating a Channel Points reward.

    Attributes:
        title: Reward title displayed to users.
        cost: Channel Points cost.
        prompt: Description shown when redeeming.
        background_color: Hex color for the reward icon.
        is_user_input_required: Whether user must enter text.
        should_skip_request_queue: Process immediately vs queue.
        global_cooldown_seconds: Cooldown between any redemption.
    """

    title: str = "Narrator TTS"
    cost: int = 500
    prompt: str = "Enter your message for the narrator"
    background_color: str = "#6441A4"
    is_user_input_required: bool = True
    should_skip_request_queue: bool = False
    global_cooldown_seconds: int = 0


@dataclass(frozen=True)
class Reward:
    """Channel Points reward information.

    Attributes:
        id: Unique reward ID.
        title: Reward title.
        cost: Channel Points cost.
        is_enabled: Whether reward is visible.
        is_paused: Whether reward is temporarily unavailable.
        is_in_stock: Whether reward can be redeemed.
        cooldown_seconds: Current cooldown setting.
    """

    id: str
    title: str
    cost: int
    is_enabled: bool
    is_paused: bool
    is_in_stock: bool
    cooldown_seconds: int


class TwitchRewardController:
    """Manages Channel Points reward lifecycle via Twitch Helix API.

    Responsibilities:
    - Create reward on startup (or find existing)
    - Pause/unpause reward based on queue state
    - Sync cooldown with rate limiter
    - Fulfill/cancel redemptions

    IMPORTANT: Only rewards created by this app (same client_id)
    can be managed. Rewards created in Twitch Dashboard
    cannot be controlled via API.

    Required OAuth scope: channel:manage:redemptions
    """

    BASE_URL = "https://api.twitch.tv/helix"

    def __init__(
        self,
        client_id: str,
        broadcaster_id: str,
    ) -> None:
        """Initialize reward controller.

        Args:
            client_id: Twitch application client ID.
            broadcaster_id: Channel's user ID (numeric string).
        """
        self._client_id = client_id
        self._broadcaster_id = broadcaster_id
        self._reward_id: str | None = None
        self._http: httpx.AsyncClient | None = None
        self._access_token: str | None = None

    @property
    def reward_id(self) -> str | None:
        """Get the managed reward ID."""
        return self._reward_id

    async def initialize(
        self,
        access_token: str,
        config: RewardConfig,
    ) -> str:
        """Initialize reward: find existing or create new.

        Args:
            access_token: Valid OAuth token.
            config: Reward configuration.

        Returns:
            Reward ID.

        Raises:
            RewardOperationError: If initialization fails.
        """
        self._access_token = access_token
        self._http = httpx.AsyncClient(
            headers={
                "Client-Id": self._client_id,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

        logger.info("reward_controller_initializing", title=config.title)

        # Try to find existing reward by title
        existing = await self._find_reward_by_title(config.title)

        if existing:
            self._reward_id = existing.id
            logger.info(
                "reward_found_existing",
                reward_id=self._reward_id,
                is_enabled=existing.is_enabled,
            )
            # Ensure it's active
            await self.set_state(RewardState.ACTIVE)
        else:
            self._reward_id = await self._create_reward(config)
            logger.info("reward_created", reward_id=self._reward_id)

        return self._reward_id

    async def set_state(self, state: RewardState) -> None:
        """Set reward state (active/paused/disabled).

        Args:
            state: Target state for the reward.

        Raises:
            RewardOperationError: If state change fails.
        """
        self._ensure_initialized()

        body: dict[str, object]

        match state:
            case RewardState.ACTIVE:
                body = {"is_enabled": True, "is_paused": False}
            case RewardState.PAUSED:
                body = {"is_enabled": True, "is_paused": True}
            case RewardState.DISABLED:
                body = {"is_enabled": False, "is_paused": False}

        await self._patch_reward(body)
        logger.info("reward_state_changed", state=state)

    async def pause(self) -> None:
        """Pause reward (visible but not redeemable)."""
        await self.set_state(RewardState.PAUSED)

    async def unpause(self) -> None:
        """Unpause reward."""
        await self.set_state(RewardState.ACTIVE)

    async def update_cooldown(self, seconds: int) -> None:
        """Sync Twitch's built-in cooldown with rate limiter.

        Args:
            seconds: Cooldown duration (0 to disable).
        """
        self._ensure_initialized()

        await self._patch_reward(
            {
                "global_cooldown_setting": {
                    "is_enabled": seconds > 0,
                    "global_cooldown_seconds": seconds,
                }
            }
        )
        logger.info("reward_cooldown_updated", seconds=seconds)

    async def fulfill_redemption(self, redemption_id: str) -> None:
        """Mark redemption as fulfilled (completed successfully).

        Call this after TTS plays successfully.

        Args:
            redemption_id: The redemption ID to fulfill.
        """
        await self._update_redemption_status(redemption_id, "FULFILLED")

    async def cancel_redemption(self, redemption_id: str) -> None:
        """Cancel redemption and refund points.

        Call this on error, queue full, or filtered message.

        Args:
            redemption_id: The redemption ID to cancel.
        """
        await self._update_redemption_status(redemption_id, "CANCELED")

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None

    def _ensure_initialized(self) -> None:
        """Ensure reward controller is initialized."""
        if self._http is None or self._reward_id is None:
            raise RewardOperationError(
                "RewardController not initialized. Call initialize() first."
            )

    async def _find_reward_by_title(self, title: str) -> Reward | None:
        """Find manageable reward by exact title match."""
        if self._http is None:
            raise RewardOperationError("HTTP client not initialized")

        response = await self._http.get(
            f"{self.BASE_URL}/channel_points/custom_rewards",
            params={
                "broadcaster_id": self._broadcaster_id,
                "only_manageable_rewards": "true",
            },
        )
        response.raise_for_status()
        data = response.json()

        for reward_data in data.get("data", []):
            if reward_data["title"] == title:
                return Reward(
                    id=reward_data["id"],
                    title=reward_data["title"],
                    cost=reward_data["cost"],
                    is_enabled=reward_data["is_enabled"],
                    is_paused=reward_data["is_paused"],
                    is_in_stock=reward_data["is_in_stock"],
                    cooldown_seconds=reward_data.get(
                        "global_cooldown_setting", {}
                    ).get("global_cooldown_seconds", 0),
                )
        return None

    async def _create_reward(self, config: RewardConfig) -> str:
        """Create new Channel Points reward."""
        if self._http is None:
            raise RewardOperationError("HTTP client not initialized")

        response = await self._http.post(
            f"{self.BASE_URL}/channel_points/custom_rewards",
            params={"broadcaster_id": self._broadcaster_id},
            json={
                "title": config.title,
                "cost": config.cost,
                "prompt": config.prompt,
                "background_color": config.background_color,
                "is_user_input_required": config.is_user_input_required,
                "should_redemptions_skip_request_queue": config.should_skip_request_queue,
                "is_enabled": True,
                "is_paused": False,
                "global_cooldown_setting": {
                    "is_enabled": config.global_cooldown_seconds > 0,
                    "global_cooldown_seconds": config.global_cooldown_seconds,
                },
            },
        )

        if response.status_code != 200:
            raise RewardOperationError(
                f"Failed to create reward: {response.status_code} {response.text}"
            )

        data: dict[str, list[dict[str, str]]] = response.json()
        return data["data"][0]["id"]

    async def _patch_reward(self, body: dict[str, object]) -> None:
        """Update reward with PATCH request."""
        if self._http is None:
            raise RewardOperationError("HTTP client not initialized")

        response = await self._http.patch(
            f"{self.BASE_URL}/channel_points/custom_rewards",
            params={
                "broadcaster_id": self._broadcaster_id,
                "id": self._reward_id,
            },
            json=body,
        )

        if response.status_code != 200:
            raise RewardOperationError(
                f"Failed to update reward: {response.status_code} {response.text}"
            )

    async def _update_redemption_status(
        self,
        redemption_id: str,
        status: Literal["FULFILLED", "CANCELED"],
    ) -> None:
        """Update redemption status."""
        if self._http is None or self._reward_id is None:
            raise RewardOperationError("Controller not initialized")

        response = await self._http.patch(
            f"{self.BASE_URL}/channel_points/custom_rewards/redemptions",
            params={
                "broadcaster_id": self._broadcaster_id,
                "reward_id": self._reward_id,
                "id": redemption_id,
            },
            json={"status": status},
        )

        if response.status_code != 200:
            logger.warning(
                "redemption_status_update_failed",
                redemption_id=redemption_id,
                status=status,
                response_status=response.status_code,
            )
