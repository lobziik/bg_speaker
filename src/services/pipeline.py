"""Narration pipeline service - orchestrates LLM → TTS flow."""

import time
import uuid
from dataclasses import dataclass

import structlog

from src.models.narration import LanguageCode, NarrationRequest, NarrationResult
from src.providers.llm.base import LLMProvider
from src.providers.tts.base import TTSProvider

logger = structlog.get_logger()


# Default narrator system prompt
DEFAULT_NARRATOR_PROMPT = """\
You are the narrator from Baldur's Gate 3, speaking in a dramatic, evocative style.

Transform the user's message into narrative prose as if describing events in a D&D campaign.
- Use vivid, atmospheric language
- Keep responses concise (1-3 sentences)
- Refer to the user by their name in third person
- Add dramatic flair without being over-the-top
- Match the tone to the message content

Examples:
- Casual: "[Username] leans back with a knowing smile, eyes glinting with mischief."
- Excited: "With barely contained excitement, [Username] bursts forth!"
- Question: "[Username] furrows their brow, pondering the mysteries before them."

Output ONLY the narrative text, no quotes or additional formatting."""


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
    2. Formats it using LLM (D&D narrator style)
    3. Synthesizes speech using TTS
    4. Returns the result with audio data
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        tts_provider: TTSProvider,
        system_prompt: str | None = None,
    ) -> None:
        """Initialize the pipeline.

        Args:
            llm_provider: LLM provider for text formatting
            tts_provider: TTS provider for audio synthesis
            system_prompt: Custom narrator prompt (optional)
        """
        self._llm = llm_provider
        self._tts = tts_provider
        self._system_prompt = system_prompt or DEFAULT_NARRATOR_PROMPT

    async def process(
        self,
        request: NarrationRequest,
        target_lang: LanguageCode = LanguageCode.EN,
    ) -> tuple[NarrationResult, PipelineMetrics]:
        """Process a narration request through the pipeline.

        Args:
            request: The narration request to process
            target_lang: Target language for TTS

        Returns:
            Tuple of (NarrationResult, PipelineMetrics)
        """
        request_id = str(uuid.uuid4())
        start_time = time.monotonic()

        logger.info(
            "pipeline_start",
            request_id=request_id,
            user=request.user,
            message_length=len(request.message),
            style=request.style,
        )

        # Step 1: LLM formatting
        llm_start = time.monotonic()
        llm_response = await self._llm.generate(
            user=request.user,
            message=request.message,
            system_prompt=self._system_prompt,
            style=request.style,
        )
        llm_latency_ms = int((time.monotonic() - llm_start) * 1000)

        formatted_text = llm_response.text

        logger.debug(
            "pipeline_llm_complete",
            request_id=request_id,
            formatted_length=len(formatted_text),
            latency_ms=llm_latency_ms,
        )

        # Step 2: TTS synthesis
        tts_start = time.monotonic()
        audio_data = await self._tts.synthesize(formatted_text)
        tts_latency_ms = int((time.monotonic() - tts_start) * 1000)

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

        # For Phase 1, translation is not implemented
        # In Phase 2+, we'll add translation step here
        was_translated = False
        text_translated = formatted_text

        result = NarrationResult(
            id=request_id,
            user=request.user,
            text_original=formatted_text,
            text_translated=text_translated,
            target_lang=target_lang,
            audio_data=audio_data,
            duration_ms=duration_ms,
            was_translated=was_translated,
        )

        metrics = PipelineMetrics(
            llm_latency_ms=llm_latency_ms,
            tts_latency_ms=tts_latency_ms,
            total_latency_ms=total_latency_ms,
            text_length=len(formatted_text),
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
        llm_ok = await self._llm.health_check()
        tts_ok = await self._tts.health_check()

        return {
            "llm": llm_ok,
            "tts": tts_ok,
            "pipeline": llm_ok and tts_ok,
        }
