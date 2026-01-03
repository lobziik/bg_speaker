"""Logs view routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.dependencies import AppStateDep, TemplatesDep
from src.db.repositories.narration_log import LogFilter, NarrationLogRepository

router = APIRouter()


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
    user: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Render logs page with filtering and pagination."""
    log_repo = NarrationLogRepository(state.db.connection)

    # Parse date filters
    date_from_dt = None
    date_to_dt = None

    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from)
        except ValueError:
            pass

    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to)
        except ValueError:
            pass

    filters = LogFilter(
        user=user,
        status=status,
        date_from=date_from_dt,
        date_to=date_to_dt,
        page=page,
        per_page=50,
    )

    logs, total = await log_repo.get_filtered(filters)
    total_pages = (total + filters.per_page - 1) // filters.per_page if total > 0 else 1
    stats = await log_repo.get_stats(filters)

    return templates.TemplateResponse(
        request,
        "pages/logs.html",
        {
            "active_page": "logs",
            "logs": logs,
            "filters": filters,
            "total": total,
            "total_pages": total_pages,
            "current_page": page,
            "stats": stats,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    )


@router.get("/logs/partials/table", response_class=HTMLResponse)
async def logs_table_partial(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
    user: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Get logs table partial for HTMX filtering."""
    log_repo = NarrationLogRepository(state.db.connection)

    # Parse date filters
    date_from_dt = None
    date_to_dt = None

    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from)
        except ValueError:
            pass

    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to)
        except ValueError:
            pass

    filters = LogFilter(
        user=user,
        status=status,
        date_from=date_from_dt,
        date_to=date_to_dt,
        page=page,
        per_page=50,
    )

    logs, total = await log_repo.get_filtered(filters)
    total_pages = (total + filters.per_page - 1) // filters.per_page if total > 0 else 1
    stats = await log_repo.get_stats(filters)

    return templates.TemplateResponse(
        request,
        "partials/log_table.html",
        {
            "logs": logs,
            "total": total,
            "total_pages": total_pages,
            "current_page": page,
            "stats": stats,
            "filters": filters,
        },
    )
