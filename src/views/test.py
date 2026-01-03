"""Test narration view routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.dependencies import AppStateDep, TemplatesDep
from src.models.narration import NarratorStyle

router = APIRouter()


@router.get("/test", response_class=HTMLResponse)
async def test_page(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Render test narration page."""
    return templates.TemplateResponse(
        request,
        "pages/test.html",
        {
            "active_page": "test",
            "narrator_styles": list(NarratorStyle),
            "pipeline_available": state.pipeline is not None,
            "worker_running": state.worker.is_running if state.worker else False,
        },
    )


@router.post("/test/narrate", response_class=HTMLResponse)
async def test_narrate(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Submit test narration and return result partial."""
    form_data = await request.form()
    message = str(form_data.get("message", ""))
    user = str(form_data.get("user", "TestUser"))

    if not message.strip():
        return templates.TemplateResponse(
            request,
            "partials/narration_result.html",
            {
                "success": False,
                "error": "Message cannot be empty",
            },
        )

    # Add to queue
    result = await state.queue.add(
        user=user,
        message=message.strip(),
        redemption_id=None,
    )

    if not result.success:
        reason_msg = str(result.rejection_reason.value) if result.rejection_reason else "Unknown error"
        return templates.TemplateResponse(
            request,
            "partials/narration_result.html",
            {
                "success": False,
                "error": reason_msg,
                "retry_after": result.retry_after_seconds,
            },
        )

    return templates.TemplateResponse(
        request,
        "partials/narration_result.html",
        {
            "success": True,
            "item_id": result.item_id,
            "queue_position": result.queue_position,
            "user": user,
            "message": message[:100],
        },
    )
