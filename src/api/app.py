"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.dependencies import get_app_state
from src.api.middleware import AccessLogMiddleware
from src.api.routes import health, overlay, test, twitch
from src.api.websocket import get_websocket_manager

# Paths for static files and templates
STATIC_PATH = Path(__file__).parent.parent / "static"
TEMPLATES_PATH = Path(__file__).parent.parent / "templates"

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: startup and shutdown.

    Initializes all services on startup and cleans up on shutdown.
    """
    logger.info("app_starting")
    state = get_app_state()
    await state.initialize()

    # Initialize WebSocket manager with services
    ws_manager = get_websocket_manager()
    ws_manager.set_services(state.queue, state.rate_limiter)

    # Initialize global cooldown manager
    from src.db.repositories.settings import SettingsRepository
    from src.models.settings import TwitchRewardSettings
    from src.services.global_cooldown import CooldownStatus, GlobalCooldownManager

    settings_repo = SettingsRepository(state.db.connection)
    reward_settings = await settings_repo.get(
        "reward", TwitchRewardSettings, TwitchRewardSettings()
    )
    cooldown_seconds = reward_settings.global_cooldown_seconds if reward_settings else 300

    async def on_cooldown_status_change(status: CooldownStatus) -> None:
        """Broadcast cooldown status changes to WebSocket clients."""
        await ws_manager.broadcast_global_cooldown_status(
            is_active=status.is_active,
            remaining_seconds=status.remaining_seconds,
            total_seconds=status.total_seconds,
        )

    state.global_cooldown = GlobalCooldownManager(
        rewards_controller=state.twitch_rewards,
        db_connection=state.db.connection,
        cooldown_seconds=cooldown_seconds,
        on_status_change=on_cooldown_status_change,
    )
    await state.global_cooldown.initialize()
    logger.info("global_cooldown_manager_initialized", cooldown_seconds=cooldown_seconds)

    # Initialize pipeline and worker if LLM key is available
    if state.env.groq_api_key:
        from src.models.settings import TTSVoiceSettings
        from src.providers.llm.groq import GroqLLMProvider
        from src.providers.tts.piper import PiperTTSProvider
        from src.services.pipeline import NarrationPipeline
        from src.services.worker import QueueWorker

        # Load TTS voice settings for per-language voice overrides
        tts_voice_settings = await settings_repo.get(
            "tts_voice", TTSVoiceSettings, TTSVoiceSettings()
        )
        voice_overrides = (
            tts_voice_settings.voice_overrides if tts_voice_settings else {}
        )

        llm_provider = GroqLLMProvider(api_key=state.env.groq_api_key)
        tts_provider = PiperTTSProvider(voice_overrides=voice_overrides)

        state.pipeline = NarrationPipeline(
            llm_provider=llm_provider,
            tts_provider=tts_provider,
        )

        state.worker = QueueWorker(
            queue=state.queue,
            pipeline=state.pipeline,
            ws_manager=ws_manager,
            rewards_controller=state.twitch_rewards,
            db_connection=state.db.connection,
            global_cooldown=state.global_cooldown,
        )

        await state.worker.start()
        logger.info("queue_worker_started")
    else:
        logger.warning("no_llm_api_key", message="Worker not started - no GROQ_API_KEY")

    logger.info("app_started")

    yield

    logger.info("app_stopping")
    await ws_manager.shutdown()
    await state.shutdown()
    logger.info("app_stopped")


def create_app() -> FastAPI:
    """Create FastAPI application instance.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(
        title="BG3 Narrator Bot",
        description="Twitch Channel Points narrator with D&D style TTS",
        version="0.2.0",
        lifespan=lifespan,
    )

    # CORS for OBS overlay
    app.add_middleware(
        CORSMiddleware,  # ty: ignore[invalid-argument-type]
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Access logging middleware (GET at DEBUG, others at INFO)
    app.add_middleware(AccessLogMiddleware)

    # Mount static files
    if STATIC_PATH.exists():
        app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")

    # Include API routers
    app.include_router(health.router, tags=["Health"])
    app.include_router(twitch.router, prefix="/auth", tags=["Twitch OAuth"])
    app.include_router(test.router, prefix="/api", tags=["Test"])
    app.include_router(overlay.router, tags=["Overlay"])

    # Include view routers (Web UI)
    from src.views import dashboard, logs, queue, settings
    from src.views import test as test_view

    app.include_router(dashboard.router, tags=["Views"])
    app.include_router(settings.router, tags=["Views"])
    app.include_router(queue.router, tags=["Views"])
    app.include_router(test_view.router, tags=["Views"])
    app.include_router(logs.router, tags=["Views"])

    return app
