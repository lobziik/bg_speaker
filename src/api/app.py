"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.dependencies import get_app_state
from src.api.routes import health, overlay, test, twitch
from src.api.websocket import get_websocket_manager

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

    # Initialize pipeline and worker if LLM key is available
    if state.env.groq_api_key:
        from src.providers.llm.groq import GroqLLMProvider
        from src.providers.tts.piper import PiperTTSProvider
        from src.services.pipeline import NarrationPipeline
        from src.services.worker import QueueWorker

        llm_provider = GroqLLMProvider(api_key=state.env.groq_api_key)
        tts_provider = PiperTTSProvider()

        state.pipeline = NarrationPipeline(
            llm_provider=llm_provider,
            tts_provider=tts_provider,
        )

        state.worker = QueueWorker(
            queue=state.queue,
            pipeline=state.pipeline,
            ws_manager=ws_manager,
            rewards_controller=state.twitch_rewards,
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
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health.router, tags=["Health"])
    app.include_router(twitch.router, prefix="/auth", tags=["Twitch OAuth"])
    app.include_router(test.router, prefix="/api", tags=["Test"])
    app.include_router(overlay.router, tags=["Overlay"])

    return app
