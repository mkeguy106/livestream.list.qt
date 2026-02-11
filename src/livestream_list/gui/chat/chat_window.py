"""Chat window with tabbed multi-channel support."""

import logging

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ...chat.manager import ChatManager
from ...chat.models import HypeTrainEvent, ModerationEvent
from ...core.models import Livestream, StreamPlatform
from ...core.settings import Settings
from ..theme import PLATFORM_COLORS as THEME_PLATFORM_COLORS
from ..theme import get_theme
from ..window_utils import (
    apply_always_on_top,
    apply_always_on_top_qt,
    is_kde_plasma,
    kwin_set_keep_above,
)
from .chat_widget import ChatWidget

logger = logging.getLogger(__name__)

# Platform colors for tab icons (from theme)
PLATFORM_COLORS = {
    StreamPlatform.TWITCH: QColor(THEME_PLATFORM_COLORS.get("twitch", "#9146ff")),
    StreamPlatform.YOUTUBE: QColor(THEME_PLATFORM_COLORS.get("youtube", "#ff0000")),
    StreamPlatform.KICK: QColor(THEME_PLATFORM_COLORS.get("kick", "#53fc18")),
}


def _create_dot_icon(color: QColor, size: int = 12) -> QIcon:
    """Create a small colored dot icon for tab indicators."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


class _NewWhisperDialog(QDialog):
    """Dialog for starting a new whisper conversation."""

    def __init__(self, known_usernames: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Whisper")
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        label = QLabel("Twitch username:")
        layout.addWidget(label)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Enter a Twitch username...")
        if known_usernames:
            completer = QCompleter(known_usernames, self)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self._input.setCompleter(completer)
        layout.addWidget(self._input)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        ok_btn = QPushButton("Open DM")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        theme = get_theme()
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {theme.window_bg};
                color: {theme.text_primary};
            }}
            QLabel {{
                color: {theme.text_primary};
                font-size: 13px;
            }}
            QLineEdit {{
                background-color: {theme.chat_input_bg};
                border: 1px solid {theme.border};
                border-radius: 4px;
                padding: 6px 8px;
                color: {theme.text_primary};
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {theme.accent};
            }}
            QPushButton {{
                background-color: {theme.accent};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {theme.accent_hover};
            }}
        """)

    def get_username(self) -> str:
        return self._input.text()


class _TabButton(QWidget):
    """Individual tab button with icon, label, and close button."""

    clicked = Signal()
    close_clicked = Signal()

    def __init__(self, icon: QIcon | None, text: str, closable: bool = True, parent=None):
        super().__init__(parent)
        self._active = False
        self._text = text
        self._icon = icon
        theme = get_theme()
        self._active_color = theme.chat_tab_active
        self._inactive_color = theme.chat_tab_inactive
        self._hover = False
        self._flash_timer: QTimer | None = None
        self._flash_on = False
        self._flash_color = "#ff6600"  # Orange flash color for mentions

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(4)

        # Icon label
        if icon:
            icon_label = QLabel()
            icon_label.setPixmap(icon.pixmap(QSize(12, 12)))
            icon_label.setFixedSize(14, 14)
            icon_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            icon_label.setStyleSheet("background: transparent;")
            layout.addWidget(icon_label)

        # Text label
        self._label = QLabel(text)
        theme = get_theme()
        self._label.setStyleSheet(f"color: {theme.text_secondary}; background: transparent;")
        layout.addWidget(self._label)

        # Close button
        if closable:
            self._close_btn = QPushButton("\u00d7")
            self._close_btn.setFixedSize(18, 18)
            self._close_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {theme.text_muted};
                    background: transparent;
                    border: none;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 0;
                }}
                QPushButton:hover {{
                    color: {theme.text_primary};
                    background-color: rgba(255, 255, 255, 0.15);
                    border-radius: 9px;
                }}
            """)
            self._close_btn.clicked.connect(self.close_clicked.emit)
            layout.addWidget(self._close_btn)
        else:
            self._close_btn = None

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_style()

    def set_colors(self, active_color: str, inactive_color: str) -> None:
        """Update tab colors."""
        self._active_color = active_color
        self._inactive_color = inactive_color
        self._update_style()

    def set_active(self, active: bool) -> None:
        """Set whether this tab is the active/selected one."""
        self._active = active
        self._update_style()

    def _update_style(self) -> None:
        theme = get_theme()
        if self._active:
            bg = self._active_color
            text_color = theme.selection_text
        else:
            bg = self._inactive_color
            text_color = theme.text_secondary
        self.setStyleSheet(f"""
            _TabButton {{
                background-color: {bg};
                border: none;
                border-radius: 0px;
            }}
        """)
        self._label.setStyleSheet(f"color: {text_color}; background: transparent;")
        # Update close button if present
        if self._close_btn:
            self._close_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {theme.text_muted};
                    background: transparent;
                    border: none;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 0;
                }}
                QPushButton:hover {{
                    color: {theme.text_primary};
                    background-color: rgba(255, 255, 255, 0.15);
                    border-radius: 9px;
                }}
            """)

    def start_flash(self, duration_ms: int = 60000) -> None:
        """Start flashing the tab background for @mention notification."""
        if self._flash_timer:
            return  # Already flashing
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(500)
        self._flash_timer.timeout.connect(self._toggle_flash)
        self._flash_on = False
        self._flash_timer.start()
        # Auto-stop after duration
        QTimer.singleShot(duration_ms, self.stop_flash)

    def stop_flash(self) -> None:
        """Stop flashing and restore normal style."""
        if self._flash_timer:
            self._flash_timer.stop()
            self._flash_timer.deleteLater()
            self._flash_timer = None
        self._flash_on = False
        self._update_style()

    def _toggle_flash(self) -> None:
        """Toggle between flash color and normal color."""
        self._flash_on = not self._flash_on
        if self._flash_on:
            self.setStyleSheet(f"""
                _TabButton {{
                    background-color: {self._flash_color};
                    border: none;
                    border-radius: 0px;
                }}
            """)
            self._label.setStyleSheet("color: #fff; background: transparent;")
        else:
            self._update_style()

    def enterEvent(self, event) -> None:  # noqa: N802
        if not self._active and not self._flash_timer:
            theme = get_theme()
            self.setStyleSheet(f"""
                _TabButton {{
                    background-color: {theme.popup_hover};
                    border: none;
                }}
            """)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._update_style()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        """Enable stylesheet backgrounds on custom QWidget subclass."""
        from PySide6.QtWidgets import QStyle, QStyleOption

        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _FlowTabBar(QWidget):
    """Container that positions tab buttons in a wrapping flow layout."""

    tab_clicked = Signal(int)
    tab_close_requested = Signal(int)
    context_menu_requested = Signal(int, object)  # index, QPoint(global)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tabs: list[_TabButton] = []
        theme = get_theme()
        self._active_color = theme.chat_tab_active
        self._inactive_color = theme.chat_tab_inactive
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(30)
        self.setStyleSheet(f"background-color: {theme.window_bg};")

    def add_tab(self, icon: QIcon | None, text: str) -> int:
        """Add a new tab button, return its index."""
        btn = _TabButton(icon, text, closable=True, parent=self)
        btn.set_colors(self._active_color, self._inactive_color)
        idx = len(self._tabs)
        btn.clicked.connect(lambda i=idx: self.tab_clicked.emit(i))
        btn.close_clicked.connect(lambda i=idx: self.tab_close_requested.emit(i))
        self._tabs.append(btn)
        btn.show()
        self._relayout()
        return idx

    def remove_tab(self, index: int) -> None:
        """Remove a tab button at index."""
        if 0 <= index < len(self._tabs):
            btn = self._tabs.pop(index)
            btn.deleteLater()
            # Reconnect signals with corrected indices
            for i, tab in enumerate(self._tabs):
                tab.clicked.disconnect()
                tab.close_clicked.disconnect()
                tab.clicked.connect(lambda idx=i: self.tab_clicked.emit(idx))
                tab.close_clicked.connect(lambda idx=i: self.tab_close_requested.emit(idx))
            self._relayout()

    def set_current(self, index: int) -> None:
        """Set which tab is visually active."""
        for i, tab in enumerate(self._tabs):
            tab.set_active(i == index)

    def set_colors(self, active_color: str, inactive_color: str) -> None:
        """Update colors on all tab buttons and store for new tabs."""
        self._active_color = active_color
        self._inactive_color = inactive_color
        for tab in self._tabs:
            tab.set_colors(active_color, inactive_color)

    def start_flash(self, index: int) -> None:
        """Start flashing a tab at the given index."""
        if 0 <= index < len(self._tabs):
            self._tabs[index].start_flash()

    def stop_flash(self, index: int) -> None:
        """Stop flashing a tab at the given index."""
        if 0 <= index < len(self._tabs):
            self._tabs[index].stop_flash()

    def count(self) -> int:
        return len(self._tabs)

    def tab_at(self, pos) -> int:
        """Find which tab index is at a local position, or -1."""
        for i, tab in enumerate(self._tabs):
            if tab.geometry().contains(pos):
                return i
        return -1

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        """Position tab buttons in a wrapping flow layout."""
        if not self._tabs:
            self.setFixedHeight(30)
            return

        x = 2
        y = 2
        row_height = 0
        available_width = self.width() - 4

        for tab in self._tabs:
            tab_size = tab.sizeHint()
            w = tab_size.width()
            h = tab_size.height()

            if x + w > available_width and x > 2:
                # Wrap to next row
                x = 2
                y += row_height + 2
                row_height = 0

            tab.move(x, y)
            tab.resize(w, h)
            x += w + 2
            row_height = max(row_height, h)

        total_height = y + row_height + 4
        self.setFixedHeight(max(30, total_height))

    def _on_context_menu(self, pos) -> None:
        index = self.tab_at(pos)
        if index >= 0:
            self.context_menu_requested.emit(index, self.mapToGlobal(pos))


class FlowTabWidget(QWidget):
    """Tab widget with wrapping multi-row tab bar."""

    tabCloseRequested = Signal(int)  # noqa: N815
    currentChanged = Signal(int)  # noqa: N815

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_index = -1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tab_bar = _FlowTabBar(self)
        self._tab_bar.tab_clicked.connect(self.setCurrentIndex)
        self._tab_bar.tab_close_requested.connect(self.tabCloseRequested.emit)
        layout.addWidget(self._tab_bar)

        self._stack = QStackedWidget(self)
        theme = get_theme()
        self._stack.setStyleSheet(f"background-color: {theme.widget_bg};")
        layout.addWidget(self._stack)

    def tabBar(self) -> _FlowTabBar:  # noqa: N802
        """Return the tab bar for context menu support."""
        return self._tab_bar

    def addTab(self, widget: QWidget, icon: QIcon, label: str) -> int:  # noqa: N802
        """Add a tab with icon and label, return the index."""
        stack_idx = self._stack.addWidget(widget)
        self._tab_bar.add_tab(icon, label)
        self.setCurrentIndex(stack_idx)
        return stack_idx

    def removeTab(self, index: int) -> None:  # noqa: N802
        """Remove tab at index."""
        if 0 <= index < self._stack.count():
            widget = self._stack.widget(index)
            self._stack.removeWidget(widget)
            self._tab_bar.remove_tab(index)
            # Update current index
            if self._stack.count() == 0:
                self._current_index = -1
            elif index <= self._current_index:
                new_idx = max(0, self._current_index - 1)
                self.setCurrentIndex(new_idx)

    def indexOf(self, widget: QWidget) -> int:  # noqa: N802
        """Return index of widget, or -1."""
        return self._stack.indexOf(widget)

    def widget(self, index: int) -> QWidget | None:
        """Return widget at index."""
        return self._stack.widget(index)

    def count(self) -> int:
        """Return number of tabs."""
        return self._stack.count()

    def currentIndex(self) -> int:  # noqa: N802
        """Return the current tab index."""
        return self._current_index

    def currentWidget(self) -> QWidget | None:  # noqa: N802
        """Return the currently active widget."""
        if 0 <= self._current_index < self._stack.count():
            return self._stack.widget(self._current_index)
        return None

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        """Set the active tab."""
        if 0 <= index < self._stack.count():
            self._current_index = index
            self._stack.setCurrentIndex(index)
            self._tab_bar.set_current(index)
            self.currentChanged.emit(index)

    def setTabColors(self, active_color: str, inactive_color: str) -> None:  # noqa: N802
        """Update tab button colors."""
        self._tab_bar.set_colors(active_color, inactive_color)


class ChatWindow(QMainWindow):
    """Main chat window with tabbed channels.

    Manages multiple ChatWidgets in a FlowTabWidget, handles opening/closing
    chat tabs, and coordinates with the ChatManager.
    """

    # Emitted when window is closed by user (hides, doesn't destroy)
    window_hidden = Signal()
    # Emitted when a widget's gear icon is clicked
    chat_settings_requested = Signal()

    def __init__(
        self,
        chat_manager: ChatManager,
        settings: Settings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.chat_manager = chat_manager
        self.settings = settings
        self._widgets: dict[str, ChatWidget] = {}
        self._livestreams: dict[str, Livestream] = {}
        self._popout_windows: dict[str, ChatPopoutWindow] = {}
        self._metrics_timer: QTimer | None = None
        self._metrics_label: QLabel | None = None
        self._backfill_versions: dict[str, int] = {}

        # Debounce emote cache updates to prevent excessive repaints
        self._emote_repaint_pending = False
        self._emote_repaint_timer = QTimer(self)
        self._emote_repaint_timer.setSingleShot(True)
        self._emote_repaint_timer.setInterval(100)  # 100ms debounce
        self._emote_repaint_timer.timeout.connect(self._do_emote_repaint)

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the chat window UI."""
        self.setWindowTitle("Chat")
        self.setMinimumSize(350, 400)

        # Restore window geometry from settings
        ws = self.settings.chat.builtin.window
        self.resize(ws.width, ws.height)
        if ws.x is not None and ws.y is not None:
            self.move(ws.x, ws.y)

        # Menu bar
        self._setup_menu_bar()

        # Flow tab widget (wraps tabs to multiple rows)
        self._tab_widget = FlowTabWidget()
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close)
        self._tab_widget.currentChanged.connect(self._on_tab_changed)

        self.setCentralWidget(self._tab_widget)

        # Status bar metrics
        self._setup_status_bar()

        # Tab bar context menu for pop-out
        tab_bar = self._tab_widget.tabBar()
        tab_bar.context_menu_requested.connect(self._on_tab_context_menu)

        # Apply always-on-top from saved settings (deferred so window is mapped first)
        if self.settings.chat.builtin.always_on_top:
            QTimer.singleShot(100, lambda: self._on_always_on_top_changed(True))

        # Apply theme styling (sets tab colors from theme)
        self.apply_theme()

    def _setup_status_bar(self) -> None:
        """Set up the status bar metrics panel."""
        status_bar = QStatusBar(self)
        status_bar.setSizeGripEnabled(False)
        self._metrics_label = QLabel("")
        self._metrics_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        status_bar.addPermanentWidget(self._metrics_label, 1)
        self.setStatusBar(status_bar)

        self._metrics_timer = QTimer(self)
        self._metrics_timer.setInterval(1000)
        self._metrics_timer.timeout.connect(self._update_metrics)
        self._metrics_timer.start()
        self._update_metrics()

    def _setup_menu_bar(self) -> None:
        """Set up the menu bar."""
        menu_bar = self.menuBar()

        # Chat menu
        chat_menu = menu_bar.addMenu("Chat")

        # New whisper action
        whisper_action = chat_menu.addAction("New Whisper...")
        whisper_action.setShortcut("Ctrl+W")
        whisper_action.triggered.connect(self._show_new_whisper_dialog)

        chat_menu.addSeparator()

        # Settings action
        settings_action = chat_menu.addAction("Settings...")
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self.chat_settings_requested.emit)

        chat_menu.addSeparator()

        zoom_in_action = chat_menu.addAction("Zoom In")
        zoom_in_action.setShortcut("Ctrl+=")
        zoom_in_action.triggered.connect(self._zoom_in)

        zoom_out_action = chat_menu.addAction("Zoom Out")
        zoom_out_action.setShortcut("Ctrl+-")
        zoom_out_action.triggered.connect(self._zoom_out)

    def update_tab_style(self) -> None:
        """Refresh tab colors from current theme (call after prefs change)."""
        theme = get_theme()
        self._tab_widget.setTabColors(theme.chat_tab_active, theme.chat_tab_inactive)

    def apply_theme(self) -> None:
        """Apply the current theme to the chat window."""
        # Disable updates during theme change to prevent cascading repaints
        self.setUpdatesEnabled(False)
        try:
            theme = get_theme()
            self.setStyleSheet(f"""
                QMainWindow {{
                    background-color: {theme.chat_bg};
                    color: {theme.text_primary};
                }}
                QWidget {{
                    background-color: {theme.chat_bg};
                    color: {theme.text_primary};
                }}
                QStatusBar {{
                    background-color: {theme.window_bg};
                    color: {theme.text_muted};
                    border-top: 1px solid {theme.border};
                }}
                QStatusBar QLabel {{
                    color: {theme.text_muted};
                }}
                QScrollBar:vertical {{
                    background-color: {theme.window_bg};
                    width: 12px;
                }}
                QScrollBar::handle:vertical {{
                    background-color: {theme.border};
                    border-radius: 4px;
                    min-height: 20px;
                }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                    height: 0px;
                }}
            """)
            # Update tab bar and colors from theme
            self._tab_widget._tab_bar.setStyleSheet(f"background-color: {theme.window_bg};")
            self._tab_widget._tab_bar._active_color = theme.chat_tab_active
            self._tab_widget._tab_bar._inactive_color = theme.chat_tab_inactive
            for tab in self._tab_widget._tab_bar._tabs:
                tab._active_color = theme.chat_tab_active
                tab._inactive_color = theme.chat_tab_inactive
                tab._update_style()
            self._tab_widget._stack.setStyleSheet(f"background-color: {theme.widget_bg};")
            # Update all chat widgets
            for widget in self._widgets.values():
                widget.apply_theme()
            # Update popout windows
            for popout in self._popout_windows.values():
                popout.apply_theme()
        finally:
            self.setUpdatesEnabled(True)

    def _update_metrics(self) -> None:
        """Refresh status bar metrics from ChatManager."""
        bar = self.statusBar()
        if not bar:
            return
        if not self.settings.chat.builtin.show_metrics:
            if bar.isVisible():
                bar.setVisible(False)
            return
        if not bar.isVisible():
            bar.setVisible(True)

        metrics = self.chat_manager.get_metrics()
        disk_mb = metrics["disk_bytes"] // (1024 * 1024)
        text = (
            f"Emotes {metrics['emote_mem']}/{metrics['emote_animated']} "
            f"(pending {metrics['emote_pending']}) | "
            f"Disk {disk_mb}/{metrics['disk_limit_mb']} MB | "
            f"DL {metrics['downloads_queued']}+{metrics['downloads_inflight']} | "
            f"MsgQ {metrics['message_queue']}"
        )
        if self._metrics_label:
            self._metrics_label.setText(text)

    def update_animation_state(self) -> None:
        """Update animation timers on all widgets (call after prefs change)."""
        animate = self.settings.chat.builtin.animate_emotes
        has_any = any(w.has_animated_emotes() for w in self._widgets.values())
        if animate and has_any:
            self.chat_manager.gif_timer.start()
        else:
            self.chat_manager.gif_timer.stop()
        for widget in self._widgets.values():
            widget.set_animation_enabled(animate)

    def update_banner_settings(self) -> None:
        """Update banner visibility and colors on all widgets (call after prefs change)."""
        for widget in self._widgets.values():
            widget.update_banner_settings()

    def update_spellcheck(self) -> None:
        """Update spellcheck enabled state on all widgets."""
        enabled = self.settings.chat.builtin.spellcheck_enabled
        for widget in self._widgets.values():
            widget.set_spellcheck_enabled(enabled)

    def update_metrics_bar(self) -> None:
        """Refresh status bar metrics/visibility."""
        self._update_metrics()

    def _connect_signals(self) -> None:
        """Connect ChatManager signals."""
        self.chat_manager.messages_received.connect(self._on_messages_received)
        self.chat_manager.moderation_received.connect(self._on_moderation_received)
        self.chat_manager.chat_opened.connect(self._on_chat_opened)
        self.chat_manager.chat_closed.connect(self._on_chat_closed)
        self.chat_manager.chat_connected.connect(self._on_chat_connected)
        self.chat_manager.emote_cache_updated.connect(self._on_emote_cache_updated)
        self.chat_manager.emote_map_updated.connect(self._on_emote_map_updated)
        self.chat_manager.auth_state_changed.connect(self._on_auth_state_changed)
        self.chat_manager.chat_error.connect(self._on_chat_error)
        self.chat_manager.chat_disconnected.connect(self._on_chat_disconnected)
        self.chat_manager.chat_reconnecting.connect(self._on_chat_reconnecting)
        self.chat_manager.chat_reconnect_failed.connect(self._on_chat_reconnect_failed)
        self.chat_manager.socials_fetched.connect(self._on_socials_fetched)
        self.chat_manager.whisper_received.connect(self._on_whisper_received)
        self.chat_manager.room_state_changed.connect(self._on_room_state_changed)
        self.chat_manager.hype_train_event.connect(self._on_hype_train_event)
        self.chat_manager.raid_received.connect(self._on_raid_received)

    def open_chat(self, livestream: Livestream) -> None:
        """Open or focus a chat tab for a livestream."""
        channel_key = livestream.channel.unique_key
        self._livestreams[channel_key] = livestream

        if channel_key in self._widgets:
            # Focus existing tab and update livestream data (e.g., title may have changed)
            widget = self._widgets[channel_key]
            widget.update_livestream(livestream)
            idx = self._tab_widget.indexOf(widget)
            if idx >= 0:
                self._tab_widget.setCurrentIndex(idx)
        else:
            # ChatManager will create the connection and emit chat_opened
            self.chat_manager.open_chat(livestream)

        self.show()
        self.raise_()
        self.activateWindow()

    def close_chat(self, channel_key: str) -> None:
        """Close a chat tab and disconnect."""
        self.chat_manager.close_chat(channel_key)

    def _on_chat_opened(self, channel_key: str, livestream: Livestream) -> None:
        """Handle a new chat connection being opened."""
        if channel_key in self._widgets:
            # Already has a widget, just focus
            widget = self._widgets[channel_key]
            idx = self._tab_widget.indexOf(widget)
            if idx >= 0:
                self._tab_widget.setCurrentIndex(idx)
            return

        self._livestreams[channel_key] = livestream

        # Create chat widget - check auth based on platform
        if livestream.channel.platform == StreamPlatform.KICK:
            authenticated = bool(self.settings.kick.access_token)
        elif livestream.channel.platform == StreamPlatform.YOUTUBE:
            authenticated = bool(self.settings.youtube.cookies)
        else:
            authenticated = bool(self.settings.twitch.access_token)
        widget = ChatWidget(
            channel_key=channel_key,
            livestream=livestream,
            settings=self.settings.chat.builtin,
            authenticated=authenticated,
            parent=self._tab_widget,
        )
        widget.message_sent.connect(self._on_message_sent)
        widget.popout_requested.connect(self._on_popout_requested)
        widget.settings_clicked.connect(self.chat_settings_requested.emit)
        widget.font_size_changed.connect(self._on_font_size_changed)
        widget.settings_changed.connect(self._on_settings_changed)
        widget.whisper_requested.connect(self._on_whisper_request_from_chat)
        widget.always_on_top_changed.connect(self._on_always_on_top_changed)

        # Set shared image store and emote map on the widget
        widget.set_image_store(self.chat_manager.emote_cache)
        widget.set_gif_timer(self.chat_manager.gif_timer)
        widget.set_emote_map(
            self.chat_manager.get_emote_map(channel_key),
            self.chat_manager.get_channel_emote_names(channel_key),
            self.chat_manager.get_user_emote_names(),
        )

        # Load disk history if chat logging is enabled
        widget.load_disk_history(self.chat_manager.chat_log_writer)

        # Apply current theme colors to the new widget
        widget.apply_theme()

        self._widgets[channel_key] = widget

        # Add tab with platform-colored dot
        platform = livestream.channel.platform
        icon = _create_dot_icon(PLATFORM_COLORS.get(platform, QColor("#888")))
        tab_name = livestream.channel.display_name or livestream.channel.channel_id
        idx = self._tab_widget.addTab(widget, icon, tab_name)
        self._tab_widget.setCurrentIndex(idx)

    def _on_chat_closed(self, channel_key: str) -> None:
        """Handle a chat connection being closed."""
        widget = self._widgets.pop(channel_key, None)
        if widget:
            idx = self._tab_widget.indexOf(widget)
            if idx >= 0:
                self._tab_widget.removeTab(idx)
            widget.deleteLater()

        self._livestreams.pop(channel_key, None)

        # Close popout window if any
        popout = self._popout_windows.pop(channel_key, None)
        if popout:
            popout.close()

        # Hide window when no tabs remain
        if self._tab_widget.count() == 0 and not self._popout_windows:
            self.save_window_state()
            self.hide()
            self.window_hidden.emit()

    def _on_chat_connected(self, channel_key: str) -> None:
        """Handle a chat connection being established."""
        widget = self._widgets.get(channel_key)
        if widget:
            widget.set_connected()

    def _on_messages_received(self, channel_key: str, messages: list) -> None:
        """Route messages to the correct chat widget."""
        widget = self._widgets.get(channel_key)
        if widget:
            widget.add_messages(messages)

            # Flash tab on @mention if it's not the current tab
            idx = self._tab_widget.indexOf(widget)
            if idx >= 0 and idx != self._tab_widget.currentIndex():
                from ...chat.models import ChatMessage as ChatMsg

                has_mention = any(isinstance(m, ChatMsg) and m.is_mention for m in messages)
                if has_mention:
                    self._tab_widget.tabBar().start_flash(idx)

    def _on_moderation_received(self, channel_key: str, event: object) -> None:
        """Route moderation events to the correct chat widget."""
        widget = self._widgets.get(channel_key)
        if widget and isinstance(event, ModerationEvent):
            widget.apply_moderation(event)

    def _on_room_state_changed(self, channel_key: str, state: object) -> None:
        """Route room state changes to the correct chat widget."""
        from ...chat.models import ChatRoomState

        widget = self._widgets.get(channel_key)
        if widget and isinstance(state, ChatRoomState):
            widget.update_room_state(state)

    def _on_hype_train_event(self, channel_key: str, event: object) -> None:
        """Route hype train events to the correct chat widget."""
        if not isinstance(event, HypeTrainEvent):
            return
        widget = self._widgets.get(channel_key)
        if widget:
            widget.update_hype_train(event)
        # Also route to popout if applicable
        popout = self._popout_windows.get(channel_key)
        if popout and popout._widget:
            popout._widget.update_hype_train(event)

    def _on_raid_received(self, channel_key: str, message: object) -> None:
        """Route raid events to the correct chat widget."""
        from ...chat.models import ChatMessage

        if not isinstance(message, ChatMessage):
            return
        widget = self._widgets.get(channel_key)
        if widget:
            widget.show_raid_banner(message)
        popout = self._popout_windows.get(channel_key)
        if popout and popout._widget:
            popout._widget.show_raid_banner(message)

    def _on_emote_cache_updated(self) -> None:
        """Handle emote/badge image loaded - debounce repaint requests."""
        self._emote_repaint_pending = True
        if not self._emote_repaint_timer.isActive():
            self._emote_repaint_timer.start()

    def _do_emote_repaint(self) -> None:
        """Actually perform the repaint (called after debounce delay)."""
        if not self._emote_repaint_pending:
            return
        self._emote_repaint_pending = False
        user_emote_names = self.chat_manager.get_user_emote_names()
        for widget in self._widgets.values():
            widget.set_emote_map(
                self.chat_manager.get_emote_map(widget.channel_key),
                self.chat_manager.get_channel_emote_names(widget.channel_key),
                user_emote_names,
            )
            widget.repaint_messages()
            # Refresh emote picker icons if it's currently visible
            if widget._emote_picker.isVisible():
                widget._emote_picker.refresh_icons()
        self.update_animation_state()

    def _on_emote_map_updated(self, channel_key: str) -> None:
        """Backfill third-party emotes for recent messages after emote map updates."""
        widgets = []
        if channel_key:
            widget = self._widgets.get(channel_key)
            if widget:
                widgets = [widget]
        else:
            widgets = list(self._widgets.values())

        for widget in widgets:
            self._schedule_emote_backfill(widget)

    def _schedule_emote_backfill(self, widget: ChatWidget) -> None:
        """Backfill third-party emotes in batches to avoid UI stalls."""
        channel_key = widget.channel_key
        version = self._backfill_versions.get(channel_key, 0) + 1
        self._backfill_versions[channel_key] = version

        messages = widget.get_all_messages()
        if not messages:
            return

        batch_size = 200
        updated_any = False

        def process_batch(start: int = 0) -> None:
            nonlocal updated_any
            if self._backfill_versions.get(channel_key) != version:
                return
            if channel_key not in self._widgets:
                return
            batch = messages[start : start + batch_size]
            if not batch:
                if updated_any:
                    widget.invalidate_message_layout()
                return
            updated = self.chat_manager.backfill_third_party_emotes(channel_key, batch)
            updated_any = updated_any or bool(updated)
            QTimer.singleShot(0, lambda: process_batch(start + batch_size))

        process_batch()

    def _on_message_sent(self, channel_key: str, text: str, reply_to_msg_id: str) -> None:
        """Handle a message being sent from a chat widget."""
        # Look up reply context for local echo
        reply_parent_display_name = ""
        reply_parent_text = ""
        if reply_to_msg_id:
            widget = self._widgets.get(channel_key)
            if widget and widget._reply_to_msg:
                reply_parent_display_name = widget._reply_to_msg.user.display_name
                reply_parent_text = widget._reply_to_msg.text
        self.chat_manager.send_message(
            channel_key,
            text,
            reply_to_msg_id=reply_to_msg_id,
            reply_parent_display_name=reply_parent_display_name,
            reply_parent_text=reply_parent_text,
        )

    def _on_font_size_changed(self, new_size: int) -> None:
        """Persist font size change and relayout active widget."""
        self.settings.chat.builtin.font_size = new_size
        self.settings.save()
        # Only relayout active widget - others will update when activated
        current_widget = self._tab_widget.currentWidget()
        if isinstance(current_widget, ChatWidget):
            current_widget._delegate.invalidate_size_cache()
            current_widget._model.layoutChanged.emit()

    def _zoom_in(self) -> None:
        """Increase chat font size."""
        current = self.settings.chat.builtin.font_size
        if current == 0:
            from PySide6.QtWidgets import QApplication

            current = QApplication.font().pointSize()
        new_size = min(30, current + 1)
        self._on_font_size_changed(new_size)

    def _zoom_out(self) -> None:
        """Decrease chat font size."""
        current = self.settings.chat.builtin.font_size
        if current == 0:
            from PySide6.QtWidgets import QApplication

            current = QApplication.font().pointSize()
        new_size = max(4, current - 1)
        self._on_font_size_changed(new_size)

    def _on_settings_changed(self) -> None:
        """Persist chat setting toggles and relayout active widget."""
        self.settings.save()
        self.chat_manager.on_emote_settings_changed()
        # Only relayout the active widget to avoid lockups
        # Other widgets will be relayouted when they become active
        current_widget = self._tab_widget.currentWidget()
        if isinstance(current_widget, ChatWidget):
            current_widget._model.layoutChanged.emit()

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab change - refresh emote map for newly active tab."""
        # Stop flashing on the newly focused tab
        self._tab_widget.tabBar().stop_flash(index)

        widget = self._tab_widget.widget(index)
        if isinstance(widget, ChatWidget):
            # Update emote map and repaint when tab becomes active
            widget.set_emote_map(
                self.chat_manager.get_emote_map(widget.channel_key),
                self.chat_manager.get_channel_emote_names(widget.channel_key),
                self.chat_manager.get_user_emote_names(),
            )
            widget.repaint_messages()

    def _on_tab_close(self, index: int) -> None:
        """Handle tab close button clicked."""
        widget = self._tab_widget.widget(index)
        if isinstance(widget, ChatWidget):
            if widget._is_dm:
                # DM tabs have no connection — just remove the widget
                channel_key = widget.channel_key
                self._widgets.pop(channel_key, None)
                self._tab_widget.removeTab(index)
                widget.deleteLater()
                if self._tab_widget.count() == 0 and not self._popout_windows:
                    self.save_window_state()
                    self.hide()
                    self.window_hidden.emit()
            else:
                self.close_chat(widget.channel_key)

    def _on_tab_context_menu(self, index: int, global_pos) -> None:
        """Show context menu on tab bar right-click."""
        from PySide6.QtWidgets import QMenu

        widget = self._tab_widget.widget(index)
        if not isinstance(widget, ChatWidget):
            return

        menu = QMenu(self)
        popout_action = menu.addAction("Pop Out")
        popout_action.triggered.connect(lambda: self._on_popout_requested(widget.channel_key))
        menu.exec(global_pos)

    def _on_auth_state_changed(self, _authenticated: bool) -> None:
        """Update all widgets when auth state changes (platform-aware)."""
        for widget in self._widgets.values():
            if widget._is_dm:
                # DM tabs are always Twitch
                widget.set_authenticated(bool(self.settings.twitch.access_token))
                continue
            platform = widget.livestream.channel.platform
            if platform == StreamPlatform.KICK:
                auth = bool(self.settings.kick.access_token)
            elif platform == StreamPlatform.YOUTUBE:
                auth = bool(self.settings.youtube.cookies)
            else:
                auth = bool(self.settings.twitch.access_token)
            widget.set_authenticated(auth)

    def _on_chat_error(self, channel_key: str, message: str) -> None:
        """Show a chat error in the relevant widget."""
        widget = self._widgets.get(channel_key)
        if widget:
            widget.show_error(message)

    def _on_chat_disconnected(self, channel_key: str) -> None:
        """Handle a chat connection being lost."""
        widget = self._widgets.get(channel_key)
        if widget:
            widget.set_disconnected()

    def _on_chat_reconnecting(self, channel_key: str, delay: float) -> None:
        """Handle a chat connection preparing to reconnect."""
        widget = self._widgets.get(channel_key)
        if widget:
            widget.set_reconnecting(delay)

    def _on_chat_reconnect_failed(self, channel_key: str) -> None:
        """Handle exhausted reconnection attempts."""
        widget = self._widgets.get(channel_key)
        if widget:
            widget.set_reconnect_failed()

    def _on_socials_fetched(self, channel_key: str, socials: dict) -> None:
        """Update a chat widget with fetched social links."""
        widget = self._widgets.get(channel_key)
        if widget:
            widget.set_socials(socials)

    def _on_whisper_received(self, platform: str, message) -> None:
        """Handle an incoming or sent whisper — create/focus a DM tab."""
        from ...chat.models import ChatMessage as ChatMsg

        if not isinstance(message, ChatMsg) or not message.is_whisper:
            return

        # Determine the DM partner
        if message.whisper_target:
            # Sent by us — partner is the target
            partner_name = message.whisper_target
            partner_id = ""  # We don't always have the ID for sent messages
        else:
            # Received — partner is the sender
            partner_name = message.user.display_name
            partner_id = message.user.id

        dm_key = f"twitch:__dm__{partner_name.lower()}"

        # Create DM tab if it doesn't exist (history is loaded from disk)
        tab_just_created = dm_key not in self._widgets
        if tab_just_created:
            self._create_dm_tab(dm_key, partner_name, partner_id)

        # Add the message — but skip if the tab was just created, since
        # _create_dm_tab already loaded history which includes this message.
        widget = self._widgets.get(dm_key)
        if widget and not tab_just_created:
            widget.add_messages([message])

            # Flash tab if not current
            idx = self._tab_widget.indexOf(widget)
            if idx >= 0 and idx != self._tab_widget.currentIndex():
                self._tab_widget.tabBar().start_flash(idx)

        # Show window
        self.show()
        self.raise_()

    def _create_dm_tab(self, dm_key: str, partner_name: str, partner_id: str) -> None:
        """Create a new DM/whisper tab."""
        # Create a minimal ChatWidget for DMs
        # We use a fake Livestream-like setup — DM tabs don't have a real livestream
        widget = ChatWidget(
            channel_key=dm_key,
            livestream=None,
            settings=self.settings.chat.builtin,
            authenticated=bool(self.settings.twitch.access_token),
            is_dm=True,
            dm_partner_name=partner_name,
            dm_partner_id=partner_id,
            parent=self._tab_widget,
        )
        widget.message_sent.connect(self._on_dm_message_sent)
        widget.whisper_requested.connect(self._on_whisper_request_from_chat)

        # Set shared image store and emote map
        widget.set_image_store(self.chat_manager.emote_cache)
        widget.set_gif_timer(self.chat_manager.gif_timer)
        widget.apply_theme()

        # Load whisper history from local storage
        from ...chat.whisper_store import load_whispers

        history = load_whispers(partner_name)
        if history:
            widget.add_messages(history)

        self._widgets[dm_key] = widget

        # Add tab with a distinct DM icon (purple dot + "DM:" prefix)
        dm_color = QColor("#9146ff")  # Twitch purple
        icon = _create_dot_icon(dm_color)
        idx = self._tab_widget.addTab(widget, icon, f"DM: {partner_name}")
        self._tab_widget.setCurrentIndex(idx)

    def open_dm_tab(self, partner_name: str, partner_id: str) -> None:
        """Open or focus a DM tab for a user (called from context menu)."""
        dm_key = f"twitch:__dm__{partner_name.lower()}"

        if dm_key in self._widgets:
            widget = self._widgets[dm_key]
            idx = self._tab_widget.indexOf(widget)
            if idx >= 0:
                self._tab_widget.setCurrentIndex(idx)
        else:
            self._create_dm_tab(dm_key, partner_name, partner_id)

        self.show()
        self.raise_()
        self.activateWindow()

    def _on_dm_message_sent(self, channel_key: str, text: str, reply_to_msg_id: str) -> None:
        """Handle a message sent from a DM tab — send as whisper."""
        widget = self._widgets.get(channel_key)
        if not widget or not widget._is_dm:
            return

        to_user_id = widget._dm_partner_id
        to_display_name = widget._dm_partner_name

        if not to_user_id:
            # Try to resolve the user ID from the username
            self._resolve_and_send_whisper(widget, to_display_name, text)
            return

        self.chat_manager.send_whisper(to_user_id, to_display_name, text)

    def _resolve_and_send_whisper(self, widget: ChatWidget, username: str, text: str) -> None:
        """Resolve a Twitch username to ID, then send the whisper."""
        from PySide6.QtCore import QThread

        class _ResolveWorker(QThread):
            from PySide6.QtCore import Signal as _Signal

            resolved = _Signal(str)  # user_id or ""
            error = _Signal(str)

            def __init__(self, login, token, client_id, parent=None):
                super().__init__(parent)
                self._login = login
                self._token = token
                self._client_id = client_id

            def run(self):
                import asyncio

                import aiohttp

                async def resolve():
                    headers = {
                        "Authorization": f"Bearer {self._token}",
                        "Client-Id": self._client_id,
                    }
                    async with aiohttp.ClientSession(headers=headers) as session:
                        async with session.get(
                            "https://api.twitch.tv/helix/users",
                            params={"login": self._login.lower()},
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                users = data.get("data", [])
                                if users:
                                    return users[0].get("id", "")
                    return ""

                loop = asyncio.new_event_loop()
                try:
                    uid = loop.run_until_complete(resolve())
                    self.resolved.emit(uid)
                except Exception as e:
                    self.error.emit(str(e))
                finally:
                    loop.close()

        token = self.settings.twitch.access_token
        client_id = self.settings.twitch.client_id or "gnvljs5w28wkpz60vfug0z5rp5d66h"

        worker = _ResolveWorker(username, token, client_id, parent=self)

        def on_resolved(user_id):
            if user_id:
                widget._dm_partner_id = user_id
                self.chat_manager.send_whisper(user_id, username, text)
            else:
                widget.show_error(f"Could not find Twitch user '{username}'")

        def on_error(err):
            widget.show_error(f"Failed to resolve user: {err}")

        worker.resolved.connect(on_resolved)
        worker.error.connect(on_error)
        worker.start()

    def _on_whisper_request_from_chat(self, partner_name: str, partner_id: str) -> None:
        """Handle whisper request from a chat widget context menu."""
        self.open_dm_tab(partner_name, partner_id)

    def _show_new_whisper_dialog(self) -> None:
        """Show a dialog to start a new whisper conversation."""
        if not self.settings.twitch.access_token:
            return

        # Collect known usernames from all chat widgets for autocomplete
        known_users: dict[str, str] = {}  # display_name -> user_id
        for widget in self._widgets.values():
            if widget._is_dm:
                continue
            for msg in widget._model._messages:
                if msg.platform == StreamPlatform.TWITCH and msg.user.id != "self":
                    known_users[msg.user.display_name] = msg.user.id

        dialog = _NewWhisperDialog(sorted(known_users.keys()), parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            username = dialog.get_username().strip()
            if not username:
                return
            # Look up user ID from known users (case-insensitive)
            user_id = ""
            for name, uid in known_users.items():
                if name.lower() == username.lower():
                    user_id = uid
                    username = name  # Use proper casing
                    break
            self.open_dm_tab(username, user_id)

    def _on_popout_requested(self, channel_key: str) -> None:
        """Pop out a chat widget into its own window."""
        widget = self._widgets.get(channel_key)
        if not widget:
            return

        livestream = self._livestreams.get(channel_key)
        if not livestream:
            return

        # Remove from tab widget (but don't delete)
        idx = self._tab_widget.indexOf(widget)
        if idx >= 0:
            self._tab_widget.removeTab(idx)

        # Create popout window
        popout = ChatPopoutWindow(
            channel_key=channel_key,
            widget=widget,
            livestream=livestream,
            parent=None,  # Independent window
        )
        popout.popin_requested.connect(self._on_popin_requested)
        popout.closed.connect(lambda key=channel_key: self._on_popout_closed(key))
        self._popout_windows[channel_key] = popout
        popout.show()
        if self.settings.chat.builtin.always_on_top:
            if is_kde_plasma():
                QTimer.singleShot(100, lambda p=popout: kwin_set_keep_above([p], True))
            else:
                apply_always_on_top_qt(popout, True)

    def _on_popin_requested(self, channel_key: str) -> None:
        """Re-dock a popped-out chat widget."""
        popout = self._popout_windows.pop(channel_key, None)
        if not popout:
            return

        widget = popout.take_widget()
        # Disconnect closed signal so close() doesn't trigger chat disconnect
        popout.closed.disconnect()
        popout.close()
        if widget and channel_key in self._livestreams:
            livestream = self._livestreams[channel_key]
            platform = livestream.channel.platform
            icon = _create_dot_icon(PLATFORM_COLORS.get(platform, QColor("#888")))
            tab_name = livestream.channel.display_name or livestream.channel.channel_id

            widget.setParent(self._tab_widget)
            idx = self._tab_widget.addTab(widget, icon, tab_name)
            self._tab_widget.setCurrentIndex(idx)
            widget.show()
        self.show()
        self.raise_()

    def _on_popout_closed(self, channel_key: str) -> None:
        """Handle popout window being closed (disconnect chat)."""
        self._popout_windows.pop(channel_key, None)
        self.close_chat(channel_key)

    def _on_always_on_top_changed(self, on_top: bool) -> None:
        """Apply always-on-top to the main chat window and all popouts."""
        windows = [self] + list(self._popout_windows.values())
        apply_always_on_top(windows, on_top)

    def save_window_state(self) -> None:
        """Save window position and size to settings."""
        ws = self.settings.chat.builtin.window
        ws.width = self.width()
        ws.height = self.height()
        pos = self.pos()
        ws.x = pos.x()
        ws.y = pos.y()

    def closeEvent(self, event) -> None:  # noqa: N802
        """Disconnect tabbed chats and hide. Popouts stay connected."""
        self.save_window_state()

        # Collect tabbed channel keys (those NOT popped out)
        tabbed_keys = [key for key in list(self._widgets.keys()) if key not in self._popout_windows]
        # Disconnect each tabbed channel
        for key in tabbed_keys:
            self.close_chat(key)

        event.ignore()
        self.hide()
        self.window_hidden.emit()


class ChatPopoutWindow(QMainWindow):
    """Standalone window for a popped-out chat."""

    popin_requested = Signal(str)  # channel_key
    closed = Signal(str)  # channel_key

    def __init__(
        self,
        channel_key: str,
        widget: ChatWidget,
        livestream: Livestream,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.channel_key = channel_key
        self._widget = widget

        channel_name = livestream.channel.display_name or livestream.channel.channel_id
        self.setWindowTitle(f"Chat - {channel_name}")
        self.setMinimumSize(300, 350)
        self.resize(380, 550)

        # Reparent widget and ensure it's visible
        widget.setParent(self)
        self.setCentralWidget(widget)
        widget.show()

        # Add toolbar with pop-in button
        toolbar = self.addToolBar("Actions")
        toolbar.setMovable(False)
        popin_action = toolbar.addAction("Pop In")
        popin_action.triggered.connect(lambda: self.popin_requested.emit(self.channel_key))

        self.apply_theme()

    def apply_theme(self) -> None:
        """Apply the current theme to the popout window."""
        theme = get_theme()
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {theme.chat_bg};
            }}
            QToolBar {{
                background-color: {theme.chat_input_bg};
                border: none;
                padding: 2px;
            }}
        """)

    def take_widget(self) -> ChatWidget | None:
        """Remove and return the chat widget for re-docking."""
        widget = self._widget
        self._widget = None
        if widget:
            # Detach widget from this window so closing doesn't destroy it
            widget.setParent(None)
            self.setCentralWidget(QWidget())
        return widget

    def closeEvent(self, event) -> None:  # noqa: N802
        """Emit closed signal when window is closed."""
        self.closed.emit(self.channel_key)
        super().closeEvent(event)
