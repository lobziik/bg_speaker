# Phase 3 Progress: WebSocket & Overlay

## Overview

Phase 3 adds real-time communication via WebSocket for the OBS overlay. The overlay displays subtitles with BG3-themed styling and plays synthesized audio from the narration pipeline.

**Deliverable:** Working overlay in OBS with audio + subtitles

## Completed Tasks

### 1. WebSocket Message Types (`src/api/ws_types.py`)

- [x] `NarrationStartMessage` - Sent when narration begins (with subtitle text)
- [x] `AudioDataMessage` - Base64-encoded WAV audio
- [x] `NarrationEndMessage` - Signals narration completion
- [x] `NarrationErrorMessage` - Processing error notification
- [x] `QueueUpdateMessage` - Queue state changes
- [x] `RateLimitStatusMessage` - TTS availability status
- [x] `ConnectionAckMessage` - Initial connection acknowledgment
- [x] `PingMessage` - Keep-alive pings
- [x] Client message types (`ClientPongMessage`, `ClientSubscribeMessage`)

### 2. WebSocket Connection Manager (`src/api/websocket.py`)

- [x] `WebSocketManager` class for overlay communication
- [x] `connect(websocket)` - Accept and register connection
- [x] `disconnect(websocket)` - Remove connection
- [x] `broadcast_narration_start()` - Send narration start to all clients
- [x] `broadcast_audio_data()` - Send base64-encoded audio
- [x] `broadcast_narration_end()` - Signal playback completion
- [x] `broadcast_narration_error()` - Send error notifications
- [x] `broadcast_queue_update()` - Send queue state changes
- [x] `broadcast_rate_limit_status()` - Send TTS availability
- [x] `handle_client_message()` - Process pong and subscribe messages
- [x] Keep-alive ping loop with timeout detection
- [x] Queue event handler registration (`set_services()`)
- [x] Graceful shutdown

### 3. WebSocket Endpoint (`src/api/routes/overlay.py`)

- [x] `GET /ws/overlay` - WebSocket endpoint for overlay
- [x] `GET /overlay` - Serve overlay HTML page
- [x] `GET /overlay/{file_path}` - Serve overlay static files (CSS, JS)
- [x] Security: path traversal prevention

### 4. Queue Processing with WebSocket Broadcasts (`src/services/worker.py`)

- [x] `QueueWorker` class for background processing
- [x] `start()` / `stop()` - Lifecycle management
- [x] Pulls items from queue (respects rate limits)
- [x] Processes through pipeline (LLM → TTS)
- [x] Broadcasts narration events to WebSocket clients
- [x] Handles errors with broadcasts and redemption cancellation
- [x] Fulfills/cancels Twitch redemptions

### 5. OBS Overlay (`overlay/` directory)

- [x] `index.html` - Main HTML page with BG3 fonts
- [x] `styles.css` - BG3-themed styling:
  - Parchment-style subtitle box
  - Gold decorative elements
  - IM Fell English SC and Cinzel fonts
  - Fade in/out animations
  - Queue indicator
  - Connection status indicator
  - Responsive design
- [x] `client.js` - WebSocket client:
  - Automatic connection with exponential backoff
  - Audio playback via HTML5 Audio API
  - Subtitle display with animations
  - Queue indicator updates
  - Ping/pong handling
  - Visibility change reconnection
  - Debug mode (`?debug=1`)

### 6. Reconnection Handling

- [x] Client-side exponential backoff (1s → 30s max)
- [x] Visibility change detection (reconnect when page visible)
- [x] Ping timeout detection (45s, server pings every 30s)
- [x] Server-side ping loop with client tracking

### 7. App Integration (`src/api/app.py`, `src/api/dependencies.py`)

- [x] WebSocket manager initialized in lifespan
- [x] Pipeline and worker created on startup (if GROQ_API_KEY available)
- [x] Worker started automatically
- [x] Graceful shutdown of WebSocket manager and worker
- [x] AppState updated with pipeline and worker fields

### 8. Tests

- [x] `tests/test_services/test_websocket.py` - 13 tests:
  - Connection/disconnection handling
  - Broadcast functionality
  - Queue event integration
  - Client message handling
  - Shutdown behavior
- [x] `tests/test_services/test_worker.py` - 11 tests:
  - Lifecycle (start/stop)
  - Processing flow
  - Error handling
  - Twitch rewards integration

## Directory Structure (New in Phase 3)

```
src/
├── api/
│   ├── ws_types.py           # WebSocket message types (NEW)
│   ├── websocket.py          # WebSocket manager (NEW)
│   └── routes/
│       └── overlay.py        # Overlay routes (NEW)
└── services/
    └── worker.py             # Queue worker (NEW)

overlay/                      # OBS Browser Source (NEW)
├── index.html
├── styles.css
└── client.js

tests/
└── test_services/
    ├── test_websocket.py     # 13 tests (NEW)
    └── test_worker.py        # 11 tests (NEW)
```

## Usage

### Start the Server

```bash
uv run python -m src.main serve --port 8000
```

### Configure OBS Browser Source

1. Add Browser Source to your scene
2. Set URL to: `http://localhost:8000/overlay`
3. Set width: 1920, height: 1080 (or match your canvas)
4. Enable "Control audio via OBS"
5. Set custom CSS (optional) for transparency

### Test the Overlay

```bash
# Add a test message
curl -X POST http://localhost:8000/api/test/narrate \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello everyone!", "user": "TestUser"}'

# Check queue
curl http://localhost:8000/api/test/queue
```

### Debug Mode

Add `?debug=1` to the overlay URL for verbose logging and always-visible indicators:

```
http://localhost:8000/overlay?debug=1
```

## Test Results

```
85 tests passed in 1.85s

- test_providers/test_llm.py: 11 passed
- test_providers/test_tts.py: 17 passed
- test_services/test_pipeline.py: 10 passed
- test_services/test_queue.py: 12 passed
- test_services/test_rate_limiter.py: 11 passed
- test_services/test_websocket.py: 13 passed (NEW)
- test_services/test_worker.py: 11 passed (NEW)
```

## Type Checking

Phase 3 modules pass mypy without errors. Pre-existing async iterator signature issues in Phase 1 providers remain unchanged.

## Message Flow

```
Twitch Redemption / Test API
        ↓
    NarrationQueue
        ↓
    QueueWorker.get_next()
        ↓
    NarrationPipeline.process()
        ↓
    WebSocketManager.broadcast_*()
        ↓
    OBS Overlay (via WebSocket)
        ↓
    Audio Playback + Subtitles
```

## Next Steps (Phase 4)

1. FastAPI + Jinja2 setup for Web UI
2. HTMX integration
3. Settings page with dynamic forms
4. Queue management page
5. Test/preview functionality
6. Styling

## Known Limitations

- No translation integration yet (translation step bypassed)
- No voice selection in UI (uses default Piper voice)
- Web UI for configuration not yet implemented (Phase 4)
- EventSub reconnection not fully tested
