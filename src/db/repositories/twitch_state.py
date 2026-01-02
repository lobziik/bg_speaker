"""Repository for Twitch OAuth tokens and state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


@dataclass
class TwitchState:
    """Stored Twitch OAuth state.

    Attributes:
        access_token: Current OAuth access token.
        refresh_token: Token for refreshing access.
        broadcaster_id: Twitch user ID of the broadcaster.
        broadcaster_login: Twitch username of the broadcaster.
        reward_id: ID of the managed Channel Points reward.
        updated_at: When the state was last updated.
    """

    access_token: str
    refresh_token: str
    broadcaster_id: str
    broadcaster_login: str
    reward_id: str | None
    updated_at: datetime


class TwitchStateRepository:
    """Repository for Twitch OAuth tokens and reward state.

    Note: Tokens are stored in plain text. In production,
    consider encrypting sensitive data at rest.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Initialize Twitch state repository.

        Args:
            connection: Active database connection.
        """
        self._conn = connection

    async def get_state(self) -> TwitchState | None:
        """Get stored Twitch state.

        Returns:
            TwitchState if exists, None otherwise.
        """
        async with self._conn.execute(
            """
            SELECT access_token, refresh_token, broadcaster_id,
                   broadcaster_login, reward_id, updated_at
            FROM twitch_state
            WHERE key = 'primary'
            """
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return TwitchState(
                access_token=row[0],
                refresh_token=row[1],
                broadcaster_id=row[2],
                broadcaster_login=row[3],
                reward_id=row[4],
                updated_at=datetime.fromisoformat(row[5]),
            )

    async def save_tokens(
        self,
        access_token: str,
        refresh_token: str,
        broadcaster_id: str,
        broadcaster_login: str,
    ) -> None:
        """Save OAuth tokens after authorization.

        Args:
            access_token: OAuth access token.
            refresh_token: OAuth refresh token.
            broadcaster_id: Twitch user ID.
            broadcaster_login: Twitch username.
        """
        await self._conn.execute(
            """
            INSERT INTO twitch_state (key, access_token, refresh_token,
                                      broadcaster_id, broadcaster_login, updated_at)
            VALUES ('primary', ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                broadcaster_id = excluded.broadcaster_id,
                broadcaster_login = excluded.broadcaster_login,
                updated_at = CURRENT_TIMESTAMP
            """,
            (access_token, refresh_token, broadcaster_id, broadcaster_login),
        )
        await self._conn.commit()

    async def update_tokens(
        self,
        access_token: str,
        refresh_token: str,
    ) -> None:
        """Update tokens after refresh.

        Args:
            access_token: New access token.
            refresh_token: New refresh token.
        """
        await self._conn.execute(
            """
            UPDATE twitch_state
            SET access_token = ?, refresh_token = ?, updated_at = CURRENT_TIMESTAMP
            WHERE key = 'primary'
            """,
            (access_token, refresh_token),
        )
        await self._conn.commit()

    async def save_reward_id(self, reward_id: str) -> None:
        """Save managed reward ID.

        Args:
            reward_id: Channel Points reward ID.
        """
        await self._conn.execute(
            """
            UPDATE twitch_state
            SET reward_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE key = 'primary'
            """,
            (reward_id,),
        )
        await self._conn.commit()

    async def clear(self) -> None:
        """Clear all Twitch state (for logout)."""
        await self._conn.execute("DELETE FROM twitch_state WHERE key = 'primary'")
        await self._conn.commit()
