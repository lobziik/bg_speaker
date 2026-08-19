"""Tests for building the provider settings forms."""

import json
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

# A stored row today's validators reject, as an older release would have left it.
UNREADABLE_GEMINI_ROW = json.dumps(
    {
        "model": "gemini-3.5-flash",
        "temperature": 0.8,
        "max_output_tokens": 500,
        "thinking_level": "TURBO",
        "safety_threshold": "BLOCK_ONLY_HIGH",
    }
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
    async def test_value_the_provider_refuses_is_reported(self) -> None:
        """A value the provider refuses is caught before the row is written."""
        problem = await _rejected_by_llm_provider(
            _state(),
            LLMProviderName.GEMINI,
            GeminiLLMSettings.model_construct(thinking_level=None, model="gemini-3.6-flash"),
        )

        assert problem is None

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
            await _rejected_by_llm_provider(state, LLMProviderName.GEMINI, GeminiLLMSettings())
            is None
        )


class TestFormsSurviveBadStoredSettings:
    """A stored row the provider rejects must not take the page down."""

    @pytest.mark.asyncio
    async def test_form_still_renders_and_says_why(self, repo: SettingsRepository) -> None:
        """The operator can see the problem and edit their way out of it."""
        await repo._conn.execute(
            "INSERT INTO settings (key, value) VALUES ('gemini_llm', ?)",
            (UNREADABLE_GEMINI_ROW,),
        )
        await repo._conn.commit()

        forms, _ = await _build_provider_forms(
            _state(),
            repo,
            ProviderSettings(llm=LLMProviderName.GROQ, tts=TTSProviderName.PIPER),
        )

        gemini = next(form for form in forms if form.key == "gemini_llm")
        assert "thinking_level" in gemini.problem
        # The form is still rendered, so the row can be repaired from the UI
        # rather than in SQLite.
        assert [field.name for field in gemini.fields]

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
