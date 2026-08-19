"""FastAPI dependency injection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from fastapi.templating import Jinja2Templates

from src.config import EnvSettings, get_env_settings
from src.db.manager import DatabaseManager
from src.db.repositories.settings import SettingsRepository
from src.db.repositories.twitch_state import TwitchStateRepository
from src.models.settings import QueueSettings
from src.services.queue import NarrationQueue
from src.services.rate_limiter import RateLimiter
from src.services.twitch.auth import TwitchAuthService

# Templates path
TEMPLATES_PATH = Path(__file__).parent.parent / "templates"

if TYPE_CHECKING:
    from src.providers.llm.base import LLMProvider
    from src.providers.tts.base import TTSProvider
    from src.services.global_cooldown import GlobalCooldownManager
    from src.services.pipeline import NarrationPipeline
    from src.services.twitch.eventsub import TwitchEventSubService
    from src.services.twitch.rewards import TwitchRewardController
    from src.services.worker import QueueWorker


@dataclass
class AppState:
    """Application state container.

    Holds all service instances that are shared across the application.
    Initialized on startup and shutdown on application termination.
    """

    env: EnvSettings
    db: DatabaseManager
    rate_limiter: RateLimiter
    queue: NarrationQueue
    twitch_auth: TwitchAuthService
    pipeline: NarrationPipeline | None = None
    worker: QueueWorker | None = None
    twitch_eventsub: TwitchEventSubService | None = None
    twitch_rewards: TwitchRewardController | None = None
    global_cooldown: GlobalCooldownManager | None = None
    llm_provider: LLMProvider | None = None
    tts_provider: TTSProvider | None = None
    _initialized: bool = field(default=False, repr=False)

    async def initialize(self) -> None:
        """Initialize all services."""
        if self._initialized:
            return
        await self.db.initialize()
        self._initialized = True

    async def rebuild_pipeline(self) -> None:
        """(Re)build the narration pipeline from the currently stored settings.

        Used both for the initial wiring at startup and after a provider change
        in the Web UI, so a new selection takes effect without restarting the
        app. Any narration already in flight finishes on the old pipeline; its
        providers are closed only afterwards. Starts the queue worker if it is
        not running yet.

        Raises:
            ProviderConfigurationError: If the selected provider cannot be built
                (e.g. its API key is missing). The previous pipeline is left
                untouched in that case.
        """
        from src.api.websocket import get_websocket_manager
        from src.providers.factory import build_providers
        from src.services.pipeline import NarrationPipeline
        from src.services.worker import QueueWorker

        settings_repo = SettingsRepository(self.db.connection)
        llm_provider, tts_provider = await build_providers(self.env, settings_repo)
        await tts_provider.start()

        pipeline = NarrationPipeline(
            llm_provider=llm_provider,
            tts_provider=tts_provider,
        )

        previous_llm = self.llm_provider
        previous_tts = self.tts_provider

        if self.worker is None:
            self.worker = QueueWorker(
                queue=self.queue,
                pipeline=pipeline,
                ws_manager=get_websocket_manager(),
                rewards_controller=self.twitch_rewards,
                db_connection=self.db.connection,
                global_cooldown=self.global_cooldown,
            )
            await self.worker.start()
        else:
            # Swapping the worker's pipeline waits for the in-flight item, which
            # is what makes closing the previous providers below safe.
            await self.worker.set_pipeline(pipeline)

        self.pipeline = pipeline
        self.llm_provider = llm_provider
        self.tts_provider = tts_provider

        if previous_tts is not None:
            await previous_tts.close()
        if previous_llm is not None:
            await previous_llm.close()

    async def shutdown(self) -> None:
        """Shutdown all services."""
        if self.worker:
            await self.worker.stop()
        if self.global_cooldown:
            await self.global_cooldown.shutdown()
        if self.twitch_eventsub:
            await self.twitch_eventsub.stop()
        if self.twitch_rewards:
            await self.twitch_rewards.close()
        if self.tts_provider:
            await self.tts_provider.close()
        if self.llm_provider:
            await self.llm_provider.close()
        await self.twitch_auth.close()
        await self.queue.shutdown()
        await self.db.close()


# Global state (initialized on startup)
_app_state: AppState | None = None


def parse_db_path(database_url: str) -> Path:
    """Parse the SQLite database path out of a DATABASE_URL.

    Args:
        database_url: Either a ``sqlite:///`` URL or a bare filesystem path.

    Returns:
        Filesystem path to the SQLite database file.
    """
    if database_url.startswith("sqlite:///"):
        return Path(database_url.replace("sqlite:///", ""))
    return Path(database_url)


def get_app_state() -> AppState:
    """Get or create application state."""
    global _app_state

    if _app_state is None:
        env = get_env_settings()
        db = DatabaseManager(parse_db_path(env.database_url))

        rate_limiter = RateLimiter(
            tts_rate_limit_seconds=10.0,
            user_cooldown_seconds=5.0,
        )

        queue = NarrationQueue(
            settings=QueueSettings(),
            rate_limiter=rate_limiter,
        )

        twitch_auth = TwitchAuthService(
            client_id=env.twitch_client_id,
            client_secret=env.twitch_client_secret,
            redirect_uri=env.twitch_redirect_uri,
        )

        _app_state = AppState(
            env=env,
            db=db,
            rate_limiter=rate_limiter,
            queue=queue,
            twitch_auth=twitch_auth,
        )

    return _app_state


def reset_app_state() -> None:
    """Reset app state. Use for testing."""
    global _app_state
    _app_state = None


# Dependency aliases
AppStateDep = Annotated[AppState, Depends(get_app_state)]


def get_twitch_state_repo() -> TwitchStateRepository:
    """Get Twitch state repository."""
    state = get_app_state()
    return TwitchStateRepository(state.db.connection)


TwitchStateRepoDep = Annotated[TwitchStateRepository, Depends(get_twitch_state_repo)]


# Templates singleton
_templates: Jinja2Templates | None = None


def get_templates() -> Jinja2Templates:
    """Get Jinja2 templates instance."""
    global _templates
    if _templates is None:
        _templates = Jinja2Templates(directory=TEMPLATES_PATH)
    return _templates


TemplatesDep = Annotated[Jinja2Templates, Depends(get_templates)]


def get_settings_repo() -> SettingsRepository:
    """Get settings repository."""
    state = get_app_state()
    return SettingsRepository(state.db.connection)


SettingsRepoDep = Annotated[SettingsRepository, Depends(get_settings_repo)]
