"""Google Gemini TTS provider implementation."""

from __future__ import annotations

import io
import wave
from typing import TYPE_CHECKING, ClassVar

import structlog
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import SecretStr

from src.core.types import StrictModel
from src.providers.tts.base import TTSProvider, TTSSettings, Voice

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.models.narration import LanguageCode

logger = structlog.get_logger()


# Gemini TTS returns raw little-endian 16-bit PCM. The sample rate is carried
# in the part's mime type (e.g. "audio/L16;codec=pcm;rate=24000") and is parsed
# per response rather than assumed.
PCM_SAMPLE_WIDTH_BYTES = 2
PCM_CHANNELS = 1

# Prebuilt Gemini TTS voices. All of them are multilingual - the spoken
# language follows the text, so the catalogue is not split per language.
# Names are paired with the character Google documents for each voice.
GEMINI_VOICES: list[Voice] = [
    Voice(id="Zephyr", name="Zephyr - Bright", language="multi"),
    Voice(id="Puck", name="Puck - Upbeat", language="multi"),
    Voice(id="Charon", name="Charon - Informative", language="multi"),
    Voice(id="Kore", name="Kore - Firm", language="multi"),
    Voice(id="Fenrir", name="Fenrir - Excitable", language="multi"),
    Voice(id="Leda", name="Leda - Youthful", language="multi"),
    Voice(id="Orus", name="Orus - Firm", language="multi"),
    Voice(id="Aoede", name="Aoede - Breezy", language="multi"),
    Voice(id="Callirrhoe", name="Callirrhoe - Easy-going", language="multi"),
    Voice(id="Autonoe", name="Autonoe - Bright", language="multi"),
    Voice(id="Enceladus", name="Enceladus - Breathy", language="multi"),
    Voice(id="Iapetus", name="Iapetus - Clear", language="multi"),
    Voice(id="Umbriel", name="Umbriel - Easy-going", language="multi"),
    Voice(id="Algieba", name="Algieba - Smooth", language="multi"),
    Voice(id="Despina", name="Despina - Smooth", language="multi"),
    Voice(id="Erinome", name="Erinome - Clear", language="multi"),
    Voice(id="Algenib", name="Algenib - Gravelly", language="multi"),
    Voice(id="Rasalgethi", name="Rasalgethi - Informative", language="multi"),
    Voice(id="Laomedeia", name="Laomedeia - Upbeat", language="multi"),
    Voice(id="Achernar", name="Achernar - Soft", language="multi"),
    Voice(id="Alnilam", name="Alnilam - Firm", language="multi"),
    Voice(id="Schedar", name="Schedar - Even", language="multi"),
    Voice(id="Gacrux", name="Gacrux - Mature", language="multi"),
    Voice(id="Pulcherrima", name="Pulcherrima - Forward", language="multi"),
    Voice(id="Achird", name="Achird - Friendly", language="multi"),
    Voice(id="Zubenelgenubi", name="Zubenelgenubi - Casual", language="multi"),
    Voice(id="Vindemiatrix", name="Vindemiatrix - Gentle", language="multi"),
    Voice(id="Sadachbia", name="Sadachbia - Lively", language="multi"),
    Voice(id="Sadaltager", name="Sadaltager - Knowledgeable", language="multi"),
    Voice(id="Sulafat", name="Sulafat - Warm", language="multi"),
]

GEMINI_VOICE_IDS: frozenset[str] = frozenset(voice.id for voice in GEMINI_VOICES)

# Default style direction. Gemini TTS is steered with natural language rather
# than numeric knobs, so the narrator persona lives here.
DEFAULT_STYLE_PROMPT = (
    "Read the following aloud as a dramatic fantasy narrator - "
    "measured pace, rich intonation, a hint of theatrical relish"
)


class GeminiTTSSettings(StrictModel):
    """Gemini TTS configuration.

    Attributes:
        model: Gemini TTS model ID.
        voice_name: Default prebuilt voice used when nothing more specific applies.
        style_prompt: Natural-language style direction prepended to the text.
            Gemini TTS has no speed/pitch parameters - this is the control surface.
        temperature: Sampling temperature for the audio model.
    """

    model: str = "gemini-2.5-flash-preview-tts"
    voice_name: str = "Charon"
    style_prompt: str = DEFAULT_STYLE_PROMPT
    temperature: float = 1.0


class GeminiTTSProvider:
    """Google Gemini TTS - cloud neural TTS steered by natural language.

    Features:
    - 30 prebuilt multilingual voices, language inferred from the text
    - Style, pace and emotion are directed with a natural-language prompt
    - No local model download, no CPU cost (unlike Piper)
    - Shares GEMINI_API_KEY with the Gemini LLM provider

    Voices: https://ai.google.dev/gemini-api/docs/speech-generation
    """

    AVAILABLE_MODELS: ClassVar[list[str]] = [
        "gemini-2.5-flash-preview-tts",
        "gemini-2.5-pro-preview-tts",
    ]

    def __init__(
        self,
        api_key: SecretStr,
        settings: GeminiTTSSettings | None = None,
        voice_overrides: dict[LanguageCode, str] | None = None,
    ) -> None:
        """Initialize the Gemini TTS provider.

        Args:
            api_key: Google AI Studio API key.
            settings: Model, default voice and style direction.
            voice_overrides: Per-language voice overrides from user settings.

        Raises:
            ValueError: If the configured default voice is not a known prebuilt voice.
        """
        self._api_key = api_key
        self._settings = settings or GeminiTTSSettings()
        self._voice_overrides = voice_overrides or {}

        self._validate_voice(self._settings.voice_name)
        for lang, voice in self._voice_overrides.items():
            self._validate_voice(voice, context=f"override for '{lang.value}'")

        self._client = genai.Client(api_key=api_key.get_secret_value())

    @staticmethod
    def _validate_voice(voice_name: str, context: str = "default voice") -> None:
        """Reject unknown voice names before they reach the API.

        Args:
            voice_name: Prebuilt voice name to check.
            context: Where the name came from, for the error message.

        Raises:
            ValueError: If the voice is not a known Gemini prebuilt voice.
        """
        if voice_name not in GEMINI_VOICE_IDS:
            raise ValueError(
                f"Unknown Gemini TTS voice '{voice_name}' ({context}). "
                f"Valid voices: {sorted(GEMINI_VOICE_IDS)}"
            )

    @property
    def name(self) -> str:
        """Provider display name."""
        return "gemini"

    @property
    def supports_streaming(self) -> bool:
        """Audio is returned as a single blob."""
        return False

    @property
    def supports_cloning(self) -> bool:
        """Gemini TTS exposes prebuilt voices only."""
        return False

    async def start(self) -> None:
        """No-op: the Gemini client needs no warm-up.

        Present so the application lifecycle can treat every TTS provider the same.
        """
        logger.debug("gemini_tts_provider_started", model=self._settings.model)

    async def close(self) -> None:
        """Close the underlying async HTTP client."""
        await self._client.aio.aclose()
        logger.info("gemini_tts_provider_closed")

    def update_settings(
        self,
        *,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
    ) -> None:
        """Not supported - these are Piper synthesis knobs.

        Gemini TTS is steered through ``style_prompt`` instead. Raising keeps a
        misrouted settings save from silently doing nothing.

        Args:
            length_scale: Unsupported.
            noise_scale: Unsupported.
            noise_w: Unsupported.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "Gemini TTS has no length_scale/noise_scale/noise_w parameters. "
            "Adjust the style prompt in Settings -> Providers instead."
        )

    def get_voice_for_language(self, lang: LanguageCode) -> str:
        """Get the voice ID for a given language.

        Gemini voices are multilingual, so the default voice covers every
        language unless the user set an explicit override.

        Args:
            lang: Target language code.

        Returns:
            Gemini prebuilt voice name (e.g. "Charon").
        """
        return self._voice_overrides.get(lang, self._settings.voice_name)

    def _build_prompt(self, text: str) -> str:
        """Combine the style direction with the text to speak.

        Args:
            text: Text to synthesize.

        Returns:
            Prompt for the TTS model.
        """
        style = self._settings.style_prompt.strip()
        if not style:
            return text
        return f"{style}: {text}"

    @staticmethod
    def _parse_sample_rate(mime_type: str) -> int:
        """Extract the PCM sample rate from an audio mime type.

        Args:
            mime_type: Mime type such as "audio/L16;codec=pcm;rate=24000".

        Returns:
            Sample rate in Hz.

        Raises:
            ValueError: If the mime type carries no parsable rate parameter.
        """
        for parameter in mime_type.split(";")[1:]:
            key, _, value = parameter.strip().partition("=")
            if key == "rate":
                try:
                    return int(value)
                except ValueError as e:
                    raise ValueError(
                        f"Gemini TTS returned an unparsable sample rate in mime type '{mime_type}'"
                    ) from e

        raise ValueError(
            f"Gemini TTS response mime type '{mime_type}' carries no rate parameter; "
            f"cannot build a valid WAV header"
        )

    @staticmethod
    def _pcm_to_wav(pcm_data: bytes, sample_rate: int) -> bytes:
        """Wrap raw PCM in a WAV container.

        Args:
            pcm_data: Raw little-endian 16-bit mono PCM samples.
            sample_rate: Sample rate in Hz.

        Returns:
            WAV audio bytes.
        """
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(PCM_CHANNELS)
            wav.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm_data)
        return buffer.getvalue()

    @staticmethod
    def _extract_audio(response: genai_types.GenerateContentResponse) -> tuple[bytes, str]:
        """Pull the audio blob out of a TTS response.

        Args:
            response: Raw SDK response.

        Returns:
            Tuple of (pcm_bytes, mime_type).

        Raises:
            RuntimeError: If the response carries no audio part.
        """
        candidates = response.candidates
        if not candidates:
            raise RuntimeError("Gemini TTS returned no candidates")

        content = candidates[0].content
        if content is None or not content.parts:
            finish_reason = candidates[0].finish_reason
            reason = finish_reason.name if finish_reason else "unknown"
            raise RuntimeError(f"Gemini TTS returned no content parts (finish_reason={reason})")

        for part in content.parts:
            inline_data = part.inline_data
            if inline_data is None or inline_data.data is None:
                continue
            if inline_data.mime_type is None:
                raise RuntimeError(
                    "Gemini TTS returned audio without a mime type; "
                    "cannot determine the sample rate"
                )
            return inline_data.data, inline_data.mime_type

        raise RuntimeError("Gemini TTS response contained no inline audio data")

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
        language: LanguageCode | None = None,
    ) -> bytes:
        """Synthesize text to WAV audio.

        Args:
            text: Text to synthesize.
            voice_id: Explicit prebuilt voice name (takes precedence).
            settings: Generic TTS settings. Gemini exposes no numeric speed or
                pitch controls, so anything other than the defaults is rejected.
            language: Target language, used to pick a per-language voice override.

        Returns:
            WAV audio bytes.

        Raises:
            ValueError: If the voice is unknown or unsupported settings were passed.
            RuntimeError: If the response carries no usable audio.
            genai_errors.APIError: If the Gemini API call fails.
        """
        if settings is not None and settings != TTSSettings():
            raise ValueError(
                "Gemini TTS does not support speed/pitch/stability settings. "
                "Use the style prompt in Settings -> Providers to direct delivery."
            )

        if voice_id:
            selected_voice = voice_id
            self._validate_voice(selected_voice, context="explicit voice_id")
        elif language:
            selected_voice = self.get_voice_for_language(language)
        else:
            selected_voice = self._settings.voice_name

        logger.debug(
            "gemini_tts_synthesis_start",
            text_length=len(text),
            voice=selected_voice,
            model=self._settings.model,
            language=language.value if language else None,
        )

        config = genai_types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            temperature=self._settings.temperature,
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name=selected_voice,
                    ),
                ),
            ),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._settings.model,
                contents=self._build_prompt(text),
                config=config,
            )
        except genai_errors.ClientError as e:
            logger.error(
                "gemini_tts_client_error",
                voice=selected_voice,
                model=self._settings.model,
                code=e.code,
                error=e.message,
            )
            raise
        except genai_errors.ServerError as e:
            logger.error(
                "gemini_tts_server_error",
                voice=selected_voice,
                model=self._settings.model,
                code=e.code,
                error=e.message,
            )
            raise

        pcm_data, mime_type = self._extract_audio(response)
        sample_rate = self._parse_sample_rate(mime_type)
        audio_bytes = self._pcm_to_wav(pcm_data, sample_rate)

        logger.debug(
            "gemini_tts_synthesis_complete",
            text_length=len(text),
            audio_size=len(audio_bytes),
            sample_rate=sample_rate,
            voice=selected_voice,
        )

        return audio_bytes

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str | None = None,
        settings: TTSSettings | None = None,
        language: LanguageCode | None = None,
    ) -> AsyncIterator[bytes]:
        """Streaming not supported - yields the full audio in one chunk.

        Yields:
            The complete WAV audio.
        """
        audio = await self.synthesize(text, voice_id, settings, language)
        yield audio

    async def list_voices(self) -> list[Voice]:
        """List the prebuilt Gemini voices."""
        return GEMINI_VOICES.copy()

    async def clone_voice(
        self,
        name: str,
        audio_files: list[bytes],
    ) -> Voice:
        """Voice cloning is not offered by Gemini TTS.

        Args:
            name: Unused.
            audio_files: Unused.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "Gemini TTS does not support voice cloning. Use ElevenLabs for voice cloning."
        )

    async def health_check(self) -> bool:
        """Check that the configured TTS model is reachable."""
        try:
            await self._client.aio.models.get(model=self._settings.model)
        except genai_errors.APIError as e:
            logger.warning(
                "gemini_tts_health_check_failed",
                model=self._settings.model,
                code=e.code,
                error=e.message,
            )
            return False
        return True

    def get_settings_schema(self) -> dict[str, object]:
        """JSON Schema for UI."""
        return {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "title": "TTS Model",
                    "description": "Gemini speech generation model",
                    "default": "gemini-2.5-flash-preview-tts",
                    "enum": list(self.AVAILABLE_MODELS),
                },
                "voice_name": {
                    "type": "string",
                    "title": "Voice",
                    "description": "Prebuilt Gemini voice (multilingual)",
                    "default": "Charon",
                    "enum": [v.id for v in GEMINI_VOICES],
                },
                "style_prompt": {
                    "type": "string",
                    "title": "Style Direction",
                    "description": (
                        "Natural-language delivery direction prepended to the text - "
                        "Gemini TTS has no numeric speed or pitch controls"
                    ),
                    "default": DEFAULT_STYLE_PROMPT,
                },
                "temperature": {
                    "type": "number",
                    "title": "Temperature",
                    "description": "Sampling temperature for the audio model",
                    "default": 1.0,
                    "minimum": 0.0,
                    "maximum": 2.0,
                },
            },
        }


# Type assertion to verify protocol compliance
def _verify_protocol() -> None:
    """Verify GeminiTTSProvider implements TTSProvider protocol."""
    provider: TTSProvider = GeminiTTSProvider(  # noqa: F841
        api_key=SecretStr("test"),
    )
