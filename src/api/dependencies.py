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
    _initialized: bool = field(default=False, repr=False)

    async def initialize(self) -> None:
        """Initialize all services."""
        if self._initialized:
            return
        await self.db.initialize()
        self._initialized = True

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
        await self.twitch_auth.close()
        await self.queue.shutdown()
        await self.db.close()


# Global state (initialized on startup)
_app_state: AppState | None = None


def _parse_db_path(database_url: str) -> Path:
    """Parse SQLite database path from URL."""
    if database_url.startswith("sqlite:///"):
        return Path(database_url.replace("sqlite:///", ""))
    return Path(database_url)


def get_app_state() -> AppState:
    """Get or create application state."""
    global _app_state

    if _app_state is None:
        env = get_env_settings()
        db = DatabaseManager(_parse_db_path(env.database_url))

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
