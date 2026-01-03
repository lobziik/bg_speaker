"""WebSocket message types for overlay communication.

These TypedDicts define the structure of messages sent from the server
to the OBS overlay client via WebSocket.
"""

from typing import Literal, TypedDict


class NarrationStartMessage(TypedDict):
    """Sent when narration processing begins.

    The overlay should prepare to display subtitles and play audio.

    Attributes:
        type: Message type identifier.
        id: Unique narration ID for correlating messages.
        user: Twitch username who triggered the narration.
        text: Formatted narrator text for subtitles.
        timestamp: Unix timestamp in milliseconds.
    """

    type: Literal["narration_start"]
    id: str
    user: str
    text: str
    timestamp: int


class AudioDataMessage(TypedDict):
    """Sent with the complete audio data for playback.

    Audio is sent as base64-encoded WAV data.

    Attributes:
        type: Message type identifier.
        id: Narration ID matching NarrationStartMessage.
        data: Base64-encoded WAV audio data.
        duration_ms: Audio duration in milliseconds.
        format: Audio format (currently only "wav").
    """

    type: Literal["audio_data"]
    id: str
    data: str
    duration_ms: int
    format: Literal["wav"]


class NarrationEndMessage(TypedDict):
    """Sent when narration playback should end.

    Overlay should hide subtitles and clean up state.

    Attributes:
        type: Message type identifier.
        id: Narration ID matching NarrationStartMessage.
        duration_ms: Total duration in milliseconds.
    """

    type: Literal["narration_end"]
    id: str
    duration_ms: int


class NarrationErrorMessage(TypedDict):
    """Sent when narration processing fails.

    Overlay may display error or simply ignore.

    Attributes:
        type: Message type identifier.
        id: Narration ID if available.
        error: Human-readable error message.
        code: Error code for programmatic handling.
    """

    type: Literal["narration_error"]
    id: str
    error: str
    code: str


class QueueItemData(TypedDict):
    """Queue item data structure for UI updates.

    Attributes:
        id: Queue item ID.
        user: Username who submitted.
        message: Original message text.
        position: Position in queue (1-indexed).
        priority: Priority level (0=normal, 1=VIP).
    """

    id: str
    user: str
    message: str
    position: int
    priority: int


class QueueUpdateMessage(TypedDict):
    """Sent when queue state changes.

    Provides current queue items for overlay/UI display.

    Attributes:
        type: Message type identifier.
        event: Type of queue event that triggered update.
        items: Current queue items.
        queue_length: Total items in queue.
    """

    type: Literal["queue_update"]
    event: str
    items: list[QueueItemData]
    queue_length: int


class RateLimitStatusMessage(TypedDict):
    """Sent periodically to indicate TTS availability.

    Overlay can use this to show rate limit status.

    Attributes:
        type: Message type identifier.
        tts_available: Whether TTS is currently available.
        tts_available_in_seconds: Seconds until TTS available (if limited).
        queue_length: Current queue length.
    """

    type: Literal["rate_limit_status"]
    tts_available: bool
    tts_available_in_seconds: float | None
    queue_length: int


class ConnectionAckMessage(TypedDict):
    """Sent on successful WebSocket connection.

    Confirms connection and provides initial state.

    Attributes:
        type: Message type identifier.
        connected: Always True.
        queue_length: Current queue length.
        tts_available: Whether TTS is currently available.
    """

    type: Literal["connection_ack"]
    connected: Literal[True]
    queue_length: int
    tts_available: bool


class PingMessage(TypedDict):
    """Keep-alive ping message.

    Clients should respond with a pong.

    Attributes:
        type: Message type identifier.
        timestamp: Server timestamp in milliseconds.
    """

    type: Literal["ping"]
    timestamp: int


# Union type of all server messages
ServerMessage = (
    NarrationStartMessage
    | AudioDataMessage
    | NarrationEndMessage
    | NarrationErrorMessage
    | QueueUpdateMessage
    | RateLimitStatusMessage
    | ConnectionAckMessage
    | PingMessage
)


# Client message types (for type checking received messages)
class ClientPongMessage(TypedDict):
    """Client pong response to ping.

    Attributes:
        type: Message type identifier.
        timestamp: Original ping timestamp.
    """

    type: Literal["pong"]
    timestamp: int


class ClientSubscribeMessage(TypedDict):
    """Client subscription request for specific events.

    Attributes:
        type: Message type identifier.
        events: List of event types to subscribe to.
    """

    type: Literal["subscribe"]
    events: list[str]


ClientMessage = ClientPongMessage | ClientSubscribeMessage
