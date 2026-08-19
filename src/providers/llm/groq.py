"""Groq LLM provider implementation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar

import structlog
from groq import APIConnectionError, APIStatusError, AsyncGroq
from pydantic import SecretStr, ValidationError

from src.providers.llm.base import (
    LLMNarrationResponse,
    LLMProvider,
    LLMResponse,
    LLMResponseParseError,
    Model,
    ModerationResult,
)

if TYPE_CHECKING:

    from groq.types.chat import (
        ChatCompletionSystemMessageParam,
        ChatCompletionUserMessageParam,
    )
    from groq.types.chat.completion_create_params import (
        ResponseFormatResponseFormatJsonObject,
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
        ),
        Model(
            id="llama-3.1-8b-instant",
            name="Llama 3.1 8B Instant",
            context_length=128000,
        ),
        Model(
            id="mixtral-8x7b-32768",
            name="Mixtral 8x7B",
            context_length=32768,
        ),
        Model(
            id="gemma2-9b-it",
            name="Gemma 2 9B",
            context_length=8192,
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
        model: str | None = None,
    ) -> LLMResponse:
        """Generate narrator text from user message.

        Args:
            user: Twitch username.
            message: Original message.
            system_prompt: Complete system prompt including JSON format instructions.
            style: Narrator style template name.
            model: Optional model ID override (defaults to provider's configured model).

        Returns:
            LLMResponse with voice_text and subtitle_text.

        Raises:
            LLMResponseParseError: If response is not valid JSON or missing fields.
        """
        effective_model = model or self._model
        user_content = f"[{user}]: {message}"

        if style != "default":
            user_content = f"[Style: {style}] {user_content}"

        # Log prompt metadata at INFO level
        logger.info(
            "llm_prompt",
            user=user,
            model=effective_model,
            style=style,
            message_preview=message[:50] if len(message) > 50 else message,
            system_prompt_length=len(system_prompt),
        )
        # Log full system prompt at DEBUG level
        logger.debug(
            "llm_system_prompt_full",
            system_prompt=system_prompt,
            user_content=user_content,
        )

        try:
            system_msg: ChatCompletionSystemMessageParam = {
                "role": "system",
                "content": system_prompt,
            }
            user_msg: ChatCompletionUserMessageParam = {
                "role": "user",
                "content": user_content,
            }
            json_format: ResponseFormatResponseFormatJsonObject = {"type": "json_object"}
            response = await self._client.chat.completions.create(
                model=effective_model,
                messages=[system_msg, user_msg],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                response_format=json_format,
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

        raw_text = response.choices[0].message.content or ""

        # Log raw response at DEBUG level
        logger.debug(
            "llm_raw_response",
            user=user,
            raw_response=raw_text,
        )

        # Parse and validate JSON response
        try:
            parsed = json.loads(raw_text)
            narration = LLMNarrationResponse.model_validate(parsed)
        except json.JSONDecodeError as e:
            logger.error(
                "llm_json_parse_error",
                user=user,
                raw_response=raw_text,
                error=str(e),
            )
            raise LLMResponseParseError(raw_text, f"Invalid JSON: {e}") from e
        except ValidationError as e:
            logger.error(
                "llm_validation_error",
                user=user,
                raw_response=raw_text,
                error=str(e),
            )
            raise LLMResponseParseError(raw_text, f"Missing required fields: {e}") from e

        voice_text = narration.voice_text.strip()
        subtitle_text = narration.subtitle_text.strip()

        # Log response at INFO level
        logger.info(
            "llm_response",
            user=user,
            model=effective_model,
            voice_text_length=len(voice_text),
            subtitle_text_length=len(subtitle_text),
            voice_text_preview=voice_text[:50] if len(voice_text) > 50 else voice_text,
        )

        return LLMResponse(
            voice_text=voice_text,
            subtitle_text=subtitle_text,
            raw_response=raw_text,
        )

    async def generate_raw(
        self,
        user: str,
        message: str,
        system_prompt: str,
        model: str | None = None,
    ) -> str:
        """Generate raw LLM response without narration validation.

        Used for moderation and other non-narration tasks where the response
        format differs from the standard narration JSON schema.

        Args:
            user: Username for logging context.
            message: The message to send to the LLM.
            system_prompt: Complete system prompt.
            model: Optional model ID override (defaults to provider's configured model).

        Returns:
            Raw response text from LLM.

        Raises:
            APIConnectionError: If connection to Groq fails.
            APIStatusError: If Groq API returns an error status.
        """
        effective_model = model or self._model
        logger.debug(
            "llm_raw_prompt",
            user=user,
            system_prompt_length=len(system_prompt),
            message_length=len(message),
        )

        try:
            system_msg: ChatCompletionSystemMessageParam = {
                "role": "system",
                "content": system_prompt,
            }
            user_msg: ChatCompletionUserMessageParam = {
                "role": "user",
                "content": message,
            }
            json_format: ResponseFormatResponseFormatJsonObject = {"type": "json_object"}
            response = await self._client.chat.completions.create(
                model=effective_model,
                messages=[system_msg, user_msg],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                response_format=json_format,
            )
        except APIConnectionError as e:
            logger.error("groq_raw_connection_error", user=user, error=str(e))
            raise
        except APIStatusError as e:
            logger.error(
                "groq_raw_api_error",
                user=user,
                status_code=e.status_code,
                error=str(e),
            )
            raise

        raw_text = response.choices[0].message.content or ""

        logger.debug(
            "llm_raw_response",
            user=user,
            raw_response=raw_text,
        )

        return raw_text

    async def moderate(
        self,
        user: str,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> ModerationResult:
        """Check a message for Twitch policy compliance.

        Args:
            user: Username who sent the message, for logging context.
            system_prompt: Moderation system prompt.
            user_prompt: User prompt carrying the message under review.
            model: Optional model ID override (defaults to provider's configured model).

        Returns:
            ModerationResult with allowed, reason, category.

        Raises:
            LLMResponseParseError: If response cannot be parsed as ModerationResult.
        """
        effective_model = model or self._model

        logger.debug(
            "llm_moderate_start",
            user=user,
            model=effective_model,
            prompt_length=len(user_prompt),
        )

        raw_response = await self.generate_raw(
            user=user,
            message=user_prompt,
            system_prompt=system_prompt,
            model=effective_model,
        )

        try:
            parsed = json.loads(raw_response)
            result = ModerationResult.model_validate(parsed)
        except json.JSONDecodeError as e:
            logger.error(
                "llm_moderate_json_error",
                user=user,
                raw_response=raw_response,
                error=str(e),
            )
            raise LLMResponseParseError(
                raw_response, f"Invalid moderation JSON: {e}"
            ) from e
        except ValidationError as e:
            logger.error(
                "llm_moderate_validation_error",
                user=user,
                raw_response=raw_response,
                error=str(e),
            )
            raise LLMResponseParseError(
                raw_response, f"Invalid moderation response: {e}"
            ) from e

        logger.info(
            "llm_moderate_complete",
            user=user,
            model=effective_model,
            allowed=result.allowed,
            category=result.category,
        )

        return result

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

    async def close(self) -> None:
        """Close the underlying async HTTP client."""
        await self._client.close()
        logger.debug("groq_client_closed")

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
