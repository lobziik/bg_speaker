"""Main entry point for the narrator bot.

Phase 1: CLI tool for testing LLM → TTS pipeline
Later phases: Full web server with Twitch integration
"""

import argparse
import asyncio
import sys
from pathlib import Path

import structlog

from src.config import get_env_settings
from src.models.narration import NarrationRequest, NarratorStyle
from src.providers.llm.groq import GroqLLMProvider
from src.providers.tts.piper import PiperSettings, PiperTTSProvider
from src.services.pipeline import NarrationPipeline

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


async def run_pipeline(
    message: str,
    user: str = "Adventurer",
    style: str = "default",
    output_path: Path | None = None,
) -> None:
    """Run the narration pipeline on a message.

    Args:
        message: Message to narrate
        user: Username (for narrative reference)
        style: Narrator style
        output_path: Path to save audio file (optional)
    """
    env = get_env_settings()

    # Check for required API key
    if not env.groq_api_key:
        logger.error("GROQ_API_KEY environment variable not set")
        sys.exit(1)

    # Initialize providers
    logger.info("Initializing providers...")

    llm = GroqLLMProvider(api_key=env.groq_api_key)
    tts = PiperTTSProvider(settings=PiperSettings())

    # Create pipeline
    pipeline = NarrationPipeline(llm_provider=llm, tts_provider=tts)

    # Check health
    health = await pipeline.health_check()
    if not health["pipeline"]:
        logger.error("Pipeline health check failed", health=health)
        sys.exit(1)

    logger.info("Pipeline ready", health=health)

    # Create request
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

    # Process
    logger.info("Processing narration...", user=user, message=message[:50])

    result, metrics = await pipeline.process(request)

    logger.info(
        "Narration complete",
        text=result.text_original,
        duration_ms=result.duration_ms,
        llm_latency_ms=metrics.llm_latency_ms,
        tts_latency_ms=metrics.tts_latency_ms,
        total_latency_ms=metrics.total_latency_ms,
    )

    # Save audio
    if output_path is None:
        output_path = Path("output.wav")

    output_path.write_bytes(result.audio_data)
    logger.info("Audio saved", path=str(output_path), size=len(result.audio_data))

    # Print the formatted text
    print("\n" + "=" * 60)
    print("NARRATOR:")
    print(result.text_original)
    print("=" * 60)
    print(f"\nAudio saved to: {output_path}")
    print(f"Duration: {result.duration_ms}ms")
    print(f"Total processing time: {metrics.total_latency_ms}ms")


def main() -> None:
    """CLI entry point."""
    # Immediate feedback before any processing
    print("BG3 Narrator Bot - Starting...")

    parser = argparse.ArgumentParser(
        description="BG3 Narrator Bot - Convert text to narrated audio",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main "Hello everyone!"
  python -m src.main "I found a legendary sword!" --user DragonSlayer
  python -m src.main "The path ahead is dark..." --style whisper --output narration.wav
        """,
    )

    parser.add_argument("message", help="Message to narrate")
    parser.add_argument(
        "--user", "-u", default="Adventurer", help="Username for narrative (default: Adventurer)"
    )
    parser.add_argument(
        "--style",
        "-s",
        default="default",
        choices=["default", "whisper", "proclaim", "mock"],
        help="Narrator style (default: default)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output audio file path (default: output.wav)",
    )

    args = parser.parse_args()

    asyncio.run(
        run_pipeline(
            message=args.message,
            user=args.user,
            style=args.style,
            output_path=args.output,
        )
    )


if __name__ == "__main__":
    main()
