"""Settings view routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

# TC001 ignored: FastAPI Depends() requires these at runtime for dependency injection
from src.api.dependencies import AppStateDep, SettingsRepoDep, TemplatesDep  # noqa: TC001
from src.models.narration import LanguageCode, NarratorStyle
from src.models.settings import (
    LanguageSettings,
    NarratorSettings,
    OverlaySettings,
    PiperSettings,
    QueueSettings,
    TTSVoiceSettings,
    TwitchRewardSettings,
)
from src.providers.tts.piper import DEFAULT_VOICES, LANGUAGE_DEFAULT_VOICES

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

    # Get available providers from environment
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
    """Save TTS provider settings (speed, variation).

    Updates Piper TTS settings and applies them to the running provider.
    """
    form_data = await request.form()

    settings = PiperSettings(
        length_scale=float(str(form_data["length_scale"])),
        noise_scale=float(str(form_data["noise_scale"])),
        noise_w=float(str(form_data.get("noise_w", 0.8))),
    )

    await settings_repo.set("piper", settings)

    # Update running provider if pipeline exists
    if state.pipeline:
        state.pipeline.tts_provider.update_settings(
            length_scale=settings.length_scale,
            noise_scale=settings.noise_scale,
            noise_w=settings.noise_w,
        )

    return Response(
        content="<div class='toast success'>TTS settings saved</div>",
        headers=_toast_response("TTS settings saved"),
    )


@router.post("/settings/tts_provider/test", response_class=HTMLResponse)
async def test_tts_provider(
    request: Request,
    state: AppStateDep,
    templates: TemplatesDep,
) -> HTMLResponse:
    """Test TTS with current form settings without saving.

    Synthesizes a test phrase and returns audio for playback.
    """
    import base64

    form_data = await request.form()

    if not state.pipeline:
        return templates.TemplateResponse(
            request,
            "partials/tts_test_result.html",
            {"success": False, "error": "Pipeline not initialized"},
        )

    try:
        length_scale = float(str(form_data.get("length_scale", 1.0)))
        noise_scale = float(str(form_data.get("noise_scale", 0.667)))
        noise_w = float(str(form_data.get("noise_w", 0.8)))

        # Temporarily update settings for test
        tts = state.pipeline.tts_provider
        original_settings = (
            tts._settings.length_scale,  # type: ignore[attr-defined]
            tts._settings.noise_scale,  # type: ignore[attr-defined]
            tts._settings.noise_w,  # type: ignore[attr-defined]
        )
        tts.update_settings(
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w=noise_w,
        )

        # Synthesize test phrase
        test_text = "Greetings, adventurer. Your voice settings have been configured."
        audio_data = await tts.synthesize(test_text)

        # Restore original settings
        tts.update_settings(
            length_scale=original_settings[0],
            noise_scale=original_settings[1],
            noise_w=original_settings[2],
        )

        # Return audio as base64 for playback
        audio_b64 = base64.b64encode(audio_data).decode()

        return templates.TemplateResponse(
            request,
            "partials/tts_test_result.html",
            {"success": True, "audio_data": audio_b64},
        )
    except Exception as e:
        return templates.TemplateResponse(
            request,
            "partials/tts_test_result.html",
            {"success": False, "error": str(e)},
        )
