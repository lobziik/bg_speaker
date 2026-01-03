"""Dashboard view routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

# TC001 ignored: FastAPI Depends() requires these at runtime for dependency injection
from src.api.dependencies import AppStateDep, TemplatesDep  # noqa: TC001

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Render dashboard page.

    Shows queue status, rate limit countdown, recent narrations, and worker status.
    """
    queue_items = await state.queue.get_items()
    rate_status = state.rate_limiter.get_status()
    worker_running = state.worker.is_running if state.worker else False

    return templates.TemplateResponse(
        request,
        "pages/dashboard.html",
        {
            "active_page": "dashboard",
            "queue_items": queue_items,
            "queue_length": len(queue_items),
            "rate_status": rate_status,
            "worker_running": worker_running,
            "pipeline_available": state.pipeline is not None,
        },
    )


@router.post("/worker/toggle", response_class=HTMLResponse)
async def toggle_worker(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Toggle worker start/stop.

    Returns updated worker status partial.
    """
    if state.worker is None:
        raise HTTPException(
            status_code=400,
            detail="Worker not configured - missing LLM API key",
        )

    if state.worker.is_running:
        await state.worker.stop()
    else:
        await state.worker.start()

    return templates.TemplateResponse(
        request,
        "partials/worker_status.html",
        {
            "worker_running": state.worker.is_running,
            "pipeline_available": state.pipeline is not None,
        },
    )


@router.get("/partials/stats-cards", response_class=HTMLResponse)
async def stats_cards_partial(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Get stats cards partial for HTMX polling."""
    queue_items = await state.queue.get_items()
    rate_status = state.rate_limiter.get_status()

    return templates.TemplateResponse(
        request,
        "partials/stats_cards.html",
        {
            "queue_length": len(queue_items),
            "rate_status": rate_status,
        },
    )


@router.get("/partials/rate-limit-status", response_class=HTMLResponse)
async def rate_limit_status_partial(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Get rate limit status partial for HTMX polling."""
    rate_status = state.rate_limiter.get_status()
    global_cooldown_status = (
        state.global_cooldown.get_status() if state.global_cooldown else None
    )

    return templates.TemplateResponse(
        request,
        "partials/rate_limit_status.html",
        {
            "rate_status": rate_status,
            "global_cooldown_status": global_cooldown_status,
        },
    )


@router.post("/cooldown/force-unpause", response_class=HTMLResponse)
async def force_unpause_cooldown(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Force unpause the Twitch reward (admin override).

    Immediately ends the global cooldown and unpauses the reward.
    """
    if state.global_cooldown:
        await state.global_cooldown.force_unpause()

    # Return updated rate limit status partial
    return await rate_limit_status_partial(request, state, templates)
