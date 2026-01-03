"""Dashboard view routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

# TC001 ignored: FastAPI Depends() requires these at runtime for dependency injection
from src.api.dependencies import AppStateDep, TemplatesDep  # noqa: TC001
from src.db.repositories.twitch_state import TwitchStateRepository

if TYPE_CHECKING:
    from src.api.dependencies import AppState

router = APIRouter()


async def _get_twitch_status(state: AppState) -> dict[str, bool | str | None]:
    """Gather Twitch integration status from all sources.

    Args:
        state: Application state with service instances.

    Returns:
        Dictionary with Twitch auth, connection, and reward status.
    """
    repo = TwitchStateRepository(state.db.connection)
    twitch_state = await repo.get_state()

    # Use global_cooldown to determine if reward is paused
    reward_paused: bool | None = None
    if state.global_cooldown and twitch_state and twitch_state.reward_id:
        cooldown_status = state.global_cooldown.get_status()
        reward_paused = cooldown_status.is_active

    return {
        "authorized": twitch_state is not None,
        "broadcaster_login": twitch_state.broadcaster_login if twitch_state else None,
        "eventsub_connected": (
            state.twitch_eventsub is not None and state.twitch_eventsub.is_connected
        ),
        "reward_id": twitch_state.reward_id if twitch_state else None,
        "reward_paused": reward_paused,
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Render dashboard page.

    Shows queue status, rate limit countdown, Twitch status, and worker status.
    """
    queue_items = await state.queue.get_items()
    rate_status = state.rate_limiter.get_status()
    worker_running = state.worker.is_running if state.worker else False
    twitch_status = await _get_twitch_status(state)

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
            "twitch_status": twitch_status,
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


@router.get("/partials/twitch-status", response_class=HTMLResponse)
async def twitch_status_partial(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Get Twitch status partial for HTMX polling.

    Provides real-time Twitch connection and reward status.
    """
    twitch_status = await _get_twitch_status(state)

    return templates.TemplateResponse(
        request,
        "partials/twitch_status.html",
        {"twitch_status": twitch_status},
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
