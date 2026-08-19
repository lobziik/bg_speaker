"""Test narration endpoint for development."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.api.dependencies import AppStateDep
from src.models.narration import NarratorStyle

router = APIRouter()


class TestNarrationRequest(BaseModel):
    """Request body for test narration.

    Attributes:
        message: Message text to narrate.
        user: Username for the narration.
        style: Narrator style to use.
    """

    message: str = Field(min_length=1, max_length=300)
    user: str = Field(default="TestUser", min_length=1, max_length=50)
    style: NarratorStyle = NarratorStyle.DEFAULT


class TestNarrationResponse(BaseModel):
    """Response from test narration.

    Attributes:
        success: Whether the message was queued.
        item_id: Queue item ID if successful.
        queue_position: Position in queue if successful.
        error: Error message if failed.
    """

    success: bool
    item_id: str | None = None
    queue_position: int | None = None
    error: str | None = None


@router.post("/test/narrate")
async def test_narration(
    state: AppStateDep,
    request: TestNarrationRequest,
) -> TestNarrationResponse:
    """Add a test message to the narration queue.

    Useful for testing the pipeline without Twitch integration.

    Args:
        state: Application state.
        request: Test narration request.

    Returns:
        Result indicating success or failure.
    """
    result = await state.queue.add(
        user=request.user,
        message=request.message,
        redemption_id=None,  # No redemption for test
    )

    if not result.success:
        return TestNarrationResponse(
            success=False,
            error=f"Queue rejected: {result.rejection_reason}",
        )

    return TestNarrationResponse(
        success=True,
        item_id=result.item_id,
        queue_position=result.queue_position,
    )


@router.get("/test/queue")
async def get_queue(state: AppStateDep) -> dict[str, object]:
    """Get current queue state for debugging.

    Returns:
        Queue items and rate limiter status.
    """
    items = await state.queue.get_items()
    rate_status = state.rate_limiter.get_status()

    return {
        "queue_size": len(items),
        "items": [
            {
                "id": item.id,
                "user": item.user,
                "message": (item.message[:50] + "..." if len(item.message) > 50 else item.message),
                "priority": item.priority,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ],
        "rate_limiter": {
            "tts_available": rate_status.tts_available,
            "tts_available_in_seconds": rate_status.tts_available_in_seconds,
            "active_cooldowns": rate_status.active_user_cooldowns,
        },
    }


@router.delete("/test/queue/{item_id}")
async def skip_queue_item(state: AppStateDep, item_id: str) -> dict[str, object]:
    """Remove an item from the queue.

    Args:
        state: Application state.
        item_id: Queue item ID to remove.

    Returns:
        Result indicating if item was found and removed.
    """
    removed = await state.queue.skip(item_id)
    return {"removed": removed, "item_id": item_id}


@router.delete("/test/queue")
async def clear_queue(state: AppStateDep) -> dict[str, int]:
    """Clear all items from the queue.

    Returns:
        Number of items cleared.
    """
    count = await state.queue.clear()
    return {"cleared": count}
