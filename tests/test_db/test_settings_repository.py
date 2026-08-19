"""Tests for the settings repository."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from src.db.manager import DatabaseManager
from src.db.repositories.settings import SettingsRepository
from src.providers.llm.prompts import PromptSettings


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> AsyncIterator[SettingsRepository]:
    """Provide a settings repository backed by a throwaway database."""
    db = DatabaseManager(db_path=tmp_path / "data" / "narrator.db")
    await db.initialize()
    try:
        yield SettingsRepository(db.connection)
    finally:
        await db.close()


class TestGetOrDefault:
    """Non-optional reads."""

    @pytest.mark.asyncio
    async def test_returns_default_when_absent(self, repo: SettingsRepository) -> None:
        """A missing key yields the supplied default."""
        default = PromptSettings(formatting="Formatting: be terse.")

        result = await repo.get_or_default("prompts", PromptSettings, default)

        assert result.formatting == "Formatting: be terse."

    @pytest.mark.asyncio
    async def test_returns_stored_value(self, repo: SettingsRepository) -> None:
        """A stored value wins over the default."""
        await repo.set("prompts", PromptSettings(formatting="Formatting: stored."))

        result = await repo.get_or_default("prompts", PromptSettings, PromptSettings())

        assert result.formatting == "Formatting: stored."


class TestSetIfAbsent:
    """Seeding editable defaults without clobbering operator edits."""

    @pytest.mark.asyncio
    async def test_writes_when_absent(self, repo: SettingsRepository) -> None:
        """A fresh install gets the defaults written out."""
        written = await repo.set_if_absent("prompts", PromptSettings())

        assert written is True
        stored = await repo.get("prompts", PromptSettings)
        assert stored is not None

    @pytest.mark.asyncio
    async def test_preserves_existing_value(self, repo: SettingsRepository) -> None:
        """Seeding on a later boot must not overwrite edited prompts."""
        await repo.set("prompts", PromptSettings(formatting="Formatting: mine."))

        written = await repo.set_if_absent("prompts", PromptSettings())

        assert written is False
        stored = await repo.get("prompts", PromptSettings)
        assert stored is not None
        assert stored.formatting == "Formatting: mine."


class TestRoundTrip:
    """Stored prompts survive serialisation, including their placeholders."""

    @pytest.mark.asyncio
    async def test_placeholders_and_braces_survive(self, repo: SettingsRepository) -> None:
        """JSON braces and $placeholders come back byte-identical."""
        original = PromptSettings()

        await repo.set("prompts", original)
        stored = await repo.get("prompts", PromptSettings)

        assert stored == original
        assert stored is not None
        assert '{"voice_text": "...", "subtitle_text": "..."}' in stored.base_system
        assert "$message" in stored.moderation_user

    @pytest.mark.asyncio
    async def test_invalid_stored_template_is_rejected_on_read(
        self, repo: SettingsRepository
    ) -> None:
        """A template edited out of band still fails loudly when loaded."""
        # Reaching into the connection deliberately: this simulates a row edited
        # outside the app, which is exactly what validation on read must catch.
        await repo._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("prompts", PromptSettings().model_dump_json().replace("$message", "$mesage")),
        )
        await repo._conn.commit()

        with pytest.raises(ValueError, match="unknown placeholder"):
            await repo.get("prompts", PromptSettings)
