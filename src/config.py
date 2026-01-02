"""Configuration loading and management."""

from pathlib import Path

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

    # LLM Providers
    groq_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    # TTS Providers (Piper doesn't need API key)
    elevenlabs_api_key: SecretStr | None = None

    # Translation
    deepl_api_key: SecretStr | None = None

    # App
    secret_key: SecretStr = SecretStr("dev-secret-key-change-in-production")
    database_url: str = "sqlite:///data/narrator.db"

    # Paths
    prompts_path: Path = Path("config/prompts")
    models_path: Path = Path("models")

    def get_available_llm_providers(self) -> list[str]:
        """Return list of configured LLM providers."""
        providers: list[str] = []
        if self.groq_api_key:
            providers.append("groq")
        if self.openai_api_key:
            providers.append("openai")
        if self.anthropic_api_key:
            providers.append("anthropic")
        if self.openrouter_api_key:
            providers.append("openrouter")
        providers.append("ollama")  # Always available (local)
        return providers

    def get_available_tts_providers(self) -> list[str]:
        """Return list of configured TTS providers."""
        providers: list[str] = ["piper"]  # Always available
        if self.elevenlabs_api_key:
            providers.append("elevenlabs")
        return providers

    def has_required_llm_provider(self) -> bool:
        """Check if at least one LLM provider is configured."""
        return bool(
            self.groq_api_key
            or self.openai_api_key
            or self.anthropic_api_key
            or self.openrouter_api_key
        )


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
