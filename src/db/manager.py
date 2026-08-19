"""SQLite database manager with migration support."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()

# Migrations ship with the source tree (src/db/manager.py -> <root>/migrations),
# so the default must be anchored to the package rather than the process working
# directory - the container runs the app from the data volume, not from /app.
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


class DatabaseNotInitializedError(Exception):
    """Raised when database is accessed before initialization."""

    pass


class MigrationError(Exception):
    """Raised when migration fails."""

    pass


class DatabaseManager:
    """Manages SQLite database connection and migrations.

    Features:
    - Lazy connection management
    - Automatic migration on startup
    - Transaction context manager
    - Connection pool-like interface

    Usage:
        db = DatabaseManager(Path("data/narrator.db"))
        await db.initialize()

        async with db.transaction() as conn:
            await conn.execute("INSERT ...")

        await db.close()
    """

    def __init__(
        self,
        db_path: Path,
        migrations_dir: Path | None = None,
    ) -> None:
        """Initialize database manager.

        Args:
            db_path: Path to SQLite database file.
            migrations_dir: Path to migrations folder. Defaults to the
                ``migrations`` directory shipped alongside the source tree.
        """
        self._db_path = db_path
        self._migrations_dir = migrations_dir or DEFAULT_MIGRATIONS_DIR
        self._connection: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        """Get database connection.

        Raises:
            DatabaseNotInitializedError: If not initialized.
        """
        if self._connection is None:
            raise DatabaseNotInitializedError("Database not initialized. Call initialize() first.")
        return self._connection

    async def initialize(self) -> None:
        """Initialize database: create directories, connect, run migrations."""
        # Ensure data directory exists
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("database_initializing", path=str(self._db_path))

        # Connect
        self._connection = await aiosqlite.connect(self._db_path)

        # Enable foreign keys
        await self._connection.execute("PRAGMA foreign_keys = ON")

        # Run migrations
        await self._run_migrations()

        logger.info("database_initialized")

    async def close(self) -> None:
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("database_closed")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Transaction context manager with automatic commit/rollback.

        Usage:
            async with db.transaction() as conn:
                await conn.execute("INSERT ...")
                # Commits automatically on success
                # Rolls back on exception

        Yields:
            The database connection within a transaction.
        """
        conn = self.connection
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def _run_migrations(self) -> None:
        """Run pending SQL migrations from the migrations folder.

        Raises:
            MigrationError: If the migrations directory is missing. Skipping it
                silently would leave the app running against a schema-less
                database and fail later with a confusing "no such table" error.
        """
        if not self._migrations_dir.exists():
            raise MigrationError(
                f"Migrations directory not found: {self._migrations_dir}. "
                f"Pass migrations_dir explicitly or run from a checkout that "
                f"contains it."
            )

        conn = self.connection

        # Create migrations tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.commit()

        # Get applied migrations
        async with conn.execute("SELECT name FROM _migrations") as cursor:
            applied = {row[0] for row in await cursor.fetchall()}

        # Find and apply new migrations
        migration_files = sorted(self._migrations_dir.glob("*.sql"))

        for migration_file in migration_files:
            if migration_file.name in applied:
                continue

            logger.info("applying_migration", name=migration_file.name)

            try:
                sql = migration_file.read_text()
                await conn.executescript(sql)
                await conn.execute(
                    "INSERT INTO _migrations (name) VALUES (?)",
                    (migration_file.name,),
                )
                await conn.commit()
                logger.info("migration_applied", name=migration_file.name)

            except aiosqlite.Error as e:
                logger.error(
                    "migration_failed",
                    name=migration_file.name,
                    error=str(e),
                )
                raise MigrationError(f"Migration {migration_file.name} failed: {e}") from e
