"""Tests for building the provider settings forms."""

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from pydantic import SecretStr

from src.config import EnvSettings
from src.db.manager import DatabaseManager
from src.db.repositories.settings import SettingsRepository
from src.models.settings import (
    GeminiLLMSettings,
    GeminiThinkingLevel,
    GroqLLMSettings,
    LLMProviderName,
    PiperSettings,
    ProviderSettings,
    TTSProviderName,
)
from src.providers.catalogue import ApiCatalogue
from src.providers.tts.gemini import GeminiTTSSettings
from src.views.settings import (
    _build_provider_forms,
    _rejected_by_llm_provider,
    _rejected_by_tts_provider,
)

# A combination the settings model accepts field by field but the provider
# refuses: the two thinking controls are mutually exclusive.
UNBUILDABLE_GEMINI = GeminiLLMSettings(
    model="gemini-3.5-flash",
    thinking_level=GeminiThinkingLevel.MINIMAL,
    thinking_budget=0,
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep EnvSettings away from the developer's shell and .env file."""
    monkeypatch.chdir(tmp_path)
    for name in ("GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def offline_catalogues(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about assembling the form, not about the API listing.

    Without this the throwaway providers would each try to reach their API and
    fall back after a timeout.
    """

    async def fallback_only(
        _self: ApiCatalogue[object],
        _fetch: object,
        fallback: list[object],
    ) -> list[object]:
        return list(fallback)

    monkeypatch.setattr(ApiCatalogue, "get", fallback_only)


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> AsyncIterator[SettingsRepository]:
    """A settings repository backed by a throwaway database."""
    db = DatabaseManager(db_path=tmp_path / "data" / "narrator.db")
    await db.initialize()
    try:
        yield SettingsRepository(db.connection)
    finally:
        await db.close()


def _state() -> MagicMock:
    """An app state with both API keys present and no running providers."""
    state = MagicMock()
    state.env = EnvSettings(
        groq_api_key=SecretStr("gsk_test"),
        gemini_api_key=SecretStr("AIza_test"),
    )
    state.llm_provider = None
    state.tts_provider = None
    return state


class TestRejectedBeforeStoring:
    """Settings that cannot build a provider never reach the database."""

    @pytest.mark.asyncio
    async def test_conflicting_thinking_controls_are_reported(self) -> None:
        """A combination the provider refuses is caught before the row is written."""
        problem = await _rejected_by_llm_provider(
            _state(), LLMProviderName.GEMINI, UNBUILDABLE_GEMINI
        )

        assert problem is not None
        assert "not both" in problem

    @pytest.mark.asyncio
    async def test_valid_settings_pass(self) -> None:
        """A workable combination reports no problem."""
        problem = await _rejected_by_llm_provider(
            _state(), LLMProviderName.GEMINI, GeminiLLMSettings()
        )

        assert problem is None

    @pytest.mark.asyncio
    async def test_groq_settings_pass(self) -> None:
        """Groq has no cross-field rules to trip over."""
        assert (
            await _rejected_by_llm_provider(_state(), LLMProviderName.GROQ, GroqLLMSettings())
            is None
        )

    @pytest.mark.asyncio
    async def test_unknown_gemini_voice_is_reported(self) -> None:
        """A voice the provider does not have is caught at save time."""
        problem = await _rejected_by_tts_provider(
            _state(),
            TTSProviderName.GEMINI,
            PiperSettings(),
            GeminiTTSSettings.model_construct(
                model="gemini-2.5-flash-preview-tts",
                voice_name="Gandalf",
                style_prompt="Narrate",
                temperature=1.0,
            ),
        )

        assert problem is not None
        assert "Unknown Gemini TTS voice" in problem

    @pytest.mark.asyncio
    async def test_unconfigured_provider_is_not_checked(self) -> None:
        """Without a key there is nothing to build, and no form to submit."""
        state = _state()
        state.env = EnvSettings(groq_api_key=SecretStr("gsk_test"))

        assert (
            await _rejected_by_llm_provider(state, LLMProviderName.GEMINI, UNBUILDABLE_GEMINI)
            is None
        )


class TestFormsSurviveBadStoredSettings:
    """A stored row the provider rejects must not take the page down."""

    @pytest.mark.asyncio
    async def test_form_still_renders_and_says_why(self, repo: SettingsRepository) -> None:
        """The operator can see the problem and edit their way out of it."""
        await repo.set("gemini_llm", UNBUILDABLE_GEMINI)

        forms, _ = await _build_provider_forms(
            _state(),
            repo,
            ProviderSettings(llm=LLMProviderName.GROQ, tts=TTSProviderName.PIPER),
        )

        gemini = next(form for form in forms if form.key == "gemini_llm")
        assert "not both" in gemini.problem
        # The form is still rendered, showing the offending values, so it can be
        # corrected without editing SQLite by hand.
        assert [field.name for field in gemini.fields]
        model_field = next(field for field in gemini.fields if field.name == "model")
        assert model_field.value == "gemini-3.5-flash"

    @pytest.mark.asyncio
    async def test_healthy_settings_report_no_problem(self, repo: SettingsRepository) -> None:
        """Nothing is flagged when every stored combination builds."""
        forms, piper_voices = await _build_provider_forms(
            _state(),
            repo,
            ProviderSettings(llm=LLMProviderName.GROQ, tts=TTSProviderName.PIPER),
        )

        assert [form.key for form in forms] == ["groq_llm", "gemini_llm", "piper", "gemini_tts"]
        assert all(not form.problem for form in forms)
        assert all(form.fields for form in forms)
        assert piper_voices
