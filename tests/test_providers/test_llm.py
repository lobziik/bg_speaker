"""Tests for LLM providers."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from groq import APIConnectionError
from pydantic import SecretStr

from src.providers.llm.base import LLMProvider, LLMResponse, Model
from src.providers.llm.groq import GroqLLMProvider


class TestLLMProviderProtocol:
    """Test LLM provider protocol compliance."""

    def test_groq_implements_protocol(self, mock_api_key: SecretStr) -> None:
        """Verify GroqLLMProvider implements LLMProvider protocol."""
        provider = GroqLLMProvider(api_key=mock_api_key)
        assert isinstance(provider, LLMProvider)

    def test_model_dataclass(self) -> None:
        """Test Model dataclass."""
        model = Model(
            id="test-model",
            name="Test Model",
            context_length=4096,
        )
        assert model.id == "test-model"
        assert model.name == "Test Model"
        assert model.context_length == 4096

    def test_llm_response_dataclass(self) -> None:
        """Test LLMResponse dataclass."""
        response = LLMResponse(
            voice_text="The hero speaks!",
            subtitle_text="The hero speaks!",
            raw_response='{"voice_text": "The hero speaks!", "subtitle_text": "The hero speaks!"}',
        )
        assert response.voice_text == "The hero speaks!"
        assert response.subtitle_text == "The hero speaks!"
        assert "voice_text" in response.raw_response


class TestGroqLLMProvider:
    """Tests for Groq LLM provider."""

    def test_initialization(self, mock_api_key: SecretStr) -> None:
        """Test provider initialization."""
        provider = GroqLLMProvider(
            api_key=mock_api_key,
            model="llama-3.3-70b-versatile",
            temperature=0.7,
            max_tokens=300,
        )
        assert provider.name == "groq"
        assert provider._model == "llama-3.3-70b-versatile"
        assert provider._temperature == 0.7
        assert provider._max_tokens == 300

    def test_default_values(self, mock_api_key: SecretStr) -> None:
        """Test default initialization values."""
        provider = GroqLLMProvider(api_key=mock_api_key)
        assert provider._model == "llama-3.3-70b-versatile"
        assert provider._temperature == 0.8
        assert provider._max_tokens == 500

    @pytest.mark.asyncio
    async def test_list_models(self, mock_api_key: SecretStr) -> None:
        """The built-in catalogue is used when the API cannot be reached."""
        provider = GroqLLMProvider(api_key=mock_api_key)
        provider._fetch_models = AsyncMock(  # type: ignore[method-assign]
            side_effect=ConnectionError("offline")
        )
        models = await provider.list_models()

        assert len(models) > 0
        assert all(isinstance(m, Model) for m in models)
        assert any(m.id == "llama-3.3-70b-versatile" for m in models)

    def test_settings_schema(self, mock_api_key: SecretStr) -> None:
        """Test settings schema generation."""
        provider = GroqLLMProvider(api_key=mock_api_key)
        schema = provider.get_settings_schema()

        assert schema["type"] == "object"
        assert "properties" in schema
        properties = cast("dict[str, object]", schema["properties"])
        assert "model" in properties
        assert "temperature" in properties
        assert "max_tokens" in properties

    @pytest.mark.asyncio
    async def test_generate_success(
        self,
        mock_api_key: SecretStr,
        sample_user: str,
        sample_message: str,
        sample_system_prompt: str,
    ) -> None:
        """Test successful generation with mocked API."""
        import json

        provider = GroqLLMProvider(api_key=mock_api_key)

        # Mock the Groq client response with JSON format
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {
                "voice_text": "The hero speaks with valor!",
                "subtitle_text": "The hero speaks with valor!",
            }
        )

        with patch.object(
            provider._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await provider.generate(
                user=sample_user,
                message=sample_message,
                system_prompt=sample_system_prompt,
            )

            assert isinstance(result, LLMResponse)
            assert result.voice_text == "The hero speaks with valor!"
            assert result.subtitle_text == "The hero speaks with valor!"
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_with_style(
        self,
        mock_api_key: SecretStr,
        sample_user: str,
        sample_message: str,
        sample_system_prompt: str,
    ) -> None:
        """Test generation with custom style."""
        import json

        provider = GroqLLMProvider(api_key=mock_api_key)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {
                "voice_text": "A whisper in the dark...",
                "subtitle_text": "A whisper in the dark...",
            }
        )

        with patch.object(
            provider._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await provider.generate(
                user=sample_user,
                message=sample_message,
                system_prompt=sample_system_prompt,
                style="whisper",
            )

            assert isinstance(result, LLMResponse)
            # Verify the style was included in the user content
            call_args = mock_create.call_args
            messages = call_args.kwargs["messages"]
            assert "[Style: whisper]" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_health_check_success(self, mock_api_key: SecretStr) -> None:
        """Test successful health check."""
        provider = GroqLLMProvider(api_key=mock_api_key)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]

        with patch.object(
            provider._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await provider.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, mock_api_key: SecretStr) -> None:
        """Test health check failure handling."""
        from groq import APIConnectionError

        provider = GroqLLMProvider(api_key=mock_api_key)

        with patch.object(
            provider._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = APIConnectionError(request=MagicMock())

            result = await provider.health_check()
            assert result is False


class TestGroqModelListing:
    """The dropdown reflects what this API key can actually use."""

    @staticmethod
    def _provider(ids: list[str] | Exception, api_key: SecretStr) -> GroqLLMProvider:
        """Build a provider whose listing call is stubbed."""
        provider = GroqLLMProvider(api_key=api_key)
        if isinstance(ids, Exception):
            provider._client.models.list = AsyncMock(side_effect=ids)  # type: ignore[method-assign]
        else:
            listing = MagicMock()
            listing.data = [MagicMock(id=model_id) for model_id in ids]
            provider._client.models.list = AsyncMock(return_value=listing)  # type: ignore[method-assign]
        return provider

    @pytest.mark.asyncio
    async def test_speech_models_are_filtered_out(self, mock_api_key: SecretStr) -> None:
        """Only chat models can answer a narration request."""
        provider = self._provider(
            [
                "llama-3.3-70b-versatile",
                "whisper-large-v3",
                "playai-tts",
                "llama-guard-4-12b",
            ],
            mock_api_key,
        )

        models = await provider.list_models()

        assert [m.id for m in models] == ["llama-3.3-70b-versatile", "llama-guard-4-12b"]

    @pytest.mark.asyncio
    async def test_models_are_sorted(self, mock_api_key: SecretStr) -> None:
        """A stable order keeps the dropdown from shuffling between renders."""
        provider = self._provider(["zeta-model", "alpha-model"], mock_api_key)

        assert [m.id for m in await provider.list_models()] == ["alpha-model", "zeta-model"]

    @pytest.mark.asyncio
    async def test_falls_back_to_the_builtin_catalogue(self, mock_api_key: SecretStr) -> None:
        """An unreachable API leaves the form usable."""
        provider = self._provider(
            APIConnectionError(request=httpx.Request("GET", "https://api.groq.com")),
            mock_api_key,
        )

        models = await provider.list_models()

        assert [m.id for m in models] == [m.id for m in GroqLLMProvider.AVAILABLE_MODELS]
