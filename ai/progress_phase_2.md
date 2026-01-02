# Phase 2 Progress: Twitch Integration

## Overview

Phase 2 adds Twitch Channel Points integration to the existing LLM → TTS pipeline. The bot connects to Twitch via EventSub WebSocket, receives redemptions, queues messages with rate limiting, and processes them through the pipeline.

**Deliverable:** Bot connects to Twitch, handles OAuth flow, and queues redemptions for processing.

## Completed Tasks

### 1. Rate Limiter Service (`src/services/rate_limiter.py`)

- [x] `RateLimiter` class with global TTS rate limit + per-user cooldowns
- [x] `check(user)` - Check if user can submit (for queue admission)
- [x] `acquire(user)` - Acquire rate limit slot (for TTS processing)
- [x] `get_status()` - Returns current rate limit state
- [x] `update_settings()` - Dynamic configuration updates
- [x] `reset()` - Clear all rate limit state
- [x] `RejectionReason` enum: `TTS_RATE_LIMITED`, `USER_COOLDOWN`, `QUEUE_FULL`, `MESSAGE_FILTERED`
- [x] `RateLimitResult` dataclass with `allowed`, `reason`, `retry_after_seconds`
- [x] 11 unit tests (all passing)

### 2. Message Queue Service (`src/services/queue.py`)

- [x] `NarrationQueue` class with asyncio-based priority queue
- [x] `add(user, message, redemption_id)` - Add message with validation
- [x] `get_next()` - Wait for rate limit and return next item
- [x] `mark_completed(item)` - Mark processing complete
- [x] `skip(item_id)` - Remove item by ID
- [x] `get_items()` - Get current queue for UI
- [x] `clear()` - Clear all items
- [x] `on_event(handler)` - Register event handlers for UI updates
- [x] Priority ordering (VIP users processed first)
- [x] Message length validation
- [x] `QueueEventType` enum: `ITEM_ADDED`, `ITEM_REMOVED`, `ITEM_PROCESSING`, `ITEM_COMPLETED`, `QUEUE_FULL`
- [x] 12 unit tests (all passing)

### 3. Database Layer (`src/db/`)

- [x] `DatabaseManager` class with migration support
- [x] Lazy connection management
- [x] Automatic migration on startup
- [x] Transaction context manager with auto commit/rollback
- [x] `SettingsRepository` - Typed repository for app settings
- [x] `TwitchStateRepository` - OAuth tokens and reward state storage
- [x] SQL migrations in `migrations/` folder:
  - `001_initial.sql` - Core tables (settings, provider_settings, twitch_state, narration_log, banned_users, banned_words)
  - `002_indexes.sql` - Performance indexes

### 4. Twitch OAuth Service (`src/services/twitch/auth.py`)

- [x] `TwitchAuthService` class for OAuth 2.0 authorization code flow
- [x] `get_authorization_url(state)` - Generate OAuth URL with CSRF protection
- [x] `exchange_code(code)` - Exchange authorization code for tokens
- [x] `refresh_tokens(refresh_token)` - Refresh expired tokens
- [x] `validate_token(access_token)` - Validate and get user info
- [x] `TwitchTokens` dataclass with access_token, refresh_token, user_id, user_login, scopes
- [x] Required scopes: `channel:read:redemptions`, `channel:manage:redemptions`
- [x] Exception types: `TwitchAuthError`, `TokenExpiredError`, `InvalidGrantError`

### 5. Twitch EventSub Service (`src/services/twitch/eventsub.py`)

- [x] `TwitchEventSubService` class for TwitchIO 3.x WebSocket
- [x] `start(access_token)` - Connect and subscribe to redemptions
- [x] `stop()` - Disconnect and cleanup
- [x] `on_redemption(handler)` - Register redemption handlers
- [x] `is_connected` property
- [x] `RedemptionEvent` dataclass with id, user, user_id, message, reward_id, reward_title, reward_cost
- [x] Target reward filtering (only handle specific reward)
- [x] Exception types: `EventSubConnectionError`, `EventSubNotConnectedError`

### 6. Twitch Rewards Controller (`src/services/twitch/rewards.py`)

- [x] `TwitchRewardController` class for Channel Points management via Helix API
- [x] `initialize(access_token, config)` - Create or find existing reward
- [x] `set_state(state)` - Set reward state (ACTIVE, PAUSED, DISABLED)
- [x] `pause()` / `unpause()` - Convenience methods
- [x] `fulfill_redemption(redemption_id)` - Mark redemption complete
- [x] `cancel_redemption(redemption_id)` - Refund points to user
- [x] `RewardConfig` dataclass with title, cost, prompt, etc.
- [x] `RewardState` enum: `ACTIVE`, `PAUSED`, `DISABLED`
- [x] Exception types: `RewardNotFoundError`, `RewardOperationError`

### 7. FastAPI Application (`src/api/`)

- [x] `create_app()` factory with lifespan context manager
- [x] CORS middleware for OBS overlay
- [x] `AppState` dataclass as dependency injection container
- [x] Health endpoint: `GET /health`
- [x] Twitch OAuth routes:
  - `GET /auth/login` - Redirect to Twitch authorization
  - `GET /auth/callback` - Handle OAuth callback
  - `GET /auth/logout` - Clear stored tokens
  - `GET /auth/status` - Check authorization status
- [x] Test routes:
  - `POST /api/test/narrate` - Test narration endpoint
  - `GET /api/test/queue` - Get current queue
  - `DELETE /api/test/queue/{item_id}` - Skip queue item

### 8. CLI Updates (`src/main.py`)

- [x] `serve` command for running FastAPI server
- [x] `test` command for CLI pipeline testing (replaces default)
- [x] Configurable host/port for server

## Directory Structure (New in Phase 2)

```
src/
├── api/
│   ├── app.py              # FastAPI factory
│   ├── dependencies.py     # DI container (AppState)
│   └── routes/
│       ├── health.py       # GET /health
│       ├── twitch.py       # OAuth endpoints
│       └── test.py         # Test/debug endpoints
├── db/
│   ├── manager.py          # DatabaseManager
│   └── repositories/
│       ├── settings.py     # SettingsRepository
│       └── twitch_state.py # TwitchStateRepository
└── services/
    ├── rate_limiter.py     # RateLimiter
    ├── queue.py            # NarrationQueue
    └── twitch/
        ├── __init__.py     # Re-exports
        ├── models.py       # TypedDicts for Twitch API
        ├── auth.py         # TwitchAuthService
        ├── eventsub.py     # TwitchEventSubService
        └── rewards.py      # TwitchRewardController

migrations/
├── 001_initial.sql         # Core tables
└── 002_indexes.sql         # Performance indexes

tests/
└── test_services/
    ├── test_rate_limiter.py  # 11 tests
    └── test_queue.py         # 12 tests
```

## Usage

```bash
# Start the server
uv run python -m src.main serve --port 8000

# Test CLI pipeline (Phase 1 functionality)
uv run python -m src.main test "Hello everyone!" --user DragonSlayer

# Run all tests
uv run pytest

# Run Phase 2 tests only
uv run pytest tests/test_services/test_rate_limiter.py tests/test_services/test_queue.py
```

### OAuth Flow

1. Start server: `uv run python -m src.main serve`
2. Visit: `http://localhost:8000/auth/login`
3. Authorize on Twitch
4. Callback saves tokens to database
5. Check status: `http://localhost:8000/auth/status`

## Configuration

Required environment variables in `.env`:

```bash
# Twitch Application (from dev.twitch.tv/console)
TWITCH_CLIENT_ID=your_client_id
TWITCH_CLIENT_SECRET=your_client_secret
TWITCH_CHANNEL=your_channel_name

# LLM Provider (at least one)
GROQ_API_KEY=your_groq_key

# App
SECRET_KEY=your_secret_key
DATABASE_URL=sqlite:///data/narrator.db
```

## Test Results

```
61 tests passed in 0.44s

- test_providers/test_llm.py: 11 passed
- test_providers/test_tts.py: 17 passed
- test_services/test_pipeline.py: 10 passed
- test_services/test_queue.py: 12 passed
- test_services/test_rate_limiter.py: 11 passed
```

## Type Checking

```
Phase 2 modules: 19 source files, no issues
Full codebase: 4 pre-existing issues in Phase 1 async iterator signatures
```

## Next Steps (Phase 3)

1. WebSocket server for real-time audio/subtitle delivery
2. OBS Browser Source overlay (HTML/CSS/JS)
3. Audio playback in browser
4. Subtitle rendering with animations
5. Queue status display

## Known Limitations

- No automatic token refresh on startup (manual re-auth required if expired)
- No web UI for configuration (Phase 4)
- EventSub reconnection not fully tested
- Translation not yet integrated into queue processing
