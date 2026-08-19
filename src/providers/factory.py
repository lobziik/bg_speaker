"""Factory that builds LLM and TTS providers from configuration.

Provider *selection* lives in the database (``providers`` settings key, edited
via Settings -> Providers), while provider *credentials* live in environment
variables only. This module is the single place where the two are combined,
so every caller - app startup, the settings view, the CLI - builds providers
the same way and fails the same way when something is missing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from src.models.settings import (
    GeminiLLMSettings,
    GroqLLMSettings,
    LLMProviderName,
    PiperSettings,
    ProviderSettings,
    TTSProviderName,
    TTSVoiceSettings,
)
from src.providers.llm.gemini import GeminiLLMProvider
from src.providers.llm.groq import GroqLLMProvider
from src.providers.tts.gemini import GeminiTTSProvider, GeminiTTSSettings
from src.providers.tts.piper import PiperSettings as PiperProviderSettings
from src.providers.tts.piper import PiperTTSProvider

if TYPE_CHECKING:
    from src.config import EnvSettings
    from src.db.repositories.settings import SettingsRepository
    from src.providers.llm.base import LLMProvider
    from src.providers.tts.base import TTSProvider

logger = structlog.get_logger()


class ProviderConfigurationError(Exception):
    """Raised when the selected provider cannot be built.

    Always carries what is missing and how to fix it - a provider that cannot
    be built must stop startup loudly rather than leave the pipeline half-wired.
    """


def build_llm_provider(
    env: EnvSettings,
    provider: LLMProviderName,
    groq_settings: GroqLLMSettings,
    gemini_settings: GeminiLLMSettings,
) -> LLMProvider:
    """Build the selected LLM provider.

    Args:
        env: Environment settings holding the API keys.
        provider: Which LLM provider to build.
        groq_settings: Model parameters for Groq.
        gemini_settings: Model parameters for Gemini.

    Returns:
        A ready-to-use LLM provider.

    Raises:
        ProviderConfigurationError: If the provider's API key is not configured.
        ValueError: If the provider settings are internally inconsistent
            (e.g. a thinking budget the chosen Gemini model cannot honour).
    """
    match provider:
        case LLMProviderName.GROQ:
            if not env.groq_api_key:
                raise ProviderConfigurationError(
                    "LLM provider 'groq' is selected but GROQ_API_KEY is not set. "
                    "Add it to your .env file or switch providers in Settings -> Providers."
                )
            return GroqLLMProvider(
                api_key=env.groq_api_key,
                model=groq_settings.model,
                temperature=groq_settings.temperature,
                max_tokens=groq_settings.max_tokens,
                reasoning_effort=(
                    groq_settings.reasoning_effort.value
                    if groq_settings.reasoning_effort is not None
                    else None
                ),
            )

        case LLMProviderName.GEMINI:
            if not env.gemini_api_key:
                raise ProviderConfigurationError(
                    "LLM provider 'gemini' is selected but GEMINI_API_KEY is not set. "
                    "Add it to your .env file or switch providers in Settings -> Providers."
                )
            return GeminiLLMProvider(
                api_key=env.gemini_api_key,
                model=gemini_settings.model,
                temperature=gemini_settings.temperature,
                max_output_tokens=gemini_settings.max_output_tokens,
                # str() rather than .value: a row written by an older release
                # can hold a plain string, and the provider validates it anyway.
                thinking_level=(
                    str(gemini_settings.thinking_level)
                    if gemini_settings.thinking_level is not None
                    else None
                ),
                safety_threshold=gemini_settings.safety_threshold.value,
            )


def build_tts_provider(
    env: EnvSettings,
    provider: TTSProviderName,
    piper_settings: PiperSettings,
    piper_voice_settings: TTSVoiceSettings,
    gemini_settings: GeminiTTSSettings,
) -> TTSProvider:
    """Build the selected TTS provider.

    Args:
        env: Environment settings holding the API keys.
        provider: Which TTS provider to build.
        piper_settings: Piper synthesis parameters (speed, variation).
        piper_voice_settings: Per-language Piper voice overrides.
        gemini_settings: Model, voice and style direction for Gemini TTS.

    Returns:
        A TTS provider. The caller is responsible for ``await provider.start()``.

    Raises:
        ProviderConfigurationError: If the provider's API key is not configured.
        ValueError: If a configured voice name is not valid for the provider.
    """
    match provider:
        case TTSProviderName.PIPER:
            return PiperTTSProvider(
                settings=PiperProviderSettings(
                    voice=piper_settings.voice,
                    length_scale=piper_settings.length_scale,
                    noise_scale=piper_settings.noise_scale,
                    noise_w=piper_settings.noise_w,
                ),
                voice_overrides=dict(piper_voice_settings.voice_overrides),
            )

        case TTSProviderName.GEMINI:
            if not env.gemini_api_key:
                raise ProviderConfigurationError(
                    "TTS provider 'gemini' is selected but GEMINI_API_KEY is not set. "
                    "Add it to your .env file or switch providers in Settings -> Providers."
                )
            return GeminiTTSProvider(
                api_key=env.gemini_api_key,
                settings=gemini_settings,
            )


def default_provider_settings(env: EnvSettings) -> ProviderSettings:
    """Pick the provider selection for an installation that has never chosen one.

    Defaults to the first configured LLM provider so a fresh deployment starts
    a working worker instead of failing on a provider whose key was never set.
    TTS defaults to Piper, which needs no credentials.

    Args:
        env: Environment settings holding the API keys.

    Returns:
        A selection to use when nothing is stored yet.
    """
    available_llm = env.get_available_llm_providers()
    llm = LLMProviderName(available_llm[0]) if available_llm else LLMProviderName.GROQ
    return ProviderSettings(llm=llm, tts=TTSProviderName.PIPER)


async def load_provider_settings(
    repo: SettingsRepository,
    env: EnvSettings,
) -> ProviderSettings:
    """Load the active provider selection from the database.

    Args:
        repo: Settings repository bound to the app database.
        env: Environment settings, used to derive first-run defaults.

    Returns:
        Stored selection, or environment-derived defaults on a fresh install.
    """
    stored = await repo.get("providers", ProviderSettings)
    return stored if stored is not None else default_provider_settings(env)


async def build_providers(
    env: EnvSettings,
    repo: SettingsRepository,
    selection: ProviderSettings | None = None,
) -> tuple[LLMProvider, TTSProvider]:
    """Build both providers from a selection and their stored settings.

    Args:
        env: Environment settings holding the API keys.
        repo: Settings repository bound to the app database.
        selection: Providers to build. Defaults to the stored selection; the
            CLI passes an explicit one to test a provider without changing it
            for the running server.

    Returns:
        Tuple of (llm_provider, tts_provider). The caller is responsible for
        ``await tts_provider.start()``.

    Raises:
        ProviderConfigurationError: If a selected provider cannot be built.
    """
    if selection is None:
        selection = await load_provider_settings(repo, env)

    groq_settings = await repo.get_or_default("groq_llm", GroqLLMSettings, GroqLLMSettings())
    gemini_llm_settings = await repo.get_or_default(
        "gemini_llm", GeminiLLMSettings, GeminiLLMSettings()
    )
    piper_settings = await repo.get_or_default("piper", PiperSettings, PiperSettings())
    piper_voice_settings = await repo.get_or_default(
        "tts_voice", TTSVoiceSettings, TTSVoiceSettings()
    )
    gemini_tts_settings = await repo.get_or_default(
        "gemini_tts", GeminiTTSSettings, GeminiTTSSettings()
    )

    llm_provider = build_llm_provider(
        env=env,
        provider=selection.llm,
        groq_settings=groq_settings,
        gemini_settings=gemini_llm_settings,
    )
    # The LLM provider opened an HTTP pool in its constructor. If the TTS
    # provider cannot be built - a missing key, a stale voice name - that pool
    # would leak on every retry from the settings form.
    try:
        tts_provider = build_tts_provider(
            env=env,
            provider=selection.tts,
            piper_settings=piper_settings,
            piper_voice_settings=piper_voice_settings,
            gemini_settings=gemini_tts_settings,
        )
    except BaseException:
        await llm_provider.close()
        raise

    logger.info(
        "providers_built",
        llm_provider=llm_provider.name,
        tts_provider=tts_provider.name,
    )

    return llm_provider, tts_provider
