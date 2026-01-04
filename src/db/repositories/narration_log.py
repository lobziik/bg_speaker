"""Narration log repository for storing and querying narration history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


@dataclass
class NarrationLogEntry:
    """Single narration log entry.

    Attributes:
        id: Unique narration ID.
        user: Username who requested narration.
        message_original: Original message text.
        text_formatted: Voice text used for TTS synthesis.
        text_translated: Subtitle text displayed in overlay.
        status: Status of the narration (success, error, moderation_rejected, etc.).
        rejection_reason: Reason for rejection (if moderation_rejected).
        error_message: Error details (if status is error).
        latency_moderation_ms: Moderation check time in milliseconds.
        latency_llm_ms: LLM processing time in milliseconds.
        latency_tts_ms: TTS synthesis time in milliseconds.
        latency_total_ms: Total processing time in milliseconds.
        created_at: Timestamp when the log was created.
    """

    id: str
    user: str
    message_original: str
    text_formatted: str | None
    text_translated: str | None
    status: str
    rejection_reason: str | None
    error_message: str | None
    latency_moderation_ms: int | None
    latency_llm_ms: int | None
    latency_tts_ms: int | None
    latency_total_ms: int | None
    created_at: datetime


@dataclass
class LogStats:
    """Aggregated log statistics.

    Attributes:
        total_count: Total number of narration attempts.
        success_count: Number of successful narrations.
        error_count: Number of failed narrations.
        moderation_rejected_count: Number of moderation rejections.
        avg_moderation_latency_ms: Average moderation check time in milliseconds.
        avg_llm_latency_ms: Average LLM processing time in milliseconds.
        avg_tts_latency_ms: Average TTS synthesis time in milliseconds.
        avg_total_latency_ms: Average total processing time in milliseconds.
    """

    total_count: int
    success_count: int
    error_count: int
    moderation_rejected_count: int
    avg_moderation_latency_ms: float | None
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
        narrator_lang: str,
        subtitle_lang: str,
        voice_text: str | None = None,
        subtitle_text: str | None = None,
        llm_provider: str | None = None,
        tts_provider: str | None = None,
        latency_moderation_ms: int | None = None,
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
            narrator_lang: Language for TTS output.
            subtitle_lang: Language for subtitles.
            voice_text: Text used for TTS synthesis.
            subtitle_text: Text displayed in subtitles.
            llm_provider: Name of LLM provider used.
            tts_provider: Name of TTS provider used.
            latency_moderation_ms: Moderation check time in milliseconds.
            latency_llm_ms: LLM processing time in milliseconds.
            latency_tts_ms: TTS processing time in milliseconds.
            latency_total_ms: Total processing time in milliseconds.
            queue_wait_ms: Time spent waiting in queue.
            rejection_reason: Reason for rejection (if filtered/rate limited).
            error_message: Error message (if failed).
        """
        # Map to existing DB columns for backward compatibility
        # DB columns: text_formatted, text_translated, source_lang, target_lang
        await self._conn.execute(
            """
            INSERT INTO narration_log (
                id, user, message_original, text_formatted, text_translated,
                source_lang, target_lang, was_translated,
                llm_provider, tts_provider,
                latency_moderation_ms, latency_llm_ms, latency_tts_ms, latency_total_ms,
                queue_wait_ms, status, rejection_reason, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id,
                user,
                message_original,
                voice_text,  # stored as text_formatted
                subtitle_text,  # stored as text_translated
                narrator_lang,  # stored as source_lang (reused column)
                subtitle_lang,  # stored as target_lang (reused column)
                narrator_lang != subtitle_lang,  # computed was_translated
                llm_provider,
                tts_provider,
                latency_moderation_ms,
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
            SELECT id, user, message_original, text_formatted, text_translated,
                   status, rejection_reason, error_message,
                   latency_moderation_ms, latency_llm_ms, latency_tts_ms,
                   latency_total_ms, created_at
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
            SELECT id, user, message_original, text_formatted, text_translated,
                   status, rejection_reason, error_message,
                   latency_moderation_ms, latency_llm_ms, latency_tts_ms,
                   latency_total_ms, created_at
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
                SUM(CASE WHEN status = 'moderation_rejected' THEN 1 ELSE 0 END) as rejected,
                AVG(latency_moderation_ms) as avg_moderation,
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
                    moderation_rejected_count=0,
                    avg_moderation_latency_ms=None,
                    avg_llm_latency_ms=None,
                    avg_tts_latency_ms=None,
                    avg_total_latency_ms=None,
                )
            return LogStats(
                total_count=row[0] or 0,
                success_count=row[1] or 0,
                error_count=row[2] or 0,
                moderation_rejected_count=row[3] or 0,
                avg_moderation_latency_ms=row[4],
                avg_llm_latency_ms=row[5],
                avg_tts_latency_ms=row[6],
                avg_total_latency_ms=row[7],
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
                SUM(CASE WHEN status = 'moderation_rejected' THEN 1 ELSE 0 END) as rejected,
                AVG(latency_moderation_ms) as avg_moderation,
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
                    moderation_rejected_count=0,
                    avg_moderation_latency_ms=None,
                    avg_llm_latency_ms=None,
                    avg_tts_latency_ms=None,
                    avg_total_latency_ms=None,
                )
            return LogStats(
                total_count=row[0] or 0,
                success_count=row[1] or 0,
                error_count=row[2] or 0,
                moderation_rejected_count=row[3] or 0,
                avg_moderation_latency_ms=row[4],
                avg_llm_latency_ms=row[5],
                avg_tts_latency_ms=row[6],
                avg_total_latency_ms=row[7],
            )

    def _row_to_entry(self, row: aiosqlite.Row) -> NarrationLogEntry:
        """Convert database row to NarrationLogEntry.

        Expected column order from SELECT:
            id, user, message_original, text_formatted, text_translated,
            status, rejection_reason, error_message,
            latency_moderation_ms, latency_llm_ms, latency_tts_ms,
            latency_total_ms, created_at

        Args:
            row: Database row.

        Returns:
            NarrationLogEntry instance.
        """
        created_at_str = str(row[12])
        # Handle both datetime string formats
        try:
            created_at = datetime.fromisoformat(created_at_str)
        except ValueError:
            # Fallback for alternate formats
            created_at = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")

        latency_moderation = row[8]
        latency_llm = row[9]
        latency_tts = row[10]
        latency_total = row[11]

        return NarrationLogEntry(
            id=str(row[0]),
            user=str(row[1]),
            message_original=str(row[2]),
            text_formatted=str(row[3]) if row[3] else None,
            text_translated=str(row[4]) if row[4] else None,
            status=str(row[5]),
            rejection_reason=str(row[6]) if row[6] else None,
            error_message=str(row[7]) if row[7] else None,
            latency_moderation_ms=(
                int(latency_moderation) if latency_moderation is not None else None
            ),
            latency_llm_ms=int(latency_llm) if latency_llm is not None else None,
            latency_tts_ms=int(latency_tts) if latency_tts is not None else None,
            latency_total_ms=int(latency_total) if latency_total is not None else None,
            created_at=created_at,
        )

    async def get_by_id(self, log_id: str) -> NarrationLogEntry | None:
        """Get a single log entry by ID.

        Args:
            log_id: The unique log ID.

        Returns:
            NarrationLogEntry if found, None otherwise.
        """
        async with self._conn.execute(
            """
            SELECT id, user, message_original, text_formatted, text_translated,
                   status, rejection_reason, error_message,
                   latency_moderation_ms, latency_llm_ms, latency_tts_ms,
                   latency_total_ms, created_at
            FROM narration_log
            WHERE id = ?
            """,
            (log_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_entry(row)
