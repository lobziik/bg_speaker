# Phase 4 Progress: Web UI

## Overview

Phase 4 adds a functional web UI for configuration and monitoring of the BG3 Narrator Bot. Built with FastAPI + Jinja2 and HTMX for interactivity without heavy JavaScript. Styled with a BG3-themed dark parchment design.

**Deliverable:** Functional web dashboard with settings, queue management, testing, and logs

## Completed Tasks

### 1. Static Assets & Theme

- [x] `src/static/js/htmx.min.js` - HTMX 2.0.4 bundled locally
- [x] `src/static/js/app.js` - WebSocket integration for live updates
- [x] `src/static/css/main.css` - BG3 theme:
  - Dark parchment background (`rgba(20, 15, 10, 0.98)`)
  - Gold accents (`#daa520`)
  - Parchment text (`#f4e4bc`)
  - IM Fell English SC and Cinzel fonts
  - Responsive sidebar layout
- [x] `src/static/css/components.css` - Reusable component styles:
  - Forms, inputs, buttons
  - Cards, tables, badges
  - Queue items, log entries
  - Toast notifications

### 2. Base Template & Layout

- [x] `src/templates/base.html` - Base layout:
  - Sidebar navigation (Dashboard, Settings, Queue, Test, Logs)
  - Header with connection status
  - Content area with flash messages
  - Toast notification container
  - Google Fonts for BG3 styling

### 3. Dashboard Page (`/`)

- [x] `src/views/dashboard.py` - Dashboard routes:
  - `GET /` - Main dashboard page
  - `POST /worker/toggle` - Start/stop queue worker
  - `GET /partials/stats-cards` - Stats cards refresh
  - `GET /partials/rate-limit-status` - Rate limit countdown
- [x] `src/templates/pages/dashboard.html` - Dashboard view:
  - Queue length card
  - TTS availability countdown
  - Worker status with toggle button
  - Pipeline availability indicator
- [x] `src/templates/partials/stats_cards.html` - Stats cards partial
- [x] `src/templates/partials/rate_limit_status.html` - Rate limit partial
- [x] `src/templates/partials/worker_status.html` - Worker status partial

### 4. Settings Page (`/settings`)

- [x] `src/views/settings.py` - Settings routes:
  - `GET /settings` - Settings page with all sections
  - `POST /settings/language` - Save language settings
  - `POST /settings/tts_voice` - Save TTS voice settings (per-language voice selection)
  - `POST /settings/narrator` - Save narrator settings
  - `POST /settings/queue` - Save queue settings
  - `POST /settings/overlay` - Save overlay settings
  - `POST /settings/reward` - Save Twitch reward settings
- [x] `src/templates/pages/settings.html` - Settings view:
  - Language settings (source, narrator, subtitle languages)
  - TTS Voice settings (per-language voice selection dropdowns)
  - Narrator settings (style, custom system prompt)
  - Queue settings (max size, message limits, cooldowns, priority users)
  - Overlay settings (font, colors, animation, position)
  - Twitch reward settings (title, cost, prompt, refund options)
- [x] Toast notifications for save confirmations

### 5. Queue Page (`/queue`)

- [x] `src/views/queue.py` - Queue routes:
  - `GET /queue` - Queue management page
  - `DELETE /queue/{item_id}` - Skip individual item
  - `DELETE /queue` - Clear entire queue
  - `GET /queue/partials/list` - Queue list refresh
- [x] `src/templates/pages/queue.html` - Queue view:
  - Queue items list with user, message preview, position
  - Skip button per item (with confirmation)
  - Clear all button (with confirmation)
  - Real-time updates via WebSocket
- [x] `src/templates/partials/queue_list.html` - Queue list partial

### 6. Test Page (`/test`)

- [x] `src/views/test.py` - Test routes:
  - `GET /test` - Test narration page
  - `POST /test/narrate` - Submit test narration
- [x] `src/templates/pages/test.html` - Test view:
  - Message textarea with character counter
  - Username input
  - Narrator style dropdown
  - Submit button with loading state
  - Result display area
  - Queue status indicator

### 7. Logs Page (`/logs`)

- [x] `src/views/logs.py` - Logs routes:
  - `GET /logs` - Logs page with filtering
  - `GET /logs/partials/table` - Filtered log table refresh
- [x] `src/templates/pages/logs.html` - Logs view:
  - Filter form (user, status, date range)
  - Stats summary (total, success rate, avg latency)
  - Paginated log table
- [x] `src/templates/partials/log_table.html` - Log table partial:
  - Time, User, Message, Status columns
  - LLM/TTS/Total latency columns
  - Status badges (success, error, filtered, rate_limited)
  - Pagination controls

### 8. Narration Logging

- [x] `src/db/repositories/narration_log.py` - Log repository:
  - `log_narration()` - Insert log entry with all metrics
  - `get_recent()` - Recent entries for dashboard
  - `get_filtered()` - Paginated filtered query
  - `get_today_stats()` - Aggregated stats for today
  - `get_stats()` - Aggregated stats with filters
- [x] `NarrationLogEntry` dataclass - Single log row
- [x] `LogStats` dataclass - Aggregated statistics
- [x] `LogFilter` dataclass - Query filter parameters
- [x] `migrations/003_logs_indexes.sql` - Database indexes:
  - `idx_narration_log_created_desc`
  - `idx_narration_log_user_created`
  - `idx_narration_log_status_created`
  - `idx_narration_log_filter`

### 9. Worker Integration

- [x] `src/services/worker.py` updated:
  - Accepts `db_connection` parameter
  - `_log_narration()` method for logging
  - Logs successful narrations with latency metrics
  - Logs failed narrations with error messages
  - Non-blocking: logging failures don't break narration

### 10. App Integration

- [x] `src/api/app.py` updated:
  - Mount static files at `/static`
  - Include view routers (dashboard, settings, queue, test, logs)
  - Pass `db_connection` to QueueWorker
- [x] `src/api/dependencies.py` updated:
  - `TemplatesDep` - Jinja2Templates dependency
  - `SettingsRepoDep` - Settings repository dependency

### 11. Dynamic TTS Voice Selection

- [x] `src/providers/tts/piper.py` - Voice selection by language:
  - `LANGUAGE_DEFAULT_VOICES` mapping `LanguageCode` to default Piper voice
  - `get_voice_for_language()` returns voice ID (override or default)
  - `get_voices_for_language()` filters available voices by language
  - Multi-voice caching in `_voices` dict (lazy-loaded per voice ID)
  - `synthesize()` accepts `language` parameter for automatic voice selection
- [x] `src/providers/tts/base.py` - Protocol updated:
  - Added `language: LanguageCode | None` to `synthesize()` signature
- [x] `src/models/settings.py` - Voice settings model:
  - `TTSVoiceSettings` with `voice_overrides: dict[LanguageCode, str]`
- [x] `src/services/worker.py` - Language flow:
  - Loads `LanguageSettings` from database
  - Passes `narrator_lang` to pipeline as `target_lang`
- [x] `src/services/pipeline.py` - TTS integration:
  - Passes `language=target_lang` to TTS `synthesize()` call
- [x] `src/api/app.py` - Startup:
  - Loads `TTSVoiceSettings` and passes `voice_overrides` to `PiperTTSProvider`

### 12. WebSocket JS Integration

- [x] `src/static/js/app.js` - NarratorUI class:
  - WebSocket connection with auto-reconnect
  - Exponential backoff (1s → 30s)
  - Message type handlers:
    - `queue_update` - Triggers HTMX refresh
    - `rate_limit_status` - Updates countdown display
    - `narration_start/end/error` - Event logging
  - `htmx:afterSwap` event listener for re-initialization

## Directory Structure (New in Phase 4)

```
src/
├── static/
│   ├── css/
│   │   ├── main.css              # BG3 theme
│   │   └── components.css        # Component styles
│   └── js/
│       ├── htmx.min.js           # HTMX 2.0.4
│       └── app.js                # WebSocket integration
├── templates/
│   ├── base.html                 # Base layout
│   ├── pages/
│   │   ├── dashboard.html
│   │   ├── settings.html
│   │   ├── queue.html
│   │   ├── test.html
│   │   └── logs.html
│   └── partials/
│       ├── stats_cards.html
│       ├── rate_limit_status.html
│       ├── worker_status.html
│       ├── queue_list.html
│       └── log_table.html
├── views/
│   ├── __init__.py
│   ├── dashboard.py
│   ├── settings.py
│   ├── queue.py
│   ├── test.py
│   └── logs.py
└── db/repositories/
    └── narration_log.py          # Log repository

migrations/
└── 003_logs_indexes.sql          # Log query indexes
```

## Web UI Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Dashboard page |
| `/worker/toggle` | POST | Toggle worker start/stop |
| `/partials/stats-cards` | GET | Stats cards refresh |
| `/partials/rate-limit-status` | GET | Rate limit countdown |
| `/settings` | GET | Settings page |
| `/settings/language` | POST | Save language settings |
| `/settings/tts_voice` | POST | Save TTS voice settings (per-language) |
| `/settings/narrator` | POST | Save narrator settings |
| `/settings/queue` | POST | Save queue settings |
| `/settings/overlay` | POST | Save overlay settings |
| `/settings/reward` | POST | Save reward settings |
| `/queue` | GET | Queue management page |
| `/queue` | DELETE | Clear all queue items |
| `/queue/{id}` | DELETE | Skip queue item |
| `/queue/partials/list` | GET | Queue list refresh |
| `/test` | GET | Test narration page |
| `/test/narrate` | POST | Submit test narration |
| `/logs` | GET | Logs page with filters |
| `/logs/partials/table` | GET | Filtered log table |

## Test Results

```
96 tests passed in 1.95s

All existing tests continue to pass.
Phase 4 UI routes tested manually.
New voice selection tests added (10 tests in TestPiperLanguageVoiceSelection).
```

## Type Checking

```
mypy: Success (50 source files)
ruff: All checks passed
```

## Usage

### Access Web UI

1. Start the server: `uv run python -m src.main serve`
2. Open browser: `http://localhost:8000`
3. Navigate using sidebar

### Configure Settings

1. Go to Settings page
2. Adjust language, narrator, queue, overlay, or reward settings
3. Click Save on each section
4. Toast notification confirms save

### Monitor Queue

1. Go to Queue page
2. View pending narrations
3. Skip individual items or clear all
4. Real-time updates via WebSocket

### Test Narration

1. Go to Test page
2. Enter message and username
3. Select narrator style
4. Click "Submit Test"
5. View result and queue position

### View Logs

1. Go to Logs page
2. Filter by user, status, or date range
3. View latency metrics
4. Paginate through history

## Key Patterns

### HTMX Form Submission
```html
<form hx-post="/settings/language" hx-target="#toast-container">
```

### Live Refresh
```html
<div hx-get="/partials/rate-limit-status" hx-trigger="every 1s">
```

### WebSocket-Triggered Refresh
```html
<div id="queue-list" hx-get="/queue/partials/list" hx-trigger="refresh from:body">
```

### Toast Response Helper
```python
def _toast_response(message: str, success: bool = True) -> dict[str, Any]:
    return {"HX-Trigger": json.dumps({"showToast": {...}})}
```

## Known Limitations

- Provider settings dynamic forms not yet implemented (placeholder in settings)
- No voice preview audio in settings (selection works, but no preview playback)
- No real-time log streaming (manual refresh required)
- No authentication on web UI (local access assumed)

## Next Steps (Phase 5)

1. Dynamic provider settings forms (JSON Schema → form)
2. Voice preview audio playback
3. Real-time log streaming
4. Basic authentication for web UI
5. Mobile-responsive improvements
