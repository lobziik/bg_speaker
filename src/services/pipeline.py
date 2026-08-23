"""Narration pipeline service - orchestrates LLM → TTS flow."""

import time
import uuid
from dataclasses import dataclass

import structlog

from src.models.narration import LanguageCode, NarrationRequest, NarrationResult
from src.providers.llm.base import (
    LLMContentBlockedError,
    LLMProvider,
    LLMResponseParseError,
)
from src.providers.llm.prompts import (
    PromptSettings,
    build_moderation_prompt,
    build_system_prompt,
)
from src.providers.tts.base import TTSProvider

logger = structlog.get_logger()


# A narration is bounded by what the LLM wrote: narrated speech runs at roughly
# twelve characters per second. A generative TTS provider can leave the text
# behind entirely - Gemini TTS once returned 655 seconds of audio for an
# 84-character line, and the worker dutifully waited it out, stalling the queue
# for eleven minutes. Audio far past the plausible reading time is a runaway, not
# a narration, so it fails here instead of reaching the overlay.
SPEECH_CHARS_PER_SECOND = 12.0

# How far past the plausible reading time audio may run before it counts as a
# runaway. Generous: pauses, slow delivery and short texts all inflate the ratio.
AUDIO_DURATION_TOLERANCE = 3.0

# Floor for the allowance, so a three-word narration is not judged against a
# fraction of a second.
MIN_PLAUSIBLE_AUDIO_SECONDS = 10.0

# Ceiling for the allowance. Nothing the narrator prompt can legitimately produce
# takes two minutes to read aloud.
MAX_AUDIO_DURATION_SECONDS = 120.0


class AudioLengthError(Exception):
    """Raised when synthesized audio is implausibly long for the text.

    Signals a TTS provider that generated rather than narrated. The worker
    treats it like any other pipeline failure: the item fails, the points are
    refunded, and the queue moves on instead of waiting out the audio.

    Attributes:
        voice_text: The text that was handed to the TTS provider.
        duration_ms: Duration of the audio that came back.
        allowed_ms: Longest duration accepted for this text.
    """

    def __init__(self, voice_text: str, duration_ms: int, allowed_ms: int) -> None:
        """Initialize the exception.

        Args:
            voice_text: The text that was handed to the TTS provider.
            duration_ms: Duration of the audio that came back.
            allowed_ms: Longest duration accepted for this text.
        """
        self.voice_text = voice_text
        self.duration_ms = duration_ms
        self.allowed_ms = allowed_ms
        super().__init__(
            f"TTS returned {duration_ms / 1000:.1f}s of audio for {len(voice_text)} "
            f"characters of text, which allows at most {allowed_ms / 1000:.1f}s. "
            f"The provider generated speech instead of narrating the text - check "
            f"that the TTS style prompt directs delivery ('read aloud as...') "
            f"rather than asking the model to produce text ('transform the "
            f"message into...')."
        )


class ModerationRejectedError(Exception):
    """Raised when content moderation rejects a user message.

    This exception signals that:
    1. The message violates Twitch policy
    2. Channel points should be consumed (NOT refunded)
    3. The narration should be skipped entirely

    Attributes:
        message: The original user message that was rejected.
        reason: Human-readable explanation of why it was rejected.
        category: Category of violation (e.g., "hate_speech", "sexual_content").
        latency_ms: Time spent on moderation check in milliseconds.
    """

    def __init__(
        self,
        message: str,
        reason: str,
        category: str = "policy_violation",
        latency_ms: int = 0,
    ) -> None:
        """Initialize the exception.

        Args:
            message: The original user message that was rejected.
            reason: Human-readable explanation of why it was rejected.
            category: Category of violation.
            latency_ms: Time spent on moderation check in milliseconds.
        """
        self.message = message
        self.reason = reason
        self.category = category
        self.latency_ms = latency_ms
        super().__init__(f"Moderation rejected: {reason}")


@dataclass
class PipelineMetrics:
    """Metrics from a pipeline run.

    Attributes:
        moderation_latency_ms: Time spent on content moderation check (0 if disabled).
        llm_latency_ms: Time spent on LLM narration formatting.
        tts_latency_ms: Time spent on TTS synthesis.
        total_latency_ms: Total pipeline execution time.
        text_length: Length of the voice text.
        audio_size_bytes: Size of the audio data in bytes.
    """

    moderation_latency_ms: int
    llm_latency_ms: int
    tts_latency_ms: int
    total_latency_ms: int
    text_length: int
    audio_size_bytes: int


class NarrationPipeline:
    """Orchestrates the narration pipeline: LLM formatting → TTS synthesis.

    This is the core service that processes narration requests:
    1. Takes user input
    2. Formats it using LLM (fantasy narrator style) with JSON response
    3. Synthesizes speech using TTS
    4. Returns the result with audio data
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        tts_provider: TTSProvider,
        custom_prompt: str = "",
    ) -> None:
        """Initialize the pipeline.

        Args:
            llm_provider: LLM provider for text formatting.
            tts_provider: TTS provider for audio synthesis.
            custom_prompt: User's custom narrator behavior prompt.
        """
        self._llm = llm_provider
        self._tts = tts_provider
        self._custom_prompt = custom_prompt

    @property
    def tts_provider(self) -> TTSProvider:
        """Get the TTS provider instance.

        Allows external access for updating provider settings at runtime.
        """
        return self._tts

    @property
    def llm_provider_name(self) -> str:
        """Name of the active LLM provider (for logging and history)."""
        return self._llm.name

    @property
    def tts_provider_name(self) -> str:
        """Name of the active TTS provider (for logging and history)."""
        return self._tts.name

    async def _check_moderation(
        self,
        user: str,
        message: str,
        request_id: str,
        prompts: PromptSettings,
    ) -> int:
        """Check message for Twitch policy compliance.

        Args:
            user: Username who sent the message.
            message: Original message to validate.
            request_id: Request ID for logging.
            prompts: Editable prompt sections from settings.

        Returns:
            Moderation latency in milliseconds.

        Raises:
            ModerationRejectedError: If message violates Twitch policy.
        """
        logger.debug(
            "pipeline_moderation_start",
            request_id=request_id,
            user=user,
            message_length=len(message),
        )

        moderation_start = time.monotonic()

        try:
            result = await self._llm.moderate(
                user=user,
                system_prompt=prompts.moderation_system,
                user_prompt=build_moderation_prompt(prompts, message=message, user=user),
            )
        except LLMContentBlockedError as e:
            # The provider's own safety filter refused the message. Treat it as
            # a policy rejection, not an outage: points are consumed, not refunded.
            blocked_latency_ms = int((time.monotonic() - moderation_start) * 1000)
            logger.warning(
                "pipeline_moderation_provider_blocked",
                request_id=request_id,
                user=user,
                reason=e.reason,
                latency_ms=blocked_latency_ms,
            )
            raise ModerationRejectedError(
                message=message,
                reason=f"Blocked by the LLM provider's safety filter ({e.reason})",
                category="provider_safety_filter",
                latency_ms=blocked_latency_ms,
            ) from e
        except LLMResponseParseError as e:
            # FAIL FAST: If we can't parse moderation response, reject
            parse_error_latency_ms = int((time.monotonic() - moderation_start) * 1000)
            logger.error(
                "pipeline_moderation_parse_error",
                request_id=request_id,
                raw_response=e.raw_response,
                error=e.parse_error,
                latency_ms=parse_error_latency_ms,
            )
            raise ModerationRejectedError(
                message=message,
                reason="Moderation check failed - cannot verify content safety",
                category="parse_error",
                latency_ms=parse_error_latency_ms,
            ) from e

        moderation_latency_ms = int((time.monotonic() - moderation_start) * 1000)

        logger.info(
            "pipeline_moderation_complete",
            request_id=request_id,
            allowed=result.allowed,
            reason=result.reason,
            category=result.category,
            latency_ms=moderation_latency_ms,
        )

        if not result.allowed:
            raise ModerationRejectedError(
                message=message,
                reason=result.reason,
                category=result.category,
                latency_ms=moderation_latency_ms,
            )

        return moderation_latency_ms

    async def process(
        self,
        request: NarrationRequest,
        *,
        narrator_lang: LanguageCode = LanguageCode.EN,
        subtitle_lang: LanguageCode = LanguageCode.EN,
        auto_translate: bool = False,
        bypass_llm: bool = False,
        voice_id: str | None = None,
        enable_moderation: bool = True,
        custom_prompt: str | None = None,
        prompts: PromptSettings | None = None,
    ) -> tuple[NarrationResult, PipelineMetrics]:
        """Process a narration request through the pipeline.

        Args:
            request: The narration request to process.
            narrator_lang: Language for TTS output (voice_text).
            subtitle_lang: Language for subtitles (subtitle_text).
            auto_translate: Enable translation between languages.
            bypass_llm: If True, skip LLM formatting and use raw message.
            voice_id: Explicit TTS voice ID (overrides language-based selection).
            enable_moderation: If True, validate message for Twitch compliance.
            custom_prompt: Narrator behaviour prompt for this run. When None the
                prompt supplied at construction time is used; passing it per
                request lets settings changes take effect without a rebuild.
            prompts: Editable prompt sections from settings. When None the
                built-in defaults are used.

        Returns:
            Tuple of (NarrationResult, PipelineMetrics).

        Raises:
            LLMResponseParseError: If LLM returns invalid JSON.
            ModerationRejectedError: If message violates Twitch policy, or the
                LLM provider's own safety filter blocked it.
        """
        effective_custom_prompt = (
            custom_prompt if custom_prompt is not None else self._custom_prompt
        )
        effective_prompts = prompts if prompts is not None else PromptSettings()
        request_id = str(uuid.uuid4())
        start_time = time.monotonic()
        moderation_latency_ms = 0

        logger.info(
            "pipeline_start",
            request_id=request_id,
            user=request.user,
            message_length=len(request.message),
            style=request.style,
            narrator_lang=narrator_lang.value,
            subtitle_lang=subtitle_lang.value,
            auto_translate=auto_translate,
            bypass_llm=bypass_llm,
            enable_moderation=enable_moderation,
        )

        # Step 0: Content moderation (if enabled)
        if enable_moderation:
            moderation_latency_ms = await self._check_moderation(
                user=request.user,
                message=request.message,
                request_id=request_id,
                prompts=effective_prompts,
            )

        # Step 1: LLM formatting (or bypass)
        if bypass_llm:
            # Skip LLM, use raw message for both voice and subtitle
            voice_text = request.message
            subtitle_text = request.message
            llm_latency_ms = 0
            logger.debug(
                "pipeline_llm_bypassed",
                request_id=request_id,
                text_length=len(voice_text),
            )
        else:
            # Build complete system prompt with language and style instructions
            system_prompt = build_system_prompt(
                effective_prompts,
                narrator_lang=narrator_lang,
                subtitle_lang=subtitle_lang,
                auto_translate=auto_translate,
                custom_prompt=effective_custom_prompt,
                style=request.style,
            )

            logger.debug(
                "pipeline_llm_start",
                request_id=request_id,
                llm_provider=self._llm.name,
            )
            llm_start = time.monotonic()
            try:
                # This may raise LLMResponseParseError - fail fast!
                llm_response = await self._llm.generate(
                    user=request.user,
                    message=request.message,
                    system_prompt=system_prompt,
                    style=request.style,
                )
            except LLMContentBlockedError as e:
                # Same treatment as a moderation rejection: the provider refused
                # the content, so the redemption is consumed rather than refunded.
                logger.warning(
                    "pipeline_llm_provider_blocked",
                    request_id=request_id,
                    user=request.user,
                    reason=e.reason,
                )
                raise ModerationRejectedError(
                    message=request.message,
                    reason=f"Blocked by the LLM provider's safety filter ({e.reason})",
                    category="provider_safety_filter",
                    latency_ms=moderation_latency_ms,
                ) from e
            llm_latency_ms = int((time.monotonic() - llm_start) * 1000)

            voice_text = llm_response.voice_text
            subtitle_text = llm_response.subtitle_text

            logger.debug(
                "pipeline_llm_complete",
                request_id=request_id,
                voice_text_length=len(voice_text),
                subtitle_text_length=len(subtitle_text),
                latency_ms=llm_latency_ms,
            )

        # Step 2: TTS synthesis using voice_text
        logger.debug(
            "pipeline_tts_start",
            request_id=request_id,
            tts_provider=self._tts.name,
            text_length=len(voice_text),
            narrator_lang=narrator_lang.value,
            voice_id=voice_id,
        )
        tts_start = time.monotonic()
        # voice_id takes precedence over language-based selection
        audio_data = await self._tts.synthesize(
            voice_text,
            voice_id=voice_id,
            language=narrator_lang,
        )
        tts_latency_ms = int((time.monotonic() - tts_start) * 1000)

        logger.debug(
            "pipeline_tts_complete",
            request_id=request_id,
            audio_size=len(audio_data),
            latency_ms=tts_latency_ms,
        )

        total_latency_ms = int((time.monotonic() - start_time) * 1000)

        # Calculate audio duration (assuming WAV format)
        duration_ms = self._estimate_audio_duration(audio_data)
        self._reject_runaway_audio(voice_text, duration_ms, request_id)

        logger.info(
            "pipeline_complete",
            request_id=request_id,
            llm_latency_ms=llm_latency_ms,
            tts_latency_ms=tts_latency_ms,
            total_latency_ms=total_latency_ms,
            audio_size=len(audio_data),
            duration_ms=duration_ms,
        )

        result = NarrationResult(
            id=request_id,
            user=request.user,
            voice_text=voice_text,
            subtitle_text=subtitle_text,
            target_lang=narrator_lang,
            audio_data=audio_data,
            duration_ms=duration_ms,
        )

        metrics = PipelineMetrics(
            moderation_latency_ms=moderation_latency_ms,
            llm_latency_ms=llm_latency_ms,
            tts_latency_ms=tts_latency_ms,
            total_latency_ms=total_latency_ms,
            text_length=len(voice_text),
            audio_size_bytes=len(audio_data),
        )

        return result, metrics

    @staticmethod
    def _reject_runaway_audio(voice_text: str, duration_ms: int, request_id: str) -> None:
        """Fail when the audio is far longer than the text could account for.

        Args:
            voice_text: Text that was handed to the TTS provider.
            duration_ms: Duration of the audio that came back.
            request_id: Request ID for logging.

        Raises:
            AudioLengthError: If the audio exceeds the allowance for this text.
        """
        expected_seconds = len(voice_text) / SPEECH_CHARS_PER_SECOND
        allowed_seconds = min(
            max(expected_seconds * AUDIO_DURATION_TOLERANCE, MIN_PLAUSIBLE_AUDIO_SECONDS),
            MAX_AUDIO_DURATION_SECONDS,
        )
        allowed_ms = int(allowed_seconds * 1000)

        if duration_ms <= allowed_ms:
            return

        logger.error(
            "pipeline_audio_runaway",
            request_id=request_id,
            text_length=len(voice_text),
            duration_ms=duration_ms,
            allowed_ms=allowed_ms,
        )
        raise AudioLengthError(
            voice_text=voice_text,
            duration_ms=duration_ms,
            allowed_ms=allowed_ms,
        )

    def _estimate_audio_duration(self, audio_data: bytes) -> int:
        """Estimate audio duration from WAV data.

        Args:
            audio_data: WAV audio bytes

        Returns:
            Duration in milliseconds
        """
        import wave
        from io import BytesIO

        try:
            with wave.open(BytesIO(audio_data), "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
                return int((frames / rate) * 1000)
        except wave.Error:
            # If it's not WAV, estimate based on size
            # Assume 16-bit mono audio at 22050 Hz
            return int((len(audio_data) / 2 / 22050) * 1000)

    async def health_check(self) -> dict[str, bool]:
        """Check health of all pipeline components.

        Returns:
            Dict with component health status
        """
        logger.debug("pipeline_health_check_start")

        llm_ok = await self._llm.health_check()
        tts_ok = await self._tts.health_check()

        health = {
            "llm": llm_ok,
            "tts": tts_ok,
            "pipeline": llm_ok and tts_ok,
        }

        logger.info(
            "pipeline_health_check_complete",
            llm_ok=llm_ok,
            tts_ok=tts_ok,
            pipeline_ok=health["pipeline"],
        )

        return health
