"""Repository for application settings storage."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    import aiosqlite

T = TypeVar("T", bound=BaseModel)


class SettingsRepository:
    """Typed repository for application settings.

    Stores Pydantic models as JSON in SQLite.
    Provides type-safe get/set operations.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Initialize settings repository.

        Args:
            connection: Active database connection.
        """
        self._conn = connection

    async def get(
        self,
        key: str,
        model: type[T],
        default: T | None = None,
    ) -> T | None:
        """Get typed setting by key.

        Handles backward compatibility by filtering out fields that no longer
        exist in the model schema. This allows schema evolution without
        requiring data migrations for removed fields.

        Args:
            key: Setting key.
            model: Pydantic model class to deserialize to.
            default: Default value if not found.

        Returns:
            Deserialized model or default.
        """
        async with self._conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return default

            # Parse JSON and filter to only known fields for backward compatibility.
            # This handles cases where fields were removed from the model.
            data = json.loads(row[0])
            known_fields = set(model.model_fields.keys())
            filtered_data = {k: v for k, v in data.items() if k in known_fields}

            return model.model_validate(filtered_data)

    async def set(self, key: str, value: T) -> None:
        """Save typed setting.

        Uses upsert to create or update.

        Args:
            key: Setting key.
            value: Pydantic model to store.
        """
        await self._conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value.model_dump_json()),
        )
        await self._conn.commit()

    async def delete(self, key: str) -> bool:
        """Delete setting by key.

        Args:
            key: Setting key to delete.

        Returns:
            True if setting existed and was deleted.
        """
        cursor = await self._conn.execute(
            "DELETE FROM settings WHERE key = ?",
            (key,),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def get_active_provider(
        self,
        provider_type: str,
    ) -> tuple[str, dict[str, object]] | None:
        """Get active provider name and settings for a provider type.

        Args:
            provider_type: 'llm', 'tts', or 'translate'.

        Returns:
            Tuple of (provider_name, settings_dict) or None.
        """
        async with self._conn.execute(
            """
            SELECT provider_name, settings FROM provider_settings
            WHERE provider_type = ? AND is_active = TRUE
            """,
            (provider_type,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return row[0], json.loads(row[1])

    async def set_provider_settings(
        self,
        provider_type: str,
        provider_name: str,
        settings: dict[str, object],
        is_active: bool = False,
    ) -> None:
        """Save provider settings.

        If is_active=True, deactivates other providers of same type.

        Args:
            provider_type: 'llm', 'tts', or 'translate'.
            provider_name: Name of the provider (e.g., 'groq', 'piper').
            settings: Provider-specific settings dict.
            is_active: Whether this provider should be active.
        """
        if is_active:
            await self._conn.execute(
                "UPDATE provider_settings SET is_active = FALSE WHERE provider_type = ?",
                (provider_type,),
            )

        await self._conn.execute(
            """
            INSERT INTO provider_settings
                (provider_type, provider_name, settings, is_active, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(provider_type, provider_name) DO UPDATE SET
                settings = excluded.settings,
                is_active = excluded.is_active,
                updated_at = CURRENT_TIMESTAMP
            """,
            (provider_type, provider_name, json.dumps(settings), is_active),
        )
        await self._conn.commit()
