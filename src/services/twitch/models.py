"""Twitch API model types with strict typing."""

from typing import TypedDict


class TwitchUser(TypedDict):
    """Twitch user information from API."""

    id: str
    login: str
    display_name: str


class TwitchRewardData(TypedDict):
    """Twitch reward information from redemption event."""

    id: str
    title: str
    cost: int
    prompt: str


class RedemptionEventPayload(TypedDict):
    """Parsed channel point redemption event."""

    id: str  # Redemption ID (for fulfill/cancel)
    user_id: str
    user_login: str
    user_name: str
    user_input: str  # Message from user
    reward: TwitchRewardData
    redeemed_at: str  # ISO timestamp


class TokenResponse(TypedDict):
    """OAuth token response from Twitch."""

    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str
    scope: list[str]


class ValidateResponse(TypedDict):
    """Token validation response."""

    client_id: str
    login: str
    scopes: list[str]
    user_id: str
    expires_in: int
