"""Health check endpoint."""

from fastapi import APIRouter

from src.api.dependencies import AppStateDep

router = APIRouter()


@router.get("/health")
async def health_check(state: AppStateDep) -> dict[str, object]:
    """Health check endpoint.

    Returns component health status for monitoring.

    Returns:
        Dictionary with health status of all components.
    """
    twitch_connected = (
        state.twitch_eventsub is not None and state.twitch_eventsub.is_connected
    )

    rate_status = state.rate_limiter.get_status()

    return {
        "status": "healthy",
        "components": {
            "database": True,  # Would fail startup if not
            "twitch_eventsub": twitch_connected,
            "queue_size": len(state.queue),
            "rate_limiter": {
                "tts_available": rate_status.tts_available,
                "active_cooldowns": rate_status.active_user_cooldowns,
            },
        },
    }
