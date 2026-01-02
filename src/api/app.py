"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.dependencies import get_app_state
from src.api.routes import health, test, twitch

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: startup and shutdown.

    Initializes all services on startup and cleans up on shutdown.
    """
    logger.info("app_starting")
    state = get_app_state()
    await state.initialize()
    logger.info("app_started")

    yield

    logger.info("app_stopping")
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

    return app
