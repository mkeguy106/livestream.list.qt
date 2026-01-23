"""Single-channel chat widget with message list and input."""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...chat.models import ChatMessage, ModerationEvent
from ...core.models import Livestream
from ...core.settings import BuiltinChatSettings
from .message_delegate import ChatMessageDelegate
from .message_model import ChatMessageModel, MessageRole
from .user_popup import UserContextMenu

logger = logging.getLogger(__name__)


class ChatWidget(QWidget):
    """Widget for a single channel's chat.

    Contains a QListView for messages, an input field, and a send button.
    Handles auto-scrolling and the "new messages" indicator.
    """

    message_sent = Signal(str, str)  # channel_key, text
    popout_requested = Signal(str)  # channel_key

    def __init__(
        self,
        channel_key: str,
        livestream: Livestream,
        settings: BuiltinChatSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.channel_key = channel_key
        self.livestream = livestream
        self.settings = settings
        self._auto_scroll = True
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the chat widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Message list
        self._model = ChatMessageModel(max_messages=self.settings.max_messages, parent=self)
        self._delegate = ChatMessageDelegate(self.settings, parent=self)

        self._list_view = QListView()
        self._list_view.setModel(self._model)
        self._list_view.setItemDelegate(self._delegate)
        self._list_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self._list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_view.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self._list_view.setWordWrap(True)
        self._list_view.setUniformItemSizes(False)
        self._list_view.setSpacing(0)

        # Style the list
        self._list_view.setStyleSheet("""
            QListView {
                background-color: #1a1a2e;
                border: none;
                padding: 4px;
            }
        """)

        layout.addWidget(self._list_view)

        # New messages indicator (hidden by default)
        self._new_msg_button = QPushButton("New messages")
        self._new_msg_button.setStyleSheet("""
            QPushButton {
                background-color: #6441a5;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #7d5bbe;
            }
        """)
        self._new_msg_button.setFixedHeight(24)
        self._new_msg_button.hide()
        self._new_msg_button.clicked.connect(self._scroll_to_bottom)
        layout.addWidget(self._new_msg_button)

        # Input area
        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(4, 4, 4, 4)
        input_layout.setSpacing(4)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Send a message...")
        self._input.setStyleSheet("""
            QLineEdit {
                background-color: #16213e;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 6px 8px;
                color: #eee;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #6441a5;
            }
        """)
        self._input.returnPressed.connect(self._on_send)
        input_layout.addWidget(self._input)

        self._send_button = QPushButton("Chat")
        self._send_button.setStyleSheet("""
            QPushButton {
                background-color: #6441a5;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #7d5bbe;
            }
            QPushButton:disabled {
                background-color: #444;
                color: #888;
            }
        """)
        self._send_button.clicked.connect(self._on_send)
        input_layout.addWidget(self._send_button)

        layout.addLayout(input_layout)

        # Context menu for user interaction
        self._list_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list_view.customContextMenuRequested.connect(self._on_context_menu)

        # Connect scroll tracking
        scrollbar = self._list_view.verticalScrollBar()
        scrollbar.valueChanged.connect(self._on_scroll_changed)

        # Copy shortcut (Ctrl+C)
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self._list_view)
        copy_shortcut.activated.connect(self._copy_selected_messages)

    def add_messages(self, messages: list[ChatMessage]) -> None:
        """Add messages to the chat.

        Filters blocked users and handles auto-scrolling.
        """
        # Filter blocked users
        blocked = set(self.settings.blocked_users)
        filtered = [msg for msg in messages if f"{msg.platform.value}:{msg.user.id}" not in blocked]

        if not filtered:
            return

        was_at_bottom = self._is_at_bottom()
        self._model.add_messages(filtered)

        if was_at_bottom or self._auto_scroll:
            self._scroll_to_bottom()
        else:
            self._new_msg_button.show()

    def apply_moderation(self, event: ModerationEvent) -> None:
        """Apply a moderation event to the message list."""
        self._model.apply_moderation(event)

    def set_emote_cache(self, cache: dict) -> None:
        """Set the shared emote cache on the delegate."""
        self._delegate.set_emote_cache(cache)

    def repaint_messages(self) -> None:
        """Trigger a repaint of visible messages (e.g. after emotes load)."""
        self._list_view.viewport().update()

    def clear(self) -> None:
        """Clear all messages."""
        self._model.clear_messages()

    def _on_send(self) -> None:
        """Handle send button/enter key."""
        text = self._input.text().strip()
        if text:
            self.message_sent.emit(self.channel_key, text)
            self._input.clear()

    def _is_at_bottom(self) -> bool:
        """Check if the view is scrolled to the bottom."""
        scrollbar = self._list_view.verticalScrollBar()
        return scrollbar.value() >= scrollbar.maximum() - 10

    def _scroll_to_bottom(self) -> None:
        """Scroll to the latest message."""
        self._list_view.scrollToBottom()
        self._new_msg_button.hide()
        self._auto_scroll = True

    def _copy_selected_messages(self) -> None:
        """Copy selected messages to clipboard as text."""
        indexes = self._list_view.selectionModel().selectedIndexes()
        if not indexes:
            return

        # Sort by row to preserve order
        indexes.sort(key=lambda idx: idx.row())

        lines: list[str] = []
        for index in indexes:
            message = index.data(MessageRole)
            if not message or not isinstance(message, ChatMessage):
                continue
            # Format: "username: message text" (emotes stay as text codes)
            name = message.user.display_name
            if message.is_action:
                lines.append(f"{name} {message.text}")
            else:
                lines.append(f"{name}: {message.text}")

        if lines:
            clipboard = QApplication.clipboard()
            clipboard.setText("\n".join(lines))

    def _on_context_menu(self, pos) -> None:
        """Show context menu on right-click."""
        from PySide6.QtWidgets import QMenu

        index = self._list_view.indexAt(pos)
        menu = QMenu(self)

        # Copy action (works with or without a specific message)
        selected = self._list_view.selectionModel().selectedIndexes()
        if selected:
            copy_action = menu.addAction("Copy")
            copy_action.triggered.connect(self._copy_selected_messages)
            menu.addSeparator()

        # User-specific actions if right-clicked on a message
        if index.isValid():
            message = index.data(MessageRole)
            if message and isinstance(message, ChatMessage):
                user_menu = UserContextMenu(
                    message.user, self.settings, parent=self
                )
                for action in user_menu.actions():
                    menu.addAction(action)

        if not menu.isEmpty():
            menu.exec(self._list_view.viewport().mapToGlobal(pos))

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Invalidate item layout cache on resize to prevent text overlap."""
        super().resizeEvent(event)
        self._list_view.scheduleDelayedItemsLayout()

    def _on_scroll_changed(self, value: int) -> None:
        """Track scroll position for auto-scroll behavior."""
        scrollbar = self._list_view.verticalScrollBar()
        if value >= scrollbar.maximum() - 10:
            self._auto_scroll = True
            self._new_msg_button.hide()
        else:
            self._auto_scroll = False
