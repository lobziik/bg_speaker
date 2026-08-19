"""Tests for the Gemini LLM provider."""

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import SecretStr

from src.providers.llm.base import (
    LLMContentBlockedError,
    LLMProvider,
    LLMResponseParseError,
    Model,
)
from src.providers.llm.gemini import GeminiLLMProvider


def _response(
    text: str | None = None,
    finish_reason: genai_types.FinishReason | None = None,
    block_reason: genai_types.BlockedReason | None = None,
) -> genai_types.GenerateContentResponse:
    """Build a minimal GenerateContentResponse for extraction tests.

    Args:
        text: Text of the single response part, or None for no parts.
        finish_reason: Candidate finish reason.
        block_reason: Prompt-level block reason.

    Returns:
        A response shaped like the SDK returns.
    """
    parts = [genai_types.Part(text=text)] if text is not None else []
    prompt_feedback = (
        genai_types.GenerateContentResponsePromptFeedback(block_reason=block_reason)
        if block_reason is not None
        else None
    )
    return genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(parts=parts, role="model"),
                finish_reason=finish_reason,
            )
        ],
        prompt_feedback=prompt_feedback,
    )


class TestGeminiLLMProviderConstruction:
    """Construction, validation and protocol compliance."""

    def test_implements_protocol(self, mock_api_key: SecretStr) -> None:
        """Verify GeminiLLMProvider implements LLMProvider protocol."""
        provider = GeminiLLMProvider(api_key=mock_api_key)
        assert isinstance(provider, LLMProvider)

    def test_defaults(self, mock_api_key: SecretStr) -> None:
        """Default model and sampling parameters."""
        provider = GeminiLLMProvider(api_key=mock_api_key)

        assert provider.name == "gemini"
        assert provider._model == "gemini-3.6-flash"
        assert provider._temperature == 0.8
        assert provider._max_output_tokens == 500
        assert provider._thinking_level is genai_types.ThinkingLevel.MINIMAL

    def test_invalid_thinking_level(self, mock_api_key: SecretStr) -> None:
        """An unknown level is rejected rather than passed through."""
        with pytest.raises(ValueError, match="Invalid thinking_level"):
            GeminiLLMProvider(api_key=mock_api_key, thinking_level="TURBO")

    def test_invalid_safety_threshold(self, mock_api_key: SecretStr) -> None:
        """An unknown threshold name is rejected with the valid values listed."""
        with pytest.raises(ValueError, match="Invalid safety_threshold"):
            GeminiLLMProvider(api_key=mock_api_key, safety_threshold="BLOCK_EVERYTHING")

    def test_safety_threshold_parsed(self, mock_api_key: SecretStr) -> None:
        """A valid threshold name becomes the SDK enum."""
        provider = GeminiLLMProvider(api_key=mock_api_key, safety_threshold="BLOCK_NONE")
        assert provider._safety_threshold is genai_types.HarmBlockThreshold.BLOCK_NONE


class TestGeminiLLMConfig:
    """Request configuration assembly."""

    def test_config_includes_schema_and_safety(self, mock_api_key: SecretStr) -> None:
        """JSON mode, schema, thinking budget and safety settings are all wired."""
        provider = GeminiLLMProvider(api_key=mock_api_key)
        schema = genai_types.Schema(type=genai_types.Type.OBJECT)

        config = provider._build_config("system prompt", schema)

        assert config.system_instruction == "system prompt"
        assert config.response_mime_type == "application/json"
        assert config.response_schema is schema
        assert config.thinking_config is not None
        assert config.thinking_config.thinking_level is genai_types.ThinkingLevel.MINIMAL
        assert config.safety_settings is not None
        assert len(config.safety_settings) == 4

    def test_config_omits_thinking_when_unset(self, mock_api_key: SecretStr) -> None:
        """With neither control set the model decides for itself."""
        provider = GeminiLLMProvider(api_key=mock_api_key, thinking_level=None)

        config = provider._build_config("system prompt", None)

        assert config.thinking_config is None


class TestGeminiLLMResponseExtraction:
    """Turning SDK responses into text, or into loud failures."""

    def test_extracts_text(self, mock_api_key: SecretStr) -> None:
        """A normal response yields its text."""
        provider = GeminiLLMProvider(api_key=mock_api_key)
        payload = json.dumps({"voice_text": "a", "subtitle_text": "b"})

        assert provider._extract_text(_response(text=payload), "narration") == payload

    def test_blocked_prompt_raises(self, mock_api_key: SecretStr) -> None:
        """A prompt-level block is reported as a content block, not a parse error."""
        provider = GeminiLLMProvider(api_key=mock_api_key)
        response = _response(block_reason=genai_types.BlockedReason.SAFETY)

        with pytest.raises(LLMContentBlockedError) as exc_info:
            provider._extract_text(response, "narration")

        assert exc_info.value.stage == "narration"
        assert "prompt blocked" in exc_info.value.reason

    def test_blocked_finish_reason_raises(self, mock_api_key: SecretStr) -> None:
        """A safety finish reason is reported as a content block."""
        provider = GeminiLLMProvider(api_key=mock_api_key)
        response = _response(text="", finish_reason=genai_types.FinishReason.SAFETY)

        with pytest.raises(LLMContentBlockedError) as exc_info:
            provider._extract_text(response, "moderation")

        assert exc_info.value.stage == "moderation"
        assert "response blocked" in exc_info.value.reason

    def test_no_candidates_raises_parse_error(self, mock_api_key: SecretStr) -> None:
        """An empty candidate list is a protocol failure, not a content block."""
        provider = GeminiLLMProvider(api_key=mock_api_key)
        response = genai_types.GenerateContentResponse(candidates=[])

        with pytest.raises(LLMResponseParseError, match="no candidates"):
            provider._extract_text(response, "narration")

    def test_empty_text_raises_parse_error(self, mock_api_key: SecretStr) -> None:
        """A truncated response reports the finish reason that explains it."""
        provider = GeminiLLMProvider(api_key=mock_api_key)
        response = _response(text="", finish_reason=genai_types.FinishReason.MAX_TOKENS)

        with pytest.raises(LLMResponseParseError, match="MAX_TOKENS"):
            provider._extract_text(response, "narration")


class TestGeminiLLMMetadata:
    """Model catalogue and settings schema."""

    @pytest.mark.asyncio
    async def test_list_models(self, mock_api_key: SecretStr) -> None:
        """The built-in catalogue contains the default model."""
        provider = GeminiLLMProvider(api_key=mock_api_key)
        provider._fetch_models = AsyncMock(  # type: ignore[method-assign]
            side_effect=ConnectionError("offline")
        )
        models = await provider.list_models()

        assert len(models) > 0
        assert all(isinstance(m, Model) for m in models)
        assert any(m.id == "gemini-3.6-flash" for m in models)

    @pytest.mark.asyncio
    async def test_listing_skips_models_that_cannot_narrate(self) -> None:
        """The same listing carries speech, image and agent models."""
        provider = GeminiLLMProvider(api_key=SecretStr("test"))
        provider._client.aio.models.list = AsyncMock(  # type: ignore[method-assign]
            return_value=_FakePager(
                [
                    genai_types.Model(
                        name="models/gemini-9.9-flash",
                        supported_actions=["generateContent"],
                    ),
                    genai_types.Model(
                        name="models/gemini-9.9-flash-preview-tts",
                        supported_actions=["generateContent"],
                    ),
                    genai_types.Model(
                        name="models/nano-banana-pro",
                        supported_actions=["generateContent"],
                    ),
                    genai_types.Model(
                        name="models/deep-research-preview",
                        supported_actions=["generateContent"],
                    ),
                ]
            )
        )

        assert [m.id for m in await provider.list_models()] == ["gemini-9.9-flash"]

    def test_settings_schema(self, mock_api_key: SecretStr) -> None:
        """The UI schema exposes every constructor knob."""
        provider = GeminiLLMProvider(api_key=mock_api_key)
        schema = provider.get_settings_schema()

        properties = schema["properties"]
        assert isinstance(properties, dict)
        assert set(properties) == {
            "model",
            "temperature",
            "max_output_tokens",
            "thinking_level",
            "safety_threshold",
        }


class _FakePager:
    """Minimal stand-in for the SDK's AsyncPager over listed models."""

    def __init__(self, entries: list[genai_types.Model]) -> None:
        self._entries = entries

    async def __aiter__(self) -> AsyncIterator[genai_types.Model]:
        for entry in self._entries:
            yield entry


class TestGeminiModelListing:
    """The dropdown reflects what this API key can actually use."""

    @staticmethod
    def _provider(entries: list[genai_types.Model] | Exception) -> GeminiLLMProvider:
        """Build a provider whose listing call is stubbed."""
        provider = GeminiLLMProvider(api_key=SecretStr("test"))
        if isinstance(entries, Exception):
            provider._client.aio.models.list = AsyncMock(side_effect=entries)  # type: ignore[method-assign]
        else:
            provider._client.aio.models.list = AsyncMock(  # type: ignore[method-assign]
                return_value=_FakePager(entries)
            )
        return provider

    @pytest.mark.asyncio
    async def test_lists_models_that_can_narrate(self) -> None:
        """Only generateContent models are offered, with the prefix stripped."""
        provider = self._provider(
            [
                genai_types.Model(
                    name="models/gemini-9.9-flash",
                    display_name="Gemini 9.9 Flash",
                    input_token_limit=2_000_000,
                    supported_actions=["generateContent", "countTokens"],
                ),
                genai_types.Model(
                    name="models/text-embedding-004",
                    display_name="Embedding",
                    supported_actions=["embedContent"],
                ),
            ]
        )

        models = await provider.list_models()

        assert [m.id for m in models] == ["gemini-9.9-flash"]
        assert models[0].name == "Gemini 9.9 Flash"
        assert models[0].context_length == 2_000_000

    @pytest.mark.asyncio
    async def test_id_is_used_when_the_api_omits_a_display_name(self) -> None:
        """A dropdown entry always has a label."""
        provider = self._provider(
            [
                genai_types.Model(
                    name="models/gemini-experimental",
                    supported_actions=["generateContent"],
                )
            ]
        )

        assert (await provider.list_models())[0].name == "gemini-experimental"

    @pytest.mark.asyncio
    async def test_falls_back_to_the_builtin_catalogue(self) -> None:
        """An unreachable API leaves the form usable."""
        provider = self._provider(genai_errors.ClientError(401, {"error": {"message": "bad key"}}))

        models = await provider.list_models()

        assert [m.id for m in models] == [m.id for m in GeminiLLMProvider.AVAILABLE_MODELS]
