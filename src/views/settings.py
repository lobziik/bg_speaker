"""Settings view routes."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from google.genai import errors as genai_errors
from pydantic import BaseModel, ValidationError

# TC001 ignored: FastAPI Depends() requires these at runtime for dependency injection
from src.api.dependencies import AppStateDep, SettingsRepoDep, TemplatesDep  # noqa: TC001
from src.models.narration import LanguageCode, NarratorStyle
from src.models.settings import (
    GeminiLLMSettings,
    GeminiSafetyThreshold,
    GeminiThinkingLevel,
    GroqLLMSettings,
    GroqReasoningEffort,
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
from src.providers.factory import (
    ProviderConfigurationError,
    build_llm_provider,
    build_providers,
    build_tts_provider,
    load_provider_settings,
)
from src.providers.llm.prompts import PromptSettings
from src.providers.tts.gemini import GeminiTTSSettings
from src.providers.tts.piper import LANGUAGE_DEFAULT_VOICES, PiperTTSProvider
from src.views.schema_form import FieldOption, SettingsField, build_fields

if TYPE_CHECKING:
    from starlette.datastructures import FormData

    from src.api.dependencies import AppState
    from src.core.types import StrictModel
    from src.db.repositories.settings import SettingsRepository
    from src.providers.llm.base import LLMProvider
    from src.providers.tts.base import TTSProvider, Voice

logger = structlog.get_logger()

router = APIRouter()


def _toast_response(message: str, success: bool = True) -> dict[str, Any]:
    """Create HX-Trigger header for toast notification."""
    return {
        "HX-Trigger": json.dumps(
            {
                "showToast": {
                    "message": message,
                    "type": "success" if success else "error",
                }
            }
        )
    }


# Which settings key, label and catalogue field belong to each provider. The
# catalogue field is the one whose options come from the provider's own
# list_models() / list_voices(), rather than from the schema's enum.
LLM_FORM_SPECS: tuple[tuple[LLMProviderName, str, str, type[StrictModel], str], ...] = (
    (LLMProviderName.GROQ, "groq_llm", "Groq", GroqLLMSettings, "model"),
    (LLMProviderName.GEMINI, "gemini_llm", "Gemini", GeminiLLMSettings, "model"),
)

TTS_FORM_SPECS: tuple[tuple[TTSProviderName, str, str, type[StrictModel], str], ...] = (
    (TTSProviderName.PIPER, "piper", "Piper", PiperSettings, "voice"),
    (TTSProviderName.GEMINI, "gemini_tts", "Gemini", GeminiTTSSettings, "voice_name"),
)


@dataclass(frozen=True)
class ProviderForm:
    """One provider's settings form, rendered from its own schema.

    Attributes:
        key: Settings key the form posts to.
        provider: Provider name ("groq", "gemini", "piper").
        kind: "LLM" or "TTS", for grouping in the UI.
        title: Section heading.
        active: Whether this provider is the one currently in use.
        configured: Whether its credentials are present, so it can be built.
        fields: Controls parsed from the provider's settings schema.
        supports_voice_test: Whether the section offers a voice preview button.
        problem: Why the form could not be built, if the stored settings are
            rejected by the provider itself.
    """

    key: str
    provider: str
    kind: str
    title: str
    active: bool
    configured: bool
    fields: list[SettingsField]
    supports_voice_test: bool
    problem: str = ""


async def _stored_or_default[T: BaseModel](
    repo: SettingsRepository,
    key: str,
    model: type[T],
    default: T,
) -> tuple[T, str]:
    """Load stored settings, surviving a row that no longer validates.

    A row written by an older release can fail today's validators. Raising here
    would take down the settings page, which is the only place the operator can
    repair it, so the defaults are used and the reason is handed back for the
    form to show.

    Args:
        repo: Settings repository.
        key: Settings key to read.
        model: Model to validate against.
        default: Value to use when the row is missing or unreadable.

    Returns:
        Tuple of (settings, problem). The problem is empty when the row loaded.
    """
    try:
        return await repo.get_or_default(key, model, default), ""
    except ValidationError as e:
        logger.warning("provider_settings_unreadable", key=key, error=str(e))
        return default, _first_validation_message(e)


async def _llm_catalogue(provider: LLMProvider, field: str) -> dict[str, list[FieldOption]]:
    """Fetch the provider's model catalogue as select options.

    Args:
        provider: Provider instance to ask.
        field: Schema field the models belong to.

    Returns:
        Options keyed by field name, for build_fields().
    """
    models = await provider.list_models()
    return {field: [FieldOption(value=model.id, label=model.name) for model in models]}


async def _tts_catalogue(provider: TTSProvider, field: str) -> dict[str, list[FieldOption]]:
    """Fetch the provider's voice catalogue as select options.

    Args:
        provider: Provider instance to ask.
        field: Schema field the voices belong to.

    Returns:
        Options keyed by field name, for build_fields().
    """
    voices = await provider.list_voices()
    return {field: [FieldOption(value=voice.id, label=voice.name) for voice in voices]}


async def _build_provider_forms(
    state: AppState,
    settings_repo: SettingsRepository,
    selection: ProviderSettings,
) -> tuple[list[ProviderForm], list[Voice]]:
    """Build a settings form for every implemented provider.

    Each form comes from the provider's own ``get_settings_schema()``, with the
    model or voice dropdown filled from ``list_models()`` / ``list_voices()``.
    Providers that are not the active one are instantiated briefly and closed
    again; constructing them performs no network I/O.

    Args:
        state: Application state, for the environment and running providers.
        settings_repo: Repository holding the stored per-provider settings.
        selection: The currently active provider selection.

    Returns:
        Tuple of (forms, piper voice catalogue). The voice catalogue is reused
        by the per-language override tab.
    """
    available_llm = state.env.get_available_llm_providers()
    available_tts = state.env.get_available_tts_providers()

    groq_settings, groq_problem = await _stored_or_default(
        settings_repo, "groq_llm", GroqLLMSettings, GroqLLMSettings()
    )
    gemini_llm_settings, gemini_llm_problem = await _stored_or_default(
        settings_repo, "gemini_llm", GeminiLLMSettings, GeminiLLMSettings()
    )
    piper_settings, piper_problem = await _stored_or_default(
        settings_repo, "piper", PiperSettings, PiperSettings()
    )
    gemini_tts_settings, gemini_tts_problem = await _stored_or_default(
        settings_repo, "gemini_tts", GeminiTTSSettings, GeminiTTSSettings()
    )
    voice_overrides = await settings_repo.get_or_default(
        "tts_voice", TTSVoiceSettings, TTSVoiceSettings()
    )

    stored_problems = {
        "groq_llm": groq_problem,
        "gemini_llm": gemini_llm_problem,
        "piper": piper_problem,
        "gemini_tts": gemini_tts_problem,
    }

    stored: dict[str, StrictModel] = {
        "groq_llm": groq_settings,
        "gemini_llm": gemini_llm_settings,
        "piper": piper_settings,
        "gemini_tts": gemini_tts_settings,
    }

    forms: list[ProviderForm] = []
    piper_voices: list[Voice] = []

    for llm_name, key, label, _model, catalogue_field in LLM_FORM_SPECS:
        configured = llm_name.value in available_llm
        active = selection.llm is llm_name
        llm_fields: list[SettingsField] = []
        problem = stored_problems[key]

        if configured:
            try:
                llm_provider = (
                    state.llm_provider
                    if active and state.llm_provider is not None
                    else build_llm_provider(
                        env=state.env,
                        provider=llm_name,
                        groq_settings=groq_settings,
                        gemini_settings=gemini_llm_settings,
                    )
                )
            except (ValueError, ProviderConfigurationError) as e:
                # A stored combination the provider rejects - written by an older
                # release, or edited into the database by hand - must not take the
                # settings page down, because the page is the only place the
                # operator can correct it. Defaults always build, and only the
                # schema and catalogue come from this instance; the controls below
                # still show the stored values.
                logger.warning("provider_form_fallback", provider=llm_name.value, error=str(e))
                problem = str(e)
                llm_provider = build_llm_provider(
                    env=state.env,
                    provider=llm_name,
                    groq_settings=GroqLLMSettings(),
                    gemini_settings=GeminiLLMSettings(),
                )

            try:
                llm_fields = build_fields(
                    llm_provider.get_settings_schema(),
                    stored[key].model_dump(),
                    await _llm_catalogue(llm_provider, catalogue_field),
                )
            finally:
                if llm_provider is not state.llm_provider:
                    await llm_provider.close()

        forms.append(
            ProviderForm(
                key=key,
                provider=llm_name.value,
                kind="LLM",
                title=f"{label} (LLM)",
                active=active,
                configured=configured,
                fields=llm_fields,
                supports_voice_test=False,
                problem=problem,
            )
        )

    for tts_name, key, label, _model, catalogue_field in TTS_FORM_SPECS:
        configured = tts_name.value in available_tts
        active = selection.tts is tts_name
        tts_fields: list[SettingsField] = []
        problem = stored_problems[key]

        if configured:
            try:
                tts_provider = (
                    state.tts_provider
                    if active and state.tts_provider is not None
                    else build_tts_provider(
                        env=state.env,
                        provider=tts_name,
                        piper_settings=piper_settings,
                        piper_voice_settings=voice_overrides,
                        gemini_settings=gemini_tts_settings,
                    )
                )
            except (ValueError, ProviderConfigurationError) as e:
                logger.warning("provider_form_fallback", provider=tts_name.value, error=str(e))
                problem = str(e)
                tts_provider = build_tts_provider(
                    env=state.env,
                    provider=tts_name,
                    piper_settings=PiperSettings(),
                    piper_voice_settings=TTSVoiceSettings(),
                    gemini_settings=GeminiTTSSettings(),
                )

            try:
                catalogue = await _tts_catalogue(tts_provider, catalogue_field)
                if tts_name is TTSProviderName.PIPER:
                    piper_voices = await tts_provider.list_voices()
                tts_fields = build_fields(
                    tts_provider.get_settings_schema(),
                    stored[key].model_dump(),
                    catalogue,
                )
            finally:
                if tts_provider is not state.tts_provider:
                    await tts_provider.close()

        forms.append(
            ProviderForm(
                key=key,
                provider=tts_name.value,
                kind="TTS",
                title=f"{label} (TTS)",
                active=active,
                configured=configured,
                fields=tts_fields,
                supports_voice_test=active,
                problem=problem,
            )
        )

    return forms, piper_voices


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
    tts_voice = await settings_repo.get_or_default(
        "tts_voice", TTSVoiceSettings, TTSVoiceSettings()
    )
    prompts = await settings_repo.get_or_default("prompts", PromptSettings, PromptSettings())

    providers = await load_provider_settings(settings_repo, state.env)
    provider_forms, piper_voices = await _build_provider_forms(state, settings_repo, providers)

    # Get available providers from environment (only configured + implemented)
    available_llm = state.env.get_available_llm_providers()
    available_tts = state.env.get_available_tts_providers()

    # Group the Piper catalogue by language for the per-language override tab
    voices_by_lang: dict[str, list[Voice]] = {}
    for voice in piper_voices:
        voices_by_lang.setdefault(voice.language, []).append(voice)

    # Get currently selected voice for each language (user override or default)
    selected_voices: dict[str, str] = {}
    for lang_code in LanguageCode:
        if lang_code in tts_voice.voice_overrides:
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
            "language_codes": list(LanguageCode),
            "narrator_styles": list(NarratorStyle),
            "available_llm": available_llm,
            "available_tts": available_tts,
            "voices_by_lang": voices_by_lang,
            "selected_voices": selected_voices,
            "providers": providers,
            "provider_forms": provider_forms,
            "prompts": prompts,
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
    priority_users = [u.strip() for u in priority_users_str.split(",") if u.strip()]

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
        await state.global_cooldown.update_cooldown_duration(settings.global_cooldown_seconds)

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


TEST_PHRASE = "Greetings, adventurer. Your voice settings have been configured."


@router.post("/settings/providers/piper", response_class=HTMLResponse)
async def save_piper_settings(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Save Piper synthesis settings.

    Persists the values and applies them to the running provider when Piper is
    active, which avoids reloading the voice model for a slider change. When
    another provider is active the values are still stored - they take effect
    the next time Piper is selected.
    """
    form_data = await request.form()

    try:
        settings = PiperSettings(
            voice=str(form_data["voice"]),
            length_scale=float(str(form_data["length_scale"])),
            noise_scale=float(str(form_data["noise_scale"])),
            noise_w=float(str(form_data["noise_w"])),
        )
    except (ValidationError, ValueError) as e:
        return _invalid_settings_toast(e)

    problem = await _rejected_by_tts_provider(
        state, TTSProviderName.PIPER, settings, GeminiTTSSettings()
    )
    if problem:
        return _not_saved_toast(problem)

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


def _preview_tts_provider(
    state: AppState,
    selection: ProviderSettings,
    form_data: FormData,
    voice_overrides: TTSVoiceSettings,
) -> TTSProvider:
    """Build a throwaway TTS provider from the values currently in the form.

    Auditioning has to use the submitted settings rather than the stored ones,
    otherwise the button previews whatever was saved last. A separate instance
    is used so an in-flight narration keeps the settings it started with.

    Args:
        state: Application state, for the environment.
        selection: The active provider selection.
        form_data: Submitted settings form.
        voice_overrides: Stored per-language Piper voices.

    Returns:
        A provider built from the submitted values. The caller must close it.

    Raises:
        ValidationError: If a submitted value is out of range.
        ValueError: If a submitted voice name is not valid for the provider.
        ProviderConfigurationError: If the provider's API key is missing.
    """
    piper_settings = PiperSettings(
        voice=str(form_data.get("voice", PiperSettings().voice)),
        length_scale=float(str(form_data.get("length_scale", PiperSettings().length_scale))),
        noise_scale=float(str(form_data.get("noise_scale", PiperSettings().noise_scale))),
        noise_w=float(str(form_data.get("noise_w", PiperSettings().noise_w))),
    )
    gemini_settings = GeminiTTSSettings(
        model=str(form_data.get("model", GeminiTTSSettings().model)),
        voice_name=str(form_data.get("voice_name", GeminiTTSSettings().voice_name)),
        style_prompt=str(form_data.get("style_prompt", GeminiTTSSettings().style_prompt)),
        temperature=float(str(form_data.get("temperature", GeminiTTSSettings().temperature))),
    )

    return build_tts_provider(
        env=state.env,
        provider=selection.tts,
        piper_settings=piper_settings,
        piper_voice_settings=voice_overrides,
        gemini_settings=gemini_settings,
    )


@router.post("/settings/providers/test_voice", response_class=HTMLResponse)
async def test_voice(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Synthesize a test phrase with the settings currently in the form.

    Works the same way for every TTS provider: the submitted values are used
    as-is, so the operator hears what they are about to save.
    """
    form_data = await request.form()
    selection = await load_provider_settings(settings_repo, state.env)
    voice_overrides = await settings_repo.get_or_default(
        "tts_voice", TTSVoiceSettings, TTSVoiceSettings()
    )

    try:
        provider = _preview_tts_provider(state, selection, form_data, voice_overrides)
    except (ValidationError, ValueError, ProviderConfigurationError) as e:
        return templates.TemplateResponse(
            request,
            "partials/tts_test_result.html",
            {"success": False, "error": str(e)},
        )

    try:
        await provider.start()
        audio_data = await provider.synthesize(TEST_PHRASE)
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
        await provider.close()

    return templates.TemplateResponse(
        request,
        "partials/tts_test_result.html",
        {"success": True, "audio_data": base64.b64encode(audio_data).decode()},
    )


# === Provider selection ===


def _invalid_settings_toast(error: ValidationError | ValueError) -> Response:
    """Report a rejected settings value inline.

    Bounds come from the provider's own schema, so the browser blocks most bad
    input; anything that still arrives is operator error, not a server fault.

    Args:
        error: The validation failure.

    Returns:
        HTMX toast response naming the offending field.
    """
    detail = _first_validation_message(error) if isinstance(error, ValidationError) else str(error)
    return _not_saved_toast(detail)


def _not_saved_toast(detail: str) -> Response:
    """Report that nothing was stored, and why.

    Args:
        detail: What the operator has to change.

    Returns:
        HTMX toast response carrying the reason.
    """
    message = f"Not saved - {detail}"
    return Response(
        content=f"<div class='toast error'>{message}</div>",
        headers=_toast_response(message, success=False),
    )


async def _rejected_by_llm_provider(
    state: AppState,
    provider: LLMProviderName,
    settings: GeminiLLMSettings | GroqLLMSettings,
) -> str | None:
    """Try to build the provider with the submitted settings, then throw it away.

    Providers enforce combinations the settings model cannot express - a Gemini
    model that will not accept the chosen thinking budget, for instance. Doing
    the trial build before storing keeps a combination that cannot run out of
    the database, where it would otherwise brick the settings page and leave the
    worker dead after the next restart.

    Args:
        state: Application state, for the environment.
        provider: Which LLM provider the settings belong to.
        settings: The submitted settings.

    Returns:
        The provider's complaint, or None when it builds.
    """
    if provider.value not in state.env.get_available_llm_providers():
        return None

    groq = settings if isinstance(settings, GroqLLMSettings) else GroqLLMSettings()
    gemini = settings if isinstance(settings, GeminiLLMSettings) else GeminiLLMSettings()

    try:
        built = build_llm_provider(
            env=state.env, provider=provider, groq_settings=groq, gemini_settings=gemini
        )
    except (ValueError, ProviderConfigurationError) as e:
        return str(e)

    await built.close()
    return None


async def _rejected_by_tts_provider(
    state: AppState,
    provider: TTSProviderName,
    piper_settings: PiperSettings,
    gemini_settings: GeminiTTSSettings,
) -> str | None:
    """Try to build the TTS provider with the submitted settings, then discard it.

    Args:
        state: Application state, for the environment.
        provider: Which TTS provider the settings belong to.
        piper_settings: Piper settings to test with.
        gemini_settings: Gemini settings to test with.

    Returns:
        The provider's complaint, or None when it builds.
    """
    if provider.value not in state.env.get_available_tts_providers():
        return None

    try:
        built = build_tts_provider(
            env=state.env,
            provider=provider,
            piper_settings=piper_settings,
            piper_voice_settings=TTSVoiceSettings(),
            gemini_settings=gemini_settings,
        )
    except (ValueError, ProviderConfigurationError) as e:
        return str(e)

    await built.close()
    return None


@router.post("/settings/providers", response_class=HTMLResponse)
async def save_provider_selection(
    request: Request,
    state: AppStateDep,
    settings_repo: SettingsRepoDep,
) -> Response:
    """Switch the active LLM and TTS providers and rebuild the pipeline."""
    form_data = await request.form()

    try:
        settings = ProviderSettings(
            llm=LLMProviderName(str(form_data["llm"])),
            tts=TTSProviderName(str(form_data["tts"])),
        )
    except (ValidationError, ValueError) as e:
        return _invalid_settings_toast(e)

    # Prove the pair can actually be built before it becomes the stored
    # selection; otherwise a failed rebuild leaves a selection behind that stops
    # the worker from starting on the next boot.
    try:
        llm_provider, tts_provider = await build_providers(
            state.env, settings_repo, selection=settings
        )
    except (ValueError, ProviderConfigurationError) as e:
        return _not_saved_toast(str(e))

    await tts_provider.close()
    await llm_provider.close()

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

    raw_effort = str(form_data.get("reasoning_effort", "")).strip()

    try:
        settings = GroqLLMSettings(
            model=str(form_data["model"]),
            temperature=float(str(form_data["temperature"])),
            max_tokens=int(str(form_data["max_tokens"])),
            reasoning_effort=GroqReasoningEffort(raw_effort) if raw_effort else None,
        )
    except (ValidationError, ValueError) as e:
        return _invalid_settings_toast(e)

    problem = await _rejected_by_llm_provider(state, LLMProviderName.GROQ, settings)
    if problem:
        return _not_saved_toast(problem)

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

    raw_level = str(form_data.get("thinking_level", "")).strip()

    try:
        settings = GeminiLLMSettings(
            model=str(form_data["model"]),
            temperature=float(str(form_data["temperature"])),
            max_output_tokens=int(str(form_data["max_output_tokens"])),
            thinking_level=GeminiThinkingLevel(raw_level) if raw_level else None,
            safety_threshold=GeminiSafetyThreshold(str(form_data["safety_threshold"])),
        )
    except (ValidationError, ValueError) as e:
        return _invalid_settings_toast(e)

    problem = await _rejected_by_llm_provider(state, LLMProviderName.GEMINI, settings)
    if problem:
        return _not_saved_toast(problem)

    await settings_repo.set("gemini_llm", settings)

    active = await load_provider_settings(settings_repo, state.env)
    if active.llm is not LLMProviderName.GEMINI:
        return Response(
            content="<div class='toast success'>Gemini LLM settings saved</div>",
            headers=_toast_response("Gemini LLM settings saved (Gemini is not the active LLM)"),
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

    try:
        settings = GeminiTTSSettings(
            model=str(form_data["model"]),
            voice_name=str(form_data["voice_name"]),
            style_prompt=str(form_data.get("style_prompt", "")),
            temperature=float(str(form_data["temperature"])),
        )
    except (ValidationError, ValueError) as e:
        return _invalid_settings_toast(e)

    problem = await _rejected_by_tts_provider(
        state, TTSProviderName.GEMINI, PiperSettings(), settings
    )
    if problem:
        return _not_saved_toast(problem)

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
