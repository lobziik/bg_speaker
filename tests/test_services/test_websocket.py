"""Tests for WebSocket manager and overlay functionality."""

import asyncio
import json
from typing import cast

import pytest
from starlette.websockets import WebSocketState

from src.api.websocket import WebSocketManager
from src.services.queue import QueueEventType, QueueItem


class MockWebSocket:
    """Mock WebSocket for testing."""

    def __init__(self) -> None:
        self.accepted = False
        self.closed = False
        self.messages: list[dict[str, object]] = []
        self._state = WebSocketState.CONNECTING

    @property
    def client_state(self) -> WebSocketState:
        return self._state

    async def accept(self) -> None:
        self.accepted = True
        self._state = WebSocketState.CONNECTED

    async def send_json(self, data: dict[str, object]) -> None:
        if self._state != WebSocketState.CONNECTED:
            raise RuntimeError("WebSocket not connected")
        self.messages.append(data)

    async def receive_text(self) -> str:
        # Block forever for testing (simulate waiting for messages)
        await asyncio.sleep(1000)
        return "{}"

    async def close(self) -> None:
        self.closed = True
        self._state = WebSocketState.DISCONNECTED


@pytest.fixture
def ws_manager() -> WebSocketManager:
    """Create a WebSocket manager for testing."""
    return WebSocketManager(ping_interval_seconds=60.0, ping_timeout_seconds=10.0)


@pytest.fixture
def mock_websocket() -> MockWebSocket:
    """Create a mock WebSocket."""
    return MockWebSocket()


class TestWebSocketManagerConnect:
    """Tests for WebSocket connection handling."""

    @pytest.mark.asyncio
    async def test_connect_accepts_websocket(
        self, ws_manager: WebSocketManager, mock_websocket: MockWebSocket
    ) -> None:
        """Connect should accept the WebSocket."""
        await ws_manager.connect(mock_websocket)  # type: ignore[arg-type]

        assert mock_websocket.accepted is True
        assert ws_manager.client_count == 1

    @pytest.mark.asyncio
    async def test_connect_sends_ack(
        self, ws_manager: WebSocketManager, mock_websocket: MockWebSocket
    ) -> None:
        """Connect should send connection acknowledgment."""
        await ws_manager.connect(mock_websocket)  # type: ignore[arg-type]

        assert len(mock_websocket.messages) == 1
        msg = mock_websocket.messages[0]
        assert msg["type"] == "connection_ack"
        assert msg["connected"] is True

    @pytest.mark.asyncio
    async def test_connect_multiple_clients(self, ws_manager: WebSocketManager) -> None:
        """Should handle multiple concurrent connections."""
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()

        await ws_manager.connect(ws1)  # type: ignore[arg-type]
        await ws_manager.connect(ws2)  # type: ignore[arg-type]

        assert ws_manager.client_count == 2


class TestWebSocketManagerDisconnect:
    """Tests for WebSocket disconnection handling."""

    @pytest.mark.asyncio
    async def test_disconnect_removes_client(
        self, ws_manager: WebSocketManager, mock_websocket: MockWebSocket
    ) -> None:
        """Disconnect should remove client from tracking."""
        await ws_manager.connect(mock_websocket)  # type: ignore[arg-type]
        assert ws_manager.client_count == 1

        await ws_manager.disconnect(mock_websocket)  # type: ignore[arg-type]
        assert ws_manager.client_count == 0

    @pytest.mark.asyncio
    async def test_disconnect_unknown_client(
        self, ws_manager: WebSocketManager, mock_websocket: MockWebSocket
    ) -> None:
        """Disconnecting unknown client should not raise."""
        await ws_manager.disconnect(mock_websocket)  # type: ignore[arg-type]
        assert ws_manager.client_count == 0


class TestWebSocketManagerBroadcast:
    """Tests for broadcast functionality."""

    @pytest.mark.asyncio
    async def test_broadcast_narration_start(
        self, ws_manager: WebSocketManager, mock_websocket: MockWebSocket
    ) -> None:
        """Should broadcast narration start to all clients."""
        await ws_manager.connect(mock_websocket)  # type: ignore[arg-type]

        await ws_manager.broadcast_narration_start(
            narration_id="test-123",
            user="TestUser",
            text="The adventurer speaks...",
        )

        assert len(mock_websocket.messages) == 2  # ack + start
        msg = mock_websocket.messages[1]
        assert msg["type"] == "narration_start"
        assert msg["id"] == "test-123"
        assert msg["user"] == "TestUser"
        assert msg["text"] == "The adventurer speaks..."

    @pytest.mark.asyncio
    async def test_broadcast_audio_data(
        self, ws_manager: WebSocketManager, mock_websocket: MockWebSocket
    ) -> None:
        """Should broadcast audio data with base64 encoding."""
        await ws_manager.connect(mock_websocket)  # type: ignore[arg-type]

        audio_data = b"RIFF\x00\x00\x00\x00WAVEfmt "  # Minimal WAV header
        await ws_manager.broadcast_audio_data(
            narration_id="test-123",
            audio_data=audio_data,
            duration_ms=5000,
        )

        msg = mock_websocket.messages[1]  # After ack
        assert msg["type"] == "audio_data"
        assert msg["id"] == "test-123"
        assert msg["duration_ms"] == 5000
        assert msg["format"] == "wav"
        assert isinstance(msg["data"], str)  # Base64 encoded

    @pytest.mark.asyncio
    async def test_broadcast_narration_end(
        self, ws_manager: WebSocketManager, mock_websocket: MockWebSocket
    ) -> None:
        """Should broadcast narration end."""
        await ws_manager.connect(mock_websocket)  # type: ignore[arg-type]

        await ws_manager.broadcast_narration_end(
            narration_id="test-123",
            duration_ms=5000,
        )

        msg = mock_websocket.messages[1]
        assert msg["type"] == "narration_end"
        assert msg["id"] == "test-123"
        assert msg["duration_ms"] == 5000

    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_clients(self, ws_manager: WebSocketManager) -> None:
        """Should broadcast to all connected clients."""
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()

        await ws_manager.connect(ws1)  # type: ignore[arg-type]
        await ws_manager.connect(ws2)  # type: ignore[arg-type]

        await ws_manager.broadcast_narration_start(
            narration_id="test-123",
            user="TestUser",
            text="Hello!",
        )

        # Both should receive the message
        assert len(ws1.messages) == 2  # ack + start
        assert len(ws2.messages) == 2


class TestWebSocketManagerQueueEvents:
    """Tests for queue event integration."""

    @pytest.mark.asyncio
    async def test_broadcast_queue_update(
        self, ws_manager: WebSocketManager, mock_websocket: MockWebSocket
    ) -> None:
        """Should broadcast queue updates."""
        await ws_manager.connect(mock_websocket)  # type: ignore[arg-type]

        from datetime import datetime

        items = [
            QueueItem(
                id="item-1",
                user="User1",
                message="Hello",
                created_at=datetime.now(),
                priority=0,
            ),
            QueueItem(
                id="item-2",
                user="VIPUser",
                message="Hi there",
                created_at=datetime.now(),
                priority=1,
            ),
        ]

        await ws_manager.broadcast_queue_update(
            event=QueueEventType.ITEM_ADDED,
            items=items,
        )

        msg = mock_websocket.messages[1]
        assert msg["type"] == "queue_update"
        assert msg["event"] == "item_added"
        assert msg["queue_length"] == 2
        msg_items = cast("list[object]", msg["items"])
        assert len(msg_items) == 2


class TestWebSocketManagerClientMessages:
    """Tests for handling client messages."""

    @pytest.mark.asyncio
    async def test_handle_pong_message(
        self, ws_manager: WebSocketManager, mock_websocket: MockWebSocket
    ) -> None:
        """Should handle pong messages from clients."""
        await ws_manager.connect(mock_websocket)  # type: ignore[arg-type]

        pong_data = json.dumps({"type": "pong", "timestamp": 12345})
        await ws_manager.handle_client_message(mock_websocket, pong_data)  # type: ignore[arg-type]

        # Should not raise, pong updates internal state

    @pytest.mark.asyncio
    async def test_handle_invalid_json(
        self, ws_manager: WebSocketManager, mock_websocket: MockWebSocket
    ) -> None:
        """Should handle invalid JSON gracefully."""
        await ws_manager.connect(mock_websocket)  # type: ignore[arg-type]

        # Should not raise
        await ws_manager.handle_client_message(mock_websocket, "not valid json")  # type: ignore[arg-type]


class TestWebSocketManagerShutdown:
    """Tests for shutdown behavior."""

    @pytest.mark.asyncio
    async def test_shutdown_closes_all_connections(self, ws_manager: WebSocketManager) -> None:
        """Shutdown should close all connections."""
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()

        await ws_manager.connect(ws1)  # type: ignore[arg-type]
        await ws_manager.connect(ws2)  # type: ignore[arg-type]

        await ws_manager.shutdown()

        assert ws_manager.client_count == 0
        assert ws1.closed is True
        assert ws2.closed is True
