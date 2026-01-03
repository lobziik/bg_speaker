"""HTTP Basic Authentication for admin routes."""

import secrets
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.config import get_env_settings

logger = structlog.get_logger()

# HTTPBasic scheme - triggers browser auth prompt
security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials) -> str:
    """Verify HTTP Basic credentials against environment variables.

    Uses timing-safe comparison to prevent timing attacks.

    Args:
        credentials: HTTP Basic credentials from request.

    Returns:
        The authenticated username.

    Raises:
        HTTPException: 401 if credentials invalid, 500 if auth not configured.
    """
    env = get_env_settings()

    # Fail fast if auth not configured
    if not env.has_basic_auth_configured():
        logger.error(
            "basic_auth_not_configured",
            message="ADMIN_USERNAME and ADMIN_PASSWORD must be set",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: Basic Auth credentials not set",
        )

    # Timing-safe comparison to prevent timing attacks
    username_correct = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        env.admin_username.encode("utf-8"),
    )
    password_correct = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        env.admin_password.get_secret_value().encode("utf-8"),
    )

    if not (username_correct and password_correct):
        logger.warning(
            "auth_failed",
            username=credentials.username,
            message="Invalid credentials",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    logger.debug("auth_success", username=credentials.username)
    return credentials.username


def require_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
) -> str:
    """FastAPI dependency that requires Basic Auth.

    Use as a dependency on routes or routers that need protection.

    Args:
        credentials: HTTP Basic credentials (injected by FastAPI).

    Returns:
        The authenticated username.

    Raises:
        HTTPException: 401 if credentials invalid, 500 if not configured.
    """
    return verify_credentials(credentials)


# Type alias for dependency injection
RequireAuthDep = Annotated[str, Depends(require_auth)]
