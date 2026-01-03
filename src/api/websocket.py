"""WebSocket connection manager for real-time overlay communication.

Manages WebSocket connections for the OBS overlay, handling:
- Connection lifecycle (connect/disconnect)
- Broadcasting narration events to all connected clients
- Queue status updates
- Keep-alive pings
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from src.api.ws_types import (
    AudioDataMessage,
    ConnectionAckMessage,
    NarrationEndMessage,
    NarrationErrorMessage,
    NarrationStartMessage,
    PingMessage,
    QueueItemData,
    QueueUpdateMessage,
    RateLimitStatusMessage,
    ServerMessage,
)
from src.services.queue import QueueEventType, QueueItem

if TYPE_CHECKING:
    from src.services.queue import NarrationQueue
    from src.services.rate_limiter import RateLimiter

logger = structlog.get_logger()


@dataclass
class ConnectedClient:
    """Represents a connected WebSocket client.

    Attributes:
        websocket: The WebSocket connection.
        connected_at: Unix timestamp when connected.
        last_pong: Unix timestamp of last pong response.
        subscribed_events: Event types the client is subscribed to.
    """

    websocket: WebSocket
    connected_at: float = field(default_factory=time.time)
    last_pong: float = field(default_factory=time.time)
    subscribed_events: set[str] = field(default_factory=lambda: {"all"})


class WebSocketManager:
    """Manages WebSocket connections for overlay clients.

    Responsibilities:
    - Track connected clients
    - Broadcast narration events (start, audio, end, error)
    - Broadcast queue status updates
    - Send periodic keep-alive pings
    - Clean up disconnected clients

    Usage:
        manager = WebSocketManager()
        await manager.connect(websocket)  # Called by route handler
        await manager.broadcast_narration_start(id, user, text)
        # ... later
        await manager.disconnect(websocket)
    """

    def __init__(
        self,
        ping_interval_seconds: float = 30.0,
        ping_timeout_seconds: float = 10.0,
    ) -> None:
        """Initialize the WebSocket manager.

        Args:
            ping_interval_seconds: Interval between keep-alive pings.
            ping_timeout_seconds: Time to wait for pong before considering disconnected.
        """
        self._clients: dict[WebSocket, ConnectedClient] = {}
        self._lock = asyncio.Lock()
        self._ping_interval = ping_interval_seconds
        self._ping_timeout = ping_timeout_seconds
        self._ping_task: asyncio.Task[None] | None = None
        self._queue: NarrationQueue | None = None
        self._rate_limiter: RateLimiter | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()

    @property
    def client_count(self) -> int:
        """Get number of connected clients."""
        return len(self._clients)

    def set_services(
        self,
        queue: NarrationQueue,
        rate_limiter: RateLimiter,
    ) -> None:
        """Set queue and rate limiter for status broadcasts.

        Called during app initialization.

        Args:
            queue: The narration queue.
            rate_limiter: The rate limiter.
        """
        self._queue = queue
        self._rate_limiter = rate_limiter

        # Register queue event handler
        queue.on_event(self._handle_queue_event)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection.

        Sends initial connection acknowledgment with current state.

        Args:
            websocket: The WebSocket to connect.
        """
        await websocket.accept()

        client = ConnectedClient(websocket=websocket)

        async with self._lock:
            self._clients[websocket] = client

        logger.info(
            "websocket_connected",
            client_count=len(self._clients),
        )

        # Send connection acknowledgment
        ack = self._create_connection_ack()
        await self._send_to_client(websocket, ack)

        # Start ping task if first client
        if len(self._clients) == 1 and self._ping_task is None:
            self._ping_task = asyncio.create_task(self._ping_loop())

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection.

        Args:
            websocket: The WebSocket to disconnect.
        """
        async with self._lock:
            if websocket in self._clients:
                del self._clients[websocket]

        logger.info(
            "websocket_disconnected",
            client_count=len(self._clients),
        )

        # Stop ping task if no clients
        if len(self._clients) == 0 and self._ping_task is not None:
            self._ping_task.cancel()
            self._ping_task = None

    async def broadcast_narration_start(
        self,
        narration_id: str,
        user: str,
        text: str,
    ) -> None:
        """Broadcast narration start event to all clients.

        Args:
            narration_id: Unique narration ID.
            user: Username who triggered narration.
            text: Formatted narrator text.
        """
        message: NarrationStartMessage = {
            "type": "narration_start",
            "id": narration_id,
            "user": user,
            "text": text,
            "timestamp": int(time.time() * 1000),
        }
        await self._broadcast(message)

    async def broadcast_audio_data(
        self,
        narration_id: str,
        audio_data: bytes,
        duration_ms: int,
    ) -> None:
        """Broadcast audio data to all clients.

        Args:
            narration_id: Narration ID matching start message.
            audio_data: WAV audio bytes.
            duration_ms: Audio duration in milliseconds.
        """
        # Encode audio as base64 for JSON transmission
        audio_base64 = base64.b64encode(audio_data).decode("ascii")

        message: AudioDataMessage = {
            "type": "audio_data",
            "id": narration_id,
            "data": audio_base64,
            "duration_ms": duration_ms,
            "format": "wav",
        }
        await self._broadcast(message)

    async def broadcast_narration_end(
        self,
        narration_id: str,
        duration_ms: int,
    ) -> None:
        """Broadcast narration end event to all clients.

        Args:
            narration_id: Narration ID matching start message.
            duration_ms: Total duration in milliseconds.
        """
        message: NarrationEndMessage = {
            "type": "narration_end",
            "id": narration_id,
            "duration_ms": duration_ms,
        }
        await self._broadcast(message)

    async def broadcast_narration_error(
        self,
        narration_id: str,
        error: str,
        code: str = "processing_error",
    ) -> None:
        """Broadcast narration error to all clients.

        Args:
            narration_id: Narration ID if available.
            error: Human-readable error message.
            code: Error code for programmatic handling.
        """
        message: NarrationErrorMessage = {
            "type": "narration_error",
            "id": narration_id,
            "error": error,
            "code": code,
        }
        await self._broadcast(message)

    async def broadcast_queue_update(
        self,
        event: QueueEventType,
        items: list[QueueItem],
    ) -> None:
        """Broadcast queue update to all clients.

        Args:
            event: Queue event that triggered update.
            items: Current queue items.
        """
        item_data: list[QueueItemData] = []
        for i, item in enumerate(items):
            queue_item: QueueItemData = {
                "id": item.id,
                "user": item.user,
                "message": item.message,
                "position": i + 1,
                "priority": item.priority,
            }
            item_data.append(queue_item)

        message: QueueUpdateMessage = {
            "type": "queue_update",
            "event": event.value,
            "items": item_data,
            "queue_length": len(items),
        }
        await self._broadcast(message)

    async def broadcast_rate_limit_status(self) -> None:
        """Broadcast current rate limit status to all clients."""
        if not self._rate_limiter or not self._queue:
            return

        status = self._rate_limiter.get_status()
        items = await self._queue.get_items()

        message: RateLimitStatusMessage = {
            "type": "rate_limit_status",
            "tts_available": status.tts_available,
            "tts_available_in_seconds": status.tts_available_in_seconds,
            "queue_length": len(items),
        }
        await self._broadcast(message)

    async def handle_client_message(
        self,
        websocket: WebSocket,
        data: str,
    ) -> None:
        """Handle incoming message from client.

        Currently handles:
        - pong: Updates last pong timestamp
        - subscribe: Updates client subscription

        Args:
            websocket: The client WebSocket.
            data: Raw JSON message string.
        """
        try:
            message = json.loads(data)
            msg_type = message.get("type")

            if msg_type == "pong":
                async with self._lock:
                    if websocket in self._clients:
                        self._clients[websocket].last_pong = time.time()

            elif msg_type == "subscribe":
                events = message.get("events", ["all"])
                async with self._lock:
                    if websocket in self._clients:
                        self._clients[websocket].subscribed_events = set(events)

        except json.JSONDecodeError:
            logger.warning("websocket_invalid_json", data=data[:100])

    async def shutdown(self) -> None:
        """Gracefully shutdown all connections."""
        if self._ping_task:
            self._ping_task.cancel()
            self._ping_task = None

        async with self._lock:
            for websocket in list(self._clients.keys()):
                with contextlib.suppress(Exception):
                    await websocket.close()
            self._clients.clear()

        logger.info("websocket_manager_shutdown")

    def _create_connection_ack(self) -> ConnectionAckMessage:
        """Create connection acknowledgment message."""
        tts_available = True
        queue_length = 0

        if self._rate_limiter:
            status = self._rate_limiter.get_status()
            tts_available = status.tts_available

        if self._queue:
            queue_length = len(self._queue)

        return {
            "type": "connection_ack",
            "connected": True,
            "queue_length": queue_length,
            "tts_available": tts_available,
        }

    async def _broadcast(self, message: ServerMessage) -> None:
        """Broadcast message to all connected clients.

        Removes clients that fail to receive.

        Args:
            message: Message to broadcast.
        """
        disconnected: list[WebSocket] = []

        async with self._lock:
            clients = list(self._clients.items())

        for websocket, _client in clients:
            if not await self._send_to_client(websocket, message):
                disconnected.append(websocket)

        # Clean up disconnected clients
        for websocket in disconnected:
            await self.disconnect(websocket)

    async def _send_to_client(
        self,
        websocket: WebSocket,
        message: ServerMessage,
    ) -> bool:
        """Send message to a single client.

        Args:
            websocket: Target client.
            message: Message to send.

        Returns:
            True if sent successfully, False if failed.
        """
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json(message)
                return True
            return False
        except WebSocketDisconnect:
            return False
        except Exception as e:
            logger.warning(
                "websocket_send_error",
                error=str(e),
            )
            return False

    async def _ping_loop(self) -> None:
        """Background task to send keep-alive pings."""
        while True:
            try:
                await asyncio.sleep(self._ping_interval)
                await self._send_pings()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("websocket_ping_error", error=str(e))

    async def _send_pings(self) -> None:
        """Send ping to all clients and check for timeouts."""
        now = time.time()
        timed_out: list[WebSocket] = []

        ping: PingMessage = {
            "type": "ping",
            "timestamp": int(now * 1000),
        }

        async with self._lock:
            clients = list(self._clients.items())

        for websocket, client in clients:
            # Check if client timed out
            if now - client.last_pong > self._ping_interval + self._ping_timeout:
                timed_out.append(websocket)
                continue

            # Send ping
            if not await self._send_to_client(websocket, ping):
                timed_out.append(websocket)

        # Disconnect timed-out clients
        for websocket in timed_out:
            logger.info("websocket_ping_timeout")
            await self.disconnect(websocket)

    async def _handle_queue_event(
        self,
        event_type: QueueEventType,
        _item: QueueItem | None,
    ) -> None:
        """Handle queue events and broadcast to clients.

        Registered as handler with NarrationQueue.

        Note: This handler must NOT call queue.get_items() because it's
        called while the queue lock is held, which would cause a deadlock.
        Instead, we schedule the broadcast to run after the lock is released.

        Args:
            event_type: Type of queue event.
            _item: The queue item involved (may be None, unused).
        """
        if not self._queue:
            return

        # Schedule the broadcast to avoid deadlock (event is emitted with lock held)
        task = asyncio.create_task(self._deferred_queue_broadcast(event_type))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _deferred_queue_broadcast(self, event_type: QueueEventType) -> None:
        """Broadcast queue update after a short delay to avoid lock contention.

        Args:
            event_type: Type of queue event that triggered the broadcast.
        """
        # Small yield to ensure the queue lock is released
        await asyncio.sleep(0)

        if not self._queue:
            return

        try:
            items = await self._queue.get_items()
            await self.broadcast_queue_update(event_type, items)
        except Exception as e:
            logger.error("deferred_queue_broadcast_error", error=str(e))


# Global WebSocket manager instance
ws_manager = WebSocketManager()


def get_websocket_manager() -> WebSocketManager:
    """Get the global WebSocket manager instance.

    Returns:
        The singleton WebSocket manager.
    """
    return ws_manager
