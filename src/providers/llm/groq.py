"""Groq LLM provider implementation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar, Literal

import structlog
from groq import APIConnectionError, APIStatusError, AsyncGroq, omit
from pydantic import SecretStr, ValidationError

from src.providers.catalogue import ApiCatalogue
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

# Values Groq accepts for reasoning_effort.
ReasoningEffort = Literal["none", "default", "low", "medium", "high"]

# Groq reports this when JSON mode came back with something that is not JSON.
# For a reasoning model that is its own thinking leaking into the content, and
# turning the thinking off is the documented cure.
JSON_VALIDATION_FAILED_CODE = "json_validate_failed"
NO_REASONING: ReasoningEffort = "none"

# Memoised API listing; see src/providers/catalogue.py for why it lives here.
MODEL_CATALOGUE: ApiCatalogue[Model] = ApiCatalogue("groq_models")

# Groq's listing mixes chat models with speech and classifier ones, and the
# SDK's Model object carries no modality to filter on - only an id. These
# substrings mark models that cannot return narration text: speech in and out
# (whisper, orpheus) and safety classifiers (prompt-guard, safeguard), which
# answer with a label rather than prose and reject JSON mode outright.
NON_CHAT_MODEL_MARKERS = ("whisper", "-tts", "tts-", "orpheus", "guard")


def _string_keyed(value: object) -> dict[str, object]:
    """Narrow an error body fragment to a dict with string keys.

    The SDK types the body as ``object``, so this makes the shape explicit
    instead of indexing something unknown.

    Args:
        value: Fragment of an API error body.

    Returns:
        The fragment as a string-keyed dict, empty if it is not a mapping.
    """
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


class GroqLLMProvider:
    """Groq LLM provider - fast inference for open models.

    Features:
    - Extremely fast inference (Groq LPU)
    - Supports Llama, Mixtral, and other open models
    - Streaming support
    - Free tier available

    Recommended model: openai/gpt-oss-120b
    """

    # Fallback only: list_models() asks the API. Kept short and current on
    # purpose - the previous hardcoded list named models the API now 404s for.
    AVAILABLE_MODELS: ClassVar[list[Model]] = [
        Model(id="openai/gpt-oss-120b", name="openai/gpt-oss-120b", context_length=0),
        Model(id="openai/gpt-oss-20b", name="openai/gpt-oss-20b", context_length=0),
        Model(id="groq/compound", name="groq/compound", context_length=0),
        Model(id="qwen/qwen3.6-27b", name="qwen/qwen3.6-27b", context_length=0),
    ]

    def __init__(
        self,
        api_key: SecretStr,
        model: str = "openai/gpt-oss-120b",
        temperature: float = 0.8,
        max_tokens: int = 500,
        reasoning_effort: str | None = None,
    ) -> None:
        """Initialize Groq provider.

        Args:
            api_key: Groq API key.
            model: Model ID to use.
            temperature: Sampling temperature (0-2).
            max_tokens: Maximum tokens to generate.
            reasoning_effort: Only meaningful for reasoning models. Left unset
                by default because a model that does not reason rejects the
                parameter; a model that needs it is handled by the retry in
                :meth:`_complete_json`.

        Raises:
            ValueError: If reasoning_effort is not a value Groq accepts.
        """
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._reasoning_effort = self._parse_reasoning_effort(reasoning_effort)
        self._client = AsyncGroq(api_key=api_key.get_secret_value())

    @staticmethod
    def _parse_reasoning_effort(effort: str | None) -> ReasoningEffort | None:
        """Check a reasoning effort against the values Groq accepts.

        Args:
            effort: Effort name, or None to leave the parameter out.

        Returns:
            The validated effort, or None.

        Raises:
            ValueError: If the name is not one Groq accepts.
        """
        if effort is None:
            return None

        valid: tuple[ReasoningEffort, ...] = ("none", "default", "low", "medium", "high")
        if effort not in valid:
            raise ValueError(f"Invalid reasoning_effort '{effort}'. Valid values: {list(valid)}")
        return effort  # type: ignore[return-value]  # narrowed by the membership check above

    async def _complete_json(
        self,
        *,
        user: str,
        model: str,
        system_prompt: str,
        user_content: str,
        stage: str,
    ) -> str:
        """Ask the model for a JSON object, coping with reasoning models.

        A reasoning model refuses JSON mode while its thinking ends up in the
        content, and Groq reports that as json_validate_failed. Retrying once
        with the thinking turned off is what makes such a model usable without
        a hardcoded list of which models reason - that list would rot the same
        way the model catalogue did.

        Args:
            user: Username for logging context.
            model: Model ID to call.
            system_prompt: System message.
            user_content: User message.
            stage: Call stage, for logging.

        Returns:
            Raw response content.

        Raises:
            APIConnectionError: If the API cannot be reached.
            APIStatusError: If the API returns an error status.
        """
        system_msg: ChatCompletionSystemMessageParam = {
            "role": "system",
            "content": system_prompt,
        }
        user_msg: ChatCompletionUserMessageParam = {"role": "user", "content": user_content}
        json_format: ResponseFormatResponseFormatJsonObject = {"type": "json_object"}

        async def call(effort: ReasoningEffort | None) -> str:
            """Make one completion request."""
            response = await self._client.chat.completions.create(
                model=model,
                messages=[system_msg, user_msg],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                response_format=json_format,
                reasoning_effort=effort if effort is not None else omit,
            )
            return response.choices[0].message.content or ""

        try:
            return await call(self._reasoning_effort)
        except APIConnectionError as e:
            logger.error("groq_connection_error", user=user, stage=stage, error=str(e))
            raise
        except APIStatusError as e:
            if self._reasoning_effort is not None or not self._is_json_validation_failure(e):
                logger.error(
                    "groq_api_error",
                    user=user,
                    stage=stage,
                    model=model,
                    status_code=e.status_code,
                    error=str(e),
                )
                raise

            logger.warning(
                "groq_json_retry_without_reasoning",
                user=user,
                stage=stage,
                model=model,
                message="JSON mode failed; retrying with reasoning turned off",
            )

        try:
            return await call(NO_REASONING)
        except APIStatusError as e:
            logger.error(
                "groq_api_error",
                user=user,
                stage=stage,
                model=model,
                status_code=e.status_code,
                error=str(e),
                retried_without_reasoning=True,
            )
            raise

    @staticmethod
    def _is_json_validation_failure(error: APIStatusError) -> bool:
        """Whether Groq rejected the response for not being valid JSON.

        Args:
            error: The API error to inspect.

        Returns:
            True when the error carries Groq's json_validate_failed code.
        """
        detail = _string_keyed(_string_keyed(error.body).get("error"))
        return detail.get("code") == JSON_VALIDATION_FAILED_CODE

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

        raw_text = await self._complete_json(
            user=user,
            model=effective_model,
            system_prompt=system_prompt,
            user_content=user_content,
            stage="narration",
        )

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

        raw_text = await self._complete_json(
            user=user,
            model=effective_model,
            system_prompt=system_prompt,
            user_content=message,
            stage="raw",
        )

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
            raise LLMResponseParseError(raw_response, f"Invalid moderation JSON: {e}") from e
        except ValidationError as e:
            logger.error(
                "llm_moderate_validation_error",
                user=user,
                raw_response=raw_response,
                error=str(e),
            )
            raise LLMResponseParseError(raw_response, f"Invalid moderation response: {e}") from e

        logger.info(
            "llm_moderate_complete",
            user=user,
            model=effective_model,
            allowed=result.allowed,
            category=result.category,
        )

        return result

    @staticmethod
    def _is_chat_model(model_id: str) -> bool:
        """Whether a listed model can answer a chat completion.

        Args:
            model_id: Model ID as returned by the API.

        Returns:
            False for speech-to-text and text-to-speech models.
        """
        lowered = model_id.lower()
        return not any(marker in lowered for marker in NON_CHAT_MODEL_MARKERS)

    async def _fetch_models(self) -> list[Model]:
        """Ask the API which models this key can use.

        Returns:
            Chat models, sorted by ID so the dropdown is stable.

        Raises:
            APIConnectionError: If the API cannot be reached.
            APIStatusError: If the API returns an error status.
        """
        listing = await self._client.models.list()

        # The SDK's Model exposes neither a context window nor a modality, so
        # the length is reported as unknown and the ID doubles as the label.
        return sorted(
            (
                Model(id=entry.id, name=entry.id, context_length=0)
                for entry in listing.data
                if self._is_chat_model(entry.id)
            ),
            key=lambda model: model.id,
        )

    async def list_models(self) -> list[Model]:
        """List the models this API key can actually use.

        Falls back to the built-in catalogue when the API cannot be reached, so
        the settings form still renders without credentials or connectivity.
        """
        return await MODEL_CATALOGUE.get(self._fetch_models, self.AVAILABLE_MODELS)

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
                    "default": "openai/gpt-oss-120b",
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
                "reasoning_effort": {
                    "type": ["string", "null"],
                    "title": "Reasoning Effort",
                    "description": (
                        "Only reasoning models accept this, and a plain model rejects it. "
                        "Leave empty: a model that needs its reasoning turned down for "
                        "JSON mode is retried automatically"
                    ),
                    "default": None,
                    "enum": ["none", "default", "low", "medium", "high"],
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
