"""Settings view routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from src.api.dependencies import AppStateDep, SettingsRepoDep, TemplatesDep
from src.models.narration import LanguageCode, NarratorStyle
from src.models.settings import (
    LanguageSettings,
    NarratorSettings,
    OverlaySettings,
    QueueSettings,
    TwitchRewardSettings,
)

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

    # Get available providers from environment
    available_llm = state.env.get_available_llm_providers()
    available_tts = state.env.get_available_tts_providers()

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
            "language_codes": list(LanguageCode),
            "narrator_styles": list(NarratorStyle),
            "available_llm": available_llm,
            "available_tts": available_tts,
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
        source_lang=LanguageCode(str(form_data["source_lang"])),
        narrator_lang=LanguageCode(str(form_data["narrator_lang"])),
        subtitle_lang=LanguageCode(str(form_data["subtitle_lang"])),
        auto_translate=form_data.get("auto_translate") == "on",
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
    )

    await settings_repo.set("reward", settings)

    return Response(
        content="<div class='toast success'>Reward settings saved</div>",
        headers=_toast_response("Reward settings saved"),
    )
