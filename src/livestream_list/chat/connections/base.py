"""Base chat connection abstract class."""

import asyncio
import logging
import random
from abc import abstractmethod

from PySide6.QtCore import QObject, Signal

from ..models import ChatMessage, ChatRoomState, ModerationEvent

logger = logging.getLogger(__name__)

# Exponential backoff constants for reconnection
INITIAL_RECONNECT_DELAY = 1.0  # seconds
MAX_RECONNECT_DELAY = 60.0  # seconds
RECONNECT_BACKOFF_FACTOR = 2.0
RECONNECT_JITTER = 0.1  # 10% jitter to prevent thundering herd


class BaseChatConnection(QObject):
    """Abstract base class for platform chat connections.

    Subclasses run their event loops in a QThread and emit signals
    for the main thread to consume.
    """

    # Emitted with batched messages (list of ChatMessage)
    messages_received = Signal(list)
    # Emitted on moderation events
    moderation_event = Signal(object)  # ModerationEvent
    # Room state changes (sub-only, slow mode, etc.)
    room_state_changed = Signal(object)  # ChatRoomState
    # Connection state signals
    connected = Signal()
    disconnected = Signal()
    error = Signal(str)
    # Emitted when broadcaster user ID is resolved (e.g., from ROOMSTATE room-id)
    broadcaster_id_resolved = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._channel_id: str = ""
        self._is_connected: bool = False
        self._reconnect_delay: float = INITIAL_RECONNECT_DELAY
        self._should_reconnect: bool = True  # Set to False for intentional disconnect
        self._max_reconnect_attempts: int = 10  # 0 = unlimited

    @property
    def channel_id(self) -> str:
        """The channel currently connected to."""
        return self._channel_id

    @property
    def is_connected(self) -> bool:
        """Whether the connection is active."""
        return self._is_connected

    @abstractmethod
    async def connect_to_channel(self, channel_id: str, **kwargs) -> None:
        """Connect to a channel's chat.

        Args:
            channel_id: The channel identifier.
            **kwargs: Platform-specific connection parameters.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the current channel."""

    @abstractmethod
    async def send_message(self, text: str, reply_to_msg_id: str = "") -> bool:
        """Send a message to the connected channel.

        Args:
            text: The message text to send.
            reply_to_msg_id: Optional message ID to reply to.

        Returns:
            True if the message was sent successfully.
        """

    def _set_connected(self, channel_id: str) -> None:
        """Mark as connected and emit signal."""
        self._channel_id = channel_id
        self._is_connected = True
        self.connected.emit()

    def _set_disconnected(self) -> None:
        """Mark as disconnected and emit signal."""
        self._is_connected = False
        self._channel_id = ""
        self.disconnected.emit()

    def _emit_messages(self, messages: list[ChatMessage]) -> None:
        """Emit a batch of messages."""
        if messages:
            self.messages_received.emit(messages)

    def _emit_moderation(self, event: ModerationEvent) -> None:
        """Emit a moderation event."""
        self.moderation_event.emit(event)

    def _emit_room_state(self, state: ChatRoomState) -> None:
        """Emit a room state change."""
        self.room_state_changed.emit(state)

    def _emit_error(self, message: str) -> None:
        """Emit an error."""
        logger.error(f"Chat connection error ({self.__class__.__name__}): {message}")
        self.error.emit(message)

    def _reset_backoff(self) -> None:
        """Reset reconnection backoff delay after successful connection."""
        self._reconnect_delay = INITIAL_RECONNECT_DELAY

    def _get_next_backoff(self) -> float:
        """Get the next backoff delay with jitter and update for next call."""
        delay = self._reconnect_delay
        # Add jitter (±10%)
        jitter = delay * RECONNECT_JITTER * (2 * random.random() - 1)
        delay_with_jitter = delay + jitter

        # Increase delay for next time with exponential backoff
        self._reconnect_delay = min(
            self._reconnect_delay * RECONNECT_BACKOFF_FACTOR,
            MAX_RECONNECT_DELAY,
        )

        return delay_with_jitter

    async def _sleep_with_backoff(self) -> None:
        """Sleep for the current backoff delay before reconnecting."""
        delay = self._get_next_backoff()
        logger.info(
            f"{self.__class__.__name__}: reconnecting in {delay:.1f}s "
            f"(next delay: {self._reconnect_delay:.1f}s)"
        )
        await asyncio.sleep(delay)
