"""Configuration loading and management."""

from typing import Literal

import structlog
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class EnvSettings(BaseSettings):
    """Environment variables - secrets only.

    These are loaded from environment variables or .env file.
    API keys and sensitive credentials should NEVER be stored in the database.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Twitch
    twitch_client_id: str = ""
    twitch_client_secret: SecretStr = SecretStr("")
    twitch_channel: str = ""
    twitch_redirect_uri: str = "http://localhost:8000/auth/callback"

    # LLM Providers
    groq_api_key: SecretStr | None = None
    # Google Gemini - powers both the LLM and the TTS provider
    gemini_api_key: SecretStr | None = None

    # App
    secret_key: SecretStr = SecretStr("dev-secret-key-change-in-production")
    database_url: str = "sqlite:///data/narrator.db"

    # Basic Auth for admin dashboard
    admin_username: str = ""
    admin_password: SecretStr = SecretStr("")

    def has_basic_auth_configured(self) -> bool:
        """Check if Basic Auth credentials are configured.

        Returns:
            True if both admin_username and admin_password are set.
        """
        return bool(self.admin_username and self.admin_password.get_secret_value())

    # Logging
    log_format: Literal["console", "json"] = "console"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    def get_available_llm_providers(self) -> list[str]:
        """Return the LLM providers that are both implemented and configured.

        Only providers with a working implementation in ``src/providers/llm``
        are reported - listing unimplemented ones would let the Web UI select
        a provider the factory cannot build.

        Returns:
            Provider names usable right now (e.g. ``["groq", "gemini"]``).
        """
        providers: list[str] = []
        if self.groq_api_key:
            providers.append("groq")
        if self.gemini_api_key:
            providers.append("gemini")
        return providers

    def get_available_tts_providers(self) -> list[str]:
        """Return the TTS providers that are both implemented and configured.

        Piper runs locally and is therefore always available; Gemini TTS
        requires ``GEMINI_API_KEY``.

        Returns:
            Provider names usable right now (e.g. ``["piper", "gemini"]``).
        """
        providers: list[str] = ["piper"]  # Always available (local, no API key)
        if self.gemini_api_key:
            providers.append("gemini")
        return providers

    def has_required_llm_provider(self) -> bool:
        """Check if at least one usable LLM provider is configured."""
        return bool(self.get_available_llm_providers())


# Global settings instance (lazy loaded)
_env_settings: EnvSettings | None = None


def get_env_settings() -> EnvSettings:
    """Get the global environment settings instance."""
    global _env_settings
    if _env_settings is None:
        _env_settings = EnvSettings()
        logger.info(
            "env_settings_loaded",
            available_llm_providers=_env_settings.get_available_llm_providers(),
            available_tts_providers=_env_settings.get_available_tts_providers(),
            has_twitch_config=bool(_env_settings.twitch_client_id),
        )
    return _env_settings
