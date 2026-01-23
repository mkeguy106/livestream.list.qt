"""Chat window with tabbed multi-channel support."""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QWidget,
)

from ...chat.manager import ChatManager
from ...chat.models import ModerationEvent
from ...core.models import Livestream, StreamPlatform
from ...core.settings import Settings
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
    from PySide6.QtGui import QPainter

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


class ChatWindow(QMainWindow):
    """Main chat window with tabbed channels.

    Manages multiple ChatWidgets in a QTabWidget, handles opening/closing
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

        # Tab widget
        self._tab_widget = QTabWidget()
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.setMovable(True)
        self._tab_widget.setDocumentMode(True)
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close)

        self._tab_widget.setStyleSheet(self._build_tab_stylesheet())

        self.setCentralWidget(self._tab_widget)

        # Tab bar context menu for pop-out
        tab_bar = self._tab_widget.tabBar()
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)

        # Window styling
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f0f1a;
            }
        """)

    def _build_tab_stylesheet(self) -> str:
        """Build the tab stylesheet using current color settings."""
        active_color = self.settings.chat.builtin.tab_active_color
        inactive_color = self.settings.chat.builtin.tab_inactive_color
        return f"""
            QTabWidget::pane {{
                border: none;
                background-color: #1a1a2e;
            }}
            QTabBar::tab {{
                background-color: {inactive_color};
                color: #ccc;
                padding: 6px 12px;
                border: none;
                margin-right: 1px;
            }}
            QTabBar::tab:selected {{
                background-color: {active_color};
                color: white;
            }}
            QTabBar::tab:hover {{
                background-color: #1f2b4d;
            }}
        """

    def update_tab_style(self) -> None:
        """Refresh tab stylesheet from current settings (call after prefs change)."""
        self._tab_widget.setStyleSheet(self._build_tab_stylesheet())

    def _connect_signals(self) -> None:
        """Connect ChatManager signals."""
        self.chat_manager.messages_received.connect(self._on_messages_received)
        self.chat_manager.moderation_received.connect(self._on_moderation_received)
        self.chat_manager.chat_opened.connect(self._on_chat_opened)
        self.chat_manager.chat_closed.connect(self._on_chat_closed)
        self.chat_manager.emote_cache_updated.connect(self._on_emote_cache_updated)
        self.chat_manager.auth_state_changed.connect(self._on_auth_state_changed)
        self.chat_manager.chat_error.connect(self._on_chat_error)

    def open_chat(self, livestream: Livestream) -> None:
        """Open or focus a chat tab for a livestream."""
        channel_key = livestream.channel.unique_key
        self._livestreams[channel_key] = livestream

        if channel_key in self._widgets:
            # Focus existing tab
            widget = self._widgets[channel_key]
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

        # Set shared emote cache and emote map on the widget
        widget.set_emote_cache(self.chat_manager.emote_cache.pixmap_dict)
        if self.chat_manager.emote_map:
            widget.set_emote_map(self.chat_manager.emote_map)

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
        emote_map = self.chat_manager.emote_map
        for widget in self._widgets.values():
            widget.set_emote_cache(cache_dict)
            if emote_map:
                widget.set_emote_map(emote_map)
            widget.repaint_messages()

    def _on_message_sent(self, channel_key: str, text: str) -> None:
        """Handle a message being sent from a chat widget."""
        self.chat_manager.send_message(channel_key, text)

    def _on_tab_close(self, index: int) -> None:
        """Handle tab close button clicked."""
        widget = self._tab_widget.widget(index)
        if isinstance(widget, ChatWidget):
            self.close_chat(widget.channel_key)

    def _on_tab_context_menu(self, pos) -> None:
        """Show context menu on tab bar right-click."""
        from PySide6.QtWidgets import QMenu

        tab_bar = self._tab_widget.tabBar()
        index = tab_bar.tabAt(pos)
        if index < 0:
            return

        widget = self._tab_widget.widget(index)
        if not isinstance(widget, ChatWidget):
            return

        menu = QMenu(self)
        popout_action = menu.addAction("Pop Out")
        popout_action.triggered.connect(lambda: self._on_popout_requested(widget.channel_key))
        menu.exec(tab_bar.mapToGlobal(pos))

    def _on_auth_state_changed(self, _authenticated: bool) -> None:
        """Update all widgets when auth state changes (platform-aware)."""
        for widget in self._widgets.values():
            platform = widget.livestream.channel.platform
            if platform == StreamPlatform.KICK:
                auth = bool(self.settings.kick.access_token)
            else:
                auth = bool(self.settings.twitch.access_token)
            widget.set_authenticated(auth)

    def _on_chat_error(self, channel_key: str, message: str) -> None:
        """Show a chat error in the relevant widget."""
        widget = self._widgets.get(channel_key)
        if widget:
            widget.show_error(message)

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
        if widget and channel_key in self._livestreams:
            livestream = self._livestreams[channel_key]
            platform = livestream.channel.platform
            icon = _create_dot_icon(PLATFORM_COLORS.get(platform, QColor("#888")))
            tab_name = livestream.channel.display_name or livestream.channel.channel_id

            widget.setParent(self._tab_widget)
            idx = self._tab_widget.addTab(widget, icon, tab_name)
            self._tab_widget.setCurrentIndex(idx)

        popout.close()
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
        """Hide the window instead of closing to keep chats alive."""
        self.save_window_state()
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

        # Reparent widget
        widget.setParent(self)
        self.setCentralWidget(widget)

        # Add toolbar with pop-in button
        toolbar = self.addToolBar("Actions")
        toolbar.setMovable(False)
        popin_action = toolbar.addAction("Pop In")
        popin_action.triggered.connect(lambda: self.popin_requested.emit(self.channel_key))

        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f0f1a;
            }
            QToolBar {
                background-color: #16213e;
                border: none;
                padding: 2px;
            }
        """)

    def take_widget(self) -> ChatWidget | None:
        """Remove and return the chat widget for re-docking."""
        widget = self._widget
        self._widget = None
        if widget:
            self.setCentralWidget(QWidget())  # Replace with empty widget
        return widget

    def closeEvent(self, event) -> None:  # noqa: N802
        """Emit closed signal when window is closed."""
        self.closed.emit(self.channel_key)
        super().closeEvent(event)
