"""WebSocket endpoint for OBS overlay.

Provides real-time narration events and audio delivery to the OBS Browser Source.
"""

from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.responses import FileResponse

from src.api.websocket import get_websocket_manager

router = APIRouter()

# Path to overlay static files
OVERLAY_PATH = Path(__file__).parent.parent.parent.parent / "overlay"


@router.websocket("/ws/overlay")
async def overlay_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for OBS overlay communication.

    Clients receive:
    - narration_start: When a narration begins (with subtitle text)
    - audio_data: Base64-encoded WAV audio
    - narration_end: When narration playback should end
    - narration_error: When processing fails
    - queue_update: When queue state changes
    - rate_limit_status: Periodic rate limit updates
    - ping: Keep-alive pings (respond with pong)

    Clients can send:
    - pong: Response to ping
    - subscribe: Subscribe to specific event types
    """
    manager = get_websocket_manager()

    await manager.connect(websocket)

    try:
        while True:
            # Receive and handle client messages
            data = await websocket.receive_text()
            await manager.handle_client_message(websocket, data)

    except WebSocketDisconnect:
        await manager.disconnect(websocket)


@router.get("/overlay")
async def serve_overlay() -> FileResponse:
    """Serve the OBS overlay HTML page.

    This is the main entry point for the OBS Browser Source.
    Configure OBS Browser Source URL to: http://host:port/overlay

    Returns:
        The overlay HTML file.
    """
    overlay_file = OVERLAY_PATH / "index.html"
    if not overlay_file.exists():
        # Return a minimal fallback if overlay not built yet
        return FileResponse(
            path=overlay_file,
            status_code=404,
            media_type="text/html",
        )
    return FileResponse(overlay_file, media_type="text/html")


@router.get("/overlay/{file_path:path}")
async def serve_overlay_static(file_path: str) -> FileResponse:
    """Serve overlay static files (CSS, JS).

    Args:
        file_path: Relative path to static file.

    Returns:
        The requested static file.
    """
    full_path = OVERLAY_PATH / file_path

    # Security: ensure we don't serve files outside overlay directory
    try:
        full_path.resolve().relative_to(OVERLAY_PATH.resolve())
    except ValueError:
        return FileResponse(
            path=full_path,
            status_code=404,
        )

    if not full_path.exists():
        return FileResponse(
            path=full_path,
            status_code=404,
        )

    # Determine media type
    media_type = "application/octet-stream"
    suffix = full_path.suffix.lower()
    if suffix == ".css":
        media_type = "text/css"
    elif suffix == ".js":
        media_type = "application/javascript"
    elif suffix == ".html":
        media_type = "text/html"
    elif suffix == ".json":
        media_type = "application/json"
    elif suffix == ".png":
        media_type = "image/png"
    elif suffix == ".svg":
        media_type = "image/svg+xml"

    return FileResponse(full_path, media_type=media_type)
