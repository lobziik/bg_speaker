"""Groq LLM provider implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import structlog
from groq import APIConnectionError, APIStatusError, AsyncGroq
from pydantic import SecretStr

from src.providers.llm.base import LLMProvider, LLMResponse, Model

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from groq.types.chat import (
        ChatCompletionSystemMessageParam,
        ChatCompletionUserMessageParam,
    )

logger = structlog.get_logger()


class GroqLLMProvider:
    """Groq LLM provider - fast inference for open models.

    Features:
    - Extremely fast inference (Groq LPU)
    - Supports Llama, Mixtral, and other open models
    - Streaming support
    - Free tier available

    Recommended model: llama-3.3-70b-versatile
    """

    # Available models on Groq
    AVAILABLE_MODELS: ClassVar[list[Model]] = [
        Model(
            id="llama-3.3-70b-versatile",
            name="Llama 3.3 70B Versatile",
            context_length=128000,
            supports_streaming=True,
        ),
        Model(
            id="llama-3.1-8b-instant",
            name="Llama 3.1 8B Instant",
            context_length=128000,
            supports_streaming=True,
        ),
        Model(
            id="mixtral-8x7b-32768",
            name="Mixtral 8x7B",
            context_length=32768,
            supports_streaming=True,
        ),
        Model(
            id="gemma2-9b-it",
            name="Gemma 2 9B",
            context_length=8192,
            supports_streaming=True,
        ),
    ]

    def __init__(
        self,
        api_key: SecretStr,
        model: str = "llama-3.3-70b-versatile",
        temperature: float = 0.8,
        max_tokens: int = 500,
    ) -> None:
        """Initialize Groq provider.

        Args:
            api_key: Groq API key
            model: Model ID to use
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
        """
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._client = AsyncGroq(api_key=api_key.get_secret_value())

    @property
    def name(self) -> str:
        """Provider display name."""
        return "groq"

    async def generate(
        self,
        user: str,
        message: str,
        system_prompt: str,
        style: str = "default",
    ) -> LLMResponse:
        """Generate narrator text from user message.

        Args:
            user: Twitch username
            message: Original message
            system_prompt: System prompt for the LLM
            style: Narrator style template name

        Returns:
            LLMResponse with formatted text
        """
        logger.debug(
            "groq_generation_start",
            user=user,
            model=self._model,
            style=style,
            input_length=len(message),
        )

        user_content = f"[{user}]: {message}"

        if style != "default":
            user_content = f"[Style: {style}] {user_content}"

        try:
            system_msg: ChatCompletionSystemMessageParam = {
                "role": "system",
                "content": system_prompt,
            }
            user_msg: ChatCompletionUserMessageParam = {
                "role": "user",
                "content": user_content,
            }
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[system_msg, user_msg],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except APIConnectionError as e:
            logger.error("groq_connection_error", user=user, error=str(e))
            raise
        except APIStatusError as e:
            logger.error(
                "groq_api_error",
                user=user,
                status_code=e.status_code,
                error=str(e),
            )
            raise

        text = response.choices[0].message.content or ""

        logger.debug(
            "groq_generation_complete",
            user=user,
            model=self._model,
            input_length=len(message),
            output_length=len(text),
        )

        return LLMResponse(
            text=text.strip(),
            raw_response=str(response),
        )

    async def generate_stream(
        self,
        user: str,
        message: str,
        system_prompt: str,
        style: str = "default",
    ) -> AsyncIterator[str]:
        """Streaming generation.

        Yields:
            Text chunks as they're generated
        """
        logger.debug(
            "groq_stream_start",
            user=user,
            model=self._model,
            style=style,
            input_length=len(message),
        )

        user_content = f"[{user}]: {message}"

        if style != "default":
            user_content = f"[Style: {style}] {user_content}"

        try:
            system_msg: ChatCompletionSystemMessageParam = {
                "role": "system",
                "content": system_prompt,
            }
            user_msg: ChatCompletionUserMessageParam = {
                "role": "user",
                "content": user_content,
            }
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=[system_msg, user_msg],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stream=True,
            )
        except APIConnectionError as e:
            logger.error("groq_stream_connection_error", user=user, error=str(e))
            raise
        except APIStatusError as e:
            logger.error(
                "groq_stream_api_error",
                user=user,
                status_code=e.status_code,
                error=str(e),
            )
            raise

        chunk_count = 0
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                chunk_count += 1
                yield chunk.choices[0].delta.content

        logger.debug("groq_stream_complete", user=user, chunk_count=chunk_count)

    async def list_models(self) -> list[Model]:
        """List available models."""
        return self.AVAILABLE_MODELS.copy()

    async def health_check(self) -> bool:
        """Check if provider is available."""
        try:
            # Make a minimal request to check connectivity
            ping_msg: ChatCompletionUserMessageParam = {
                "role": "user",
                "content": "ping",
            }
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[ping_msg],
                max_tokens=1,
            )
            return bool(response.choices)
        except (APIConnectionError, APIStatusError) as e:
            logger.warning("groq_health_check_failed", error=str(e))
            return False

    def get_settings_schema(self) -> dict[str, object]:
        """JSON Schema for provider settings."""
        return {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "title": "Model",
                    "description": "Groq model to use",
                    "default": "llama-3.3-70b-versatile",
                    "enum": [m.id for m in self.AVAILABLE_MODELS],
                },
                "temperature": {
                    "type": "number",
                    "title": "Temperature",
                    "description": "Sampling temperature (0=deterministic, 2=creative)",
                    "default": 0.8,
                    "minimum": 0,
                    "maximum": 2,
                },
                "max_tokens": {
                    "type": "integer",
                    "title": "Max Tokens",
                    "description": "Maximum tokens to generate",
                    "default": 500,
                    "minimum": 50,
                    "maximum": 2000,
                },
            },
        }


# Type assertion to verify protocol compliance
def _verify_protocol() -> None:
    """Verify GroqLLMProvider implements LLMProvider protocol."""
    provider: LLMProvider = GroqLLMProvider(  # noqa: F841
        api_key=SecretStr("test"),
    )
