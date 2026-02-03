"""Single-channel chat widget with message list and input."""

import logging
import re
import webbrowser
import time

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QHelpEvent, QKeyEvent, QKeySequence, QMouseEvent, QShortcut, QWheelEvent
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
from ...chat.emotes.cache import EmoteCache
from ...core.models import Livestream
from ...core.settings import BuiltinChatSettings
from ..theme import ThemeManager, get_theme
from .emote_completer import EmoteCompleter
from .mention_completer import MentionCompleter
from .message_delegate import ChatMessageDelegate
from .message_model import ChatMessageModel, MessageRole
from .search_mixin import ChatSearchMixin
from .user_popup import UserContextMenu

logger = logging.getLogger(__name__)

# Regex to find !command patterns in stream titles
COMMAND_PATTERN = re.compile(r"(!\w+)")


class ClickableTitleLabel(QLabel):
    """QLabel that supports clickable !command links."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )


class DismissibleBanner(QWidget):
    """Banner widget with a label and overlay dismiss button.

    The banner dynamically resizes based on content (word wrap).
    The dismiss button floats over the content in the top-right corner.
    """

    dismissed = Signal()
    link_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None, clickable_links: bool = True):
        super().__init__(parent)
        self._clickable_external = clickable_links

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Content label - no max height, allows natural word wrap sizing
        if clickable_links:
            self._label = QLabel()
            self._label.setOpenExternalLinks(True)
        else:
            self._label = ClickableTitleLabel()
            self._label.linkActivated.connect(self.link_activated.emit)
        self._label.setWordWrap(True)
        self._label.setSizePolicy(
            self._label.sizePolicy().horizontalPolicy(),
            self._label.sizePolicy().verticalPolicy(),
        )
        layout.addWidget(self._label, 1)

        # Close button - overlay in top-right corner (not in layout)
        self._close_btn = QPushButton("×", self)
        self._close_btn.setFixedSize(20, 20)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.clicked.connect(self._on_dismiss)
        self._close_btn.raise_()  # Ensure button is on top

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Reposition close button when banner resizes."""
        super().resizeEvent(event)
        # Position button in top-right corner with small margin
        btn_x = self.width() - self._close_btn.width() - 4
        btn_y = 4
        self._close_btn.move(btn_x, btn_y)

    def _on_dismiss(self) -> None:
        """Handle dismiss button click."""
        self.hide()
        self.dismissed.emit()

    def setText(self, text: str) -> None:  # noqa: N802
        """Set the banner text."""
        self._label.setText(text)

    def setToolTip(self, text: str) -> None:  # noqa: N802
        """Set tooltip on the label."""
        self._label.setToolTip(text)

    def setStyleSheet(self, style: str) -> None:  # noqa: N802
        """Apply stylesheet to the banner."""
        # Extract colors from the QLabel style for the close button
        super().setStyleSheet(style)
        self._label.setStyleSheet(style)

    def applyBannerStyle(self, bg_color: str, text_color: str) -> None:  # noqa: N802
        """Apply banner colors."""
        # Style the label (fills the banner)
        self._label.setStyleSheet(f"""
            QLabel {{
                background-color: {bg_color};
                color: {text_color};
                font-size: 11px;
                padding: 6px 28px 6px 8px;
                border: none;
                border-bottom: 1px solid #333;
            }}
            QLabel a {{
                color: #6db3f2;
                text-decoration: none;
            }}
        """)
        # Style the overlay close button - semi-transparent, rounds nicely
        self._close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(0, 0, 0, 0.3);
                color: {text_color};
                border: none;
                border-radius: 10px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 100, 100, 0.5);
                color: #fff;
            }}
        """)


class ChatInput(QLineEdit):
    """Custom QLineEdit that routes key events to completers."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._completers: list = []

    def add_completer(self, completer) -> None:
        """Add a completer to the chain."""
        self._completers.append(completer)

    def event(self, event: QEvent) -> bool:  # noqa: N802
        """Intercept Tab key before Qt's focus navigation handles it."""
        if event.type() == QEvent.Type.KeyPress:
            key_event = event
            if key_event.key() == Qt.Key.Key_Tab:
                # Check if any completer wants to handle Tab
                for completer in self._completers:
                    if completer.handle_key_press(key_event.key()):
                        return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Route navigation keys to completers first."""
        for completer in self._completers:
            if completer.handle_key_press(event.key()):
                return
        super().keyPressEvent(event)


class ChatWidget(QWidget, ChatSearchMixin):
    """Widget for a single channel's chat.

    Contains a QListView for messages, an input field, and a send button.
    Handles auto-scrolling and the "new messages" indicator.
    Uses ChatSearchMixin for search functionality.
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
        self._gif_timer = None
        self._animation_time_ms = 0
        self._image_store: EmoteCache | None = None
        self._last_rehydrate_ts: float = 0.0
        self._socials: dict[str, str] = {}  # Stored socials for re-display on toggle
        self._title_dismissed = False  # Track if user dismissed the title banner
        self._socials_dismissed = False  # Track if user dismissed the socials banner
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the chat widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Stream title bar (shows current stream title with clickable !commands)
        self._title_banner = DismissibleBanner(clickable_links=False)
        self._title_banner.link_activated.connect(self._on_title_link_clicked)
        self._title_banner.dismissed.connect(self._on_title_dismissed)
        layout.addWidget(self._title_banner)

        # Socials banner (shows channel socials like Discord, Instagram, etc.)
        self._socials_banner = DismissibleBanner(clickable_links=True)
        self._socials_banner.dismissed.connect(self._on_socials_dismissed)
        self._socials_banner.hide()  # Hidden until socials are loaded
        layout.addWidget(self._socials_banner)

        # Apply banner styling and update title
        self._update_banner_style()
        self._update_stream_title()
        if not self.settings.show_stream_title:
            self._title_banner.hide()

        # Search bar (hidden by default)
        # Object names are used for consolidated stylesheet in apply_theme()
        self._search_widget = QWidget()
        self._search_widget.setObjectName("chat_search_widget")
        search_layout = QHBoxLayout(self._search_widget)
        search_layout.setContentsMargins(6, 4, 6, 4)
        search_layout.setSpacing(4)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("chat_search_input")
        self._search_input.setPlaceholderText("Search messages...")
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._search_next)
        search_layout.addWidget(self._search_input)

        self._search_count_label = QLabel("")
        self._search_count_label.setObjectName("chat_search_count")
        search_layout.addWidget(self._search_count_label)

        self._search_prev_btn = QPushButton("\u25b2")
        self._search_prev_btn.setObjectName("chat_search_btn")
        self._search_prev_btn.setFixedSize(24, 24)
        self._search_prev_btn.clicked.connect(self._search_prev)
        search_layout.addWidget(self._search_prev_btn)

        self._search_next_btn = QPushButton("\u25bc")
        self._search_next_btn.setObjectName("chat_search_btn")
        self._search_next_btn.setFixedSize(24, 24)
        self._search_next_btn.clicked.connect(self._search_next)
        search_layout.addWidget(self._search_next_btn)

        self._search_close_btn = QPushButton("\u2715")
        self._search_close_btn.setObjectName("chat_search_btn")
        self._search_close_btn.setFixedSize(24, 24)
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
        self._list_view.setObjectName("chat_list_view")
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

        layout.addWidget(self._list_view)

        # Connecting indicator (shown until connection is established)
        self._connecting_label = QLabel("Connecting to chat...")
        self._connecting_label.setObjectName("chat_connecting")
        self._connecting_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        )
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
        self._new_msg_button.setObjectName("chat_new_msg")
        self._new_msg_button.setFixedHeight(24)
        self._new_msg_button.hide()
        self._new_msg_button.clicked.connect(self._scroll_to_bottom)
        layout.addWidget(self._new_msg_button)

        # Input area
        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(4, 4, 4, 4)
        input_layout.setSpacing(4)

        self._input = ChatInput()
        self._input.setObjectName("chat_input")
        self._input.returnPressed.connect(self._on_send)
        input_layout.addWidget(self._input)

        self._send_button = QPushButton("Chat")
        self._send_button.setObjectName("chat_send_btn")
        self._send_button.clicked.connect(self._on_send)
        input_layout.addWidget(self._send_button)

        self._settings_button = QPushButton("\u2699")
        self._settings_button.setObjectName("chat_settings_btn")
        self._settings_button.setFixedSize(28, 28)
        self._settings_button.setToolTip("Chat settings")
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

        # Restriction banner with "Open in Browser" button (for restricted YouTube chats)
        self._restriction_banner = QWidget()
        self._restriction_banner.setStyleSheet("""
            QWidget {
                background-color: #2a1a0a;
                border: 1px solid #503010;
                border-radius: 3px;
            }
        """)
        restriction_layout = QHBoxLayout(self._restriction_banner)
        restriction_layout.setContentsMargins(8, 4, 8, 4)
        restriction_layout.setSpacing(8)
        self._restriction_label = QLabel()
        self._restriction_label.setStyleSheet(
            "color: #f0a030; font-size: 11px; background: transparent; border: none;"
        )
        restriction_layout.addWidget(self._restriction_label, 1)
        self._open_browser_btn = QPushButton("Open in Browser")
        self._open_browser_btn.setStyleSheet("""
            QPushButton {
                background-color: #6441a5;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 3px 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #7d5bbe;
            }
        """)
        self._open_browser_btn.clicked.connect(self._open_chat_in_browser)
        restriction_layout.addWidget(self._open_browser_btn)
        self._restriction_banner.hide()
        layout.addWidget(self._restriction_banner)

        # Emote autocomplete
        self._emote_completer = EmoteCompleter(self._input, parent=self)
        self._emote_completer.set_platform(self.livestream.channel.platform.value)
        self._input.add_completer(self._emote_completer)

        # Mention autocomplete
        self._mention_completer = MentionCompleter(self._input, parent=self)
        self._input.add_completer(self._mention_completer)

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

        # Note: apply_theme() is called by the parent ChatWindow after widget creation
        # to avoid duplicate theme application during construction

    def add_messages(self, messages: list[ChatMessage]) -> None:
        """Add messages to the chat.

        Filters blocked users and handles auto-scrolling.
        """
        # Filter blocked users
        blocked = set(self.settings.blocked_users)
        filtered = [msg for msg in messages if f"{msg.platform.value}:{msg.user.id}" not in blocked]

        if not filtered:
            return

        # Track usernames for @mention autocomplete
        for msg in filtered:
            self._mention_completer.add_username(msg.user.display_name)

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

        # Rehydrate animated emotes for newly visible messages
        if self.settings.animate_emotes:
            self._rehydrate_visible_animated_emotes()

    def apply_moderation(self, event: ModerationEvent) -> None:
        """Apply a moderation event to the message list."""
        self._model.apply_moderation(event)

        # Forward to any open user history dialogs
        if self._history_dialogs:
            for dialog in list(self._history_dialogs):
                dialog.apply_moderation(event)

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
            self._auth_banner.setText(f"Not logged in \u2014 {platform_name} chat is read-only")
            self._auth_banner.show()

    def show_error(self, message: str) -> None:
        """Show an error message in the appropriate banner."""
        from ...core.models import StreamPlatform

        # For YouTube restriction errors, show the banner with "Open in Browser" button
        if (
            self.livestream.channel.platform == StreamPlatform.YOUTUBE
            and "Cannot send:" in message
            and "Use browser" in message
        ):
            # Extract just the restriction message (before "Use browser")
            restriction_msg = message.split(". Use browser")[0]
            self._restriction_label.setText(restriction_msg)
            self._restriction_banner.show()
            self._auth_banner.hide()
        else:
            self._auth_banner.setText(message)
            self._auth_banner.show()
            self._restriction_banner.hide()

    def _open_chat_in_browser(self) -> None:
        """Open the YouTube chat popout in the default browser."""
        from ...core.models import StreamPlatform

        if self.livestream.channel.platform != StreamPlatform.YOUTUBE:
            return

        # Get the video ID from the livestream
        video_id = getattr(self.livestream, "video_id", None)
        if video_id:
            url = f"https://www.youtube.com/live_chat?is_popout=1&v={video_id}"
            try:
                webbrowser.open(url)
            except Exception as e:
                logger.error(f"Failed to open YouTube chat URL: {e}")

    def set_image_store(self, store: EmoteCache) -> None:
        """Set the shared image store on the delegate and completer."""
        self._image_store = store
        self._delegate.set_image_store(store)
        self._emote_completer.set_image_store(store)

    def set_emote_map(self, emote_map: dict[str, ChatEmote]) -> None:
        """Set the emote map for autocomplete."""
        self._emote_completer.set_emotes(emote_map)

    def _current_scale(self) -> float:
        """Return the current device scale factor for image selection."""
        try:
            return float(self.devicePixelRatioF())
        except Exception:
            return 1.0

    def _get_emote_image_ref(self, emote: ChatEmote):
        """Get a bound ImageRef for the given emote."""
        if not self._image_store or not emote.image_set:
            return None
        image_set = emote.image_set.bind(self._image_store)
        emote.image_set = image_set
        return image_set.get_image_or_loaded(scale=self._current_scale())

    def set_gif_timer(self, timer) -> None:
        """Attach a shared GIF timer for animation frames."""
        if self._gif_timer is not None:
            try:
                self._gif_timer.tick.disconnect(self.on_gif_tick)
            except Exception:
                pass
        self._gif_timer = timer
        self._gif_timer.tick.connect(self.on_gif_tick)

    def set_animation_enabled(self, enabled: bool) -> None:
        """Update animation enabled state."""
        self.settings.animate_emotes = enabled

    def has_animated_emotes(self) -> bool:
        return bool(self._image_store and self._image_store.animated_dict)

    def on_gif_tick(self, elapsed_ms: int) -> None:
        """Advance animation frame from shared timer."""
        if not self.settings.animate_emotes:
            return
        self._rehydrate_visible_animated_emotes()
        self._animation_time_ms = elapsed_ms
        self._delegate.set_animation_frame(self._animation_time_ms)
        self._list_view.viewport().update()

    def _rehydrate_visible_animated_emotes(self) -> None:
        """Rehydrate animated emotes for visible messages if frames were evicted."""
        if not self._image_store or not self.settings.animate_emotes:
            return

        now = time.monotonic()
        if now - self._last_rehydrate_ts < 1.0:
            return
        self._last_rehydrate_ts = now

        viewport = self._list_view.viewport()
        if not viewport:
            return

        top_index = self._list_view.indexAt(viewport.rect().topLeft())
        bottom_index = self._list_view.indexAt(viewport.rect().bottomLeft())

        if not top_index.isValid():
            return

        start_row = top_index.row()
        end_row = bottom_index.row() if bottom_index.isValid() else start_row

        cache = self._image_store
        for row in range(start_row, end_row + 1):
            msg = self._model.get_message(row)
            if not msg or not msg.emote_positions:
                continue
            for _start, _end, emote in msg.emote_positions:
                image_ref = self._get_emote_image_ref(emote)
                if not image_ref:
                    continue
                key = image_ref.key
                if cache.has_animation_data(key):
                    if key in cache.animated_dict:
                        cache.touch_animated(key)
                    else:
                        cache.request_animation_frames(key)

    def repaint_messages(self) -> None:
        """Trigger a repaint of visible messages (e.g. after emotes load)."""
        self._list_view.viewport().update()

    def invalidate_message_layout(self) -> None:
        """Force relayout after message content changes (e.g. backfilled emotes)."""
        self._delegate.invalidate_size_cache()
        self._model.layoutChanged.emit()
        self.repaint_messages()

    def get_recent_messages(self, limit: int = 300) -> list[ChatMessage]:
        """Get recent messages for backfill operations."""
        return self._model.get_recent_messages(limit)

    def get_all_messages(self) -> list[ChatMessage]:
        """Get all messages for full backfill operations."""
        return self._model.get_all_messages()

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
                user_menu = UserContextMenu(message.user, self.settings, parent=self)
                for action in user_menu.actions():
                    menu.addAction(action)

        if not menu.isEmpty():
            menu.exec(self._list_view.viewport().mapToGlobal(pos))

    def hideEvent(self, event) -> None:  # noqa: N802
        """Handle widget hidden."""
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        """Handle widget shown."""
        super().showEvent(event)

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
        self._delegate.invalidate_size_cache()
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

    def _update_banner_style(self) -> None:
        """Apply banner colors from settings (theme-aware)."""
        is_dark = ThemeManager.is_dark_mode()
        colors = self.settings.get_colors(is_dark)
        self._title_banner.applyBannerStyle(colors.banner_bg_color, colors.banner_text_color)
        self._socials_banner.applyBannerStyle(colors.banner_bg_color, colors.banner_text_color)

    def _update_stream_title(self) -> None:
        """Update the stream title banner from the livestream data."""
        title = ""
        if self.livestream and self.livestream.title:
            title = self.livestream.title
        # Show if we have a title, setting is enabled, and user hasn't dismissed
        if title and self.settings.show_stream_title and not self._title_dismissed:
            # Convert !commands to clickable links
            html_title = self._format_title_with_commands(title)
            self._title_banner.setText(html_title)
            self._title_banner.setToolTip(title)  # Full title on hover (plain text)
            self._title_banner.show()
        else:
            self._title_banner.hide()

    def _on_title_dismissed(self) -> None:
        """Handle title banner dismissal."""
        self._title_dismissed = True

    def _on_socials_dismissed(self) -> None:
        """Handle socials banner dismissal."""
        self._socials_dismissed = True

    def _format_title_with_commands(self, title: str) -> str:
        """Convert !command patterns in title to clickable links."""
        import html

        # Escape HTML entities first
        escaped = html.escape(title)
        # Replace !command patterns with links
        html_title = COMMAND_PATTERN.sub(
            r'<a href="cmd:\1">\1</a>',
            escaped,
        )
        return html_title

    def _on_title_link_clicked(self, url: str) -> None:
        """Handle clicks on !command links in the title."""
        if url.startswith("cmd:"):
            command = url[4:]  # Remove "cmd:" prefix
            self._input.setText(command)
            self._input.setFocus()

    def update_livestream(self, livestream: Livestream) -> None:
        """Update the livestream data and refresh the title."""
        self.livestream = livestream
        self._update_stream_title()

    def set_socials(self, socials: dict[str, str]) -> None:
        """Set channel socials and update the banner.

        Args:
            socials: Dict mapping platform names to URLs, e.g. {"discord": "https://..."}
        """
        # Store socials so we can re-display when setting is toggled
        self._socials = socials

        # Don't show if: no socials, setting is off, or user dismissed it
        if not socials or not self.settings.show_socials_banner or self._socials_dismissed:
            self._socials_banner.hide()
            return

        # Format socials as clickable links with icons/emojis
        social_icons = {
            "discord": "\U0001F4AC",  # Speech bubble
            "instagram": "\U0001F4F7",  # Camera
            "twitter": "\U0001F426",  # Bird
            "x": "\U0001F426",  # Bird (X/Twitter)
            "tiktok": "\U0001F3B5",  # Musical note
            "youtube": "\U0001F3AC",  # Clapper
            "facebook": "\U0001F465",  # People
            "patreon": "\U0001F49B",  # Yellow heart
            "merch": "\U0001F455",  # T-shirt
        }

        links = []
        for platform, url in socials.items():
            icon = social_icons.get(platform.lower(), "\U0001F517")  # Link emoji default
            label = platform.capitalize()
            links.append(f'{icon} <a href="{url}">{label}</a>')

        if links:
            self._socials_banner.setText("  ".join(links))
            self._socials_banner.show()
        else:
            self._socials_banner.hide()

    def update_banner_settings(self) -> None:
        """Update banner visibility and colors after settings change.

        Note: If user has dismissed a banner, re-enabling the setting will show it again
        (resets the dismissed state for that banner type).
        """
        self._update_banner_style()

        # Update title visibility - re-enabling in settings resets dismissed state
        if self.settings.show_stream_title:
            self._title_dismissed = False
            self._update_stream_title()
        else:
            self._title_banner.hide()

        # Update socials visibility - re-enabling in settings resets dismissed state
        if self.settings.show_socials_banner:
            self._socials_dismissed = False
            if self._socials:
                self.set_socials(self._socials)
        else:
            self._socials_banner.hide()

    def apply_theme(self) -> None:
        """Apply the current theme to the chat widget.

        Uses a single consolidated stylesheet with ID selectors for performance.
        This reduces ~15 individual setStyleSheet() calls to 1, significantly
        reducing layout recalculations during theme switches.
        """
        theme = get_theme()

        # Consolidated stylesheet using object name (ID) selectors
        # This is much faster than setting styles on each widget individually
        self.setStyleSheet(f"""
            /* Widget background */
            ChatWidget {{
                background-color: {theme.chat_bg};
            }}

            /* Search widget container */
            #chat_search_widget {{
                background-color: {theme.chat_input_bg};
                border-bottom: 1px solid {theme.border_light};
            }}

            /* Search input */
            #chat_search_input {{
                background-color: {theme.widget_bg};
                border: 1px solid {theme.border};
                border-radius: 3px;
                padding: 3px 6px;
                color: {theme.text_primary};
                font-size: 12px;
            }}
            #chat_search_input:focus {{
                border-color: {theme.accent};
            }}

            /* Search count label */
            #chat_search_count {{
                color: {theme.text_muted};
                font-size: 11px;
                background: transparent;
                min-width: 50px;
            }}

            /* Search buttons (prev, next, close) */
            #chat_search_btn {{
                background: transparent;
                color: {theme.text_muted};
                border: none;
                font-size: 14px;
                padding: 2px 6px;
            }}
            #chat_search_btn:hover {{
                color: {theme.text_primary};
                background: rgba(255,255,255,0.1);
                border-radius: 3px;
            }}

            /* Message list view */
            #chat_list_view {{
                background-color: {theme.chat_bg};
                border: none;
            }}

            /* Connecting indicator */
            #chat_connecting {{
                background-color: {theme.chat_bg};
                color: {theme.text_muted};
                font-size: 13px;
            }}

            /* New messages button */
            #chat_new_msg {{
                background-color: {theme.accent};
                color: white;
                border: none;
                border-radius: 12px;
                padding: 4px 12px;
                font-size: 11px;
            }}
            #chat_new_msg:hover {{
                background-color: {theme.accent_hover};
            }}

            /* Chat input field */
            #chat_input {{
                background-color: {theme.chat_input_bg};
                border: 1px solid {theme.border_light};
                border-radius: 4px;
                padding: 6px 8px;
                color: {theme.text_primary};
                font-size: 13px;
            }}
            #chat_input:focus {{
                border-color: {theme.accent};
            }}

            /* Send button */
            #chat_send_btn {{
                background-color: {theme.accent};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }}
            #chat_send_btn:hover {{
                background-color: {theme.accent_hover};
            }}
            #chat_send_btn:disabled {{
                background-color: {theme.border};
                color: {theme.text_muted};
            }}

            /* Settings button */
            #chat_settings_btn {{
                background-color: transparent;
                color: {theme.text_muted};
                border: none;
                border-radius: 4px;
                font-size: 14px;
            }}
            #chat_settings_btn:hover {{
                background-color: {theme.popup_hover};
                color: {theme.text_primary};
            }}
        """)

        # Update banners AFTER main stylesheet to prevent cascade override
        # Use settings colors (theme-aware) for banners
        is_dark = ThemeManager.is_dark_mode()
        colors = self.settings.get_colors(is_dark)
        self._title_banner.applyBannerStyle(colors.banner_bg_color, colors.banner_text_color)
        self._socials_banner.applyBannerStyle(colors.banner_bg_color, colors.banner_text_color)

        # Update delegate theme and force repaint to show new colors
        self._delegate.apply_theme()
        self._list_view.viewport().update()

        # Update completers theme
        self._emote_completer.apply_theme()
        self._mention_completer.apply_theme()

    def _show_settings_menu(self) -> None:
        """Show a popup menu with quick chat toggles."""
        from PySide6.QtWidgets import QMenu

        theme = get_theme()
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {theme.popup_bg};
                color: {theme.text_primary};
                border: 1px solid {theme.border};
            }}
            QMenu::item:selected {{
                background-color: {theme.popup_hover};
            }}
            QMenu::indicator:checked {{
                color: {theme.accent};
            }}
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

        # Username click → show user's chat history; URL click → open browser
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
                        url = self._delegate._get_url_at_position(event.pos(), option, message)
                        if url:
                            try:
                                webbrowser.open(url)
                            except Exception as e:
                                logger.error(f"Failed to open URL: {e}")
                            return True

        # Cursor changes for clickable elements (URLs, usernames)
        if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            viewport = self._list_view.viewport()
            index = self._list_view.indexAt(event.pos())
            if index.isValid():
                message = index.data(MessageRole)
                if message and isinstance(message, ChatMessage):
                    option = QStyleOptionViewItem()
                    self._list_view.initViewItemOption(option)
                    option.rect = self._list_view.visualRect(index)
                    name_rect = self._delegate._get_username_rect(option, message)
                    if name_rect.isValid() and name_rect.contains(event.pos()):
                        viewport.setCursor(Qt.CursorShape.PointingHandCursor)
                        return False
                    url = self._delegate._get_url_at_position(event.pos(), option, message)
                    if url:
                        viewport.setCursor(Qt.CursorShape.PointingHandCursor)
                        return False
            viewport.setCursor(Qt.CursorShape.ArrowCursor)
            return False

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
                    badge = self._delegate._get_badge_at_position(event.pos(), option, message)
                    if badge:
                        QToolTip.showText(tip_pos, badge.name, viewport)
                        return True

                    # Check emotes
                    if message.emote_positions:
                        emote = self._delegate._get_emote_at_position(event.pos(), option, message)
                        if emote:
                            providers = {
                                "twitch": "Twitch",
                                "kick": "Kick",
                                "7tv": "7TV",
                                "bttv": "BTTV",
                                "ffz": "FFZ",
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
            msg
            for msg in self._model._messages
            if msg.user.id == user.id and msg.user.platform == user.platform
        ]

        if not user_messages:
            return

        dialog = UserHistoryDialog(
            user=user,
            messages=user_messages,
            settings=self.settings,
            image_store=self._image_store,
            parent=self,
        )
        # Non-modal: main window stays interactive, multiple dialogs allowed
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: self._history_dialogs.discard(dialog))
        self._history_dialogs.add(dialog)
        dialog.show()

    # Search methods provided by ChatSearchMixin:
    # _toggle_search, _close_search, _on_search_text_changed,
    # _search_next, _search_prev, _scroll_to_search_match

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Handle Escape to close search bar."""
        if self._handle_search_key_press(event.key()):
            return
        super().keyPressEvent(event)


class UserHistoryDialog(QDialog, ChatSearchMixin):
    """Dialog showing all messages from a specific user in the current chat session."""

    def __init__(
        self,
        user,
        messages: list[ChatMessage],
        settings: BuiltinChatSettings,
        image_store: EmoteCache | None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)
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
        layout.addWidget(self._header)
        self._display_name = user.display_name

        # Search bar (hidden by default)
        self._search_widget = QWidget()
        search_layout = QHBoxLayout(self._search_widget)
        search_layout.setContentsMargins(6, 4, 6, 4)
        search_layout.setSpacing(4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search messages...")
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._search_next)
        search_layout.addWidget(self._search_input)

        self._search_count_label = QLabel("")
        search_layout.addWidget(self._search_count_label)

        self._search_prev_btn = QPushButton("\u25b2")
        self._search_prev_btn.setFixedSize(24, 24)
        self._search_prev_btn.clicked.connect(self._search_prev)
        search_layout.addWidget(self._search_prev_btn)

        self._search_next_btn = QPushButton("\u25bc")
        self._search_next_btn.setFixedSize(24, 24)
        self._search_next_btn.clicked.connect(self._search_next)
        search_layout.addWidget(self._search_next_btn)

        self._search_close_btn = QPushButton("\u2715")
        self._search_close_btn.setFixedSize(24, 24)
        self._search_close_btn.clicked.connect(self._close_search)
        search_layout.addWidget(self._search_close_btn)

        self._search_widget.hide()
        layout.addWidget(self._search_widget)

        self._search_matches: list[int] = []
        self._search_current: int = -1

        # Message list using the same delegate
        self._model = ChatMessageModel(max_messages=5000, parent=self)
        self._delegate = ChatMessageDelegate(settings, parent=self)
        if image_store:
            self._delegate.set_image_store(image_store)

        self._list_view = QListView()
        self._list_view.setModel(self._model)
        self._list_view.setItemDelegate(self._delegate)
        self._list_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self._list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list_view.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self._list_view.setWordWrap(True)
        self._list_view.setUniformItemSizes(False)
        self._list_view.setSpacing(0)
        layout.addWidget(self._list_view)

        # Enable mouse tracking for URL cursor changes and Ctrl+scroll
        self._list_view.viewport().setMouseTracking(True)
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

        # Apply theme colors
        self.apply_theme()

    def apply_theme(self) -> None:
        """Apply the current theme to the dialog."""
        theme = get_theme()

        # Dialog background
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {theme.window_bg};
            }}
        """)

        # Header
        self._header.setStyleSheet(f"""
            QLabel {{
                background-color: {theme.chat_input_bg};
                color: {theme.text_primary};
                padding: 8px;
                font-weight: bold;
                font-size: 13px;
            }}
        """)

        # Search widget
        self._search_widget.setStyleSheet(f"""
            QWidget {{
                background-color: {theme.chat_input_bg};
                border-bottom: 1px solid {theme.border_light};
            }}
        """)
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {theme.widget_bg};
                border: 1px solid {theme.border};
                border-radius: 3px;
                padding: 3px 6px;
                color: {theme.text_primary};
                font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {theme.accent}; }}
        """)
        self._search_count_label.setStyleSheet(
            f"color: {theme.text_muted}; font-size: 11px; background: transparent; min-width: 50px;"
        )
        search_btn_style = f"""
            QPushButton {{
                background: transparent; color: {theme.text_muted}; border: none;
                font-size: 14px; padding: 2px 6px;
            }}
            QPushButton:hover {{
                color: {theme.text_primary}; background: rgba(255,255,255,0.1);
                border-radius: 3px;
            }}
        """
        self._search_prev_btn.setStyleSheet(search_btn_style)
        self._search_next_btn.setStyleSheet(search_btn_style)
        self._search_close_btn.setStyleSheet(search_btn_style)

        # List view
        self._list_view.setStyleSheet(f"""
            QListView {{
                background-color: {theme.chat_bg};
                border: none;
                padding: 4px;
            }}
        """)

        # Update delegate theme and force repaint
        self._delegate.apply_theme()
        self._list_view.viewport().update()

    def add_messages(self, messages: list[ChatMessage]) -> None:
        """Add new messages from the tracked user (called by ChatWidget)."""
        user_msgs = [
            msg
            for msg in messages
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

    def apply_moderation(self, event) -> None:
        """Apply a moderation event to messages in this dialog."""
        self._model.apply_moderation(event)

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
        self._delegate.invalidate_size_cache()
        self._model.layoutChanged.emit()

    # Search methods provided by ChatSearchMixin:
    # _toggle_search, _close_search, _on_search_text_changed,
    # _search_next, _search_prev, _scroll_to_search_match

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Handle Escape to close search bar."""
        if self._handle_search_key_press(event.key()):
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802
        """Handle Ctrl+Wheel, URL clicks, and cursor changes."""
        if obj is not self._list_view.viewport():
            return super().eventFilter(obj, event)

        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
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

        # URL click → open browser
        if event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                index = self._list_view.indexAt(event.pos())
                if index.isValid():
                    message = index.data(MessageRole)
                    if message and isinstance(message, ChatMessage):
                        option = QStyleOptionViewItem()
                        self._list_view.initViewItemOption(option)
                        option.rect = self._list_view.visualRect(index)
                        url = self._delegate._get_url_at_position(event.pos(), option, message)
                        if url:
                            try:
                                webbrowser.open(url)
                            except Exception as e:
                                logger.error(f"Failed to open URL: {e}")
                            return True

        # Cursor changes for URLs
        if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            viewport = self._list_view.viewport()
            index = self._list_view.indexAt(event.pos())
            if index.isValid():
                message = index.data(MessageRole)
                if message and isinstance(message, ChatMessage):
                    option = QStyleOptionViewItem()
                    self._list_view.initViewItemOption(option)
                    option.rect = self._list_view.visualRect(index)
                    url = self._delegate._get_url_at_position(event.pos(), option, message)
                    if url:
                        viewport.setCursor(Qt.CursorShape.PointingHandCursor)
                        return False
            viewport.setCursor(Qt.CursorShape.ArrowCursor)
            return False

        return super().eventFilter(obj, event)
