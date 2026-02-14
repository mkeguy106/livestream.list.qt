"""Single-channel chat widget with message list and input."""

import logging
import re
import time
import unicodedata
import webbrowser

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QHelpEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QProgressBar,
    QPushButton,
    QStyle,
    QStyleOptionFrame,
    QStyleOptionViewItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ...chat.emotes.cache import EmoteCache
from ...chat.models import ChatEmote, ChatMessage, ChatRoomState, HypeTrainEvent, ModerationEvent
from ...core.models import Livestream, StreamPlatform
from ...core.settings import BuiltinChatSettings
from ..theme import get_theme
from .emote_completer import EmoteCompleter
from .emote_picker import EmotePickerWidget
from .link_preview import LinkPreviewCache
from .mention_completer import MentionCompleter
from .message_delegate import ChatMessageDelegate
from .message_model import ChatMessageModel, MessageRole
from .search_mixin import ChatSearchMixin
from .spell_completer import SpellCompleter
from .user_card import UserCardFetchWorker, UserCardPopup
from .user_popup import UserContextMenu

logger = logging.getLogger(__name__)

# Regex to find !command patterns in stream titles
COMMAND_PATTERN = re.compile(r"(!\w+)")

# Number of rows above/below viewport to pre-fetch animation frames for
ANIMATION_BUFFER_ROWS = 50


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
    """Custom QLineEdit that routes key events to completers.

    Supports spellcheck with red wavy underlines drawn via paintEvent.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._completers: list = []
        self._spell_checker = None
        self._spell_completer = None
        self._misspelled_ranges: list[tuple[int, int]] = []
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(150)
        self._check_timer.timeout.connect(self._run_check)
        # Autocorrect state
        self._autocorrect_enabled: bool = True
        self._green_highlights: list[tuple[int, int, str]] = []  # (start, end, corrected_word)
        self._corrected_words: set[str] = set()  # words already autocorrected (one attempt only)
        self._green_timer = QTimer(self)
        self._green_timer.setSingleShot(True)
        self._green_timer.setInterval(3000)
        self._green_timer.timeout.connect(self._clear_green_highlights)
        # Message history for up/down arrow cycling
        self._message_history: list[str] = []
        self._history_index: int = -1
        self._draft: str = ""
        self._max_history: int = 100

    def add_to_history(self, text: str) -> None:
        """Add a sent message to the history buffer."""
        if not text:
            return
        # Avoid consecutive duplicates
        if self._message_history and self._message_history[0] == text:
            self._history_index = -1
            self._draft = ""
            return
        self._message_history.insert(0, text)
        if len(self._message_history) > self._max_history:
            self._message_history.pop()
        self._history_index = -1
        self._draft = ""

    def add_completer(self, completer) -> None:
        """Add a completer to the chain."""
        self._completers.append(completer)

    def set_spell_checker(self, checker, completer=None) -> None:
        """Set the spell checker and connect the debounced recheck."""
        self._spell_checker = checker
        self._spell_completer = completer
        self.textChanged.connect(self._schedule_check)

    def set_spellcheck_enabled(self, enabled: bool) -> None:
        """Enable or disable spellcheck rendering."""
        if not enabled:
            self._misspelled_ranges.clear()
            self._green_highlights.clear()
            self.update()
        else:
            self._run_check()

    def set_autocorrect_enabled(self, enabled: bool) -> None:
        """Enable or disable autocorrect."""
        self._autocorrect_enabled = enabled
        if not enabled:
            self._green_highlights.clear()
            self._green_timer.stop()
            self.update()

    def _clear_green_highlights(self) -> None:
        """Clear green correction highlights after timeout."""
        self._green_highlights.clear()
        self.update()

    def _schedule_check(self) -> None:
        """Schedule a debounced spellcheck."""
        if self._spell_checker:
            self._check_timer.start()

    @staticmethod
    def _match_case(original: str, replacement: str) -> str:
        """Match the casing of the original word to the replacement."""
        if original.isupper():
            return replacement.upper()
        if original and original[0].isupper():
            return replacement.capitalize()
        return replacement

    def _run_check(self) -> None:
        """Run spellcheck on the current text, applying autocorrect for confident fixes."""
        if not self._spell_checker:
            return
        text = self.text()
        results = self._spell_checker.check_text(text)

        if not self._autocorrect_enabled:
            self._misspelled_ranges = [(s, e) for s, e, _w in results]
            self.update()
            return

        # Separate words into: auto-correctable (past + confident) vs keep-red
        corrections: list[tuple[int, int, str, str]] = []  # (start, end, original, replacement)
        keep_red: list[tuple[int, int]] = []

        for start, end, word in results:
            # Only autocorrect each word once per message — if the user changes it
            # back, respect their intent and don't correct again
            if word.lower() in self._corrected_words:
                keep_red.append((start, end))
                continue
            # "past" = user has moved on: text after word starts with space + alpha
            is_past = (
                end < len(text)
                and text[end] == " "
                and end + 1 < len(text)
                and text[end + 1].isalpha()
            )
            if is_past:
                correction = self._spell_checker.get_confident_correction(word)
                if correction:
                    cased = self._match_case(word, correction)
                    corrections.append((start, end, word, cased))
                    continue
            keep_red.append((start, end))

        if not corrections:
            self._misspelled_ranges = keep_red
            self.update()
            return

        # Apply corrections from end to start to preserve positions
        corrections.sort(key=lambda c: c[0], reverse=True)
        cursor_pos = self.cursorPosition()

        self.blockSignals(True)
        new_text = text
        new_greens: list[tuple[int, int, str]] = []
        for start, end, original, replacement in corrections:
            new_text = new_text[:start] + replacement + new_text[end:]
            diff = len(replacement) - len(original)
            if cursor_pos > end:
                cursor_pos += diff
            elif cursor_pos > start:
                cursor_pos = start + len(replacement)
            new_greens.append((start, start + len(replacement), replacement))
            self._corrected_words.add(original.lower())

        self.setText(new_text)
        self.setCursorPosition(cursor_pos)
        self.blockSignals(False)

        # Re-run spellcheck on corrected text (without autocorrect to avoid recursion)
        new_results = self._spell_checker.check_text(new_text)
        self._misspelled_ranges = [(s, e) for s, e, _w in new_results]

        # Set green highlights and restart timer
        self._green_highlights = new_greens
        self._green_timer.start()
        self.update()

    def event(self, event: QEvent) -> bool:  # noqa: N802
        """Intercept Tab key before Qt's focus navigation handles it."""
        if event.type() == QEvent.Type.KeyPress:
            key_event = event
            if key_event.key() == Qt.Key.Key_Tab:
                for completer in self._completers:
                    if completer.handle_key_press(key_event.key()):
                        return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Route navigation keys to completers first."""
        # Escape cancels reply mode on the parent ChatWidget
        if event.key() == Qt.Key.Key_Escape:
            parent = self.parent()
            while parent and not isinstance(parent, ChatWidget):
                parent = parent.parent()
            if parent and parent._reply_to_msg is not None:
                parent._cancel_reply()
                return
        # Up/Down arrow: cycle through message history
        if event.key() == Qt.Key.Key_Up and self._message_history:
            if self._history_index == -1:
                self._draft = self.text()
            if self._history_index < len(self._message_history) - 1:
                self._history_index += 1
                self.setText(self._message_history[self._history_index])
            return
        if event.key() == Qt.Key.Key_Down:
            if self._history_index > 0:
                self._history_index -= 1
                self.setText(self._message_history[self._history_index])
                return
            if self._history_index == 0:
                self._history_index = -1
                self.setText(self._draft)
                return
        # Dismiss spell popup on Space (user moved on to next word)
        if event.key() == Qt.Key.Key_Space and self._spell_completer:
            self._spell_completer._dismiss()
        for completer in self._completers:
            if completer.handle_key_press(event.key()):
                return
        super().keyPressEvent(event)

    def _text_content_rect(self):
        """Return the rect where text is actually rendered inside the QLineEdit."""
        opt = QStyleOptionFrame()
        self.initStyleOption(opt)
        return self.style().subElementRect(QStyle.SubElement.SE_LineEditContents, opt, self)

    def paintEvent(self, event) -> None:  # noqa: N802
        """Draw the default text, then overlay wavy red and straight green underlines."""
        super().paintEvent(event)

        has_red = bool(self._misspelled_ranges)
        has_green = bool(self._green_highlights)
        if not has_red and not has_green:
            return

        text = self.text()
        if not text:
            return

        fm = self.fontMetrics()
        cr = self._text_content_rect()

        # Compute where position 0 of text renders, accounting for scroll.
        # Use cursorRect center (not left edge) since the caret aligns with
        # the center of the cursor bounding rect, not its left edge.
        cur_pos = self.cursorPosition()
        cur_visible_x = self.cursorRect().center().x()
        cur_text_x = fm.horizontalAdvance(text[:cur_pos])
        text_origin_x = cur_visible_x - cur_text_x

        baseline_y = cr.y() + (cr.height() + fm.ascent() - fm.descent()) / 2 + 1

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Red wavy underlines for misspelled words
        if has_red:
            pen = QPen(QColor(255, 60, 60, 200))
            pen.setWidthF(1.2)
            painter.setPen(pen)

            for start, end in self._misspelled_ranges:
                x_start = text_origin_x + fm.horizontalAdvance(text[:start])
                x_end = text_origin_x + fm.horizontalAdvance(text[:end])
                if x_end < 0 or x_start > self.width():
                    continue
                self._draw_wavy_line(painter, x_start, x_end, baseline_y)

        # Green straight underlines for autocorrected words
        if has_green:
            green_pen = QPen(QColor(60, 200, 60, 200))
            green_pen.setWidthF(1.5)
            painter.setPen(green_pen)

            for start, end, corrected_word in self._green_highlights:
                if end <= len(text) and text[start:end] == corrected_word:
                    x_start = text_origin_x + fm.horizontalAdvance(text[:start])
                    x_end = text_origin_x + fm.horizontalAdvance(text[:end])
                    if x_end < 0 or x_start > self.width():
                        continue
                    painter.drawLine(int(x_start), int(baseline_y), int(x_end), int(baseline_y))

        painter.end()

    @staticmethod
    def _draw_wavy_line(painter: QPainter, x_start: float, x_end: float, y: float) -> None:
        """Draw a wavy (squiggly) underline from x_start to x_end at y."""
        wave_height = 2.0
        wave_length = 4.0

        path = QPainterPath()
        path.moveTo(x_start, y)

        x = x_start
        going_up = True
        while x < x_end:
            next_x = min(x + wave_length / 2, x_end)
            cy = y - wave_height if going_up else y + wave_height
            path.quadTo((x + next_x) / 2, cy, next_x, y)
            x = next_x
            going_up = not going_up

        painter.drawPath(path)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """On left-click, show spell suggestions or dismiss popup."""
        if event.button() == Qt.MouseButton.LeftButton and self._spell_completer:
            click_pos = self.cursorPositionAt(event.pos())
            text = self.text()

            # Check if click is on a misspelled word
            for start, end in self._misspelled_ranges:
                if start <= click_pos < end and end <= len(text):
                    super().mousePressEvent(event)
                    self._spell_completer.show_suggestions_at_word(start, end)
                    return

            # Click was NOT on a misspelled word — dismiss any open popup
            self._spell_completer._dismiss()

        super().mousePressEvent(event)


class ChatWidget(QWidget, ChatSearchMixin):
    """Widget for a single channel's chat.

    Contains a QListView for messages, an input field, and a send button.
    Handles auto-scrolling and the "new messages" indicator.
    Uses ChatSearchMixin for search functionality.
    """

    message_sent = Signal(str, str, str)  # channel_key, text, reply_to_msg_id
    popout_requested = Signal(str)  # channel_key
    settings_clicked = Signal()
    font_size_changed = Signal(int)  # new font size
    settings_changed = Signal()  # any chat setting toggled
    whisper_requested = Signal(str, str)  # partner_display_name, partner_user_id
    always_on_top_changed = Signal(bool)

    def __init__(
        self,
        channel_key: str,
        livestream: Livestream | None,
        settings: BuiltinChatSettings,
        authenticated: bool = False,
        is_dm: bool = False,
        dm_partner_name: str = "",
        dm_partner_id: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.channel_key = channel_key
        self.livestream = livestream
        self.settings = settings
        self._authenticated = authenticated
        self._is_dm = is_dm
        self._dm_partner_name = dm_partner_name
        self._dm_partner_id = dm_partner_id
        self._auto_scroll = True
        self._scroll_pause_timer = QTimer(self)
        self._scroll_pause_timer.setSingleShot(True)
        self._scroll_pause_timer.setInterval(5 * 60 * 1000)  # 5 minutes
        self._scroll_pause_timer.timeout.connect(self._scroll_to_bottom)
        self._countdown_remaining = 300
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_tick)
        self._resize_timer: QTimer | None = None
        self._history_dialogs: set = set()
        self._gif_timer = None
        self._animation_time_ms = 0
        self._image_store: EmoteCache | None = None
        self._last_rehydrate_ts: float = 0.0
        self._visible_has_animated: bool = False
        self._socials: dict[str, str] = {}  # Stored socials for re-display on toggle
        self._title_dismissed = False  # Track if user dismissed the title banner
        self._socials_dismissed = False  # Track if user dismissed the socials banner
        self._reply_to_msg: ChatMessage | None = None  # Message being replied to
        self._slow_mode_remaining: int = 0
        self._slow_mode_timer = QTimer(self)
        self._slow_mode_timer.setInterval(1000)
        self._slow_mode_timer.timeout.connect(self._slow_mode_tick)
        self._title_refresh_timer = QTimer(self)
        self._title_refresh_timer.setInterval(30_000)
        self._title_refresh_timer.timeout.connect(self._update_stream_title)
        self._emotes_by_provider: dict[str, list] = {}
        self._channel_emote_names: set[str] = set()
        self._locked_emote_names: set[str] = set()
        self._link_preview_cache = LinkPreviewCache()
        # User card hover state
        self._card_hover_timer = QTimer(self)
        self._card_hover_timer.setSingleShot(True)
        self._card_hover_timer.setInterval(400)
        self._card_hover_timer.timeout.connect(self._on_card_hover_timeout)
        self._card_hover_user = None  # ChatUser or None
        self._card_hover_pos = QPoint()
        self._active_user_card: UserCardPopup | None = None
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

        self._room_state: ChatRoomState | None = None

        # Apply banner styling and update title
        self._update_banner_style()
        if self._is_dm:
            self._title_banner.hide()
            self._socials_banner.hide()
        else:
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
        self._search_input.setPlaceholderText("Search... (from:user has:link is:sub)")
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
        if self._is_dm:
            # DM tabs don't connect — show list immediately
            self._connecting_label.hide()
        else:
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

        # Hype Train progress banner (hidden by default)
        self._hype_train_banner = QWidget()
        self._hype_train_banner.setStyleSheet("""
            QWidget {
                background-color: #3b1f8a;
                border: 1px solid #9146ff;
                border-radius: 4px;
            }
        """)
        ht_outer = QVBoxLayout(self._hype_train_banner)
        ht_outer.setContentsMargins(8, 4, 4, 4)
        ht_outer.setSpacing(3)
        # Top row: level label + dismiss button
        ht_top = QHBoxLayout()
        ht_top.setSpacing(4)
        self._hype_train_level_label = QLabel()
        self._hype_train_level_label.setStyleSheet(
            "color: #d4b8ff; font-size: 12px; font-weight: bold; border: none;"
        )
        ht_top.addWidget(self._hype_train_level_label, 1)
        ht_dismiss = QPushButton("\u2715")
        ht_dismiss.setFixedSize(20, 20)
        ht_dismiss.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #aaa;
                border: none;
                font-size: 12px;
            }
            QPushButton:hover { color: #fff; }
        """)
        ht_dismiss.clicked.connect(self._dismiss_hype_train_banner)
        ht_top.addWidget(ht_dismiss)
        ht_outer.addLayout(ht_top)
        # Progress bar
        self._hype_train_progress = QProgressBar()
        self._hype_train_progress.setFixedHeight(10)
        self._hype_train_progress.setTextVisible(False)
        self._hype_train_progress.setStyleSheet("""
            QProgressBar {
                background-color: #231052;
                border: none;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background-color: #9146ff;
                border-radius: 4px;
            }
        """)
        ht_outer.addWidget(self._hype_train_progress)
        # Countdown label
        self._hype_train_countdown = QLabel()
        self._hype_train_countdown.setStyleSheet("color: #b9a3e3; font-size: 11px; border: none;")
        ht_outer.addWidget(self._hype_train_countdown)
        self._hype_train_banner.hide()
        layout.addWidget(self._hype_train_banner)
        # Timer for hype train countdown
        self._hype_train_timer = QTimer(self)
        self._hype_train_timer.setInterval(1000)
        self._hype_train_timer.timeout.connect(self._hype_train_tick)
        self._hype_train_expires_at: str = ""
        self._hype_train_auto_hide_timer: QTimer | None = None

        # Raid banner (hidden by default)
        self._raid_banner = QWidget()
        self._raid_banner.setStyleSheet("""
            QWidget {
                background-color: #3a1500;
                border: 1px solid #cc5500;
                border-radius: 4px;
            }
        """)
        raid_layout = QHBoxLayout(self._raid_banner)
        raid_layout.setContentsMargins(8, 4, 4, 4)
        raid_layout.setSpacing(6)
        self._raid_label = QLabel()
        self._raid_label.setStyleSheet(
            "color: #ff8c00; font-size: 11px; font-weight: bold; border: none;"
        )
        self._raid_label.setWordWrap(True)
        raid_layout.addWidget(self._raid_label, 1)
        raid_dismiss = QPushButton("\u2715")
        raid_dismiss.setFixedSize(20, 20)
        raid_dismiss.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #aaa;
                border: none;
                font-size: 12px;
            }
            QPushButton:hover { color: #fff; }
        """)
        raid_dismiss.clicked.connect(self._dismiss_raid_banner)
        raid_layout.addWidget(raid_dismiss)
        self._raid_banner.hide()
        layout.addWidget(self._raid_banner)
        self._raid_auto_hide_timer: QTimer | None = None
        self._raid_remaining: int = 0
        self._raid_base_text: str = ""

        # New messages indicator (hidden by default)
        self._new_msg_button = QPushButton("New messages")
        self._new_msg_button.setObjectName("chat_new_msg")
        self._new_msg_button.setFixedHeight(24)
        self._new_msg_button.hide()
        self._new_msg_button.clicked.connect(self._scroll_to_bottom)
        layout.addWidget(self._new_msg_button)

        # Room state indicator (sub-only, slow mode, etc.)
        self._room_state_widget = QWidget()
        self._room_state_widget.setObjectName("chat_room_state")
        rs_layout = QHBoxLayout(self._room_state_widget)
        rs_layout.setContentsMargins(8, 4, 4, 4)
        rs_layout.setSpacing(4)
        self._room_state_label = QLabel()
        self._room_state_label.setObjectName("chat_room_state_label")
        self._room_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rs_layout.addWidget(self._room_state_label, 1)
        rs_close = QPushButton("\u2715")
        rs_close.setObjectName("chat_room_state_close")
        rs_close.setFixedSize(20, 20)
        rs_close.clicked.connect(self._dismiss_room_state)
        rs_layout.addWidget(rs_close)
        self._room_state_widget.hide()
        self._room_state_dismissed = False
        layout.addWidget(self._room_state_widget)

        # Reply indicator (hidden by default)
        self._reply_widget = QWidget()
        self._reply_widget.setObjectName("chat_reply_widget")
        reply_layout = QHBoxLayout(self._reply_widget)
        reply_layout.setContentsMargins(8, 4, 4, 4)
        reply_layout.setSpacing(6)
        self._reply_label = QLabel()
        self._reply_label.setObjectName("chat_reply_label")
        reply_layout.addWidget(self._reply_label, 1)
        reply_close = QPushButton("\u2715")
        reply_close.setObjectName("chat_reply_close")
        reply_close.setFixedSize(20, 20)
        reply_close.clicked.connect(self._cancel_reply)
        reply_layout.addWidget(reply_close)
        self._reply_widget.hide()
        layout.addWidget(self._reply_widget)

        # Input area
        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(4, 4, 4, 4)
        input_layout.setSpacing(4)

        self._input = ChatInput()
        self._input.setObjectName("chat_input")
        self._input.returnPressed.connect(self._on_send)
        self._input.textChanged.connect(self._update_char_counter)
        input_layout.addWidget(self._input)

        self._char_counter = QLabel()
        self._char_counter.setObjectName("chat_char_counter")
        self._char_counter.setStyleSheet("QLabel { color: #888; font-size: 11px; }")
        self._char_counter.setFixedWidth(32)
        self._char_counter.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._char_counter.hide()
        input_layout.addWidget(self._char_counter)

        self._send_button = QPushButton("Whisper" if self._is_dm else "Chat")
        self._send_button.setObjectName("chat_send_btn")
        self._send_button.clicked.connect(self._on_send)
        input_layout.addWidget(self._send_button)

        self._emote_button = QPushButton("\U0001f642")
        self._emote_button.setObjectName("chat_emote_btn")
        self._emote_button.setFixedSize(28, 28)
        self._emote_button.setToolTip("Emote picker (Ctrl+E)")
        self._emote_button.clicked.connect(self._show_emote_picker)
        input_layout.addWidget(self._emote_button)

        self._settings_button = QPushButton("\u2699")
        self._settings_button.setObjectName("chat_settings_btn")
        self._settings_button.setFixedSize(28, 28)
        self._settings_button.setToolTip("Chat settings")
        self._settings_button.clicked.connect(self._show_settings_menu)
        input_layout.addWidget(self._settings_button)

        layout.addLayout(input_layout)

        # Emote picker popup (hidden until activated)
        self._emote_picker = EmotePickerWidget(self)
        self._emote_picker.emote_selected.connect(self._insert_emote)

        # Auth feedback banner (shown when not authenticated)
        theme = get_theme()
        banner_bg = theme.chat_banner_bg
        banner_text = theme.chat_banner_text
        banner_border = theme.border

        self._auth_banner = QLabel("Not logged in \u2014 chat is read-only")
        self._auth_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._auth_banner_style = f"""
            QLabel {{
                background-color: {banner_bg};
                color: {banner_text};
                border: 1px solid {banner_border};
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }}
        """
        sc = QColor(theme.chat_system_message)
        sc.setAlpha(50)
        rgba = f"rgba({sc.red()}, {sc.green()}, {sc.blue()}, {sc.alpha()})"
        self._auth_banner_error_style = f"""
            QLabel {{
                background-color: {rgba};
                color: {theme.chat_system_message};
                border: 1px solid {theme.chat_system_message};
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }}
        """
        self._auth_banner.setStyleSheet(self._auth_banner_style)
        self._auth_banner.hide()
        layout.addWidget(self._auth_banner)

        # Restriction banner with "Open in Browser" button (for restricted YouTube chats)
        self._restriction_banner = QWidget()
        self._restriction_banner.setStyleSheet(f"""
            QWidget {{
                background-color: {banner_bg};
                border: 1px solid {banner_border};
                border-radius: 3px;
            }}
        """)
        restriction_layout = QHBoxLayout(self._restriction_banner)
        restriction_layout.setContentsMargins(8, 4, 8, 4)
        restriction_layout.setSpacing(8)
        self._restriction_label = QLabel()
        self._restriction_label.setStyleSheet(
            f"color: {banner_text}; font-size: 11px; background: transparent; border: none;"
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
        if self.livestream:
            self._emote_completer.set_platform(self.livestream.channel.platform.value)
        elif self._is_dm:
            self._emote_completer.set_platform("twitch")
        self._input.add_completer(self._emote_completer)

        # Mention autocomplete
        self._mention_completer = MentionCompleter(self._input, parent=self)
        self._input.add_completer(self._mention_completer)

        # Spellcheck completer (lowest priority in chain)
        self._spell_checker = None
        self._spell_completer = None
        if self.settings.spellcheck_enabled:
            self._init_spellcheck()
        # Autocorrect state (depends on spellcheck being enabled)
        self._input.set_autocorrect_enabled(
            self.settings.autocorrect_enabled and self.settings.spellcheck_enabled
        )

        # Auth gating
        self.set_authenticated(self._authenticated)

        # Context menu for user interaction
        self._list_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list_view.customContextMenuRequested.connect(self._on_context_menu)

        # Connect scroll tracking
        scrollbar = self._list_view.verticalScrollBar()
        scrollbar.valueChanged.connect(self._on_scroll_changed)
        scrollbar.rangeChanged.connect(self._on_scroll_range_changed)

        # Copy shortcut (Ctrl+C)
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self._list_view)
        copy_shortcut.activated.connect(self._copy_selected_messages)

        # Find shortcut (Ctrl+F)
        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self._toggle_search)

        # Emote picker shortcut (Ctrl+E)
        emote_shortcut = QShortcut(QKeySequence("Ctrl+E"), self)
        emote_shortcut.activated.connect(self._show_emote_picker)

        # Note: apply_theme() is called by the parent ChatWindow after widget creation
        # to avoid duplicate theme application during construction

    def _init_spellcheck(self) -> None:
        """Initialize spellcheck components (checker + completer)."""
        try:
            from ...chat.spellcheck import SpellChecker

            self._spell_checker = SpellChecker()
            self._spell_completer = SpellCompleter(self._input, self._spell_checker, parent=self)
            self._input.add_completer(self._spell_completer)
            self._input.set_spell_checker(self._spell_checker, self._spell_completer)
        except ImportError:
            logger.warning("pyhunspell not installed, spellcheck disabled")
        except (FileNotFoundError, OSError) as e:
            logger.warning(f"Spellcheck unavailable: {e}")

    def set_spellcheck_enabled(self, enabled: bool) -> None:
        """Enable or disable spellcheck at runtime."""
        if enabled and not self._spell_checker:
            self._init_spellcheck()
        self._input.set_spellcheck_enabled(enabled)

    def set_autocorrect_enabled(self, enabled: bool) -> None:
        """Enable or disable autocorrect at runtime."""
        self._input.set_autocorrect_enabled(enabled)

    def add_messages(self, messages: list[ChatMessage]) -> None:
        """Add messages to the chat.

        Filters blocked users and handles auto-scrolling.
        """
        # Filter blocked users
        blocked = set(self.settings.blocked_users)
        filtered = [msg for msg in messages if f"{msg.platform.value}:{msg.user.id}" not in blocked]

        if not filtered:
            return

        # Track usernames for @mention autocomplete and spellcheck dictionary
        for msg in filtered:
            self._mention_completer.add_username(msg.user.display_name)
            if self._spell_checker:
                self._spell_checker.dictionary.add_username(msg.user.display_name)

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

        should_scroll = self._auto_scroll
        self._model.add_messages(filtered)

        # Forward to any open user history dialogs
        if self._history_dialogs:
            for dialog in list(self._history_dialogs):
                dialog.add_messages(filtered)

        if should_scroll:
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

    def load_disk_history(self, chat_log_writer) -> None:
        """Load recent messages from disk chat logs into the message list."""
        settings = chat_log_writer.settings
        if not settings.enabled or not settings.load_history_on_open:
            return
        messages = chat_log_writer.load_recent_history(self._channel_key, settings.history_lines)
        if messages:
            self._model.add_messages(messages)

    def set_connected(self) -> None:
        """Hide the connecting indicator and show the message list."""
        self._connecting_label.hide()
        self._list_view.show()
        # Stop any active reconnect countdown
        if hasattr(self, "_reconnect_timer") and self._reconnect_timer.isActive():
            self._reconnect_timer.stop()

    def set_disconnected(self) -> None:
        """Show a disconnected message alongside the message list."""
        self._connecting_label.setText("Disconnected. Reconnecting\u2026")
        self._connecting_label.show()
        # Keep _list_view visible so user can still see past messages

    def set_reconnecting(self, delay: float) -> None:
        """Show a reconnecting countdown alongside the message list."""
        self._reconnect_remaining = int(delay)
        self._update_reconnect_label()
        self._connecting_label.show()
        if not hasattr(self, "_reconnect_timer"):
            self._reconnect_timer = QTimer(self)
            self._reconnect_timer.setInterval(1000)
            self._reconnect_timer.timeout.connect(self._reconnect_countdown_tick)
        self._reconnect_timer.start()

    def set_reconnect_failed(self) -> None:
        """Show a permanent connection lost message."""
        if hasattr(self, "_reconnect_timer"):
            self._reconnect_timer.stop()
        self._connecting_label.setText("Connection lost. Could not reconnect.")
        self._connecting_label.show()

    def _reconnect_countdown_tick(self) -> None:
        """Decrement the reconnect countdown label each second."""
        self._reconnect_remaining -= 1
        if self._reconnect_remaining <= 0:
            self._reconnect_timer.stop()
            self._connecting_label.setText("Reconnecting\u2026")
        else:
            self._update_reconnect_label()

    def _update_reconnect_label(self) -> None:
        """Update the reconnect label with the current countdown."""
        self._connecting_label.setText(f"Reconnecting in {self._reconnect_remaining}s\u2026")

    def set_authenticated(self, state: bool) -> None:
        """Enable or disable the input based on authentication state."""
        self._authenticated = state
        self._input.setEnabled(state)
        self._send_button.setEnabled(state)
        if state:
            if self._is_dm:
                self._input.setPlaceholderText(f"Whisper to {self._dm_partner_name}...")
            else:
                self._input.setPlaceholderText("Send a message...")
            self._auth_banner.hide()
        else:
            if self._is_dm:
                platform_name = "Twitch"
            else:
                platform_name = self.livestream.channel.platform.value.title()
            self._input.setPlaceholderText(f"Log in to {platform_name} to chat")
            self._auth_banner.setText(f"Not logged in \u2014 {platform_name} chat is read-only")
            self._auth_banner.setStyleSheet(self._auth_banner_style)
            self._auth_banner.show()

    def show_error(self, message: str) -> None:
        """Show an error message in the appropriate banner."""
        # For YouTube restriction errors, show the banner with "Open in Browser" button
        if (
            self.livestream
            and self.livestream.channel.platform == StreamPlatform.YOUTUBE
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
            self._auth_banner.setStyleSheet(self._auth_banner_error_style)
            self._auth_banner.show()
            self._restriction_banner.hide()

    def _open_chat_in_browser(self) -> None:
        """Open the YouTube chat popout in the default browser."""
        if not self.livestream or self.livestream.channel.platform != StreamPlatform.YOUTUBE:
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

    def set_emote_map(
        self,
        emote_map: dict[str, ChatEmote],
        channel_emote_names: set[str] | None = None,
        user_emote_names: set[str] | None = None,
    ) -> None:
        """Set the emote map for autocomplete and emote picker."""
        self._emote_completer.set_emotes(emote_map)
        if self._spell_checker:
            self._spell_checker.dictionary.set_emote_names(set(emote_map.keys()))
        # Group emotes by provider, channel-specific first in each group
        self._channel_emote_names = channel_emote_names or set()
        user_names = user_emote_names or set()
        # Determine locked emotes: platform channel emotes user can't use
        self._locked_emote_names: set[str] = set()
        for name in self._channel_emote_names:
            emote = emote_map.get(name)
            if emote and emote.provider in ("twitch", "kick") and name not in user_names:
                self._locked_emote_names.add(name)
        by_provider: dict[str, list[ChatEmote]] = {}
        for emote in emote_map.values():
            by_provider.setdefault(emote.provider, []).append(emote)
        # Sort each provider: channel emotes first, then alphabetical
        for provider in by_provider:
            by_provider[provider].sort(
                key=lambda e: (
                    0 if e.name in self._channel_emote_names else 1,
                    e.name.lower(),
                )
            )
        self._emotes_by_provider = by_provider

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
        # Only update if this widget is actually visible (active tab)
        if not self.isVisible():
            return
        self._rehydrate_visible_animated_emotes()
        if not self._visible_has_animated:
            return  # No animated emotes in or near viewport — skip repaint
        self._animation_time_ms = elapsed_ms
        self._delegate.set_animation_frame(self._animation_time_ms)
        self._list_view.viewport().update()

    def _rehydrate_visible_animated_emotes(self) -> None:
        """Rehydrate animated emotes for visible + buffer zone messages."""
        if not self._image_store or not self.settings.animate_emotes:
            self._visible_has_animated = False
            return

        now = time.monotonic()
        if now - self._last_rehydrate_ts < 1.0:
            return
        self._last_rehydrate_ts = now

        viewport = self._list_view.viewport()
        if not viewport:
            self._visible_has_animated = False
            return

        top_index = self._list_view.indexAt(viewport.rect().topLeft())
        bottom_index = self._list_view.indexAt(viewport.rect().bottomLeft())

        if not top_index.isValid():
            self._visible_has_animated = False
            return

        start_row = top_index.row()
        end_row = bottom_index.row() if bottom_index.isValid() else start_row

        # Expand to buffer zone for pre-fetching animation frames
        row_count = self._model.rowCount()
        buf_start = max(0, start_row - ANIMATION_BUFFER_ROWS)
        buf_end = min(row_count - 1, end_row + ANIMATION_BUFFER_ROWS)

        found_animated = False
        cache = self._image_store
        for row in range(buf_start, buf_end + 1):
            msg = self._model.get_message(row)
            if not msg or not msg.emote_positions:
                continue
            for _start, _end, emote in msg.emote_positions:
                image_ref = self._get_emote_image_ref(emote)
                if not image_ref:
                    continue
                key = image_ref.key
                if cache.has_animation_data(key):
                    found_animated = True
                    if key in cache.animated_dict:
                        cache.touch_animated(key)
                    else:
                        cache.request_animation_frames(key)
        self._visible_has_animated = found_animated

    def repaint_messages(self) -> None:
        """Trigger a repaint of visible messages (e.g. after emotes load)."""
        self._last_rehydrate_ts = 0  # Force rehydrate on next tick
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

    def _get_char_limit(self) -> int:
        """Return the character limit for the current platform."""
        if self.livestream:
            platform = self.livestream.channel.platform
            if platform == StreamPlatform.YOUTUBE:
                return 200
        return 500  # Twitch and Kick both use 500

    def _update_char_counter(self, text: str) -> None:
        """Update the character counter label."""
        limit = self._get_char_limit()
        length = len(text)
        if length == 0:
            self._char_counter.hide()
            return
        remaining = limit - length
        self._char_counter.setText(str(remaining))
        if remaining < 0:
            self._char_counter.setStyleSheet("QLabel { color: #e74c3c; font-size: 11px; }")
        elif remaining <= 50:
            self._char_counter.setStyleSheet("QLabel { color: #e67e22; font-size: 11px; }")
        else:
            self._char_counter.setStyleSheet("QLabel { color: #888; font-size: 11px; }")
        self._char_counter.show()

    def _on_send(self) -> None:
        """Handle send button/enter key."""
        text = self._input.text().strip()
        if text:
            self._input.add_to_history(text)
            # Track emote usage for autocomplete sorting
            emote_map = self._emote_completer._emote_map
            if emote_map:
                for word in text.split():
                    if word in emote_map:
                        self._emote_completer.record_usage(word)
            reply_id = self._reply_to_msg.id if self._reply_to_msg else ""
            self.message_sent.emit(self.channel_key, text, reply_id)
            self._input.clear()
            self._input._corrected_words.clear()
            self._cancel_reply()
            # Start slow mode countdown if active
            if self._room_state and self._room_state.slow > 0:
                self._slow_mode_remaining = self._room_state.slow
                self._input.setEnabled(False)
                self._send_button.setEnabled(False)
                self._input.setPlaceholderText(f"Slow mode ({self._slow_mode_remaining}s)...")
                self._slow_mode_timer.start()

    def _slow_mode_tick(self) -> None:
        """Tick the slow mode countdown timer."""
        self._slow_mode_remaining -= 1
        if self._slow_mode_remaining <= 0:
            self._slow_mode_timer.stop()
            self._restore_input_after_slow_mode()
        else:
            self._input.setPlaceholderText(f"Slow mode ({self._slow_mode_remaining}s)...")

    def _restore_input_after_slow_mode(self) -> None:
        """Restore input field after slow mode countdown expires."""
        self._slow_mode_remaining = 0
        self._slow_mode_timer.stop()
        if self._authenticated:
            self._input.setEnabled(True)
            self._send_button.setEnabled(True)
            if self._is_dm:
                self._input.setPlaceholderText(f"Whisper to {self._dm_partner_name}...")
            else:
                self._input.setPlaceholderText("Send a message...")

    def _is_at_bottom(self) -> bool:
        """Check if the view is scrolled to the bottom."""
        scrollbar = self._list_view.verticalScrollBar()
        return scrollbar.value() >= scrollbar.maximum() - 10

    def _scroll_to_bottom(self) -> None:
        """Scroll to the latest message."""
        self._auto_scroll = True
        self._model._trim_paused = False
        self._model.flush_trim()
        self._list_view.scrollToBottom()
        self._new_msg_button.hide()
        self._countdown_timer.stop()

    def _countdown_tick(self) -> None:
        """Update the countdown display on the new-messages button."""
        self._countdown_remaining -= 1
        if self._countdown_remaining <= 0:
            return  # _scroll_pause_timer handles the actual scroll
        mins, secs = divmod(self._countdown_remaining, 60)
        self._new_msg_button.setText(f"New messages ({mins}:{secs:02d})")

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
                prefix = f"[{message.timestamp.astimezone().strftime(self.settings.ts_strftime)}] "
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
                user_menu.nickname_changed.connect(self._on_nickname_changed)
                user_menu.note_changed.connect(self._on_note_changed)
                for action in user_menu.actions():
                    menu.addAction(action)

                # "Reply" option (requires auth, not for system messages)
                if self._authenticated and not message.is_system:
                    menu.addSeparator()
                    reply_action = menu.addAction(f"Reply to @{message.user.display_name}")
                    reply_action.triggered.connect(
                        lambda checked=False, m=message: self._start_reply(m)
                    )

                # "Send Whisper" option for Twitch users (not our own messages)
                if message.user.platform == StreamPlatform.TWITCH and message.user.id != "self":
                    menu.addSeparator()
                    whisper_action = menu.addAction(f"Send Whisper to {message.user.display_name}")
                    whisper_action.triggered.connect(
                        lambda checked=False, u=message.user: self.whisper_requested.emit(
                            u.display_name, u.id
                        )
                    )

        # Export chat log
        if self._model.rowCount() > 0:
            menu.addSeparator()
            export_action = menu.addAction("Export chat log...")
            export_action.triggered.connect(self._export_chat_log)

        if not menu.isEmpty():
            menu.exec(self._list_view.viewport().mapToGlobal(pos))

    def _on_nickname_changed(self, user_key: str, nickname: str) -> None:
        """Handle nickname change — invalidate delegate cache and save settings."""
        if hasattr(self, "_delegate"):
            self._delegate._size_cache.clear()
            self._list_view.viewport().update()
        self._save_settings()

    def _on_note_changed(self, user_key: str, note: str) -> None:
        """Handle note change — invalidate delegate cache and save settings."""
        if hasattr(self, "_delegate"):
            self._delegate._size_cache.clear()
            self._list_view.viewport().update()
        self._save_settings()

    def _save_settings(self) -> None:
        """Save settings via the app."""
        try:
            from ..app import Application

            app = Application.instance()
            if app:
                app.save_settings()
        except Exception:
            pass

    def _export_chat_log(self) -> None:
        """Export chat messages to a text, HTML, or JSON file."""
        from datetime import datetime as dt

        from PySide6.QtWidgets import QFileDialog

        # Build default filename (no extension — dialog filter controls it)
        channel_name = self.channel_key.split(":", 1)[-1] if ":" in self.channel_key else "chat"
        date_str = dt.now().strftime("%Y-%m-%d")
        default_name = f"{channel_name}_{date_str}"

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Chat Log",
            default_name,
            "Text files (*.txt);;HTML files (*.html);;JSON files (*.json);;All files (*)",
        )
        if not path:
            return

        messages = self.get_all_messages()
        try:
            if path.endswith(".html"):
                self._export_as_html(path, messages)
            elif path.endswith(".json"):
                self._export_as_json(path, messages)
            else:
                self._export_as_text(path, messages)
        except OSError as e:
            logger.error(f"Failed to export chat log: {e}")
            self._show_export_error(str(e))

    def _export_as_text(self, path: str, messages: list) -> None:
        """Export chat messages as plain text."""
        with open(path, "w", encoding="utf-8") as f:
            for msg in messages:
                ts = msg.timestamp.astimezone().strftime("%H:%M:%S")
                if msg.is_system and msg.system_text:
                    f.write(f"[{ts}] *** {msg.system_text} ***\n")
                    if msg.text:
                        f.write(f"[{ts}] {msg.user.display_name}: {msg.text}\n")
                elif msg.is_action:
                    f.write(f"[{ts}] {msg.user.display_name} {msg.text}\n")
                else:
                    f.write(f"[{ts}] {msg.user.display_name}: {msg.text}\n")

    def _export_as_html(self, path: str, messages: list) -> None:
        """Export chat messages as a dark-themed HTML file."""
        import html

        channel_name = self.channel_key.split(":", 1)[-1] if ":" in self.channel_key else "chat"
        lines: list[str] = []
        lines.append("<!DOCTYPE html>")
        lines.append("<html><head><meta charset='utf-8'>")
        lines.append(f"<title>Chat Log — {html.escape(channel_name)}</title>")
        lines.append("<style>")
        lines.append(
            "body{background:#1a1a2e;color:#e0e0e0;font-family:monospace;font-size:14px;"
            "margin:20px;}"
        )
        lines.append(".msg{margin:2px 0;line-height:1.5;}")
        lines.append(".ts{color:#666;}")
        lines.append(".system{color:#888;font-style:italic;}")
        lines.append(".badge{color:#999;font-size:0.85em;}")
        lines.append(".reply{color:#777;font-size:0.9em;margin-left:20px;}")
        lines.append(".hype{background:#4a3000;border-left:3px solid #ffb300;padding:2px 6px;}")
        lines.append("</style></head><body>")
        lines.append(f"<h2>Chat Log — {html.escape(channel_name)}</h2>")

        for msg in messages:
            ts = msg.timestamp.astimezone().strftime("%H:%M:%S")
            ts_span = f"<span class='ts'>[{ts}]</span>"

            if msg.is_system and msg.system_text:
                lines.append(
                    f"<div class='msg system'>{ts_span} *** "
                    f"{html.escape(msg.system_text)} ***</div>"
                )
                if msg.text:
                    lines.append(
                        f"<div class='msg'>{ts_span} "
                        f"{html.escape(msg.user.display_name)}: "
                        f"{html.escape(msg.text)}</div>"
                    )
                continue

            # Reply context
            if msg.reply_parent_display_name:
                lines.append(
                    f"<div class='reply'>{ts_span} Replying to "
                    f"@{html.escape(msg.reply_parent_display_name)}: "
                    f"{html.escape(msg.reply_parent_text)}</div>"
                )

            # Badges
            badge_str = ""
            if msg.user.badges:
                badge_names = [html.escape(b.name) for b in msg.user.badges]
                badge_str = "<span class='badge'>[" + "][".join(badge_names) + "]</span> "

            # Username color
            color = msg.user.color or "#aaa"
            name_span = (
                f"<span style='color:{html.escape(color)};font-weight:bold'>"
                f"{html.escape(msg.user.display_name)}</span>"
            )

            text_escaped = html.escape(msg.text)
            css_class = "msg hype" if msg.is_hype_chat else "msg"

            if msg.is_action:
                lines.append(
                    f"<div class='{css_class}'>{ts_span} {badge_str}"
                    f"{name_span} {text_escaped}</div>"
                )
            else:
                lines.append(
                    f"<div class='{css_class}'>{ts_span} {badge_str}"
                    f"{name_span}: {text_escaped}</div>"
                )

        lines.append("</body></html>")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _export_as_json(self, path: str, messages: list) -> None:
        """Export chat messages as a JSON array."""
        import json

        entries = []
        for msg in messages:
            entry = {
                "id": msg.id,
                "timestamp": msg.timestamp.isoformat(),
                "platform": msg.platform.value,
                "user": {
                    "id": msg.user.id,
                    "name": msg.user.name,
                    "display_name": msg.user.display_name,
                    "color": msg.user.color,
                    "badges": [{"id": b.id, "name": b.name} for b in msg.user.badges],
                },
                "text": msg.text,
                "emotes": [
                    {"id": e.id, "name": e.name, "start": s, "end": end}
                    for s, end, e in msg.emote_positions
                ],
                "is_action": msg.is_action,
                "is_system": msg.is_system,
                "system_text": msg.system_text,
            }
            if msg.reply_parent_display_name:
                entry["reply"] = {
                    "parent_display_name": msg.reply_parent_display_name,
                    "parent_text": msg.reply_parent_text,
                }
            if msg.is_hype_chat:
                entry["hype_chat"] = {
                    "amount": msg.hype_chat_amount,
                    "currency": msg.hype_chat_currency,
                    "level": msg.hype_chat_level,
                }
            entries.append(entry)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)

    def _show_export_error(self, error: str) -> None:
        """Show an error dialog for export failures."""
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "Export Failed", f"Could not export chat log:\n{error}")

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
        at_bottom = value >= scrollbar.maximum() - 10
        if at_bottom:
            if not self._auto_scroll:
                # Returning to bottom — flush any deferred trim
                self._auto_scroll = True
                self._model._trim_paused = False
                self._model.flush_trim()
            self._scroll_pause_timer.stop()
            self._countdown_timer.stop()
            self._new_msg_button.hide()
        elif scrollbar.maximum() > 0:
            # User scrolled up — pause trimming so the view doesn't jump
            self._auto_scroll = False
            self._model._trim_paused = True
            # Reset and (re)start the 5-minute countdown
            self._countdown_remaining = 300
            self._new_msg_button.setText("New messages (5:00)")
            self._scroll_pause_timer.start()
            self._countdown_timer.start()

    def _on_scroll_range_changed(self, _min: int, _max: int) -> None:
        """Keep the view pinned to the bottom when content height changes."""
        if self._auto_scroll and _max > 0:
            self._list_view.scrollToBottom()

    def _dismiss_hype_banner(self) -> None:
        """Dismiss the hype chat pinned banner."""
        self._hype_banner.hide()

    def update_hype_train(self, event: HypeTrainEvent) -> None:
        """Update the hype train banner based on an EventSub event."""
        # Cancel any pending auto-hide
        if self._hype_train_auto_hide_timer:
            self._hype_train_auto_hide_timer.stop()

        if event.type in ("begin", "progress"):
            self._hype_train_level_label.setText(
                f"\U0001f682 Hype Train \u2014 Level {event.level}"
            )
            self._hype_train_progress.setMaximum(max(event.goal, 1))
            self._hype_train_progress.setValue(min(event.total, event.goal))
            self._hype_train_expires_at = event.expires_at
            self._hype_train_tick()  # Update countdown immediately
            self._hype_train_timer.start()
            self._hype_train_banner.show()
        elif event.type == "end":
            self._hype_train_timer.stop()
            self._hype_train_level_label.setText(
                f"\U0001f682 Hype Train Complete! Level {event.level}"
            )
            self._hype_train_progress.setMaximum(1)
            self._hype_train_progress.setValue(1)
            self._hype_train_countdown.setText("")
            self._hype_train_banner.show()
            # Auto-hide after 10 seconds
            self._hype_train_auto_hide_timer = QTimer(self)
            self._hype_train_auto_hide_timer.setSingleShot(True)
            self._hype_train_auto_hide_timer.setInterval(10000)
            self._hype_train_auto_hide_timer.timeout.connect(self._auto_hide_hype_train)
            self._hype_train_auto_hide_timer.start()

    def _hype_train_tick(self) -> None:
        """Update hype train countdown each second."""
        if not self._hype_train_expires_at:
            self._hype_train_countdown.setText("")
            return
        from datetime import datetime, timezone

        try:
            expires = datetime.fromisoformat(self._hype_train_expires_at.replace("Z", "+00:00"))
            remaining = (expires - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                self._hype_train_countdown.setText("Expiring...")
                self._hype_train_timer.stop()
            else:
                mins, secs = divmod(int(remaining), 60)
                self._hype_train_countdown.setText(f"{mins}:{secs:02d} remaining")
        except (ValueError, TypeError):
            self._hype_train_countdown.setText("")

    def _dismiss_hype_train_banner(self) -> None:
        """Dismiss the hype train banner."""
        self._hype_train_timer.stop()
        self._hype_train_banner.hide()
        if self._hype_train_auto_hide_timer:
            self._hype_train_auto_hide_timer.stop()

    def _auto_hide_hype_train(self) -> None:
        """Auto-hide the hype train banner after train ends."""
        self._hype_train_banner.hide()

    def show_raid_banner(self, raid_msg: ChatMessage) -> None:
        """Show the raid banner with raider info and 120s countdown."""
        raider = raid_msg.user.display_name or raid_msg.user.name
        count = raid_msg.raid_viewer_count
        self._raid_base_text = f"\U0001f6a8 {raider} is raiding with {count:,} viewers!"
        self._raid_remaining = 120
        self._raid_label.setText(f"{self._raid_base_text}  ({self._raid_remaining}s)")
        self._raid_banner.show()
        if self._raid_auto_hide_timer:
            self._raid_auto_hide_timer.stop()
        self._raid_auto_hide_timer = QTimer(self)
        self._raid_auto_hide_timer.setInterval(1000)
        self._raid_auto_hide_timer.timeout.connect(self._raid_countdown_tick)
        self._raid_auto_hide_timer.start()

    def _raid_countdown_tick(self) -> None:
        """Update the raid banner countdown each second."""
        self._raid_remaining -= 1
        if self._raid_remaining <= 0:
            self._dismiss_raid_banner()
            return
        self._raid_label.setText(f"{self._raid_base_text}  ({self._raid_remaining}s)")

    def _dismiss_raid_banner(self) -> None:
        """Dismiss the raid banner."""
        self._raid_banner.hide()
        if self._raid_auto_hide_timer:
            self._raid_auto_hide_timer.stop()

    def _start_reply(self, message: ChatMessage) -> None:
        """Enter reply mode for the given message."""
        self._reply_to_msg = message
        self._reply_label.setText(f"Replying to @{message.user.display_name}")
        self._reply_widget.show()
        self._input.setFocus()

    def _cancel_reply(self) -> None:
        """Exit reply mode."""
        self._reply_to_msg = None
        self._reply_widget.hide()

    def _update_banner_style(self) -> None:
        """Apply banner colors from theme."""
        theme = get_theme()
        self._title_banner.applyBannerStyle(theme.chat_banner_bg, theme.chat_banner_text)
        self._socials_banner.applyBannerStyle(theme.chat_banner_bg, theme.chat_banner_text)

    def _update_stream_title(self) -> None:
        """Update the stream title banner from the livestream data."""
        title = ""
        if self.livestream and self.livestream.title:
            title = self.livestream.title
        # Show if we have a title, setting is enabled, and user hasn't dismissed
        if title and self.settings.show_stream_title and not self._title_dismissed:
            # Convert !commands to clickable links
            html_title = self._format_title_with_commands(title)
            # Append viewer count and uptime on a second line
            meta_parts: list[str] = []
            if self.livestream and self.livestream.live:
                if self.livestream.viewers:
                    meta_parts.append(f"\U0001f464 {self.livestream.viewers_str}")
                uptime = self.livestream.uptime_str
                if uptime:
                    meta_parts.append(f"\U0001f550 {uptime}")
            if meta_parts:
                meta_html = " &nbsp;\u00b7&nbsp; ".join(meta_parts)
                html_title += (
                    f'<br><span style="font-size: 10px; opacity: 0.7;">'
                    f"{meta_html}</span>"
                )
            self._title_banner.setText(html_title)
            self._title_banner.setToolTip(title)  # Full title on hover (plain text)
            self._title_banner.show()
        else:
            self._title_banner.hide()

    def _on_title_dismissed(self) -> None:
        """Handle title banner dismissal."""
        self._title_dismissed = True
        self._title_refresh_timer.stop()

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
            # Normalize Unicode fancy text (italic/bold/script) to plain ASCII
            command = unicodedata.normalize("NFKC", command)
            self._input.setText(command)
            self._input.setFocus()

    def update_livestream(self, livestream: Livestream) -> None:
        """Update the livestream data and refresh the title."""
        self.livestream = livestream
        self._update_stream_title()
        if livestream and livestream.live:
            self._title_refresh_timer.start()
        else:
            self._title_refresh_timer.stop()

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
            "discord": "\U0001f4ac",  # Speech bubble
            "instagram": "\U0001f4f7",  # Camera
            "twitter": "\U0001f426",  # Bird
            "x": "\U0001f426",  # Bird (X/Twitter)
            "tiktok": "\U0001f3b5",  # Musical note
            "youtube": "\U0001f3ac",  # Clapper
            "facebook": "\U0001f465",  # People
            "patreon": "\U0001f49b",  # Yellow heart
            "merch": "\U0001f455",  # T-shirt
        }

        links = []
        for platform, url in socials.items():
            icon = social_icons.get(platform.lower(), "\U0001f517")  # Link emoji default
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

    def update_room_state(self, state: ChatRoomState) -> None:
        """Update the room state indicator label."""
        # Cancel slow mode countdown if slow mode was disabled
        if self._room_state and self._room_state.slow > 0 and state.slow == 0:
            if self._slow_mode_timer.isActive():
                self._restore_input_after_slow_mode()
        self._room_state = state
        parts: list[str] = []
        if state.subs_only:
            parts.append("Sub-only")
        if state.emote_only:
            parts.append("Emote-only")
        if state.r9k:
            parts.append("R9K")
        if state.slow > 0:
            parts.append(f"Slow ({state.slow}s)")
        if state.followers_only >= 0:
            if state.followers_only == 0:
                parts.append("Followers-only")
            else:
                parts.append(f"Followers-only ({state.followers_only}m)")
        if parts:
            self._room_state_label.setText(" | ".join(parts))
            # New modes → reset dismissed state so user sees changes
            if self._room_state_dismissed:
                self._room_state_dismissed = False
            self._room_state_widget.show()
        else:
            self._room_state_widget.hide()
            self._room_state_dismissed = False

    def _dismiss_room_state(self) -> None:
        """Dismiss the room state indicator."""
        self._room_state_dismissed = True
        self._room_state_widget.hide()

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

            /* Room state indicator */
            #chat_room_state {{
                background-color: {theme.accent};
                border-bottom: 1px solid {theme.border_light};
                margin-top: 3px;
            }}
            #chat_room_state_label {{
                color: white;
                font-size: 11px;
                font-weight: bold;
                background: transparent;
            }}
            #chat_room_state_close {{
                background: rgba(0, 0, 0, 0.2);
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 12px;
                font-weight: bold;
            }}
            #chat_room_state_close:hover {{
                background: rgba(255, 100, 100, 0.5);
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

            /* Reply indicator bar */
            #chat_reply_widget {{
                background-color: {theme.chat_input_bg};
                border-left: 3px solid {theme.accent};
                border-bottom: 1px solid {theme.border_light};
            }}
            #chat_reply_label {{
                color: {theme.text_muted};
                font-size: 11px;
                background: transparent;
            }}
            #chat_reply_close {{
                background: transparent;
                color: {theme.text_muted};
                border: none;
                font-size: 12px;
            }}
            #chat_reply_close:hover {{
                color: {theme.text_primary};
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
        self._title_banner.applyBannerStyle(theme.chat_banner_bg, theme.chat_banner_text)
        self._socials_banner.applyBannerStyle(theme.chat_banner_bg, theme.chat_banner_text)

        # Update delegate theme and force repaint to show new colors
        self._delegate.apply_theme()
        self._list_view.viewport().update()

        # Update completers theme
        self._emote_completer.apply_theme()
        self._mention_completer.apply_theme()
        if self._spell_completer:
            self._spell_completer.apply_theme()

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

        # Spellcheck toggle
        spell_action = menu.addAction("Spellcheck")
        spell_action.setCheckable(True)
        spell_action.setChecked(self.settings.spellcheck_enabled)
        spell_action.toggled.connect(self._toggle_spellcheck)

        # Autocorrect toggle
        autocorrect_action = menu.addAction("Auto-Correct")
        autocorrect_action.setCheckable(True)
        autocorrect_action.setChecked(self.settings.autocorrect_enabled)
        autocorrect_action.setEnabled(self.settings.spellcheck_enabled)
        autocorrect_action.toggled.connect(self._toggle_autocorrect)
        spell_action.toggled.connect(autocorrect_action.setEnabled)

        # User card hover toggle
        hover_action = menu.addAction("User Card on Hover")
        hover_action.setCheckable(True)
        hover_action.setChecked(self.settings.user_card_hover)
        hover_action.toggled.connect(self._toggle_user_card_hover)

        menu.addSeparator()

        # Title banner toggle
        title_action = menu.addAction("Show Title Banner")
        title_action.setCheckable(True)
        title_action.setChecked(self.settings.show_stream_title)
        title_action.toggled.connect(self._toggle_title_banner)

        # Socials banner toggle
        socials_action = menu.addAction("Show Socials Banner")
        socials_action.setCheckable(True)
        socials_action.setChecked(self.settings.show_socials_banner)
        socials_action.toggled.connect(self._toggle_socials_banner)

        menu.addSeparator()

        # Always on top toggle
        aot_action = menu.addAction("Always on Top")
        aot_action.setCheckable(True)
        aot_action.setChecked(self.settings.always_on_top)
        aot_action.toggled.connect(self._toggle_always_on_top)

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

    def _toggle_spellcheck(self, checked: bool) -> None:
        """Toggle spellcheck on the input field."""
        self.settings.spellcheck_enabled = checked
        self.set_spellcheck_enabled(checked)
        self.set_autocorrect_enabled(checked and self.settings.autocorrect_enabled)
        self.settings_changed.emit()

    def _toggle_autocorrect(self, checked: bool) -> None:
        """Toggle autocorrect on the input field."""
        self.settings.autocorrect_enabled = checked
        self.set_autocorrect_enabled(checked and self.settings.spellcheck_enabled)
        self.settings_changed.emit()

    def _toggle_user_card_hover(self, checked: bool) -> None:
        """Toggle user card on hover."""
        self.settings.user_card_hover = checked
        if not checked:
            self._card_hover_timer.stop()
            self._card_hover_user = None
        self.settings_changed.emit()

    def _toggle_title_banner(self, checked: bool) -> None:
        """Toggle title banner visibility."""
        self.settings.show_stream_title = checked
        self.update_banner_settings()
        self.settings_changed.emit()

    def _toggle_socials_banner(self, checked: bool) -> None:
        """Toggle socials banner visibility."""
        self.settings.show_socials_banner = checked
        self.update_banner_settings()
        self.settings_changed.emit()

    def _toggle_always_on_top(self, checked: bool) -> None:
        """Toggle always on top."""
        self.settings.always_on_top = checked
        self.always_on_top_changed.emit(checked)
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

        # Username click → show user card; URL click → open browser
        # @mention click → show conversation; reply context click → show conversation
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
                            self._show_user_card(message.user, event.globalPos())
                            return True
                        url = self._delegate._get_url_at_position(event.pos(), option, message)
                        if url:
                            try:
                                webbrowser.open(url)
                            except Exception as e:
                                logger.error(f"Failed to open URL: {e}")
                            return True
                        mention = self._delegate._get_mention_at_position(
                            event.pos(), option, message
                        )
                        if mention:
                            self._show_conversation(message.user.display_name, mention)
                            return True
                        reply_rect = self._delegate._get_reply_context_rect(option, message)
                        if reply_rect.isValid() and reply_rect.contains(event.pos()):
                            # Open reply thread rooted at the parent message
                            root_id = message.reply_parent_msg_id or message.id
                            self._show_reply_thread(root_id)
                            return True

        # Cursor changes for clickable elements (URLs, usernames, @mentions, reply context)
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
                        # Start hover timer for user card
                        if self.settings.user_card_hover and (
                            not self._card_hover_timer.isActive()
                            or self._card_hover_user != message.user
                        ):
                            self._card_hover_user = message.user
                            self._card_hover_pos = event.globalPos()
                            self._card_hover_timer.start()
                        return False
                    url = self._delegate._get_url_at_position(event.pos(), option, message)
                    if url:
                        viewport.setCursor(Qt.CursorShape.PointingHandCursor)
                        return False
                    mention = self._delegate._get_mention_at_position(event.pos(), option, message)
                    if mention:
                        viewport.setCursor(Qt.CursorShape.PointingHandCursor)
                        return False
                    reply_rect = self._delegate._get_reply_context_rect(option, message)
                    if reply_rect.isValid() and reply_rect.contains(event.pos()):
                        viewport.setCursor(Qt.CursorShape.PointingHandCursor)
                        return False
            viewport.setCursor(Qt.CursorShape.ArrowCursor)
            # Cancel hover timer when not over a username
            if self._card_hover_timer.isActive():
                self._card_hover_timer.stop()
                self._card_hover_user = None
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
                        QToolTip.showText(tip_pos, badge.title or badge.name, viewport)
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

                    # Check URLs for link preview tooltips
                    url = self._delegate._get_url_at_position(event.pos(), option, message)
                    if url:
                        from urllib.parse import urlparse

                        domain = urlparse(url).netloc
                        title = self._link_preview_cache.get_or_fetch(url, parent=self)
                        if title is None:
                            QToolTip.showText(tip_pos, f"{domain}\nLoading...", viewport)
                        elif title:
                            QToolTip.showText(tip_pos, f"{title}\n{domain}", viewport)
                        else:
                            QToolTip.showText(tip_pos, domain, viewport)
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

    def _show_reply_thread(self, root_msg_id: str) -> None:
        """Show a reply thread dialog for the given root message ID."""
        dialog = ReplyThreadDialog(
            root_msg_id=root_msg_id,
            messages=list(self._model._messages),
            settings=self.settings,
            image_store=self._image_store,
            parent=self,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: self._history_dialogs.discard(dialog))
        self._history_dialogs.add(dialog)
        dialog.show()

    def _show_conversation(self, user_a_name: str, user_b_name: str) -> None:
        """Show a dialog with the conversation between two users.

        A message is considered part of the conversation if it's from user A
        mentioning/replying-to B, or from user B mentioning/replying-to A.
        """
        if user_a_name.lower() == user_b_name.lower():
            return

        def matches_conversation(msg: ChatMessage) -> bool:
            name = msg.user.display_name.lower()
            a_low = user_a_name.lower()
            b_low = user_b_name.lower()
            if name == a_low:
                other = b_low
            elif name == b_low:
                other = a_low
            else:
                return False
            # Check reply parent
            if msg.reply_parent_display_name and msg.reply_parent_display_name.lower() == other:
                return True
            # Check @mentions in text
            for m in re.finditer(r"@(\w+)", msg.text):
                if m.group(1).lower() == other:
                    return True
            return False

        convo_messages = [msg for msg in self._model._messages if matches_conversation(msg)]

        dialog = ConversationDialog(
            user_a_name=user_a_name,
            user_b_name=user_b_name,
            messages=convo_messages,
            settings=self.settings,
            image_store=self._image_store,
            parent=self,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: self._history_dialogs.discard(dialog))
        self._history_dialogs.add(dialog)
        dialog.show()

    def _on_card_hover_timeout(self) -> None:
        """Show user card after hover delay."""
        if not self._card_hover_user:
            return
        # Skip if the card for this exact user is already showing
        try:
            if (
                self._active_user_card
                and self._active_user_card.isVisible()
                and self._active_user_card._user.id == self._card_hover_user.id
            ):
                return
        except RuntimeError:
            # C++ object deleted
            self._active_user_card = None
        self._show_user_card(self._card_hover_user, self._card_hover_pos)

    def _show_user_card(self, user, pos) -> None:
        """Show a user card popup at the given global position."""
        from ...chat.models import ChatUser

        if not isinstance(user, ChatUser):
            return

        # Close any existing user card
        if self._active_user_card:
            try:
                self._active_user_card.close()
            except RuntimeError:
                pass  # C++ object already deleted
            self._active_user_card = None

        # Count messages from this user
        message_count = sum(
            1
            for msg in self._model._messages
            if msg.user.id == user.id and msg.user.platform == user.platform
        )

        card = UserCardPopup(
            user=user,
            message_count=message_count,
            settings=self.settings,
            image_store=self._image_store,
            parent=self,
        )
        card.history_requested.connect(lambda u=user: self._show_user_history(u))
        card.show_at(pos)
        self._active_user_card = card

        # Async fetch for Twitch, YouTube, and Kick users
        if user.platform == StreamPlatform.TWITCH:
            self._fetch_user_card_info(card, user.name)
        elif user.platform == StreamPlatform.YOUTUBE:
            self._fetch_youtube_user_card_info(card, user.id)
        elif user.platform == StreamPlatform.KICK:
            self._fetch_kick_user_card_info(card, user.name)

    def _fetch_user_card_info(self, card: UserCardPopup, login: str) -> None:
        """Fetch Twitch user card info, pronouns, and avatar asynchronously."""
        import asyncio

        from PySide6.QtCore import QThread

        # Extract channel login from channel_key (format: "twitch:channelname")
        channel_login = ""
        if ":" in self.channel_key:
            channel_login = self.channel_key.split(":", 1)[-1]

        class _CardFetchThread(QThread):
            def __init__(self, login, channel_login, parent=None):
                super().__init__(parent)
                self._login = login
                self._channel_login = channel_login
                self.result: dict | None = None
                self.pronouns: str = ""
                self.avatar_data: bytes = b""

            def run(self):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # Fetch user info and pronouns in parallel
                    user_info_coro = UserCardFetchWorker.fetch_twitch_user_info(
                        self._login, self._channel_login
                    )
                    pronouns_coro = UserCardFetchWorker.fetch_pronouns(self._login)
                    results = loop.run_until_complete(asyncio.gather(user_info_coro, pronouns_coro))
                    self.result = results[0]
                    self.pronouns = results[1]
                    # Fetch avatar if we got a URL
                    if self.result and self.result.get("profile_image_url"):
                        self.avatar_data = loop.run_until_complete(
                            UserCardFetchWorker.fetch_avatar(self.result["profile_image_url"])
                        )
                except Exception as e:
                    logger.debug(f"User card fetch error for {self._login}: {e}")
                finally:
                    loop.close()

        thread = _CardFetchThread(login, channel_login, parent=self)

        def on_finished():
            if thread.result:
                card.update_created_at(thread.result.get("created_at", ""))
                card.update_bio(thread.result.get("description", ""))
                card.update_followers(thread.result.get("follower_count", 0))
                card.update_follow_age(thread.result.get("followed_at", ""))
            else:
                card.update_created_at("")
            if thread.pronouns:
                card.update_pronouns(thread.pronouns)
            if thread.avatar_data:
                card.update_avatar(thread.avatar_data)

        thread.finished.connect(on_finished)
        thread.start()

    def _fetch_youtube_user_card_info(self, card: UserCardPopup, channel_id: str) -> None:
        """Fetch YouTube user card info and avatar asynchronously."""
        import asyncio

        from PySide6.QtCore import QThread

        class _YTCardFetchThread(QThread):
            def __init__(self, channel_id, parent=None):
                super().__init__(parent)
                self._channel_id = channel_id
                self.result: dict | None = None
                self.avatar_data: bytes = b""

            def run(self):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    self.result = loop.run_until_complete(
                        UserCardFetchWorker.fetch_youtube_user_info(self._channel_id)
                    )
                    if self.result and self.result.get("avatar_url"):
                        self.avatar_data = loop.run_until_complete(
                            UserCardFetchWorker.fetch_avatar(self.result["avatar_url"])
                        )
                except Exception as e:
                    logger.debug(f"YouTube user card fetch error for {self._channel_id}: {e}")
                finally:
                    loop.close()

        thread = _YTCardFetchThread(channel_id, parent=self)

        def on_finished():
            if thread.result:
                card.update_bio(thread.result.get("description", ""))
                card.update_created_at(thread.result.get("joined_date_text", ""))
                card.update_subscribers(thread.result.get("subscriber_count_text", ""))
                card.update_country(thread.result.get("country", ""))
            else:
                if card._created_label:
                    card._created_label.setText("Joined: Unknown")
            if thread.avatar_data:
                card.update_avatar(thread.avatar_data)

        thread.finished.connect(on_finished)
        thread.start()

    def _fetch_kick_user_card_info(self, card: UserCardPopup, slug: str) -> None:
        """Fetch Kick user card info and avatar asynchronously."""
        import asyncio

        from PySide6.QtCore import QThread

        class _KickCardFetchThread(QThread):
            def __init__(self, slug, parent=None):
                super().__init__(parent)
                self._slug = slug
                self.result: dict | None = None
                self.avatar_data: bytes = b""

            def run(self):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    self.result = loop.run_until_complete(
                        UserCardFetchWorker.fetch_kick_user_info(self._slug)
                    )
                    if self.result and self.result.get("profile_pic_url"):
                        self.avatar_data = loop.run_until_complete(
                            UserCardFetchWorker.fetch_avatar(
                                self.result["profile_pic_url"]
                            )
                        )
                except Exception as e:
                    logger.debug(f"Kick user card fetch error for {self._slug}: {e}")
                finally:
                    loop.close()

        thread = _KickCardFetchThread(slug, parent=self)

        def on_finished():
            if thread.result:
                card.update_bio(thread.result.get("bio", ""))
                card.update_followers(thread.result.get("followers_count", 0))
                card.update_country(thread.result.get("country", ""))
                card.update_verified(thread.result.get("verified", False))
                # Kick API doesn't expose account creation date
                if card._created_label:
                    card._created_label.hide()
            else:
                if card._created_label:
                    card._created_label.hide()
            if thread.avatar_data:
                card.update_avatar(thread.avatar_data)

        thread.finished.connect(on_finished)
        thread.start()

    def _show_emote_picker(self) -> None:
        """Show the emote picker popup above the emote button."""
        if self._emotes_by_provider:
            self._emote_picker.set_emotes(
                self._emotes_by_provider,
                self._channel_emote_names,
                self._locked_emote_names,
            )
        if self._image_store:
            self._emote_picker.set_image_store(self._image_store)
        if self._gif_timer:
            self._emote_picker.set_gif_timer(self._gif_timer)
        # Position above the emote button
        btn_pos = self._emote_button.mapToGlobal(self._emote_button.rect().topLeft())
        picker_pos = btn_pos - QPoint(0, self._emote_picker.height())
        self._emote_picker.show_picker(picker_pos)

    def _insert_emote(self, emote_name: str) -> None:
        """Insert an emote name at the cursor position in the input."""
        current = self._input.text()
        cursor_pos = self._input.cursorPosition()
        # Add space before if not at start and previous char isn't space
        prefix = " " if current and cursor_pos > 0 and current[cursor_pos - 1] != " " else ""
        suffix = " "
        new_text = current[:cursor_pos] + prefix + emote_name + suffix + current[cursor_pos:]
        self._input.setText(new_text)
        self._input.setCursorPosition(cursor_pos + len(prefix) + len(emote_name) + len(suffix))
        self._input.setFocus()

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
        self._model = ChatMessageModel(max_messages=settings.max_messages, parent=self)
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
                prefix = f"[{message.timestamp.astimezone().strftime(self._settings.ts_strftime)}] "
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


class ConversationDialog(QDialog, ChatSearchMixin):
    """Dialog showing the conversation between two users (via @mentions and replies)."""

    def __init__(
        self,
        user_a_name: str,
        user_b_name: str,
        messages: list[ChatMessage],
        settings: BuiltinChatSettings,
        image_store: EmoteCache | None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)
        self._user_a_name = user_a_name
        self._user_b_name = user_b_name
        self._settings = settings
        self.setWindowTitle(f"Conversation \u2014 {user_a_name} & {user_b_name}")
        self.setMinimumSize(400, 300)
        self.resize(450, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        self._header = QLabel(f"  {user_a_name} & {user_b_name} \u2014 {len(messages)} messages")
        layout.addWidget(self._header)

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
        self._model = ChatMessageModel(max_messages=settings.max_messages, parent=self)
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

    def _matches_conversation(self, msg: ChatMessage) -> bool:
        """Check if a message is part of the conversation between user A and user B."""
        name = msg.user.display_name.lower()
        a_low = self._user_a_name.lower()
        b_low = self._user_b_name.lower()
        if name == a_low:
            other = b_low
        elif name == b_low:
            other = a_low
        else:
            return False
        if msg.reply_parent_display_name and msg.reply_parent_display_name.lower() == other:
            return True
        for m in re.finditer(r"@(\w+)", msg.text):
            if m.group(1).lower() == other:
                return True
        return False

    def apply_theme(self) -> None:
        """Apply the current theme to the dialog."""
        theme = get_theme()

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {theme.window_bg};
            }}
        """)

        self._header.setStyleSheet(f"""
            QLabel {{
                background-color: {theme.chat_input_bg};
                color: {theme.text_primary};
                padding: 8px;
                font-weight: bold;
                font-size: 13px;
            }}
        """)

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

        self._list_view.setStyleSheet(f"""
            QListView {{
                background-color: {theme.chat_bg};
                border: none;
                padding: 4px;
            }}
        """)

        self._delegate.apply_theme()
        self._list_view.viewport().update()

    def add_messages(self, messages: list[ChatMessage]) -> None:
        """Add new messages that match the conversation (called by ChatWidget)."""
        convo_msgs = [msg for msg in messages if self._matches_conversation(msg)]
        if not convo_msgs:
            return

        was_at_bottom = self._is_at_bottom()
        self._model.add_messages(convo_msgs)

        count = self._model.rowCount()
        self._header.setText(f"  {self._user_a_name} & {self._user_b_name} \u2014 {count} messages")

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
                prefix = f"[{message.timestamp.astimezone().strftime(self._settings.ts_strftime)}] "
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


class ReplyThreadDialog(QDialog, ChatSearchMixin):
    """Dialog showing a reply thread: the original message and all replies to it."""

    def __init__(
        self,
        root_msg_id: str,
        messages: list[ChatMessage],
        settings: BuiltinChatSettings,
        image_store: EmoteCache | None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)
        self._root_msg_id = root_msg_id
        self._settings = settings

        # Collect thread messages: root + all replies (direct and nested)
        thread_ids = self._collect_thread_ids(root_msg_id, messages)
        thread_messages = [m for m in messages if m.id in thread_ids]
        thread_messages.sort(key=lambda m: m.timestamp)

        # Find root message for title
        root_msg = next((m for m in messages if m.id == root_msg_id), None)
        root_name = root_msg.user.display_name if root_msg else "Unknown"
        self.setWindowTitle(f"Reply Thread \u2014 @{root_name}")
        self.setMinimumSize(400, 300)
        self.resize(450, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        self._header = QLabel(f"  Reply thread \u2014 {len(thread_messages)} messages")
        layout.addWidget(self._header)

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

        # Message list
        self._model = ChatMessageModel(max_messages=settings.max_messages, parent=self)
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

        self._list_view.viewport().setMouseTracking(True)
        self._list_view.viewport().installEventFilter(self)

        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self._list_view)
        copy_shortcut.activated.connect(self._copy_selected_messages)

        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self._toggle_search)

        self._resize_timer: QTimer | None = None

        self._model.add_messages(thread_messages)
        self._list_view.scrollToBottom()
        self.apply_theme()

    @staticmethod
    def _collect_thread_ids(root_id: str, messages: list[ChatMessage]) -> set[str]:
        """Collect the root message and all descendants in the reply chain."""
        thread_ids = {root_id}
        changed = True
        while changed:
            changed = False
            for msg in messages:
                if msg.id not in thread_ids and msg.reply_parent_msg_id in thread_ids:
                    thread_ids.add(msg.id)
                    changed = True
        return thread_ids

    def _matches_thread(self, msg: ChatMessage) -> bool:
        """Check if a message belongs to this thread."""
        return msg.id == self._root_msg_id or msg.reply_parent_msg_id == self._root_msg_id

    def add_messages(self, messages: list[ChatMessage]) -> None:
        """Add new messages that belong to this thread."""
        thread_msgs = [msg for msg in messages if self._matches_thread(msg)]
        if not thread_msgs:
            return

        was_at_bottom = self._is_at_bottom()
        self._model.add_messages(thread_msgs)

        count = self._model.rowCount()
        self._header.setText(f"  Reply thread \u2014 {count} messages")

        if was_at_bottom:
            self._list_view.scrollToBottom()

    def apply_moderation(self, event) -> None:
        """Apply a moderation event to messages in this dialog."""
        self._model.apply_moderation(event)

    def _is_at_bottom(self) -> bool:
        scrollbar = self._list_view.verticalScrollBar()
        return scrollbar.value() >= scrollbar.maximum() - 10

    def _copy_selected_messages(self) -> None:
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
                ts = message.timestamp.astimezone().strftime(self._settings.ts_strftime)
                prefix = f"[{ts}] "
            name = message.user.display_name
            if message.is_action:
                lines.append(f"{prefix}{name} {message.text}")
            else:
                lines.append(f"{prefix}{name}: {message.text}")
        if lines:
            clipboard = QApplication.clipboard()
            clipboard.setText("\n".join(lines))

    def apply_theme(self) -> None:
        """Apply the current theme to the reply thread dialog."""
        theme = get_theme()
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {theme.window_bg};
            }}
        """)
        self._header.setStyleSheet(f"""
            QLabel {{
                background-color: {theme.chat_input_bg};
                color: {theme.text_primary};
                padding: 8px;
                font-weight: bold;
                font-size: 13px;
            }}
        """)
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
        self._list_view.setStyleSheet(f"""
            QListView {{
                background-color: {theme.chat_bg};
                border: none;
                padding: 4px;
            }}
        """)
        self._delegate.apply_theme()
        self._list_view.viewport().update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Invalidate item layout cache on resize."""
        super().resizeEvent(event)
        self._list_view.scheduleDelayedItemsLayout()
        if self._resize_timer is None:
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._on_resize_debounced)
        self._resize_timer.start(30)

    def _on_resize_debounced(self) -> None:
        self._delegate.invalidate_size_cache()
        self._model.layoutChanged.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Handle Escape to close search bar in reply thread."""
        if self._handle_search_key_press(event.key()):
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802
        """Handle Ctrl+Wheel, URL clicks, and cursor changes in reply thread."""
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
