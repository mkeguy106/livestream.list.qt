"""Chat window with tabbed multi-channel support."""

import logging

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...chat.manager import ChatManager
from ...chat.models import ModerationEvent
from ...core.models import Livestream, StreamPlatform
from ...core.settings import Settings
from ..theme import get_theme
from .chat_widget import ChatWidget

logger = logging.getLogger(__name__)

# Platform colors for tab icons
PLATFORM_COLORS = {
    StreamPlatform.TWITCH: QColor("#9146ff"),
    StreamPlatform.YOUTUBE: QColor("#ff0000"),
    StreamPlatform.KICK: QColor("#53fc18"),
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

    def enterEvent(self, event) -> None:  # noqa: N802
        if not self._active:
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

        # Flow tab widget (wraps tabs to multiple rows)
        self._tab_widget = FlowTabWidget()
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close)

        self.setCentralWidget(self._tab_widget)

        # Tab bar context menu for pop-out
        tab_bar = self._tab_widget.tabBar()
        tab_bar.context_menu_requested.connect(self._on_tab_context_menu)

        # Apply theme styling (sets tab colors from theme)
        self.apply_theme()

    def update_tab_style(self) -> None:
        """Refresh tab colors from theme (call after prefs change)."""
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
            # Update tab bar and colors
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

    def update_animation_state(self) -> None:
        """Update animation timers on all widgets (call after prefs change)."""
        for widget in self._widgets.values():
            widget.update_animation_state()

    def update_banner_settings(self) -> None:
        """Update banner visibility and colors on all widgets (call after prefs change)."""
        for widget in self._widgets.values():
            widget.update_banner_settings()

    def _connect_signals(self) -> None:
        """Connect ChatManager signals."""
        self.chat_manager.messages_received.connect(self._on_messages_received)
        self.chat_manager.moderation_received.connect(self._on_moderation_received)
        self.chat_manager.chat_opened.connect(self._on_chat_opened)
        self.chat_manager.chat_closed.connect(self._on_chat_closed)
        self.chat_manager.chat_connected.connect(self._on_chat_connected)
        self.chat_manager.emote_cache_updated.connect(self._on_emote_cache_updated)
        self.chat_manager.auth_state_changed.connect(self._on_auth_state_changed)
        self.chat_manager.chat_error.connect(self._on_chat_error)
        self.chat_manager.socials_fetched.connect(self._on_socials_fetched)

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

        # Set shared emote cache, animated cache, and emote map on the widget
        widget.set_emote_cache(self.chat_manager.emote_cache.pixmap_dict)
        widget.set_animated_cache(self.chat_manager.emote_cache.animated_dict)
        if self.chat_manager.emote_map:
            widget.set_emote_map(self.chat_manager.emote_map)

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

    def _on_moderation_received(self, channel_key: str, event: object) -> None:
        """Route moderation events to the correct chat widget."""
        widget = self._widgets.get(channel_key)
        if widget and isinstance(event, ModerationEvent):
            widget.apply_moderation(event)

    def _on_emote_cache_updated(self) -> None:
        """Handle emote/badge image loaded - update cache refs and repaint."""
        cache_dict = self.chat_manager.emote_cache.pixmap_dict
        animated_dict = self.chat_manager.emote_cache.animated_dict
        emote_map = self.chat_manager.emote_map
        for widget in self._widgets.values():
            widget.set_emote_cache(cache_dict)
            widget.set_animated_cache(animated_dict)
            if emote_map:
                widget.set_emote_map(emote_map)
            widget.repaint_messages()

    def _on_message_sent(self, channel_key: str, text: str) -> None:
        """Handle a message being sent from a chat widget."""
        self.chat_manager.send_message(channel_key, text)

    def _on_font_size_changed(self, new_size: int) -> None:
        """Persist font size change and relayout all widgets."""
        self.settings.chat.builtin.font_size = new_size
        self.settings.save()
        # Relayout all other widgets to match
        for widget in self._widgets.values():
            widget._model.layoutChanged.emit()

    def _on_settings_changed(self) -> None:
        """Persist chat setting toggles and relayout all widgets."""
        self.settings.save()
        for widget in self._widgets.values():
            widget._model.layoutChanged.emit()

    def _on_tab_close(self, index: int) -> None:
        """Handle tab close button clicked."""
        widget = self._tab_widget.widget(index)
        if isinstance(widget, ChatWidget):
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

    def _on_socials_fetched(self, channel_key: str, socials: dict) -> None:
        """Update a chat widget with fetched social links."""
        widget = self._widgets.get(channel_key)
        if widget:
            widget.set_socials(socials)

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
