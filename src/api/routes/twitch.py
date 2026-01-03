"""Twitch OAuth callback routes."""

import secrets
from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from src.api.auth import RequireAuthDep
from src.api.dependencies import AppStateDep
from src.db.repositories.twitch_state import TwitchStateRepository
from src.services.twitch.auth import InvalidGrantError, TwitchAuthError
from src.services.twitch.init_services import initialize_twitch_services

logger = structlog.get_logger()

router = APIRouter()

# In-memory state storage (use Redis in production)
_oauth_states: set[str] = set()


@router.get("/login")
async def login(state: AppStateDep, _username: RequireAuthDep) -> RedirectResponse:
    """Initiate Twitch OAuth flow.

    Requires Basic Auth.

    Generates CSRF state token and redirects to Twitch authorization page.

    Args:
        state: Application state.
        _username: Authenticated username (unused, ensures auth).

    Returns:
        Redirect to Twitch OAuth authorization URL.
    """
    csrf_state = secrets.token_urlsafe(32)
    _oauth_states.add(csrf_state)

    auth_url = state.twitch_auth.get_authorization_url(csrf_state)
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def oauth_callback(
    state: AppStateDep,
    code: Annotated[str, Query()],
    oauth_state: Annotated[str, Query(alias="state")],
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
) -> dict[str, str]:
    """Handle Twitch OAuth callback.

    Exchanges authorization code for tokens and stores them in database.

    Args:
        state: Application state.
        code: Authorization code from Twitch.
        oauth_state: CSRF state token.
        error: OAuth error code if authorization failed.
        error_description: Human-readable error description.

    Returns:
        Success message with authorized user info.

    Raises:
        HTTPException: If OAuth fails or state is invalid.
    """
    # Check for OAuth errors
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"OAuth error: {error} - {error_description}",
        )

    # Verify CSRF state
    if oauth_state not in _oauth_states:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired state parameter",
        )
    _oauth_states.discard(oauth_state)

    # Exchange code for tokens
    try:
        tokens = await state.twitch_auth.exchange_code(code)
    except InvalidGrantError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TwitchAuthError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    # Store tokens in database
    repo = TwitchStateRepository(state.db.connection)
    await repo.save_tokens(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        broadcaster_id=tokens.user_id,
        broadcaster_login=tokens.user_login,
    )

    # Initialize Twitch services after successful OAuth
    try:
        success = await initialize_twitch_services(
            state=state,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            broadcaster_id=tokens.user_id,
        )
        if success:
            logger.info("twitch_services_initialized_after_oauth")
        else:
            logger.warning("twitch_services_init_failed_after_oauth")
    except Exception as e:
        logger.warning(
            "twitch_init_after_oauth_error",
            error=str(e),
            error_type=type(e).__name__,
        )
        # Continue - tokens are saved, user can retry via page refresh

    return {
        "status": "authorized",
        "user": tokens.user_login,
        "message": "Twitch authorization successful. You can close this window.",
    }


@router.post("/logout")
async def logout(state: AppStateDep, _username: RequireAuthDep) -> dict[str, str]:
    """Clear stored Twitch tokens.

    Requires Basic Auth.

    Stops EventSub connection, closes rewards controller, and clears
    all stored OAuth tokens.

    Args:
        state: Application state.
        _username: Authenticated username (unused, ensures auth).

    Returns:
        Logout confirmation.
    """
    repo = TwitchStateRepository(state.db.connection)
    await repo.clear()

    if state.twitch_eventsub:
        await state.twitch_eventsub.stop()
        state.twitch_eventsub = None

    if state.twitch_rewards:
        await state.twitch_rewards.close()
        state.twitch_rewards = None

    logger.info("twitch_logged_out")
    return {"status": "logged_out"}


@router.get("/status")
async def auth_status(state: AppStateDep, _username: RequireAuthDep) -> dict[str, object]:
    """Get current Twitch authorization status.

    Requires Basic Auth.

    Args:
        state: Application state.
        _username: Authenticated username (unused, ensures auth).

    Returns:
        Authorization status and connected user info.
    """
    repo = TwitchStateRepository(state.db.connection)
    twitch_state = await repo.get_state()

    if twitch_state is None:
        return {
            "authorized": False,
            "eventsub_connected": False,
        }

    return {
        "authorized": True,
        "broadcaster_login": twitch_state.broadcaster_login,
        "broadcaster_id": twitch_state.broadcaster_id,
        "reward_id": twitch_state.reward_id,
        "eventsub_connected": (
            state.twitch_eventsub is not None and state.twitch_eventsub.is_connected
        ),
    }
