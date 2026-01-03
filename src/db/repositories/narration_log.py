"""Narration log repository for storing and querying narration history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    pass


@dataclass
class NarrationLogEntry:
    """Single narration log entry."""

    id: str
    user: str
    message_original: str
    text_formatted: str | None
    status: str
    latency_llm_ms: int | None
    latency_tts_ms: int | None
    latency_total_ms: int | None
    created_at: datetime


@dataclass
class LogStats:
    """Aggregated log statistics."""

    total_count: int
    success_count: int
    error_count: int
    avg_llm_latency_ms: float | None
    avg_tts_latency_ms: float | None
    avg_total_latency_ms: float | None


@dataclass
class LogFilter:
    """Filter parameters for log queries."""

    user: str | None = None
    status: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    page: int = 1
    per_page: int = 50


class NarrationLogRepository:
    """Repository for narration history logs.

    Provides methods to log narration attempts and query history
    with filtering, pagination, and aggregation.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Initialize repository with database connection.

        Args:
            connection: Active aiosqlite database connection.
        """
        self._conn = connection

    async def log_narration(
        self,
        *,
        id: str,
        user: str,
        message_original: str,
        status: str,
        source_lang: str,
        target_lang: str,
        text_formatted: str | None = None,
        text_translated: str | None = None,
        was_translated: bool = False,
        llm_provider: str | None = None,
        tts_provider: str | None = None,
        latency_llm_ms: int | None = None,
        latency_tts_ms: int | None = None,
        latency_total_ms: int | None = None,
        queue_wait_ms: int | None = None,
        rejection_reason: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Log a narration attempt.

        Args:
            id: Unique narration ID.
            user: Username who requested narration.
            message_original: Original message text.
            status: Status of the narration (success, error, filtered, etc.).
            source_lang: Source language code.
            target_lang: Target language code.
            text_formatted: LLM-formatted narrator text.
            text_translated: Translated text (if translation was needed).
            was_translated: Whether translation was performed.
            llm_provider: Name of LLM provider used.
            tts_provider: Name of TTS provider used.
            latency_llm_ms: LLM processing time in milliseconds.
            latency_tts_ms: TTS processing time in milliseconds.
            latency_total_ms: Total processing time in milliseconds.
            queue_wait_ms: Time spent waiting in queue.
            rejection_reason: Reason for rejection (if filtered/rate limited).
            error_message: Error message (if failed).
        """
        await self._conn.execute(
            """
            INSERT INTO narration_log (
                id, user, message_original, text_formatted, text_translated,
                source_lang, target_lang, was_translated,
                llm_provider, tts_provider,
                latency_llm_ms, latency_tts_ms, latency_total_ms, queue_wait_ms,
                status, rejection_reason, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id,
                user,
                message_original,
                text_formatted,
                text_translated,
                source_lang,
                target_lang,
                was_translated,
                llm_provider,
                tts_provider,
                latency_llm_ms,
                latency_tts_ms,
                latency_total_ms,
                queue_wait_ms,
                status,
                rejection_reason,
                error_message,
            ),
        )
        await self._conn.commit()

    async def get_recent(self, limit: int = 10) -> list[NarrationLogEntry]:
        """Get most recent log entries.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            List of recent log entries ordered by creation time descending.
        """
        async with self._conn.execute(
            """
            SELECT id, user, message_original, text_formatted, status,
                   latency_llm_ms, latency_tts_ms, latency_total_ms, created_at
            FROM narration_log
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_entry(row) for row in rows]

    async def get_filtered(
        self,
        filters: LogFilter,
    ) -> tuple[list[NarrationLogEntry], int]:
        """Get filtered log entries with pagination.

        Args:
            filters: Filter parameters including user, status, date range, and pagination.

        Returns:
            Tuple of (log entries list, total count matching filters).
        """
        where_clauses: list[str] = []
        params: list[object] = []

        if filters.user:
            where_clauses.append("user LIKE ?")
            params.append(f"%{filters.user}%")

        if filters.status:
            where_clauses.append("status = ?")
            params.append(filters.status)

        if filters.date_from:
            where_clauses.append("created_at >= ?")
            params.append(filters.date_from.isoformat())

        if filters.date_to:
            where_clauses.append("created_at <= ?")
            params.append(filters.date_to.isoformat())

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Get total count
        async with self._conn.execute(
            f"SELECT COUNT(*) FROM narration_log WHERE {where_sql}",
            params,
        ) as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0

        # Get paginated results
        offset = (filters.page - 1) * filters.per_page
        async with self._conn.execute(
            f"""
            SELECT id, user, message_original, text_formatted, status,
                   latency_llm_ms, latency_tts_ms, latency_total_ms, created_at
            FROM narration_log
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, filters.per_page, offset],
        ) as cursor:
            rows = await cursor.fetchall()
            entries = [self._row_to_entry(row) for row in rows]

        return entries, total

    async def get_today_stats(self) -> LogStats:
        """Get statistics for today.

        Returns:
            Aggregated statistics for narrations created today.
        """
        today = date.today().isoformat()
        async with self._conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error,
                AVG(latency_llm_ms) as avg_llm,
                AVG(latency_tts_ms) as avg_tts,
                AVG(latency_total_ms) as avg_total
            FROM narration_log
            WHERE DATE(created_at) = ?
            """,
            (today,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return LogStats(
                    total_count=0,
                    success_count=0,
                    error_count=0,
                    avg_llm_latency_ms=None,
                    avg_tts_latency_ms=None,
                    avg_total_latency_ms=None,
                )
            return LogStats(
                total_count=row[0] or 0,
                success_count=row[1] or 0,
                error_count=row[2] or 0,
                avg_llm_latency_ms=row[3],
                avg_tts_latency_ms=row[4],
                avg_total_latency_ms=row[5],
            )

    async def get_stats(self, filters: LogFilter) -> LogStats:
        """Get statistics for filtered entries.

        Args:
            filters: Filter parameters (date range, user, status).

        Returns:
            Aggregated statistics for matching entries.
        """
        where_clauses: list[str] = []
        params: list[object] = []

        if filters.user:
            where_clauses.append("user LIKE ?")
            params.append(f"%{filters.user}%")

        if filters.status:
            where_clauses.append("status = ?")
            params.append(filters.status)

        if filters.date_from:
            where_clauses.append("created_at >= ?")
            params.append(filters.date_from.isoformat())

        if filters.date_to:
            where_clauses.append("created_at <= ?")
            params.append(filters.date_to.isoformat())

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        async with self._conn.execute(
            f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error,
                AVG(latency_llm_ms) as avg_llm,
                AVG(latency_tts_ms) as avg_tts,
                AVG(latency_total_ms) as avg_total
            FROM narration_log
            WHERE {where_sql}
            """,
            params,
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return LogStats(
                    total_count=0,
                    success_count=0,
                    error_count=0,
                    avg_llm_latency_ms=None,
                    avg_tts_latency_ms=None,
                    avg_total_latency_ms=None,
                )
            return LogStats(
                total_count=row[0] or 0,
                success_count=row[1] or 0,
                error_count=row[2] or 0,
                avg_llm_latency_ms=row[3],
                avg_tts_latency_ms=row[4],
                avg_total_latency_ms=row[5],
            )

    def _row_to_entry(self, row: aiosqlite.Row) -> NarrationLogEntry:
        """Convert database row to NarrationLogEntry.

        Args:
            row: Database row.

        Returns:
            NarrationLogEntry instance.
        """
        created_at_str = str(row[8])
        # Handle both datetime string formats
        try:
            created_at = datetime.fromisoformat(created_at_str)
        except ValueError:
            # Fallback for alternate formats
            created_at = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")

        latency_llm = row[5]
        latency_tts = row[6]
        latency_total = row[7]

        return NarrationLogEntry(
            id=str(row[0]),
            user=str(row[1]),
            message_original=str(row[2]),
            text_formatted=str(row[3]) if row[3] else None,
            status=str(row[4]),
            latency_llm_ms=int(latency_llm) if latency_llm is not None else None,
            latency_tts_ms=int(latency_tts) if latency_tts is not None else None,
            latency_total_ms=int(latency_total) if latency_total is not None else None,
            created_at=created_at,
        )
