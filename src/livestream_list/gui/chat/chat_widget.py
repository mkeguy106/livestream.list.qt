"""Single-channel chat widget with message list and input."""

import logging

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QHelpEvent, QKeyEvent, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QStyleOptionViewItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ...chat.models import ChatEmote, ChatMessage, ModerationEvent
from ...core.models import Livestream
from ...core.settings import BuiltinChatSettings
from .emote_completer import EmoteCompleter
from .message_delegate import ChatMessageDelegate
from .message_model import ChatMessageModel, MessageRole
from .user_popup import UserContextMenu

logger = logging.getLogger(__name__)


class ChatInput(QLineEdit):
    """Custom QLineEdit that routes key events to the emote completer."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._completer: EmoteCompleter | None = None

    def set_completer(self, completer: EmoteCompleter) -> None:
        """Set the emote completer to route key events to."""
        self._completer = completer

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Route navigation keys to the completer first."""
        if self._completer and self._completer.handle_key_press(event.key()):
            return
        super().keyPressEvent(event)


class ChatWidget(QWidget):
    """Widget for a single channel's chat.

    Contains a QListView for messages, an input field, and a send button.
    Handles auto-scrolling and the "new messages" indicator.
    """

    message_sent = Signal(str, str)  # channel_key, text
    popout_requested = Signal(str)  # channel_key
    settings_clicked = Signal()

    def __init__(
        self,
        channel_key: str,
        livestream: Livestream,
        settings: BuiltinChatSettings,
        authenticated: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.channel_key = channel_key
        self.livestream = livestream
        self.settings = settings
        self._authenticated = authenticated
        self._auto_scroll = True
        self._resize_timer: QTimer | None = None
        self._history_dialogs: set = set()
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

        # Enable mouse tracking for emote tooltips
        self._list_view.viewport().setMouseTracking(True)
        self._list_view.viewport().installEventFilter(self)

        # Style the list
        self._list_view.setStyleSheet("""
            QListView {
                background-color: #1a1a2e;
                border: none;
                padding: 4px;
            }
        """)

        layout.addWidget(self._list_view)

        # Connecting indicator (shown until connection is established)
        self._connecting_label = QLabel("Connecting to chat...")
        self._connecting_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self._connecting_label.setStyleSheet("""
            QLabel {
                background-color: #1a1a2e;
                color: #888;
                font-size: 13px;
            }
        """)
        self._connecting_label.setSizePolicy(
            self._connecting_label.sizePolicy().horizontalPolicy(),
            self._list_view.sizePolicy().verticalPolicy(),
        )
        layout.addWidget(self._connecting_label)
        self._list_view.hide()

        # Hype chat pinned banner (hidden by default)
        self._hype_banner = QWidget()
        self._hype_banner.setStyleSheet("""
            QWidget {
                background-color: #3a3000;
                border: 1px solid #6a5a00;
                border-radius: 4px;
            }
        """)
        hype_layout = QHBoxLayout(self._hype_banner)
        hype_layout.setContentsMargins(8, 4, 4, 4)
        hype_layout.setSpacing(6)
        self._hype_label = QLabel()
        self._hype_label.setStyleSheet("color: #ffd700; font-size: 11px; border: none;")
        self._hype_label.setWordWrap(True)
        hype_layout.addWidget(self._hype_label, 1)
        hype_dismiss = QPushButton("\u2715")
        hype_dismiss.setFixedSize(20, 20)
        hype_dismiss.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #aaa;
                border: none;
                font-size: 12px;
            }
            QPushButton:hover { color: #fff; }
        """)
        hype_dismiss.clicked.connect(self._dismiss_hype_banner)
        hype_layout.addWidget(hype_dismiss)
        self._hype_banner.hide()
        layout.addWidget(self._hype_banner)

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

        self._input = ChatInput()
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

        self._settings_button = QPushButton("\u2699")
        self._settings_button.setFixedSize(28, 28)
        self._settings_button.setToolTip("Chat settings")
        self._settings_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #aaa;
                border: none;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #2a2a4a;
                color: #eee;
            }
        """)
        self._settings_button.clicked.connect(self.settings_clicked.emit)
        input_layout.addWidget(self._settings_button)

        layout.addLayout(input_layout)

        # Auth feedback banner (shown when not authenticated)
        self._auth_banner = QLabel("Not logged in \u2014 chat is read-only")
        self._auth_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._auth_banner.setStyleSheet("""
            QLabel {
                background-color: #2a1a0a;
                color: #f0a030;
                border: 1px solid #503010;
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }
        """)
        self._auth_banner.hide()
        layout.addWidget(self._auth_banner)

        # Emote autocomplete
        self._completer = EmoteCompleter(self._input, parent=self)
        self._input.set_completer(self._completer)

        # Auth gating
        self.set_authenticated(self._authenticated)

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

        # Show hype chat banner for paid pinned messages
        for msg in filtered:
            if msg.is_hype_chat:
                amount_str = f"{msg.hype_chat_currency} {msg.hype_chat_amount}"
                self._hype_label.setText(
                    f"<b>{msg.user.display_name}</b> \u2014 {amount_str}: {msg.text}"
                )
                self._hype_banner.show()

        was_at_bottom = self._is_at_bottom()
        self._model.add_messages(filtered)

        # Forward to any open user history dialogs
        if self._history_dialogs:
            for dialog in list(self._history_dialogs):
                dialog.add_messages(filtered)

        if was_at_bottom or self._auto_scroll:
            self._scroll_to_bottom()
        else:
            self._new_msg_button.show()

    def apply_moderation(self, event: ModerationEvent) -> None:
        """Apply a moderation event to the message list."""
        self._model.apply_moderation(event)

    def set_connected(self) -> None:
        """Hide the connecting indicator and show the message list."""
        self._connecting_label.hide()
        self._list_view.show()

    def set_authenticated(self, state: bool) -> None:
        """Enable or disable the input based on authentication state."""
        self._authenticated = state
        self._input.setEnabled(state)
        self._send_button.setEnabled(state)
        if state:
            self._input.setPlaceholderText("Send a message...")
            self._auth_banner.hide()
        else:
            platform_name = self.livestream.channel.platform.value.title()
            self._input.setPlaceholderText(f"Log in to {platform_name} to chat")
            self._auth_banner.setText(
                f"Not logged in \u2014 {platform_name} chat is read-only"
            )
            self._auth_banner.show()

    def show_error(self, message: str) -> None:
        """Show an error message in the auth banner."""
        self._auth_banner.setText(message)
        self._auth_banner.show()

    def set_emote_cache(self, cache: dict) -> None:
        """Set the shared emote cache on the delegate and completer."""
        self._delegate.set_emote_cache(cache)
        self._completer.set_emote_cache(cache)

    def set_emote_map(self, emote_map: dict[str, ChatEmote]) -> None:
        """Set the emote map for autocomplete."""
        self._completer.set_emotes(emote_map)

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
        # Debounce full relayout to avoid spam during drag-resize
        if self._resize_timer is None:
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._on_resize_debounced)
        self._resize_timer.start(30)

    def _on_resize_debounced(self) -> None:
        """Force full relayout after resize settles."""
        self._model.layoutChanged.emit()

    def _on_scroll_changed(self, value: int) -> None:
        """Track scroll position for auto-scroll behavior."""
        scrollbar = self._list_view.verticalScrollBar()
        if value >= scrollbar.maximum() - 10:
            self._auto_scroll = True
            self._new_msg_button.hide()
        else:
            self._auto_scroll = False

    def _dismiss_hype_banner(self) -> None:
        """Dismiss the hype chat pinned banner."""
        self._hype_banner.hide()

    def eventFilter(self, obj, event):  # noqa: N802
        """Handle tooltip and click events on the list view viewport."""
        if obj is not self._list_view.viewport():
            return super().eventFilter(obj, event)

        # Username click → show user's chat history
        if event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                index = self._list_view.indexAt(event.pos())
                if index.isValid():
                    message = index.data(MessageRole)
                    if message and isinstance(message, ChatMessage):
                        option = QStyleOptionViewItem()
                        self._list_view.initViewItemOption(option)
                        option.rect = self._list_view.visualRect(index)
                        name_rect = self._delegate._get_username_rect(option, message)
                        if name_rect.isValid() and name_rect.contains(event.pos()):
                            self._show_user_history(message.user)
                            return True

        if isinstance(event, QHelpEvent):
            index = self._list_view.indexAt(event.pos())
            if index.isValid():
                message = index.data(MessageRole)
                if message and isinstance(message, ChatMessage):
                    option = QStyleOptionViewItem()
                    self._list_view.initViewItemOption(option)
                    option.rect = self._list_view.visualRect(index)
                    viewport = self._list_view.viewport()
                    tip_pos = event.globalPos()

                    # Check badges
                    badge = self._delegate._get_badge_at_position(
                        event.pos(), option, message
                    )
                    if badge:
                        QToolTip.showText(tip_pos, badge.name, viewport)
                        return True

                    # Check emotes
                    if message.emote_positions:
                        emote = self._delegate._get_emote_at_position(
                            event.pos(), option, message
                        )
                        if emote:
                            providers = {
                                "twitch": "Twitch", "kick": "Kick",
                                "7tv": "7TV", "bttv": "BTTV", "ffz": "FFZ",
                            }
                            provider = providers.get(emote.provider, emote.provider)
                            QToolTip.showText(
                                tip_pos,
                                f"{emote.name}\n({provider})",
                                viewport,
                            )
                            return True
            QToolTip.hideText()
            return True
        return super().eventFilter(obj, event)

    def _show_user_history(self, user) -> None:
        """Show a popup with all messages from this user in the current chat."""
        from ...chat.models import ChatUser

        if not isinstance(user, ChatUser):
            return

        # Collect all messages from this user
        user_messages = [
            msg for msg in self._model._messages
            if msg.user.id == user.id and msg.user.platform == user.platform
        ]

        if not user_messages:
            return

        dialog = UserHistoryDialog(
            user=user,
            messages=user_messages,
            settings=self.settings,
            emote_cache=self._delegate._emote_cache,
            parent=self,
        )
        # Non-modal: main window stays interactive, multiple dialogs allowed
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: self._history_dialogs.discard(dialog))
        self._history_dialogs.add(dialog)
        dialog.show()


class UserHistoryDialog(QDialog):
    """Dialog showing all messages from a specific user in the current chat session."""

    def __init__(
        self,
        user,
        messages: list[ChatMessage],
        settings: BuiltinChatSettings,
        emote_cache: dict,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint
        )
        self._user_id = user.id
        self._user_platform = user.platform
        self.setWindowTitle(f"Chat History - {user.display_name}")
        self.setMinimumSize(400, 300)
        self.resize(450, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        self._header = QLabel(f"  {user.display_name} — {len(messages)} messages")
        self._header.setStyleSheet("""
            QLabel {
                background-color: #16213e;
                color: #eee;
                padding: 8px;
                font-weight: bold;
                font-size: 13px;
            }
        """)
        layout.addWidget(self._header)
        self._display_name = user.display_name

        # Message list using the same delegate
        self._model = ChatMessageModel(max_messages=5000, parent=self)
        delegate = ChatMessageDelegate(settings, parent=self)
        delegate.set_emote_cache(emote_cache)

        self._list_view = QListView()
        self._list_view.setModel(self._model)
        self._list_view.setItemDelegate(delegate)
        self._list_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self._list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_view.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self._list_view.setWordWrap(True)
        self._list_view.setUniformItemSizes(False)
        self._list_view.setSpacing(0)
        self._list_view.setStyleSheet("""
            QListView {
                background-color: #1a1a2e;
                border: none;
                padding: 4px;
            }
        """)
        layout.addWidget(self._list_view)

        # Copy shortcut (Ctrl+C)
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self._list_view)
        copy_shortcut.activated.connect(self._copy_selected_messages)

        # Populate
        self._model.add_messages(messages)

        # Scroll to bottom (most recent)
        self._list_view.scrollToBottom()

        self.setStyleSheet("""
            QDialog {
                background-color: #0f0f1a;
            }
        """)

    def add_messages(self, messages: list[ChatMessage]) -> None:
        """Add new messages from the tracked user (called by ChatWidget)."""
        user_msgs = [
            msg for msg in messages
            if msg.user.id == self._user_id and msg.user.platform == self._user_platform
        ]
        if not user_msgs:
            return

        was_at_bottom = self._is_at_bottom()
        self._model.add_messages(user_msgs)

        # Update header count
        count = self._model.rowCount()
        self._header.setText(f"  {self._display_name} — {count} messages")

        if was_at_bottom:
            self._list_view.scrollToBottom()

    def _is_at_bottom(self) -> bool:
        """Check if the view is scrolled to the bottom."""
        scrollbar = self._list_view.verticalScrollBar()
        return scrollbar.value() >= scrollbar.maximum() - 10

    def _copy_selected_messages(self) -> None:
        """Copy selected messages to clipboard as text."""
        indexes = self._list_view.selectionModel().selectedIndexes()
        if not indexes:
            return

        indexes.sort(key=lambda idx: idx.row())

        lines: list[str] = []
        for index in indexes:
            message = index.data(MessageRole)
            if not message or not isinstance(message, ChatMessage):
                continue
            name = message.user.display_name
            if message.is_action:
                lines.append(f"{name} {message.text}")
            else:
                lines.append(f"{name}: {message.text}")

        if lines:
            clipboard = QApplication.clipboard()
            clipboard.setText("\n".join(lines))
