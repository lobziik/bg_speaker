"""Twitch OAuth authentication service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx
import structlog

if TYPE_CHECKING:
    from pydantic import SecretStr

    from src.services.twitch.models import TokenResponse, ValidateResponse

logger = structlog.get_logger()


class TwitchAuthError(Exception):
    """Raised when Twitch authentication fails."""

    pass


class TokenExpiredError(TwitchAuthError):
    """Raised when token is expired and refresh failed."""

    pass


class InvalidGrantError(TwitchAuthError):
    """Raised when OAuth code is invalid or expired."""

    pass


@dataclass(frozen=True)
class TwitchTokens:
    """Validated Twitch OAuth tokens.

    Attributes:
        access_token: OAuth access token for API calls.
        refresh_token: Token for refreshing access.
        user_id: Twitch user ID of the authenticated user.
        user_login: Twitch username of the authenticated user.
        scopes: Set of granted OAuth scopes.
    """

    access_token: str
    refresh_token: str
    user_id: str
    user_login: str
    scopes: frozenset[str]


class TwitchAuthService:
    """Handles Twitch OAuth 2.0 authorization code flow.

    Responsibilities:
    - Generate authorization URLs
    - Exchange authorization codes for tokens
    - Refresh expired tokens
    - Validate tokens

    Required scopes for this app:
    - channel:read:redemptions (receive redemption events)
    - channel:manage:redemptions (fulfill/cancel redemptions)
    """

    AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
    TOKEN_URL = "https://id.twitch.tv/oauth2/token"
    VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
    REVOKE_URL = "https://id.twitch.tv/oauth2/revoke"

    REQUIRED_SCOPES = frozenset(
        [
            "channel:read:redemptions",
            "channel:manage:redemptions",
        ]
    )

    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        redirect_uri: str,
    ) -> None:
        """Initialize auth service.

        Args:
            client_id: Twitch application client ID.
            client_secret: Twitch application client secret.
            redirect_uri: OAuth callback URL.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        """Lazy-load HTTP client."""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    def get_authorization_url(self, state: str) -> str:
        """Generate OAuth authorization URL.

        Args:
            state: CSRF protection token (should be stored in session).

        Returns:
            Full authorization URL for redirect.
        """
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.REQUIRED_SCOPES),
            "state": state,
        }
        return f"{self.AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> TwitchTokens:
        """Exchange authorization code for tokens.

        Args:
            code: Authorization code from OAuth callback.

        Returns:
            Validated tokens with user info.

        Raises:
            InvalidGrantError: If code is invalid/expired.
            TwitchAuthError: For other auth failures.
        """
        logger.info("auth_exchanging_code")
        http = await self._get_http()

        try:
            logger.debug(
                "auth_http_request",
                method="POST",
                url=self.TOKEN_URL,
                grant_type="authorization_code",
            )
            response = await http.post(
                self.TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret.get_secret_value(),
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self._redirect_uri,
                },
            )
            logger.debug(
                "auth_http_response",
                status=response.status_code,
            )

            if response.status_code == 400:
                error_data = response.json()
                if error_data.get("error") == "invalid_grant":
                    raise InvalidGrantError(
                        "Authorization code expired or invalid. Please try again."
                    )
                raise TwitchAuthError(f"Token exchange failed: {error_data}")

            response.raise_for_status()
            token_data: TokenResponse = response.json()

        except httpx.HTTPStatusError as e:
            logger.error("auth_code_exchange_failed", status=e.response.status_code)
            raise TwitchAuthError(f"Token exchange failed: {e}") from e

        # Validate token and get user info
        tokens = await self._validate_and_build_tokens(
            token_data["access_token"],
            token_data["refresh_token"],
        )
        logger.info(
            "auth_code_exchanged",
            user_id=tokens.user_id,
            user_login=tokens.user_login,
        )
        return tokens

    async def refresh_tokens(self, refresh_token: str) -> TwitchTokens:
        """Refresh expired access token.

        Args:
            refresh_token: Current refresh token.

        Returns:
            New validated tokens.

        Raises:
            TokenExpiredError: If refresh token is also expired.
        """
        logger.info("auth_refreshing_token")
        http = await self._get_http()

        try:
            logger.debug(
                "auth_http_request",
                method="POST",
                url=self.TOKEN_URL,
                grant_type="refresh_token",
            )
            response = await http.post(
                self.TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret.get_secret_value(),
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            logger.debug(
                "auth_http_response",
                status=response.status_code,
            )

            if response.status_code == 400:
                raise TokenExpiredError("Refresh token expired. User must re-authorize.")

            response.raise_for_status()
            token_data: TokenResponse = response.json()

        except httpx.HTTPStatusError as e:
            logger.error("auth_token_refresh_failed", status=e.response.status_code)
            raise TwitchAuthError(f"Token refresh failed: {e}") from e

        tokens = await self._validate_and_build_tokens(
            token_data["access_token"],
            token_data["refresh_token"],
        )
        logger.info(
            "auth_token_refreshed",
            user_id=tokens.user_id,
            user_login=tokens.user_login,
        )
        return tokens

    async def validate_token(self, access_token: str) -> ValidateResponse:
        """Validate access token and get user info.

        Args:
            access_token: Token to validate.

        Returns:
            Validation response with user info.

        Raises:
            TokenExpiredError: If token is invalid/expired.
        """
        logger.info("auth_validating_token")
        http = await self._get_http()

        logger.debug(
            "auth_http_request",
            method="GET",
            url=self.VALIDATE_URL,
        )
        response = await http.get(
            self.VALIDATE_URL,
            headers={"Authorization": f"OAuth {access_token}"},
        )
        logger.debug(
            "auth_http_response",
            status=response.status_code,
        )

        if response.status_code == 401:
            raise TokenExpiredError("Access token is invalid or expired")

        response.raise_for_status()
        result: ValidateResponse = response.json()
        logger.info(
            "auth_token_validated",
            user_id=result["user_id"],
            user_login=result["login"],
            scopes=result["scopes"],
        )
        return result

    async def _validate_and_build_tokens(
        self,
        access_token: str,
        refresh_token: str,
    ) -> TwitchTokens:
        """Validate token and build TwitchTokens object."""
        validation = await self.validate_token(access_token)

        # Verify required scopes
        granted_scopes = frozenset(validation["scopes"])
        missing = self.REQUIRED_SCOPES - granted_scopes
        if missing:
            raise TwitchAuthError(
                f"Missing required scopes: {missing}. "
                "User must re-authorize with correct permissions."
            )

        return TwitchTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=validation["user_id"],
            user_login=validation["login"],
            scopes=granted_scopes,
        )

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None
