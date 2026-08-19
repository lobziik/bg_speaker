"""Google Gemini LLM provider implementation."""

from __future__ import annotations

import json
from typing import ClassVar

import structlog
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import SecretStr, ValidationError

from src.providers.llm.base import (
    LLMContentBlockedError,
    LLMNarrationResponse,
    LLMProvider,
    LLMResponse,
    LLMResponseParseError,
    Model,
    ModerationResult,
)

logger = structlog.get_logger()


# Response schema for narration calls. Declared explicitly rather than derived
# from LLMNarrationResponse: StrictModel emits JSON Schema keywords the Gemini
# API rejects (additionalProperties), so hand-writing keeps the two in sync
# under our control.
NARRATION_RESPONSE_SCHEMA = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        "voice_text": genai_types.Schema(type=genai_types.Type.STRING),
        "subtitle_text": genai_types.Schema(type=genai_types.Type.STRING),
    },
    required=["voice_text", "subtitle_text"],
)

# Response schema for moderation calls, mirroring ModerationResult.
MODERATION_RESPONSE_SCHEMA = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        "allowed": genai_types.Schema(type=genai_types.Type.BOOLEAN),
        "reason": genai_types.Schema(type=genai_types.Type.STRING),
        "category": genai_types.Schema(type=genai_types.Type.STRING),
    },
    required=["allowed", "reason", "category"],
)

# Harm categories the Gemini safety filter can be tuned for.
SAFETY_CATEGORIES: tuple[genai_types.HarmCategory, ...] = (
    genai_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
    genai_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
    genai_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
    genai_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
)

# Finish reasons that mean the model refused rather than completed.
BLOCKING_FINISH_REASONS: frozenset[genai_types.FinishReason] = frozenset(
    {
        genai_types.FinishReason.SAFETY,
        genai_types.FinishReason.PROHIBITED_CONTENT,
        genai_types.FinishReason.BLOCKLIST,
        genai_types.FinishReason.RECITATION,
        genai_types.FinishReason.SPII,
    }
)


class GeminiLLMProvider:
    """Google Gemini LLM provider.

    Features:
    - Native structured output via response schemas (no JSON coaxing needed)
    - Configurable thinking budget - 0 keeps narration latency low
    - Tunable safety threshold, needed because default filters reject a lot of
      ordinary fantasy combat description
    - Shares GEMINI_API_KEY with the Gemini TTS provider

    Recommended model: gemini-2.5-flash
    """

    AVAILABLE_MODELS: ClassVar[list[Model]] = [
        Model(
            id="gemini-2.5-flash",
            name="Gemini 2.5 Flash",
            context_length=1_048_576,
        ),
        Model(
            id="gemini-2.5-flash-lite",
            name="Gemini 2.5 Flash Lite",
            context_length=1_048_576,
        ),
        Model(
            id="gemini-2.5-pro",
            name="Gemini 2.5 Pro",
            context_length=1_048_576,
        ),
        Model(
            id="gemini-2.0-flash",
            name="Gemini 2.0 Flash",
            context_length=1_048_576,
        ),
    ]

    # Models that reject thinking_budget=0 - Pro always reasons, and the 2.0
    # generation has no thinking config at all.
    MODELS_REQUIRING_THINKING: ClassVar[frozenset[str]] = frozenset({"gemini-2.5-pro"})
    MODELS_WITHOUT_THINKING: ClassVar[frozenset[str]] = frozenset({"gemini-2.0-flash"})

    def __init__(
        self,
        api_key: SecretStr,
        model: str = "gemini-2.5-flash",
        temperature: float = 0.8,
        max_output_tokens: int = 500,
        thinking_budget: int | None = 0,
        safety_threshold: str = "BLOCK_ONLY_HIGH",
    ) -> None:
        """Initialize the Gemini provider.

        Args:
            api_key: Google AI Studio API key.
            model: Model ID to use (see AVAILABLE_MODELS).
            temperature: Sampling temperature (0-2).
            max_output_tokens: Maximum tokens to generate per response.
            thinking_budget: Thinking token budget. 0 disables thinking for the
                lowest latency, -1 lets the model decide, None omits the setting
                entirely and uses the model default.
            safety_threshold: Gemini safety filter threshold applied to the
                harassment / hate / sexual / dangerous categories. Defaults to
                BLOCK_ONLY_HIGH so fantasy violence passes while high-severity
                content is still blocked; the bot's own moderation step is the
                Twitch-policy gate.

        Raises:
            ValueError: If thinking_budget is incompatible with the model, or
                safety_threshold is not a valid HarmBlockThreshold value.
        """
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._thinking_budget = thinking_budget
        self._safety_threshold = self._parse_safety_threshold(safety_threshold)

        self._validate_thinking_budget(model, thinking_budget)

        self._client = genai.Client(api_key=api_key.get_secret_value())

    @staticmethod
    def _parse_safety_threshold(threshold: str) -> genai_types.HarmBlockThreshold:
        """Convert a threshold name into the SDK enum.

        Membership is checked explicitly: the SDK's enums accept unknown values
        with only a UserWarning, which would let a typo through as a silently
        wrong safety setting.

        Args:
            threshold: Threshold name (e.g. "BLOCK_ONLY_HIGH").

        Returns:
            The matching HarmBlockThreshold enum member.

        Raises:
            ValueError: If the name is not a valid threshold.
        """
        by_value = {member.value: member for member in genai_types.HarmBlockThreshold}
        if threshold not in by_value:
            raise ValueError(
                f"Invalid safety_threshold '{threshold}'. Valid values: {sorted(by_value)}"
            )
        return by_value[threshold]

    @classmethod
    def _validate_thinking_budget(cls, model: str, thinking_budget: int | None) -> None:
        """Reject thinking budgets the selected model cannot honour.

        Failing here surfaces the misconfiguration at startup instead of as a
        400 on the first redemption.

        Args:
            model: Model ID that will be used.
            thinking_budget: Requested thinking budget (None = model default).

        Raises:
            ValueError: If the budget is incompatible with the model.
        """
        if thinking_budget is None:
            return

        if model in cls.MODELS_WITHOUT_THINKING:
            raise ValueError(
                f"Model '{model}' does not support a thinking budget. "
                f"Set thinking_budget to None for this model."
            )

        if model in cls.MODELS_REQUIRING_THINKING and thinking_budget == 0:
            raise ValueError(
                f"Model '{model}' cannot disable thinking. Use a positive budget "
                f"(e.g. 128), -1 for dynamic thinking, or None for the model default."
            )

    @property
    def name(self) -> str:
        """Provider display name."""
        return "gemini"

    def _build_config(
        self,
        system_prompt: str,
        response_schema: genai_types.Schema | None,
    ) -> genai_types.GenerateContentConfig:
        """Build the per-request generation config.

        Args:
            system_prompt: System instruction for the model.
            response_schema: Structured-output schema, or None for free-form JSON.

        Returns:
            GenerateContentConfig for a generate_content call.
        """
        thinking_config: genai_types.ThinkingConfig | None = None
        if self._thinking_budget is not None:
            thinking_config = genai_types.ThinkingConfig(thinking_budget=self._thinking_budget)

        return genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
            response_mime_type="application/json",
            response_schema=response_schema,
            thinking_config=thinking_config,
            safety_settings=[
                genai_types.SafetySetting(
                    category=category,
                    threshold=self._safety_threshold,
                )
                for category in SAFETY_CATEGORIES
            ],
        )

    @staticmethod
    def _extract_text(response: genai_types.GenerateContentResponse, stage: str) -> str:
        """Pull the response text out, failing loudly on refusals.

        Args:
            response: Raw SDK response.
            stage: Call stage for error messages ("narration" or "moderation").

        Returns:
            The generated text.

        Raises:
            LLMContentBlockedError: If a safety filter blocked prompt or response.
            LLMResponseParseError: If the response carries no usable text.
        """
        prompt_feedback = response.prompt_feedback
        if prompt_feedback is not None and prompt_feedback.block_reason is not None:
            raise LLMContentBlockedError(
                reason=f"prompt blocked: {prompt_feedback.block_reason.name}",
                stage=stage,
            )

        candidates = response.candidates
        if not candidates:
            raise LLMResponseParseError("", "Gemini returned no candidates")

        candidate = candidates[0]
        finish_reason = candidate.finish_reason
        if finish_reason is not None and finish_reason in BLOCKING_FINISH_REASONS:
            raise LLMContentBlockedError(
                reason=f"response blocked: {finish_reason.name}",
                stage=stage,
            )

        text = response.text
        if not text:
            reason = finish_reason.name if finish_reason else "unknown"
            raise LLMResponseParseError("", f"Gemini returned empty text (finish_reason={reason})")

        return text

    async def _generate_json(
        self,
        *,
        user: str,
        contents: str,
        system_prompt: str,
        model: str | None,
        response_schema: genai_types.Schema | None,
        stage: str,
    ) -> str:
        """Run a single JSON-mode generation and return its raw text.

        Args:
            user: Username for logging context.
            contents: User-role content to send.
            system_prompt: System instruction.
            model: Optional model override.
            response_schema: Structured-output schema, or None for free-form JSON.
            stage: Call stage for logging and error messages.

        Returns:
            Raw response text (JSON).

        Raises:
            genai_errors.APIError: If the Gemini API call fails.
            LLMContentBlockedError: If a safety filter blocked the request.
            LLMResponseParseError: If the response carries no usable text.
        """
        effective_model = model or self._model

        try:
            response = await self._client.aio.models.generate_content(
                model=effective_model,
                contents=contents,
                config=self._build_config(system_prompt, response_schema),
            )
        except genai_errors.ClientError as e:
            logger.error(
                "gemini_client_error",
                user=user,
                stage=stage,
                model=effective_model,
                code=e.code,
                error=e.message,
            )
            raise
        except genai_errors.ServerError as e:
            logger.error(
                "gemini_server_error",
                user=user,
                stage=stage,
                model=effective_model,
                code=e.code,
                error=e.message,
            )
            raise

        return self._extract_text(response, stage)

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
            LLMContentBlockedError: If a Gemini safety filter blocked the request.
        """
        effective_model = model or self._model
        user_content = f"[{user}]: {message}"

        if style != "default":
            user_content = f"[Style: {style}] {user_content}"

        logger.info(
            "llm_prompt",
            user=user,
            model=effective_model,
            style=style,
            message_preview=message[:50] if len(message) > 50 else message,
            system_prompt_length=len(system_prompt),
        )
        logger.debug(
            "llm_system_prompt_full",
            system_prompt=system_prompt,
            user_content=user_content,
        )

        raw_text = await self._generate_json(
            user=user,
            contents=user_content,
            system_prompt=system_prompt,
            model=effective_model,
            response_schema=NARRATION_RESPONSE_SCHEMA,
            stage="narration",
        )

        logger.debug("llm_raw_response", user=user, raw_response=raw_text)

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

        Args:
            user: Username for logging context.
            message: The message to send to the LLM.
            system_prompt: Complete system prompt.
            model: Optional model ID override (defaults to provider's configured model).

        Returns:
            Raw response text from Gemini (JSON mode, no schema enforced).

        Raises:
            genai_errors.APIError: If the Gemini API call fails.
            LLMContentBlockedError: If a Gemini safety filter blocked the request.
        """
        logger.debug(
            "llm_raw_prompt",
            user=user,
            system_prompt_length=len(system_prompt),
            message_length=len(message),
        )

        raw_text = await self._generate_json(
            user=user,
            contents=message,
            system_prompt=system_prompt,
            model=model,
            response_schema=None,
            stage="raw",
        )

        logger.debug("llm_raw_response", user=user, raw_response=raw_text)

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
            LLMContentBlockedError: If a Gemini safety filter blocked the request.
        """
        effective_model = model or self._model

        logger.debug(
            "llm_moderate_start",
            user=user,
            model=effective_model,
            prompt_length=len(user_prompt),
        )

        raw_response = await self._generate_json(
            user=user,
            contents=user_prompt,
            system_prompt=system_prompt,
            model=effective_model,
            response_schema=MODERATION_RESPONSE_SCHEMA,
            stage="moderation",
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

    async def list_models(self) -> list[Model]:
        """List available models."""
        return self.AVAILABLE_MODELS.copy()

    async def health_check(self) -> bool:
        """Check if provider is available."""
        try:
            await self._client.aio.models.generate_content(
                model=self._model,
                contents="ping",
                config=genai_types.GenerateContentConfig(max_output_tokens=1),
            )
        except genai_errors.APIError as e:
            logger.warning("gemini_health_check_failed", code=e.code, error=e.message)
            return False
        return True

    async def close(self) -> None:
        """Close the underlying async HTTP client."""
        await self._client.aio.aclose()
        logger.debug("gemini_llm_client_closed")

    def get_settings_schema(self) -> dict[str, object]:
        """JSON Schema for provider settings."""
        return {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "title": "Model",
                    "description": "Gemini model to use",
                    "default": "gemini-2.5-flash",
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
                "max_output_tokens": {
                    "type": "integer",
                    "title": "Max Output Tokens",
                    "description": "Maximum tokens to generate",
                    "default": 500,
                    "minimum": 50,
                    "maximum": 2000,
                },
                "thinking_budget": {
                    "type": ["integer", "null"],
                    "title": "Thinking Budget",
                    "description": (
                        "0 disables thinking (lowest latency, 2.5 Flash/Flash-Lite only), "
                        "-1 lets the model decide, null uses the model default"
                    ),
                    "default": 0,
                    "minimum": -1,
                    "maximum": 24576,
                },
                "safety_threshold": {
                    "type": "string",
                    "title": "Safety Threshold",
                    "description": (
                        "Gemini safety filter strictness. BLOCK_ONLY_HIGH lets fantasy "
                        "violence through while the bot's own moderation enforces Twitch policy"
                    ),
                    "default": "BLOCK_ONLY_HIGH",
                    "enum": [
                        "BLOCK_LOW_AND_ABOVE",
                        "BLOCK_MEDIUM_AND_ABOVE",
                        "BLOCK_ONLY_HIGH",
                        "BLOCK_NONE",
                    ],
                },
            },
        }


# Type assertion to verify protocol compliance
def _verify_protocol() -> None:
    """Verify GeminiLLMProvider implements LLMProvider protocol."""
    provider: LLMProvider = GeminiLLMProvider(  # noqa: F841
        api_key=SecretStr("test"),
    )
