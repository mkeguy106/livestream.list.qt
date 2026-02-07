"""Chat message model backed by a ring buffer."""

import collections

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from ...chat.models import ChatMessage, ModerationEvent

# Custom roles for accessing message data
MessageRole = Qt.ItemDataRole.UserRole + 1


class ChatMessageModel(QAbstractListModel):
    """Model for chat messages using a deque ring buffer.

    Provides efficient O(1) append and automatic eviction of old messages
    when the buffer is full.
    """

    def __init__(self, max_messages: int = 5000, parent=None):
        super().__init__(parent)
        # No maxlen on deque — we manage trimming ourselves so we can defer it
        # when the user has scrolled up (to prevent visible scroll jumping)
        self._messages: collections.deque[ChatMessage] = collections.deque()
        self._max_messages = max_messages
        self._trim_paused = False

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._messages)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._messages):
            return None

        message = self._messages[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            return f"{message.user.display_name}: {message.text}"
        elif role == MessageRole:
            return message

        return None

    def add_messages(self, messages: list[ChatMessage]) -> None:
        """Add a batch of messages to the model.

        If adding would exceed max_messages, old messages are trimmed from the
        front — unless trimming is paused (user scrolled up), in which case
        the buffer grows beyond max_messages temporarily to avoid scroll jumping.
        """
        if not messages:
            return

        new_count = len(messages)

        # Trim from front unless paused (user scrolled up)
        if not self._trim_paused:
            overflow = max(0, len(self._messages) + new_count - self._max_messages)
            if overflow > 0:
                self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
                for _ in range(overflow):
                    self._messages.popleft()
                self.endRemoveRows()

        # Insert new messages at the end
        insert_start = len(self._messages)
        self.beginInsertRows(QModelIndex(), insert_start, insert_start + new_count - 1)
        self._messages.extend(messages)
        self.endInsertRows()

    def flush_trim(self) -> None:
        """Trim deferred overflow after the user scrolls back to the bottom."""
        overflow = max(0, len(self._messages) - self._max_messages)
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            for _ in range(overflow):
                self._messages.popleft()
            self.endRemoveRows()

    def apply_moderation(self, event: ModerationEvent) -> None:
        """Apply a moderation event to existing messages."""
        if event.type == "delete" and event.target_message_id:
            for i, msg in enumerate(self._messages):
                if msg.id == event.target_message_id:
                    msg.is_moderated = True
                    idx = self.index(i)
                    self.dataChanged.emit(idx, idx)
                    break
        elif event.type in ("ban", "timeout") and event.target_user_id:
            for i, msg in enumerate(self._messages):
                if msg.user.id == event.target_user_id:
                    msg.is_moderated = True
                    idx = self.index(i)
                    self.dataChanged.emit(idx, idx)

    def clear_messages(self) -> None:
        """Remove all messages."""
        if self._messages:
            self.beginResetModel()
            self._messages.clear()
            self.endResetModel()

    def get_message(self, row: int) -> ChatMessage | None:
        """Get a message by row index."""
        if 0 <= row < len(self._messages):
            return self._messages[row]
        return None

    def get_recent_messages(self, limit: int) -> list[ChatMessage]:
        """Return up to the last N messages."""
        if limit <= 0 or not self._messages:
            return []
        if len(self._messages) <= limit:
            return list(self._messages)
        return list(self._messages)[-limit:]

    def get_all_messages(self) -> list[ChatMessage]:
        """Return all messages in the model."""
        return list(self._messages)
