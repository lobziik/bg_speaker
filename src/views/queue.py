"""Queue management view routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from src.api.dependencies import AppStateDep, TemplatesDep

router = APIRouter()


@router.get("/queue", response_class=HTMLResponse)
async def queue_page(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Render queue management page."""
    queue_items = await state.queue.get_items()
    rate_status = state.rate_limiter.get_status()

    return templates.TemplateResponse(
        request,
        "pages/queue.html",
        {
            "active_page": "queue",
            "queue_items": queue_items,
            "rate_status": rate_status,
        },
    )


@router.get("/queue/partials/queue-list", response_class=HTMLResponse)
async def queue_list_partial(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Get queue list partial for HTMX refresh."""
    queue_items = await state.queue.get_items()

    return templates.TemplateResponse(
        request,
        "partials/queue_list.html",
        {
            "queue_items": queue_items,
        },
    )


@router.delete("/queue/{item_id}", response_class=HTMLResponse)
async def skip_queue_item(
    item_id: str,
    state: AppStateDep,
) -> HTMLResponse:
    """Skip (remove) item from queue.

    Returns empty response - item removal is handled via WebSocket update.
    """
    removed = await state.queue.skip(item_id)

    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Item {item_id} not found in queue",
        )

    # Return empty content - WebSocket will trigger queue list refresh
    return HTMLResponse(content="", status_code=200)


@router.delete("/queue", response_class=HTMLResponse)
async def clear_queue(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Clear all items from queue."""
    count = await state.queue.clear()

    # Return updated queue list
    queue_items = await state.queue.get_items()

    return templates.TemplateResponse(
        request,
        "partials/queue_list.html",
        {
            "queue_items": queue_items,
            "cleared_count": count,
        },
    )
