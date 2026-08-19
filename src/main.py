"""Main entry point for the narrator bot.

Supports two modes:
- CLI: Test the LLM → TTS pipeline directly
- Server: Run FastAPI server with Twitch integration
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Literal

import structlog
import uvicorn

from src.api.dependencies import parse_db_path
from src.config import get_env_settings
from src.db.manager import DatabaseManager
from src.db.repositories.settings import SettingsRepository
from src.models.narration import NarrationRequest, NarratorStyle
from src.models.settings import (
    LanguageSettings,
    LLMProviderName,
    NarratorSettings,
    ProviderSettings,
    TTSProviderName,
)
from src.providers.factory import (
    ProviderConfigurationError,
    build_providers,
    load_provider_settings,
)
from src.providers.llm.prompts import PromptSettings
from src.services.pipeline import NarrationPipeline

LogLevel = Literal["debug", "info", "warning", "error"]
LogFormat = Literal["console", "json"]


def configure_logging(
    level: LogLevel = "info",
    log_format: LogFormat = "console",
) -> None:
    """Configure structlog and stdlib logging.

    Routes all stdlib logging through structlog processors for consistent output.

    Args:
        level: Log level (debug, info, warning, error).
        log_format: Output format ("console" for human-readable, "json" for structured).
    """
    # Map string level to logging constants
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    numeric_level = level_map[level]

    # Shared processors for both structlog and stdlib
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,  # Allow reconfiguration
    )

    # Choose renderer based on format
    if log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # Create formatter that renders structlog output
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Configure root handler
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    # Reset root logger
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Silence noisy third-party loggers - these produce excessive output
    noisy_loggers = [
        "aiosqlite",  # DB operation traces
        "websockets",  # WebSocket frame-level traces
        "websockets.client",
        "websockets.server",
        "websockets.protocol",
        "uvicorn",  # Uvicorn internal logs
        "uvicorn.error",
        "uvicorn.access",
    ]
    for logger_name in noisy_loggers:
        lib_logger = logging.getLogger(logger_name)
        lib_logger.handlers.clear()  # Remove uvicorn's handlers
        lib_logger.addHandler(handler)  # Use our structlog handler
        lib_logger.setLevel(logging.WARNING)
        lib_logger.propagate = False  # Don't double-log


# Initialize with env settings (can be reconfigured via CLI)
_init_env = get_env_settings()
configure_logging(level=_init_env.log_level, log_format=_init_env.log_format)

logger = structlog.get_logger()


async def run_pipeline(
    message: str,
    user: str = "Adventurer",
    style: str = "default",
    output_path: Path | None = None,
    llm_provider: str | None = None,
    tts_provider: str | None = None,
) -> None:
    """Run the narration pipeline on a message.

    Uses the same providers and language settings as the server, so a CLI test
    reflects what a Twitch redemption would actually produce. The provider
    overrides apply to this run only - nothing is written back to the database.

    Args:
        message: Message to narrate.
        user: Username (for narrative reference).
        style: Narrator style.
        output_path: Path to save audio file (defaults to output.wav).
        llm_provider: Override the stored LLM provider for this run.
        tts_provider: Override the stored TTS provider for this run.
    """
    env = get_env_settings()

    db = DatabaseManager(parse_db_path(env.database_url))
    await db.initialize()

    try:
        settings_repo = SettingsRepository(db.connection)
        selection = await load_provider_settings(settings_repo, env)
        if llm_provider or tts_provider:
            selection = ProviderSettings(
                llm=LLMProviderName(llm_provider) if llm_provider else selection.llm,
                tts=TTSProviderName(tts_provider) if tts_provider else selection.tts,
            )

        logger.info(
            "Initializing providers...",
            llm_provider=selection.llm.value,
            tts_provider=selection.tts.value,
        )

        try:
            llm, tts = await build_providers(env, settings_repo, selection)
        except ProviderConfigurationError as e:
            logger.error("provider_configuration_error", error=str(e))
            sys.exit(1)

        try:
            await tts.start()
            language = await settings_repo.get_or_default(
                "language", LanguageSettings, LanguageSettings()
            )
            narrator = await settings_repo.get_or_default(
                "narrator", NarratorSettings, NarratorSettings()
            )
            prompts = await settings_repo.get_or_default(
                "prompts", PromptSettings, PromptSettings()
            )

            pipeline = NarrationPipeline(llm_provider=llm, tts_provider=tts)

            health = await pipeline.health_check()
            if not health["pipeline"]:
                logger.error("Pipeline health check failed", health=health)
                sys.exit(1)

            logger.info("Pipeline ready", health=health)

            try:
                narrator_style = NarratorStyle(style)
            except ValueError:
                logger.warning(f"Unknown style '{style}', using default")
                narrator_style = NarratorStyle.DEFAULT

            request = NarrationRequest(
                user=user,
                message=message,
                style=narrator_style,
            )

            logger.info("Processing narration...", user=user, message=message[:50])

            result, metrics = await pipeline.process(
                request,
                narrator_lang=language.narrator_lang,
                subtitle_lang=language.subtitle_lang,
                auto_translate=narrator.auto_translate,
                bypass_llm=narrator.bypass_llm,
                enable_moderation=narrator.enable_moderation,
                custom_prompt=narrator.system_prompt,
                prompts=prompts,
            )
        finally:
            await tts.close()
            await llm.close()
    finally:
        await db.close()

    logger.info(
        "Narration complete",
        voice_text=result.voice_text,
        duration_ms=result.duration_ms,
        moderation_latency_ms=metrics.moderation_latency_ms,
        llm_latency_ms=metrics.llm_latency_ms,
        tts_latency_ms=metrics.tts_latency_ms,
        total_latency_ms=metrics.total_latency_ms,
    )

    if output_path is None:
        output_path = Path("output.wav")

    output_path.write_bytes(result.audio_data)
    logger.info("Audio saved", path=str(output_path), size=len(result.audio_data))

    print("\n" + "=" * 60)
    print("NARRATOR:")
    print(result.voice_text)
    print("=" * 60)
    print(f"\nAudio saved to: {output_path}")
    print(f"Duration: {result.duration_ms}ms")
    print(f"Total processing time: {metrics.total_latency_ms}ms")


def run_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool = False,
    log_level: LogLevel = "info",
    log_format: LogFormat = "console",
) -> None:
    """Run the FastAPI server.

    Args:
        host: Host to bind to.
        port: Port to bind to.
        reload: Enable auto-reload for development.
        log_level: Log level for the application.
        log_format: Output format ("console" or "json").
    """
    from src.api.app import create_app

    # Reconfigure logging with the specified level and format
    configure_logging(log_level, log_format)

    logger.info("server_starting", host=host, port=port, log_level=log_level, log_format=log_format)

    # Create the app
    app = create_app()

    # Run with uvicorn
    # - log_config=None prevents uvicorn from reconfiguring logging
    # - access_log=False disables uvicorn's access log (we use middleware)
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload,
        log_config=None,
        access_log=False,
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Narrator Bot - Twitch Channel Points narrator with dramatic fantasy TTS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Server command
    server_parser = subparsers.add_parser(
        "serve",
        help="Run the web server with Twitch integration",
    )
    server_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    server_parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=8000,
        help="Port to bind to (default: 8000)",
    )
    server_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    server_parser.add_argument(
        "--log-level",
        "-l",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Log level (default: info)",
    )

    # CLI test command
    cli_parser = subparsers.add_parser(
        "test",
        help="Test the LLM → TTS pipeline directly",
    )
    cli_parser.add_argument("message", help="Message to narrate")
    cli_parser.add_argument(
        "--user",
        "-u",
        default="Adventurer",
        help="Username for narrative (default: Adventurer)",
    )
    cli_parser.add_argument(
        "--style",
        "-s",
        default="default",
        choices=["default", "whisper", "proclaim", "mock"],
        help="Narrator style (default: default)",
    )
    cli_parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output audio file path (default: output.wav)",
    )
    cli_parser.add_argument(
        "--llm-provider",
        default=None,
        choices=[p.value for p in LLMProviderName],
        help="Override the configured LLM provider for this run",
    )
    cli_parser.add_argument(
        "--tts-provider",
        default=None,
        choices=[p.value for p in TTSProviderName],
        help="Override the configured TTS provider for this run",
    )
    cli_parser.add_argument(
        "--log-level",
        "-l",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Log level (default: info)",
    )

    args = parser.parse_args()

    if args.command == "serve":
        env = get_env_settings()
        run_server(
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
            log_format=env.log_format,
        )
    elif args.command == "test":
        env = get_env_settings()
        configure_logging(args.log_level, env.log_format)
        print("Narrator Bot - Testing pipeline...")
        asyncio.run(
            run_pipeline(
                message=args.message,
                user=args.user,
                style=args.style,
                output_path=args.output,
                llm_provider=args.llm_provider,
                tts_provider=args.tts_provider,
            )
        )
    else:
        # Default to serve if no command given
        parser.print_help()


if __name__ == "__main__":
    main()
