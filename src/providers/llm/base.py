"""LLM Provider protocol and base types."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Model:
    """LLM model information."""

    id: str
    name: str
    context_length: int
    supports_streaming: bool


@dataclass(frozen=True)
class LLMResponse:
    """Response from LLM generation."""

    text: str  # Generated text in target format
    raw_response: str  # Full LLM response for debugging


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
        ...

    async def generate_stream(
        self,
        user: str,
        message: str,
        system_prompt: str,
        style: str = "default",
    ) -> AsyncIterator[str]:
        """Streaming generation (optional).

        Yields:
            Text chunks as they're generated
        """
        ...

    async def list_models(self) -> list[Model]:
        """List available models."""
        ...

    async def health_check(self) -> bool:
        """Check if provider is available."""
        ...

    def get_settings_schema(self) -> dict[str, object]:
        """Return JSON Schema for provider-specific settings.

        Used by Web UI to render dynamic settings form.
        """
        ...
