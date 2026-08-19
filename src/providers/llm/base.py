"""LLM Provider protocol and base types."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.core.types import StrictModel


@dataclass(frozen=True)
class Model:
    """LLM model information."""

    id: str
    name: str
    context_length: int


class LLMNarrationResponse(StrictModel):
    """Structured response from LLM narration.

    Contains both the text for TTS synthesis (voice_text) and
    the text for subtitle display (subtitle_text).

    Attributes:
        voice_text: Text in narrator_lang for TTS synthesis.
        subtitle_text: Text in subtitle_lang for overlay display.
    """

    voice_text: str
    subtitle_text: str


class ModerationResult(StrictModel):
    """Result of content moderation check.

    Attributes:
        allowed: Whether the message passes moderation.
        reason: Explanation for the decision (empty if allowed).
        category: Violation category if not allowed (empty if allowed).
    """

    allowed: bool
    reason: str = ""
    category: str = ""


class LLMResponseParseError(Exception):
    """Raised when LLM response cannot be parsed as expected JSON.

    Attributes:
        raw_response: The raw response text that failed to parse.
        parse_error: Description of what went wrong during parsing.
    """

    def __init__(self, raw_response: str, parse_error: str) -> None:
        """Initialize the exception.

        Args:
            raw_response: The raw LLM response that failed to parse.
            parse_error: Description of the parsing failure.
        """
        self.raw_response = raw_response
        self.parse_error = parse_error
        super().__init__(f"Failed to parse LLM response: {parse_error}")


class LLMContentBlockedError(Exception):
    """Raised when the LLM provider's own safety filter blocks a request.

    Distinct from :class:`LLMResponseParseError`: nothing went wrong
    technically, the provider simply refused to process or return content.
    The pipeline maps this onto a moderation rejection so the redemption is
    consumed instead of refunded - a blocked message is a policy problem,
    not an outage.

    Attributes:
        reason: Provider-reported reason (e.g. a finish reason or block reason).
        stage: Which call was blocked ("narration" or "moderation").
    """

    def __init__(self, reason: str, stage: str) -> None:
        """Initialize the exception.

        Args:
            reason: Provider-reported reason for the block.
            stage: Which call was blocked ("narration" or "moderation").
        """
        self.reason = reason
        self.stage = stage
        super().__init__(f"LLM provider blocked the {stage} request: {reason}")


@dataclass(frozen=True)
class LLMResponse:
    """Response from LLM generation.

    Attributes:
        voice_text: Text for TTS synthesis (in narrator_lang).
        subtitle_text: Text for subtitles (in subtitle_lang).
        raw_response: Full LLM response for debugging.
    """

    voice_text: str
    subtitle_text: str
    raw_response: str


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers.

    All LLM providers must implement this interface to be used
    in the narration pipeline.
    """

    @property
    def name(self) -> str:
        """Provider display name."""
        ...

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
        ...

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
        """
        ...

    async def moderate(
        self,
        user: str,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> ModerationResult:
        """Check a message for Twitch policy compliance.

        Both prompts are supplied by the caller because they are operator-editable
        settings, not provider internals.

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
        ...

    async def list_models(self) -> list[Model]:
        """List available models."""
        ...

    async def health_check(self) -> bool:
        """Check if provider is available."""
        ...

    async def close(self) -> None:
        """Release provider resources (HTTP clients, connection pools).

        Called on application shutdown and whenever the active provider is
        swapped at runtime. Implementations must be safe to call multiple times.
        """
        ...

    def get_settings_schema(self) -> dict[str, object]:
        """Return JSON Schema for provider-specific settings.

        Used by Web UI to render dynamic settings form.
        """
        ...
