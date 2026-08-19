"""Tests for the provider factory."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from src.config import EnvSettings
from src.models.settings import (
    GeminiLLMSettings,
    GroqLLMSettings,
    LLMProviderName,
    PiperSettings,
    TTSProviderName,
    TTSVoiceSettings,
)
from src.providers.factory import (
    ProviderConfigurationError,
    build_llm_provider,
    build_tts_provider,
    default_provider_settings,
)
from src.providers.llm.gemini import GeminiLLMProvider
from src.providers.llm.groq import GroqLLMProvider
from src.providers.tts.gemini import GeminiTTSProvider, GeminiTTSSettings
from src.providers.tts.piper import PiperTTSProvider


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep these tests independent of the developer's shell and .env file.

    EnvSettings reads both, so a locally exported GROQ_API_KEY would otherwise
    make the "missing key" assertions pass or fail by accident. Running from an
    empty directory hides the repository's .env; delenv hides the process env.
    """
    monkeypatch.chdir(tmp_path)
    for name in ("GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _env(groq: str | None = None, gemini: str | None = None) -> EnvSettings:
    """Build EnvSettings with only the API keys under test set.

    Args:
        groq: Value for GROQ_API_KEY, or None to leave it unset.
        gemini: Value for GEMINI_API_KEY, or None to leave it unset.

    Returns:
        Settings instance carrying just the requested keys.
    """
    return EnvSettings(
        groq_api_key=SecretStr(groq) if groq is not None else None,
        gemini_api_key=SecretStr(gemini) if gemini is not None else None,
    )


class TestBuildLLMProvider:
    """LLM provider construction from selection plus environment."""

    def test_builds_groq(self) -> None:
        """Groq is built with the stored model parameters."""
        provider = build_llm_provider(
            env=_env(groq="gsk_test"),
            provider=LLMProviderName.GROQ,
            groq_settings=GroqLLMSettings(model="llama-3.1-8b-instant", temperature=0.4),
            gemini_settings=GeminiLLMSettings(),
        )

        assert isinstance(provider, GroqLLMProvider)
        assert provider._model == "llama-3.1-8b-instant"
        assert provider._temperature == 0.4

    def test_builds_gemini(self) -> None:
        """Gemini is built with the stored model parameters."""
        provider = build_llm_provider(
            env=_env(gemini="AIza_test"),
            provider=LLMProviderName.GEMINI,
            groq_settings=GroqLLMSettings(),
            gemini_settings=GeminiLLMSettings(model="gemini-2.5-flash-lite"),
        )

        assert isinstance(provider, GeminiLLMProvider)
        assert provider._model == "gemini-2.5-flash-lite"

    def test_missing_groq_key(self) -> None:
        """Selecting Groq without a key names the variable and the fix."""
        with pytest.raises(ProviderConfigurationError, match="GROQ_API_KEY"):
            build_llm_provider(
                env=_env(gemini="AIza_test"),
                provider=LLMProviderName.GROQ,
                groq_settings=GroqLLMSettings(),
                gemini_settings=GeminiLLMSettings(),
            )

    def test_missing_gemini_key(self) -> None:
        """Selecting Gemini without a key names the variable and the fix."""
        with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
            build_llm_provider(
                env=_env(groq="gsk_test"),
                provider=LLMProviderName.GEMINI,
                groq_settings=GroqLLMSettings(),
                gemini_settings=GeminiLLMSettings(),
            )

    def test_blank_key_treated_as_missing(self) -> None:
        """An env file line like `GROQ_API_KEY=` must not count as configured."""
        with pytest.raises(ProviderConfigurationError, match="GROQ_API_KEY"):
            build_llm_provider(
                env=_env(groq=""),
                provider=LLMProviderName.GROQ,
                groq_settings=GroqLLMSettings(),
                gemini_settings=GeminiLLMSettings(),
            )


class TestBuildTTSProvider:
    """TTS provider construction from selection plus environment."""

    def test_builds_piper_without_key(self) -> None:
        """Piper is local and needs no credentials."""
        provider = build_tts_provider(
            env=_env(),
            provider=TTSProviderName.PIPER,
            piper_settings=PiperSettings(length_scale=1.3),
            piper_voice_settings=TTSVoiceSettings(),
            gemini_settings=GeminiTTSSettings(),
        )

        assert isinstance(provider, PiperTTSProvider)
        assert provider._settings.length_scale == 1.3

    def test_builds_gemini(self) -> None:
        """Gemini TTS is built with the stored voice."""
        provider = build_tts_provider(
            env=_env(gemini="AIza_test"),
            provider=TTSProviderName.GEMINI,
            piper_settings=PiperSettings(),
            piper_voice_settings=TTSVoiceSettings(),
            gemini_settings=GeminiTTSSettings(voice_name="Sulafat"),
        )

        assert isinstance(provider, GeminiTTSProvider)
        assert provider._settings.voice_name == "Sulafat"

    def test_missing_gemini_key(self) -> None:
        """Selecting Gemini TTS without a key names the variable and the fix."""
        with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
            build_tts_provider(
                env=_env(),
                provider=TTSProviderName.GEMINI,
                piper_settings=PiperSettings(),
                piper_voice_settings=TTSVoiceSettings(),
                gemini_settings=GeminiTTSSettings(),
            )


class TestDefaultProviderSettings:
    """First-run defaults derived from the configured keys."""

    def test_prefers_groq_when_configured(self) -> None:
        """Groq is the historical default and wins when its key is present."""
        defaults = default_provider_settings(_env(groq="gsk_test", gemini="AIza_test"))

        assert defaults.llm is LLMProviderName.GROQ
        assert defaults.tts is TTSProviderName.PIPER

    def test_falls_back_to_gemini(self) -> None:
        """A Gemini-only deployment starts on Gemini instead of a dead worker."""
        defaults = default_provider_settings(_env(gemini="AIza_test"))

        assert defaults.llm is LLMProviderName.GEMINI
        assert defaults.tts is TTSProviderName.PIPER

    def test_no_keys_still_returns_a_selection(self) -> None:
        """With nothing configured the selection is reported, and building it fails."""
        defaults = default_provider_settings(_env())

        assert defaults.llm is LLMProviderName.GROQ


class TestDefaultsMatchCatalogues:
    """A default the provider does not offer cannot be shown or selected."""

    @pytest.mark.asyncio
    async def test_gemini_llm_default_model_is_offered(self) -> None:
        """The settings default must appear in the built-in dropdown.

        The API listing is stubbed out: this is about the catalogue that ships,
        which is what a form falls back to.
        """
        provider = GeminiLLMProvider(api_key=SecretStr("test"))
        provider._fetch_models = AsyncMock(side_effect=ConnectionError("offline"))  # type: ignore[method-assign]
        catalogue = {model.id for model in await provider.list_models()}

        assert GeminiLLMSettings().model in catalogue

    @pytest.mark.asyncio
    async def test_groq_default_model_is_offered(self) -> None:
        """Same for Groq: the form renders options from list_models()."""
        provider = GroqLLMProvider(api_key=SecretStr("test"))
        provider._fetch_models = AsyncMock(side_effect=ConnectionError("offline"))  # type: ignore[method-assign]
        catalogue = {model.id for model in await provider.list_models()}

        assert GroqLLMSettings().model in catalogue

    @pytest.mark.asyncio
    async def test_gemini_tts_default_voice_is_offered(self) -> None:
        """The default voice must be one the provider actually has."""
        provider = GeminiTTSProvider(api_key=SecretStr("test"))
        catalogue = {voice.id for voice in await provider.list_voices()}

        assert GeminiTTSSettings().voice_name in catalogue

    @pytest.mark.asyncio
    async def test_piper_default_voice_is_offered(self) -> None:
        """Piper's fallback voice must be in its catalogue too."""
        provider = PiperTTSProvider()
        catalogue = {voice.id for voice in await provider.list_voices()}

        assert PiperSettings().voice in catalogue

    def test_gemini_defaults_build_a_provider(self) -> None:
        """The shipped defaults must build a provider without arguing."""
        settings = GeminiLLMSettings()

        GeminiLLMProvider(
            api_key=SecretStr("test"),
            model=settings.model,
            thinking_level=(settings.thinking_level.value if settings.thinking_level else None),
        )
