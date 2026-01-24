"""Single-channel chat widget with message list and input."""

import logging

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QHelpEvent, QKeyEvent, QKeySequence, QMouseEvent, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    font_size_changed = Signal(int)  # new font size
    settings_changed = Signal()  # any chat setting toggled

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
        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._on_animation_tick)
        self._animation_frame = 0
        self._has_animated_emotes = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the chat widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Search bar (hidden by default)
        self._search_widget = QWidget()
        self._search_widget.setStyleSheet("""
            QWidget {
                background-color: #16213e;
                border-bottom: 1px solid #333;
            }
        """)
        search_layout = QHBoxLayout(self._search_widget)
        search_layout.setContentsMargins(6, 4, 6, 4)
        search_layout.setSpacing(4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search messages...")
        self._search_input.setStyleSheet("""
            QLineEdit {
                background-color: #1a1a2e;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 3px 6px;
                color: #eee;
                font-size: 12px;
            }
            QLineEdit:focus { border-color: #6441a5; }
        """)
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._search_next)
        search_layout.addWidget(self._search_input)

        self._search_count_label = QLabel("")
        self._search_count_label.setStyleSheet(
            "color: #aaa; font-size: 11px; background: transparent; min-width: 50px;"
        )
        search_layout.addWidget(self._search_count_label)

        search_btn_style = """
            QPushButton {
                background: transparent; color: #aaa; border: none;
                font-size: 14px; padding: 2px 6px;
            }
            QPushButton:hover {
                color: #fff; background: rgba(255,255,255,0.1);
                border-radius: 3px;
            }
        """
        self._search_prev_btn = QPushButton("\u25b2")
        self._search_prev_btn.setFixedSize(24, 24)
        self._search_prev_btn.setStyleSheet(search_btn_style)
        self._search_prev_btn.clicked.connect(self._search_prev)
        search_layout.addWidget(self._search_prev_btn)

        self._search_next_btn = QPushButton("\u25bc")
        self._search_next_btn.setFixedSize(24, 24)
        self._search_next_btn.setStyleSheet(search_btn_style)
        self._search_next_btn.clicked.connect(self._search_next)
        search_layout.addWidget(self._search_next_btn)

        self._search_close_btn = QPushButton("\u2715")
        self._search_close_btn.setFixedSize(24, 24)
        self._search_close_btn.setStyleSheet(search_btn_style)
        self._search_close_btn.clicked.connect(self._close_search)
        search_layout.addWidget(self._search_close_btn)

        self._search_widget.hide()
        layout.addWidget(self._search_widget)

        self._search_matches: list[int] = []
        self._search_current: int = -1

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
        self._settings_button.clicked.connect(self._show_settings_menu)
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

        # Find shortcut (Ctrl+F)
        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self._toggle_search)

    def add_messages(self, messages: list[ChatMessage]) -> None:
        """Add messages to the chat.

        Filters blocked users and handles auto-scrolling.
        """
        # Filter blocked users
        blocked = set(self.settings.blocked_users)
        filtered = [msg for msg in messages if f"{msg.platform.value}:{msg.user.id}" not in blocked]

        if not filtered:
            return

        # Hide the "Waiting for messages" indicator on first message
        if self._connecting_label.isVisible():
            self._connecting_label.hide()

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
        self._connecting_label.setText("Waiting for messages\u2026")
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

    def set_animated_cache(self, cache: dict[str, list]) -> None:
        """Set the animated frame cache on the delegate and start timer if needed."""
        self._delegate.set_animated_cache(cache)
        had_animated = self._has_animated_emotes
        self._has_animated_emotes = bool(cache)
        if self._has_animated_emotes and self.settings.animate_emotes:
            if not self._animation_timer.isActive():
                self._animation_timer.start(50)  # 20fps
        elif not self._has_animated_emotes and had_animated:
            self._animation_timer.stop()

    def _on_animation_tick(self) -> None:
        """Advance the global animation frame and repaint."""
        self._animation_frame += 1
        self._delegate.set_animation_frame(self._animation_frame)
        self._list_view.viewport().update()

    def update_animation_state(self) -> None:
        """Start or stop the animation timer based on settings."""
        if self._has_animated_emotes and self.settings.animate_emotes:
            if not self._animation_timer.isActive():
                self._animation_timer.start(50)
        else:
            self._animation_timer.stop()

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
            prefix = ""
            if self.settings.show_timestamps:
                prefix = f"[{message.timestamp.strftime('%H:%M')}] "
            name = message.user.display_name
            if message.is_action:
                lines.append(f"{prefix}{name} {message.text}")
            else:
                lines.append(f"{prefix}{name}: {message.text}")

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

    def hideEvent(self, event) -> None:  # noqa: N802
        """Stop animation timer when widget is hidden."""
        self._animation_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        """Restart animation timer when widget is shown (if applicable)."""
        super().showEvent(event)
        if self._has_animated_emotes and self.settings.animate_emotes:
            self._animation_timer.start(50)

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

    def _show_settings_menu(self) -> None:
        """Show a popup menu with quick chat toggles."""
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1a1a2e;
                color: #eee;
                border: 1px solid #333;
            }
            QMenu::item:selected {
                background-color: #2a2a4a;
            }
            QMenu::indicator:checked {
                color: #6441a5;
            }
        """)

        # Name colors toggle
        color_action = menu.addAction("Show Name Colors")
        color_action.setCheckable(True)
        color_action.setChecked(self.settings.use_platform_name_colors)
        color_action.toggled.connect(self._toggle_name_colors)

        # Timestamps toggle
        ts_action = menu.addAction("Show Timestamps")
        ts_action.setCheckable(True)
        ts_action.setChecked(self.settings.show_timestamps)
        ts_action.toggled.connect(self._toggle_timestamps)

        # Badges toggle
        badge_action = menu.addAction("Show Badges")
        badge_action.setCheckable(True)
        badge_action.setChecked(self.settings.show_badges)
        badge_action.toggled.connect(self._toggle_badges)

        # Emotes toggle
        emote_action = menu.addAction("Show Emotes")
        emote_action.setCheckable(True)
        emote_action.setChecked(self.settings.show_emotes)
        emote_action.toggled.connect(self._toggle_emotes)

        # Animate emotes toggle
        anim_action = menu.addAction("Animate Emotes")
        anim_action.setCheckable(True)
        anim_action.setChecked(self.settings.animate_emotes)
        anim_action.setEnabled(self.settings.show_emotes)
        anim_action.toggled.connect(self._toggle_animate_emotes)

        menu.addSeparator()
        more_action = menu.addAction("More Settings...")
        more_action.triggered.connect(self.settings_clicked.emit)

        # Show menu above the button
        btn = self._settings_button
        pos = btn.mapToGlobal(btn.rect().topLeft())
        pos.setY(pos.y() - menu.sizeHint().height())
        menu.exec(pos)

    def _toggle_name_colors(self, checked: bool) -> None:
        """Toggle user-defined name colors."""
        self.settings.use_platform_name_colors = checked
        self._model.layoutChanged.emit()
        self.settings_changed.emit()

    def _toggle_timestamps(self, checked: bool) -> None:
        """Toggle timestamp display."""
        self.settings.show_timestamps = checked
        self._model.layoutChanged.emit()
        self.settings_changed.emit()

    def _toggle_badges(self, checked: bool) -> None:
        """Toggle badge display."""
        self.settings.show_badges = checked
        self._model.layoutChanged.emit()
        self.settings_changed.emit()

    def _toggle_emotes(self, checked: bool) -> None:
        """Toggle emote display."""
        self.settings.show_emotes = checked
        self._model.layoutChanged.emit()
        self.settings_changed.emit()

    def _toggle_animate_emotes(self, checked: bool) -> None:
        """Toggle emote animation (static first frame when off)."""
        self.settings.animate_emotes = checked
        self._model.layoutChanged.emit()
        self.settings_changed.emit()

    def eventFilter(self, obj, event):  # noqa: N802
        """Handle tooltip, click, and wheel events on the list view viewport."""
        if obj is not self._list_view.viewport():
            return super().eventFilter(obj, event)

        # Ctrl+Wheel: adjust font size
        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    new_size = min(self.settings.font_size + 1, 30)
                elif delta < 0:
                    new_size = max(self.settings.font_size - 1, 4)
                else:
                    return True
                if new_size != self.settings.font_size:
                    self.settings.font_size = new_size
                    self._model.layoutChanged.emit()
                    self.font_size_changed.emit(new_size)
                return True

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

    def _toggle_search(self) -> None:
        """Toggle the search bar visibility."""
        if self._search_widget.isVisible():
            self._close_search()
        else:
            self._search_widget.show()
            self._search_input.setFocus()
            self._search_input.selectAll()

    def _close_search(self) -> None:
        """Hide the search bar and clear highlights."""
        self._search_widget.hide()
        self._search_input.clear()
        self._search_matches.clear()
        self._search_current = -1
        self._search_count_label.setText("")

    def _on_search_text_changed(self, text: str) -> None:
        """Update search matches when query changes."""
        self._search_matches.clear()
        self._search_current = -1

        if not text:
            self._search_count_label.setText("")
            return

        query = text.lower()
        for row in range(self._model.rowCount()):
            index = self._model.index(row, 0)
            msg = index.data(MessageRole)
            if not msg or not isinstance(msg, ChatMessage):
                continue
            if query in msg.user.display_name.lower() or query in msg.text.lower():
                self._search_matches.append(row)

        if self._search_matches:
            # Start at the most recent match
            self._search_current = len(self._search_matches) - 1
            self._scroll_to_search_match()
        else:
            self._search_count_label.setText("No matches")

    def _search_next(self) -> None:
        """Navigate to the next search match."""
        if not self._search_matches:
            return
        self._search_current = (self._search_current + 1) % len(self._search_matches)
        self._scroll_to_search_match()

    def _search_prev(self) -> None:
        """Navigate to the previous search match."""
        if not self._search_matches:
            return
        self._search_current = (self._search_current - 1) % len(self._search_matches)
        self._scroll_to_search_match()

    def _scroll_to_search_match(self) -> None:
        """Scroll to the current search match and update the count label."""
        if not self._search_matches or self._search_current < 0:
            return
        row = self._search_matches[self._search_current]
        index = self._model.index(row, 0)
        self._list_view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._list_view.setCurrentIndex(index)
        total = len(self._search_matches)
        current = self._search_current + 1
        self._search_count_label.setText(f"{current}/{total}")

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Handle Escape to close search bar."""
        if event.key() == Qt.Key.Key_Escape and self._search_widget.isVisible():
            self._close_search()
            return
        super().keyPressEvent(event)


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
        self._settings = settings
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

        # Search bar (hidden by default)
        self._search_widget = QWidget()
        self._search_widget.setStyleSheet("""
            QWidget {
                background-color: #16213e;
                border-bottom: 1px solid #333;
            }
        """)
        search_layout = QHBoxLayout(self._search_widget)
        search_layout.setContentsMargins(6, 4, 6, 4)
        search_layout.setSpacing(4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search messages...")
        self._search_input.setStyleSheet("""
            QLineEdit {
                background-color: #1a1a2e;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 3px 6px;
                color: #eee;
                font-size: 12px;
            }
            QLineEdit:focus { border-color: #6441a5; }
        """)
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._search_next)
        search_layout.addWidget(self._search_input)

        self._search_count_label = QLabel("")
        self._search_count_label.setStyleSheet(
            "color: #aaa; font-size: 11px; background: transparent;"
            " min-width: 50px;"
        )
        search_layout.addWidget(self._search_count_label)

        search_btn_style = """
            QPushButton {
                background: transparent; color: #aaa; border: none;
                font-size: 14px; padding: 2px 6px;
            }
            QPushButton:hover {
                color: #fff; background: rgba(255,255,255,0.1);
                border-radius: 3px;
            }
        """
        prev_btn = QPushButton("\u25b2")
        prev_btn.setFixedSize(24, 24)
        prev_btn.setStyleSheet(search_btn_style)
        prev_btn.clicked.connect(self._search_prev)
        search_layout.addWidget(prev_btn)

        next_btn = QPushButton("\u25bc")
        next_btn.setFixedSize(24, 24)
        next_btn.setStyleSheet(search_btn_style)
        next_btn.clicked.connect(self._search_next)
        search_layout.addWidget(next_btn)

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(search_btn_style)
        close_btn.clicked.connect(self._close_search)
        search_layout.addWidget(close_btn)

        self._search_widget.hide()
        layout.addWidget(self._search_widget)

        self._search_matches: list[int] = []
        self._search_current: int = -1

        # Message list using the same delegate
        self._model = ChatMessageModel(max_messages=5000, parent=self)
        self._delegate = ChatMessageDelegate(settings, parent=self)
        self._delegate.set_emote_cache(emote_cache)

        self._list_view = QListView()
        self._list_view.setModel(self._model)
        self._list_view.setItemDelegate(self._delegate)
        self._list_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self._list_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._list_view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._list_view.setSelectionMode(
            QListView.SelectionMode.ExtendedSelection
        )
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

        # Enable Ctrl+scroll font size on viewport
        self._list_view.viewport().installEventFilter(self)

        # Copy shortcut (Ctrl+C)
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self._list_view)
        copy_shortcut.activated.connect(self._copy_selected_messages)

        # Find shortcut (Ctrl+F)
        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self._toggle_search)

        # Resize debounce timer
        self._resize_timer: QTimer | None = None

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
            prefix = ""
            if self._settings.show_timestamps:
                prefix = f"[{message.timestamp.strftime('%H:%M')}] "
            name = message.user.display_name
            if message.is_action:
                lines.append(f"{prefix}{name} {message.text}")
            else:
                lines.append(f"{prefix}{name}: {message.text}")

        if lines:
            clipboard = QApplication.clipboard()
            clipboard.setText("\n".join(lines))

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Invalidate item layout cache on resize to prevent text overlap."""
        super().resizeEvent(event)
        self._list_view.scheduleDelayedItemsLayout()
        if self._resize_timer is None:
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._on_resize_debounced)
        self._resize_timer.start(30)

    def _on_resize_debounced(self) -> None:
        """Force full relayout after resize settles."""
        self._model.layoutChanged.emit()

    def _toggle_search(self) -> None:
        """Toggle the search bar visibility."""
        if self._search_widget.isVisible():
            self._close_search()
        else:
            self._search_widget.show()
            self._search_input.setFocus()
            self._search_input.selectAll()

    def _close_search(self) -> None:
        """Hide the search bar and clear state."""
        self._search_widget.hide()
        self._search_input.clear()
        self._search_matches.clear()
        self._search_current = -1
        self._search_count_label.setText("")

    def _on_search_text_changed(self, text: str) -> None:
        """Update search matches when query changes."""
        self._search_matches.clear()
        self._search_current = -1

        if not text:
            self._search_count_label.setText("")
            return

        query = text.lower()
        for row in range(self._model.rowCount()):
            index = self._model.index(row, 0)
            msg = index.data(MessageRole)
            if not msg or not isinstance(msg, ChatMessage):
                continue
            if query in msg.user.display_name.lower() or query in msg.text.lower():
                self._search_matches.append(row)

        if self._search_matches:
            self._search_current = len(self._search_matches) - 1
            self._scroll_to_search_match()
        else:
            self._search_count_label.setText("No matches")

    def _search_next(self) -> None:
        """Navigate to the next search match."""
        if not self._search_matches:
            return
        self._search_current = (self._search_current + 1) % len(
            self._search_matches
        )
        self._scroll_to_search_match()

    def _search_prev(self) -> None:
        """Navigate to the previous search match."""
        if not self._search_matches:
            return
        self._search_current = (self._search_current - 1) % len(
            self._search_matches
        )
        self._scroll_to_search_match()

    def _scroll_to_search_match(self) -> None:
        """Scroll to the current search match and update count."""
        if not self._search_matches or self._search_current < 0:
            return
        row = self._search_matches[self._search_current]
        index = self._model.index(row, 0)
        self._list_view.scrollTo(
            index, QAbstractItemView.ScrollHint.PositionAtCenter
        )
        self._list_view.setCurrentIndex(index)
        total = len(self._search_matches)
        current = self._search_current + 1
        self._search_count_label.setText(f"{current}/{total}")

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Handle Escape to close search bar."""
        if event.key() == Qt.Key.Key_Escape and self._search_widget.isVisible():
            self._close_search()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802
        """Handle Ctrl+Wheel for font size adjustment."""
        if obj is self._list_view.viewport():
            if (
                event.type() == QEvent.Type.Wheel
                and isinstance(event, QWheelEvent)
            ):
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta > 0:
                        new_size = min(self._settings.font_size + 1, 30)
                    elif delta < 0:
                        new_size = max(self._settings.font_size - 1, 4)
                    else:
                        return True
                    if new_size != self._settings.font_size:
                        self._settings.font_size = new_size
                        self._model.layoutChanged.emit()
                    return True
        return super().eventFilter(obj, event)
