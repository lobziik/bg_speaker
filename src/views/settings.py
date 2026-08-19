"""Settings view routes."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from google.genai import errors as genai_errors
from pydantic import ValidationError

# TC001 ignored: FastAPI Depends() requires these at runtime for dependency injection
from src.api.dependencies import AppStateDep, SettingsRepoDep, TemplatesDep  # noqa: TC001
from src.models.narration import LanguageCode, NarratorStyle
from src.models.settings import (
    GeminiLLMSettings,
    GeminiSafetyThreshold,
    GroqLLMSettings,
    LanguageSettings,
    LLMProviderName,
    NarratorSettings,
    OverlaySettings,
    PiperSettings,
    ProviderSettings,
    QueueSettings,
    TTSProviderName,
    TTSVoiceSettings,
    TwitchRewardSettings,
)
from src.providers.factory import ProviderConfigurationError, load_provider_settings
from src.providers.llm.gemini import GeminiLLMProvider
from src.providers.llm.groq import GroqLLMProvider
from src.providers.llm.prompts import PromptSettings
from src.providers.tts.gemini import GEMINI_VOICES, GeminiTTSProvider, GeminiTTSSettings
from src.providers.tts.piper import DEFAULT_VOICES, LANGUAGE_DEFAULT_VOICES, PiperTTSProvider

if TYPE_CHECKING:
    from starlette.datastructures import FormData

    from src.api.dependencies import AppState

router = APIRouter()


def _toast_response(message: str, success: bool = True) -> dict[str, Any]:
    """Create HX-Trigger header for toast notification."""
    return {
        "HX-Trigger": json.dumps({
            "showToast": {
                "message": message,
                "type": "success" if success else "error",
            }
        })
    }


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Render settings page."""
    # Load current settings from DB
    language = await settings_repo.get("language", LanguageSettings, LanguageSettings())
    narrator = await settings_repo.get("narrator", NarratorSettings, NarratorSettings())
    queue = await settings_repo.get("queue", QueueSettings, QueueSettings())
    overlay = await settings_repo.get("overlay", OverlaySettings, OverlaySettings())
    reward = await settings_repo.get("reward", TwitchRewardSettings, TwitchRewardSettings())
    tts_voice = await settings_repo.get(
        "tts_voice", TTSVoiceSettings, TTSVoiceSettings()
    )
    piper = await settings_repo.get("piper", PiperSettings, PiperSettings())

    # Provider selection and per-provider settings
    providers = await load_provider_settings(settings_repo, state.env)
    groq_llm = await settings_repo.get_or_default(
        "groq_llm", GroqLLMSettings, GroqLLMSettings()
    )
    gemini_llm = await settings_repo.get_or_default(
        "gemini_llm", GeminiLLMSettings, GeminiLLMSettings()
    )
    gemini_tts = await settings_repo.get_or_default(
        "gemini_tts", GeminiTTSSettings, GeminiTTSSettings()
    )
    prompts = await settings_repo.get_or_default("prompts", PromptSettings, PromptSettings())

    # Get available providers from environment (only configured + implemented)
    available_llm = state.env.get_available_llm_providers()
    available_tts = state.env.get_available_tts_providers()

    # Group voices by language for the UI
    voices_by_lang: dict[str, list[dict[str, str]]] = {}
    for voice in DEFAULT_VOICES:
        if voice.language not in voices_by_lang:
            voices_by_lang[voice.language] = []
        voices_by_lang[voice.language].append({
            "id": voice.id,
            "name": voice.name,
        })

    # Get currently selected voice for each language (user override or default)
    selected_voices: dict[str, str] = {}
    for lang_code in LanguageCode:
        if tts_voice and lang_code in tts_voice.voice_overrides:
            selected_voices[lang_code.value] = tts_voice.voice_overrides[lang_code]
        elif lang_code in LANGUAGE_DEFAULT_VOICES:
            selected_voices[lang_code.value] = LANGUAGE_DEFAULT_VOICES[lang_code]

    return templates.TemplateResponse(
        request,
        "pages/settings.html",
        {
            "active_page": "settings",
            "language": language,
            "narrator": narrator,
            "queue": queue,
            "overlay": overlay,
            "reward": reward,
            "tts_voice": tts_voice,
            "piper": piper,
            "language_codes": list(LanguageCode),
            "narrator_styles": list(NarratorStyle),
            "available_llm": available_llm,
            "available_tts": available_tts,
            "voices_by_lang": voices_by_lang,
            "selected_voices": selected_voices,
            "providers": providers,
            "groq_llm": groq_llm,
            "gemini_llm": gemini_llm,
            "gemini_tts": gemini_tts,
            "prompts": prompts,
            "groq_models": GroqLLMProvider.AVAILABLE_MODELS,
            "gemini_models": GeminiLLMProvider.AVAILABLE_MODELS,
            "gemini_tts_models": GeminiTTSProvider.AVAILABLE_MODELS,
            "gemini_voices": GEMINI_VOICES,
            "safety_thresholds": list(GeminiSafetyThreshold),
            "active_tts_is_piper": providers.tts is TTSProviderName.PIPER,
            "worker_running": state.worker is not None and state.worker.is_running,
        },
    )


@router.post("/settings/language", response_class=HTMLResponse)
async def save_language_settings(
    request: Request,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save language settings."""
    form_data = await request.form()

    settings = LanguageSettings(
        narrator_lang=LanguageCode(str(form_data["narrator_lang"])),
        subtitle_lang=LanguageCode(str(form_data["subtitle_lang"])),
    )

    await settings_repo.set("language", settings)

    return Response(
        content="<div class='toast success'>Language settings saved</div>",
        headers=_toast_response("Language settings saved"),
    )


@router.post("/settings/narrator", response_class=HTMLResponse)
async def save_narrator_settings(
    request: Request,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save narrator settings."""
    form_data = await request.form()

    settings = NarratorSettings(
        default_style=NarratorStyle(str(form_data["default_style"])),
        system_prompt=str(form_data.get("system_prompt", "")),
        bypass_llm=form_data.get("bypass_llm") == "on",
        auto_translate=form_data.get("auto_translate") == "on",
        enable_moderation=form_data.get("enable_moderation") == "on",
    )

    await settings_repo.set("narrator", settings)

    return Response(
        content="<div class='toast success'>Narrator settings saved</div>",
        headers=_toast_response("Narrator settings saved"),
    )


@router.post("/settings/queue", response_class=HTMLResponse)
async def save_queue_settings(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save queue settings."""
    form_data = await request.form()

    priority_users_str = str(form_data.get("priority_users", ""))
    priority_users = [
        u.strip() for u in priority_users_str.split(",") if u.strip()
    ]

    settings = QueueSettings(
        max_size=int(str(form_data["max_size"])),
        message_min_length=int(str(form_data["message_min_length"])),
        message_max_length=int(str(form_data["message_max_length"])),
        cooldown_seconds=int(str(form_data["cooldown_seconds"])),
        tts_rate_limit_seconds=int(str(form_data["tts_rate_limit_seconds"])),
        priority_users=priority_users,
    )

    await settings_repo.set("queue", settings)

    # Update rate limiter with new settings
    await state.rate_limiter.update_settings(
        tts_rate_limit_seconds=float(settings.tts_rate_limit_seconds),
        user_cooldown_seconds=float(settings.cooldown_seconds),
    )

    return Response(
        content="<div class='toast success'>Queue settings saved</div>",
        headers=_toast_response("Queue settings saved"),
    )


@router.post("/settings/overlay", response_class=HTMLResponse)
async def save_overlay_settings(
    request: Request,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save overlay settings."""
    form_data = await request.form()

    position = str(form_data.get("position", "bottom"))
    if position not in ("top", "center", "bottom"):
        position = "bottom"

    settings = OverlaySettings(
        font_family=str(form_data["font_family"]),
        font_size=int(str(form_data["font_size"])),
        text_color=str(form_data["text_color"]),
        background_color=str(form_data["background_color"]),
        animation_duration_ms=int(str(form_data["animation_duration_ms"])),
        position=position,  # type: ignore[arg-type]
    )

    await settings_repo.set("overlay", settings)

    return Response(
        content="<div class='toast success'>Overlay settings saved</div>",
        headers=_toast_response("Overlay settings saved"),
    )


@router.post("/settings/reward", response_class=HTMLResponse)
async def save_reward_settings(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save Twitch reward settings."""
    form_data = await request.form()

    settings = TwitchRewardSettings(
        title=str(form_data["title"]),
        cost=int(str(form_data["cost"])),
        prompt=str(form_data["prompt"]),
        background_color=str(form_data["background_color"]),
        sync_cooldown_with_rate_limit=form_data.get("sync_cooldown_with_rate_limit") == "on",
        refund_on_queue_full=form_data.get("refund_on_queue_full") == "on",
        refund_on_filtered=form_data.get("refund_on_filtered") == "on",
        refund_on_banned_user=form_data.get("refund_on_banned_user") == "on",
        global_cooldown_seconds=int(str(form_data.get("global_cooldown_seconds", 300))),
    )

    await settings_repo.set("reward", settings)

    # Update global cooldown manager with new duration
    if state.global_cooldown:
        await state.global_cooldown.update_cooldown_duration(
            settings.global_cooldown_seconds
        )

    return Response(
        content="<div class='toast success'>Reward settings saved</div>",
        headers=_toast_response("Reward settings saved"),
    )


@router.post("/settings/tts_voice", response_class=HTMLResponse)
async def save_tts_voice_settings(
    request: Request,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save TTS voice settings (per-language voice selection)."""
    form_data = await request.form()

    # Build voice overrides from form data
    # Form fields are named voice_<lang_code> (e.g., voice_en, voice_ru)
    voice_overrides: dict[LanguageCode, str] = {}
    for lang_code in LanguageCode:
        field_name = f"voice_{lang_code.value}"
        voice_value = form_data.get(field_name)
        if voice_value:
            voice_str = str(voice_value)
            # Only store if different from default (to keep overrides minimal)
            default_voice = LANGUAGE_DEFAULT_VOICES.get(lang_code)
            if voice_str != default_voice:
                voice_overrides[lang_code] = voice_str

    settings = TTSVoiceSettings(voice_overrides=voice_overrides)
    await settings_repo.set("tts_voice", settings)

    return Response(
        content="<div class='toast success'>TTS voice settings saved</div>",
        headers=_toast_response("TTS voice settings saved"),
    )


@router.post("/settings/tts_provider", response_class=HTMLResponse)
async def save_tts_provider_settings(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save Piper synthesis settings (speed, variation).

    Persists the values and applies them to the running provider when Piper is
    the active TTS provider. When another provider is active the values are
    still stored - they take effect the next time Piper is selected.
    """
    form_data = await request.form()

    settings = PiperSettings(
        length_scale=float(str(form_data["length_scale"])),
        noise_scale=float(str(form_data["noise_scale"])),
        noise_w=float(str(form_data.get("noise_w", 0.8))),
    )

    await settings_repo.set("piper", settings)

    running_tts = state.pipeline.tts_provider if state.pipeline else None
    if isinstance(running_tts, PiperTTSProvider):
        running_tts.update_settings(
            length_scale=settings.length_scale,
            noise_scale=settings.noise_scale,
            noise_w=settings.noise_w,
        )
        message = "Piper settings saved and applied"
    else:
        message = "Piper settings saved (Piper is not the active TTS provider)"

    return Response(
        content=f"<div class='toast success'>{message}</div>",
        headers=_toast_response(message),
    )


@router.post("/settings/tts_provider/test", response_class=HTMLResponse)
async def test_tts_provider(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Synthesize a test phrase with the active TTS provider.

    For Piper the un-saved form values are applied for the duration of the test
    and then restored, so the sliders can be auditioned before saving. Other
    providers are tested with their currently saved settings.
    """
    form_data = await request.form()

    if not state.pipeline:
        return templates.TemplateResponse(
            request,
            "partials/tts_test_result.html",
            {"success": False, "error": "Pipeline not initialized"},
        )

    tts = state.pipeline.tts_provider
    test_text = "Greetings, adventurer. Your voice settings have been configured."
    restore: tuple[float, float, float] | None = None

    try:
        if isinstance(tts, PiperTTSProvider):
            saved = await _apply_piper_form_settings(tts, form_data)
            restore = saved

        audio_data = await tts.synthesize(test_text)
    except (ValueError, RuntimeError, NotImplementedError, OSError) as e:
        return templates.TemplateResponse(
            request,
            "partials/tts_test_result.html",
            {"success": False, "error": str(e)},
        )
    except genai_errors.APIError as e:
        return templates.TemplateResponse(
            request,
            "partials/tts_test_result.html",
            {"success": False, "error": f"Gemini API error {e.code}: {e.message}"},
        )
    finally:
        if restore is not None and isinstance(tts, PiperTTSProvider):
            tts.update_settings(
                length_scale=restore[0],
                noise_scale=restore[1],
                noise_w=restore[2],
            )

    audio_b64 = base64.b64encode(audio_data).decode()

    return templates.TemplateResponse(
        request,
        "partials/tts_test_result.html",
        {"success": True, "audio_data": audio_b64},
    )


async def _apply_piper_form_settings(
    tts: PiperTTSProvider,
    form_data: FormData,
) -> tuple[float, float, float]:
    """Apply un-saved Piper slider values for a test synthesis.

    Args:
        tts: The running Piper provider.
        form_data: Submitted settings form.

    Returns:
        The previous (length_scale, noise_scale, noise_w) so the caller can restore them.
    """
    previous = tts.current_settings()

    tts.update_settings(
        length_scale=float(str(form_data.get("length_scale", previous[0]))),
        noise_scale=float(str(form_data.get("noise_scale", previous[1]))),
        noise_w=float(str(form_data.get("noise_w", previous[2]))),
    )

    return previous


# === Provider selection ===


@router.post("/settings/providers", response_class=HTMLResponse)
async def save_provider_selection(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Switch the active LLM and TTS providers and rebuild the pipeline."""
    form_data = await request.form()

    settings = ProviderSettings(
        llm=LLMProviderName(str(form_data["llm"])),
        tts=TTSProviderName(str(form_data["tts"])),
    )

    await settings_repo.set("providers", settings)

    return await _rebuild_and_report(
        state,
        success_message=f"Providers switched to {settings.llm.value} + {settings.tts.value}",
    )


@router.post("/settings/providers/groq_llm", response_class=HTMLResponse)
async def save_groq_llm_settings(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save Groq model parameters, rebuilding the pipeline if Groq is active."""
    form_data = await request.form()

    settings = GroqLLMSettings(
        model=str(form_data["model"]),
        temperature=float(str(form_data["temperature"])),
        max_tokens=int(str(form_data["max_tokens"])),
    )

    await settings_repo.set("groq_llm", settings)

    active = await load_provider_settings(settings_repo, state.env)
    if active.llm is not LLMProviderName.GROQ:
        return Response(
            content="<div class='toast success'>Groq settings saved</div>",
            headers=_toast_response("Groq settings saved (Groq is not the active LLM)"),
        )

    return await _rebuild_and_report(state, success_message="Groq settings applied")


@router.post("/settings/providers/gemini_llm", response_class=HTMLResponse)
async def save_gemini_llm_settings(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save Gemini LLM parameters, rebuilding the pipeline if Gemini is active."""
    form_data = await request.form()

    raw_budget = str(form_data.get("thinking_budget", "")).strip()
    thinking_budget = int(raw_budget) if raw_budget else None

    settings = GeminiLLMSettings(
        model=str(form_data["model"]),
        temperature=float(str(form_data["temperature"])),
        max_output_tokens=int(str(form_data["max_output_tokens"])),
        thinking_budget=thinking_budget,
        safety_threshold=GeminiSafetyThreshold(str(form_data["safety_threshold"])),
    )

    await settings_repo.set("gemini_llm", settings)

    active = await load_provider_settings(settings_repo, state.env)
    if active.llm is not LLMProviderName.GEMINI:
        return Response(
            content="<div class='toast success'>Gemini LLM settings saved</div>",
            headers=_toast_response(
                "Gemini LLM settings saved (Gemini is not the active LLM)"
            ),
        )

    return await _rebuild_and_report(state, success_message="Gemini LLM settings applied")


@router.post("/settings/providers/gemini_tts", response_class=HTMLResponse)
async def save_gemini_tts_settings(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save Gemini TTS parameters, rebuilding the pipeline if Gemini TTS is active."""
    form_data = await request.form()

    settings = GeminiTTSSettings(
        model=str(form_data["model"]),
        voice_name=str(form_data["voice_name"]),
        style_prompt=str(form_data.get("style_prompt", "")),
        temperature=float(str(form_data["temperature"])),
    )

    await settings_repo.set("gemini_tts", settings)

    active = await load_provider_settings(settings_repo, state.env)
    if active.tts is not TTSProviderName.GEMINI:
        return Response(
            content="<div class='toast success'>Gemini TTS settings saved</div>",
            headers=_toast_response(
                "Gemini TTS settings saved (Gemini is not the active TTS provider)"
            ),
        )

    return await _rebuild_and_report(state, success_message="Gemini TTS settings applied")


async def _rebuild_and_report(state: AppState, success_message: str) -> Response:
    """Rebuild the pipeline and turn any configuration failure into a toast.

    A bad provider configuration is an operator mistake, not a server fault, so
    it is reported inline instead of surfacing as a 500.

    Args:
        state: Application state holding the pipeline and worker.
        success_message: Toast text to show when the rebuild succeeds.

    Returns:
        HTMX toast response describing the outcome.
    """
    try:
        await state.rebuild_pipeline()
    except (ProviderConfigurationError, ValueError) as e:
        message = f"Provider not applied: {e}"
        return Response(
            content=f"<div class='toast error'>{message}</div>",
            headers=_toast_response(message, success=False),
        )

    return Response(
        content=f"<div class='toast success'>{success_message}</div>",
        headers=_toast_response(success_message),
    )


# === Prompt templates ===


def _prompt_settings_from_form(form_data: FormData) -> PromptSettings:
    """Build PromptSettings from the submitted prompts form.

    Args:
        form_data: Submitted form.

    Returns:
        Validated prompt sections.

    Raises:
        ValidationError: If a section is empty or uses bad placeholders.
    """
    return PromptSettings(
        base_system=str(form_data["base_system"]),
        language_dual=str(form_data["language_dual"]),
        language_single=str(form_data["language_single"]),
        style_default=str(form_data["style_default"]),
        style_whisper=str(form_data["style_whisper"]),
        style_proclaim=str(form_data["style_proclaim"]),
        style_mock=str(form_data["style_mock"]),
        formatting=str(form_data["formatting"]),
        moderation_system=str(form_data["moderation_system"]),
        moderation_user=str(form_data["moderation_user"]),
    )


def _first_validation_message(error: ValidationError) -> str:
    """Turn a ValidationError into one line an operator can act on.

    Args:
        error: The validation failure.

    Returns:
        The first error message, without Pydantic's "Value error, " prefix.
    """
    first = error.errors()[0]
    message = str(first["msg"]).removeprefix("Value error, ")
    field = ".".join(str(part) for part in first["loc"])
    return f"{field}: {message}" if field and field not in message else message


@router.post("/settings/prompts", response_class=HTMLResponse)
async def save_prompt_settings(
    request: Request,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save the editable prompt sections.

    A bad template is an operator mistake, so it comes back as a toast naming
    the offending section instead of a 500. Nothing is stored in that case.
    """
    form_data = await request.form()

    try:
        settings = _prompt_settings_from_form(form_data)
    except ValidationError as e:
        message = f"Prompts not saved - {_first_validation_message(e)}"
        return Response(
            content=f"<div class='toast error'>{message}</div>",
            headers=_toast_response(message, success=False),
        )

    await settings_repo.set("prompts", settings)

    return Response(
        content="<div class='toast success'>Prompts saved</div>",
        headers=_toast_response("Prompts saved - applied from the next narration"),
    )


@router.post("/settings/prompts/reset", response_class=HTMLResponse)
async def reset_prompt_settings(
    request: Request,
    settings_repo: SettingsRepoDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Restore every prompt section to its built-in default.

    Returns the re-rendered form so the textareas show the restored text.
    """
    defaults = PromptSettings()
    await settings_repo.set("prompts", defaults)

    return templates.TemplateResponse(
        request,
        "partials/prompts_form.html",
        {"prompts": defaults},
        headers=_toast_response("Prompts reset to defaults"),
    )
