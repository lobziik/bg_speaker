"""Narration pipeline service - orchestrates LLM → TTS flow."""

import time
import uuid
from dataclasses import dataclass

import structlog

from src.models.narration import LanguageCode, NarrationRequest, NarrationResult
from src.providers.llm.base import LLMProvider
from src.providers.llm.prompts import build_system_prompt
from src.providers.tts.base import TTSProvider

logger = structlog.get_logger()


@dataclass
class PipelineMetrics:
    """Metrics from a pipeline run."""

    llm_latency_ms: int
    tts_latency_ms: int
    total_latency_ms: int
    text_length: int
    audio_size_bytes: int


class NarrationPipeline:
    """Orchestrates the narration pipeline: LLM formatting → TTS synthesis.

    This is the core service that processes narration requests:
    1. Takes user input
    2. Formats it using LLM (D&D narrator style) with JSON response
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

    async def process(
        self,
        request: NarrationRequest,
        *,
        narrator_lang: LanguageCode = LanguageCode.EN,
        subtitle_lang: LanguageCode = LanguageCode.EN,
        auto_translate: bool = False,
        bypass_llm: bool = False,
        voice_id: str | None = None,
    ) -> tuple[NarrationResult, PipelineMetrics]:
        """Process a narration request through the pipeline.

        Args:
            request: The narration request to process.
            narrator_lang: Language for TTS output (voice_text).
            subtitle_lang: Language for subtitles (subtitle_text).
            auto_translate: Enable translation between languages.
            bypass_llm: If True, skip LLM formatting and use raw message.
            voice_id: Explicit TTS voice ID (overrides language-based selection).

        Returns:
            Tuple of (NarrationResult, PipelineMetrics).

        Raises:
            LLMResponseParseError: If LLM returns invalid JSON.
        """
        request_id = str(uuid.uuid4())
        start_time = time.monotonic()

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
                narrator_lang=narrator_lang,
                subtitle_lang=subtitle_lang,
                auto_translate=auto_translate,
                custom_prompt=self._custom_prompt,
                style=request.style,
            )

            logger.debug(
                "pipeline_llm_start",
                request_id=request_id,
                llm_provider=self._llm.name,
            )
            llm_start = time.monotonic()
            # This may raise LLMResponseParseError - fail fast!
            llm_response = await self._llm.generate(
                user=request.user,
                message=request.message,
                system_prompt=system_prompt,
                style=request.style,
            )
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
            llm_latency_ms=llm_latency_ms,
            tts_latency_ms=tts_latency_ms,
            total_latency_ms=total_latency_ms,
            text_length=len(voice_text),
            audio_size_bytes=len(audio_data),
        )

        return result, metrics

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
