"""Tests for the SQLite database manager."""

from pathlib import Path

import pytest

from src.db.manager import DEFAULT_MIGRATIONS_DIR, DatabaseManager, MigrationError


class TestMigrations:
    """Migration discovery and application."""

    def test_default_migrations_dir_ships_with_the_source(self) -> None:
        """The default is anchored to the repo, not the process working directory.

        The container runs the app from the data volume, so a CWD-relative
        default would silently find nothing.
        """
        assert DEFAULT_MIGRATIONS_DIR.is_dir()
        assert (DEFAULT_MIGRATIONS_DIR / "001_initial.sql").is_file()

    @pytest.mark.asyncio
    async def test_missing_migrations_dir_fails_loudly(self, tmp_path: Path) -> None:
        """A missing migrations directory must not leave a schema-less database."""
        db = DatabaseManager(
            db_path=tmp_path / "narrator.db",
            migrations_dir=tmp_path / "does-not-exist",
        )

        with pytest.raises(MigrationError, match="Migrations directory not found"):
            await db.initialize()

        await db.close()

    @pytest.mark.asyncio
    async def test_initialize_applies_migrations(self, tmp_path: Path) -> None:
        """A fresh database ends up with the settings table the app expects."""
        db = DatabaseManager(db_path=tmp_path / "data" / "narrator.db")

        await db.initialize()
        try:
            async with db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
            ) as cursor:
                assert await cursor.fetchone() is not None
        finally:
            await db.close()
