"""Main window and UI components for the Qt application."""

import asyncio
import fnmatch
import logging
import re
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..__version__ import __version__
from ..core.autostart import is_autostart_enabled, set_autostart
from ..core.chat import ChatLauncher
from ..core.models import Channel, Livestream, SortMode, StreamPlatform, UIStyle

if TYPE_CHECKING:
    from .app import Application

logger = logging.getLogger(__name__)

# Platform colors
PLATFORM_COLORS = {
    StreamPlatform.TWITCH: "#9146FF",
    StreamPlatform.KICK: "#53FC18",
    StreamPlatform.YOUTUBE: "#FF0000",
}

# UI style configurations
UI_STYLES = {
    UIStyle.DEFAULT: {
        "name": "Default", "margin_v": 4, "margin_h": 12,
        "spacing": 10, "icon_size": 16,
    },
    UIStyle.COMPACT_1: {
        "name": "Compact 1", "margin_v": 4, "margin_h": 12,
        "spacing": 8, "icon_size": 14,
    },
    UIStyle.COMPACT_2: {
        "name": "Compact 2", "margin_v": 2, "margin_h": 6,
        "spacing": 4, "icon_size": 12,
    },
    UIStyle.COMPACT_3: {
        "name": "Compact 3", "margin_v": 1, "margin_h": 4,
        "spacing": 2, "icon_size": 10,
    },
}


class StreamRow(QWidget):
    """Widget representing a single stream in the list."""

    play_clicked = Signal(object)  # Livestream
    stop_clicked = Signal(str)  # channel_key
    favorite_clicked = Signal(str)  # channel_key
    chat_clicked = Signal(str, str, str)  # channel_id, platform, video_id
    browser_clicked = Signal(str, str)  # channel_id, platform

    def __init__(self, livestream: Livestream, is_playing: bool, settings, parent=None):
        super().__init__(parent)
        self.livestream = livestream
        self._is_playing = is_playing
        self._settings = settings
        self._setup_ui()
        self.update(livestream, is_playing)

    def _setup_ui(self):
        """Set up the row UI."""
        style = UI_STYLES.get(self._settings.ui_style, UI_STYLES[UIStyle.DEFAULT])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(style["margin_h"], style["margin_v"],
                                  style["margin_h"], style["margin_v"])
        layout.setSpacing(style["spacing"])

        # Selection checkbox (hidden by default)
        self.checkbox = QCheckBox()
        self.checkbox.setVisible(False)
        layout.addWidget(self.checkbox)

        # Live indicator
        self.live_indicator = QLabel()
        self.live_indicator.setFixedWidth(16)
        layout.addWidget(self.live_indicator)

        # Platform icon
        self.platform_label = QLabel()
        self.platform_label.setFixedWidth(20)
        if self._settings.channel_icons.show_platform:
            layout.addWidget(self.platform_label)

        # Channel name and info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        info_layout.setContentsMargins(0, 0, 0, 0)

        # Name row
        name_row = QHBoxLayout()
        name_row.setSpacing(style["spacing"])

        self.name_label = QLabel()
        self.name_label.setStyleSheet("font-weight: bold;")
        self.name_label.setMinimumWidth(80)  # Ensure channel name stays visible
        name_row.addWidget(self.name_label)

        self.duration_label = QLabel()
        self.duration_label.setStyleSheet("color: gray;")
        name_row.addWidget(self.duration_label)

        self.playing_label = QLabel()
        self.playing_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        self.playing_label.setVisible(False)
        name_row.addWidget(self.playing_label)

        name_row.addStretch()

        self.viewers_label = QLabel()
        self.viewers_label.setStyleSheet("color: gray;")
        name_row.addWidget(self.viewers_label)

        info_layout.addLayout(name_row)

        # Title row (only in default style)
        if self._settings.ui_style == UIStyle.DEFAULT:
            from PySide6.QtWidgets import QSizePolicy
            self.title_label = QLabel()
            self.title_label.setStyleSheet("color: gray; font-size: 11px;")
            self.title_label.setWordWrap(False)
            # Allow title to shrink and hide when window is small
            self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self.title_label.setMinimumWidth(0)
            info_layout.addWidget(self.title_label)
        else:
            self.title_label = None

        layout.addLayout(info_layout, 1)

        # Buttons
        icon_size = style["icon_size"]

        # Browser button
        if self._settings.channel_icons.show_browser:
            self.browser_btn = QPushButton("B")
            self.browser_btn.setFixedSize(icon_size + 8, icon_size + 8)
            self.browser_btn.setToolTip("Open in browser")
            self.browser_btn.clicked.connect(self._on_browser_clicked)
            layout.addWidget(self.browser_btn)
        else:
            self.browser_btn = None

        # Chat button
        if self._settings.channel_icons.show_chat:
            self.chat_btn = QPushButton("C")
            self.chat_btn.setFixedSize(icon_size + 8, icon_size + 8)
            self.chat_btn.setToolTip("Open chat")
            self.chat_btn.clicked.connect(self._on_chat_clicked)
            layout.addWidget(self.chat_btn)
        else:
            self.chat_btn = None

        # Favorite button
        if self._settings.channel_icons.show_favorite:
            self.favorite_btn = QPushButton()
            self.favorite_btn.setFixedSize(icon_size + 8, icon_size + 8)
            self.favorite_btn.clicked.connect(self._on_favorite_clicked)
            layout.addWidget(self.favorite_btn)
        else:
            self.favorite_btn = None

        # Play/Stop button
        if self._settings.channel_icons.show_play:
            self.play_btn = QPushButton()
            self.play_btn.setFixedSize(icon_size + 12, icon_size + 8)
            self.play_btn.clicked.connect(self._on_play_clicked)
            layout.addWidget(self.play_btn)
        else:
            self.play_btn = None

    def update(self, livestream: Livestream, is_playing: bool):
        """Update the row with new data."""
        self.livestream = livestream
        self._is_playing = is_playing

        channel = livestream.channel

        # Live indicator
        if livestream.live:
            self.live_indicator.setText("🟢")
            self.live_indicator.setToolTip("Live")
        else:
            self.live_indicator.setText("⚫")
            self.live_indicator.setToolTip("Offline")

        # Platform icon
        platform_icons = {"twitch": "T", "youtube": "Y", "kick": "K"}
        platform_name = channel.platform.value
        self.platform_label.setText(platform_icons.get(platform_name, "?"))
        if self._settings.platform_colors:
            color = PLATFORM_COLORS.get(channel.platform, "#888888")
            self.platform_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        else:
            self.platform_label.setStyleSheet("font-weight: bold;")

        # Channel name
        self.name_label.setText(channel.display_name or channel.channel_id)
        if self._settings.platform_colors:
            color = PLATFORM_COLORS.get(channel.platform, "#888888")
            self.name_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        else:
            self.name_label.setStyleSheet("font-weight: bold;")

        # Duration / Last seen
        if self._settings.channel_info.show_live_duration:
            if livestream.live and livestream.start_time:
                self.duration_label.setText(livestream.live_duration_str)
                self.duration_label.setVisible(True)
            elif not livestream.live and livestream.last_live_time:
                self.duration_label.setText(livestream.last_seen_str)
                self.duration_label.setVisible(True)
            else:
                self.duration_label.setVisible(False)
        else:
            self.duration_label.setVisible(False)

        # Playing indicator
        self.playing_label.setText("▶ Playing")
        self.playing_label.setVisible(is_playing)

        # Viewers
        if self._settings.channel_info.show_viewers and livestream.live:
            self.viewers_label.setText(livestream.viewers_str)
            self.viewers_label.setVisible(True)
        else:
            self.viewers_label.setVisible(False)

        # Title
        if self.title_label:
            if livestream.live:
                parts = []
                if livestream.game:
                    parts.append(livestream.game)
                if livestream.title:
                    parts.append(livestream.title)
                self.title_label.setText(" - ".join(parts) if parts else "")
                self.title_label.setVisible(bool(parts))
            else:
                self.title_label.setVisible(False)

        # Favorite button
        if self.favorite_btn:
            if channel.favorite:
                self.favorite_btn.setText("★")
                self.favorite_btn.setToolTip("Remove from favorites")
            else:
                self.favorite_btn.setText("☆")
                self.favorite_btn.setToolTip("Add to favorites")

        # Play/Stop button
        if self.play_btn:
            if is_playing:
                self.play_btn.setText("■")
                self.play_btn.setToolTip("Stop playback")
                self.play_btn.setStyleSheet("color: red;")
            else:
                self.play_btn.setText("▶")
                self.play_btn.setToolTip("Play stream")
                self.play_btn.setStyleSheet("")

    def set_selection_mode(self, enabled: bool):
        """Show/hide selection checkbox."""
        self.checkbox.setVisible(enabled)

    def is_selected(self) -> bool:
        """Return whether this row is selected."""
        return self.checkbox.isChecked()

    def set_selected(self, selected: bool):
        """Set selection state."""
        self.checkbox.setChecked(selected)

    def _on_play_clicked(self):
        if self._is_playing:
            self.stop_clicked.emit(self.livestream.channel.unique_key)
        else:
            self.play_clicked.emit(self.livestream)

    def _on_favorite_clicked(self):
        self.favorite_clicked.emit(self.livestream.channel.unique_key)

    def _on_chat_clicked(self):
        ch = self.livestream.channel
        video_id = getattr(self.livestream, 'video_id', None) or ""
        self.chat_clicked.emit(ch.channel_id, ch.platform.value, video_id)

    def _on_browser_clicked(self):
        ch = self.livestream.channel
        self.browser_clicked.emit(ch.channel_id, ch.platform.value)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, app: "Application"):
        super().__init__()
        self.app = app
        self._stream_rows: dict[str, StreamRow] = {}
        self._selection_mode = False
        self._initial_check_complete = False
        self._name_filter = ""
        self._platform_filter: StreamPlatform | None = None
        self._chat_launcher = ChatLauncher(app.settings.chat)
        self._force_quit = False  # When True, closeEvent quits instead of minimizing

        self._setup_ui()
        self._setup_shortcuts()
        self._connect_signals()
        self._apply_settings()

    def _setup_ui(self):
        """Set up the main window UI."""
        self.setWindowTitle("Livestream List (Qt)")
        self.resize(
            self.app.settings.window.width,
            self.app.settings.window.height
        )
        # Restore window position if saved
        if self.app.settings.window.x is not None and self.app.settings.window.y is not None:
            self.move(self.app.settings.window.x, self.app.settings.window.y)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Menu bar
        self._create_menu_bar()

        # Toolbar
        self._create_toolbar()

        # Filter bar
        self._create_filter_bar(layout)

        # Stacked widget for different views
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        # Loading page
        loading_page = QWidget()
        loading_layout = QVBoxLayout(loading_page)
        loading_layout.setAlignment(Qt.AlignCenter)

        self.loading_label = QLabel("Loading channels...")
        self.loading_label.setAlignment(Qt.AlignCenter)
        loading_layout.addWidget(self.loading_label)

        self.loading_progress = QProgressBar()
        self.loading_progress.setMaximumWidth(300)
        self.loading_progress.setRange(0, 0)  # Indeterminate
        loading_layout.addWidget(self.loading_progress, 0, Qt.AlignCenter)

        self.loading_detail = QLabel("")
        self.loading_detail.setAlignment(Qt.AlignCenter)
        self.loading_detail.setStyleSheet("color: gray;")
        loading_layout.addWidget(self.loading_detail)

        self.stack.addWidget(loading_page)  # Index 0

        # Empty page
        empty_page = QWidget()
        empty_layout = QVBoxLayout(empty_page)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_label = QLabel("No channels added yet.\nClick the + button to add a channel.")
        empty_label.setAlignment(Qt.AlignCenter)
        empty_label.setStyleSheet("color: gray;")
        empty_layout.addWidget(empty_label)
        self.stack.addWidget(empty_page)  # Index 1

        # All offline page
        all_offline_page = QWidget()
        all_offline_layout = QVBoxLayout(all_offline_page)
        all_offline_layout.setAlignment(Qt.AlignCenter)
        self.all_offline_label = QLabel("All channels are offline")
        self.all_offline_label.setAlignment(Qt.AlignCenter)
        self.all_offline_label.setStyleSheet("color: gray;")
        all_offline_layout.addWidget(self.all_offline_label)
        self.stack.addWidget(all_offline_page)  # Index 2

        # Stream list page
        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)

        self.stream_list = QListWidget()
        self.stream_list.setSpacing(0)
        self.stream_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        list_layout.addWidget(self.stream_list)

        self.stack.addWidget(list_page)  # Index 3

        # Selection action bar
        self.selection_bar = QWidget()
        selection_layout = QHBoxLayout(self.selection_bar)
        selection_layout.setContentsMargins(8, 4, 8, 4)

        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all)
        selection_layout.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(self._deselect_all)
        selection_layout.addWidget(deselect_all_btn)

        selection_layout.addStretch()

        self.selection_count_label = QLabel("0 selected")
        selection_layout.addWidget(self.selection_count_label)

        selection_layout.addStretch()

        delete_selected_btn = QPushButton("Delete Selected")
        delete_selected_btn.setStyleSheet("color: red;")
        delete_selected_btn.clicked.connect(self._delete_selected)
        selection_layout.addWidget(delete_selected_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._exit_selection_mode)
        selection_layout.addWidget(cancel_btn)

        self.selection_bar.setVisible(False)
        layout.addWidget(self.selection_bar)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label, 1)

        self.live_count_label = QLabel("")
        self.status_bar.addPermanentWidget(self.live_count_label)

    def _create_menu_bar(self):
        """Create the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        add_action = file_menu.addAction("&Add Channel")
        add_action.setShortcut("Ctrl+N")
        add_action.triggered.connect(self.show_add_channel_dialog)

        file_menu.addSeparator()

        export_action = file_menu.addAction("&Export...")
        export_action.triggered.connect(self.show_export_dialog)

        import_action = file_menu.addAction("&Import...")
        import_action.triggered.connect(self.show_import_dialog)

        file_menu.addSeparator()

        quit_action = file_menu.addAction("&Quit")
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self._quit_app)

        # Edit menu
        edit_menu = menubar.addMenu("&Edit")

        refresh_action = edit_menu.addAction("&Refresh")
        refresh_action.setShortcut("Ctrl+R")
        refresh_action.triggered.connect(self._on_refresh)

        edit_menu.addSeparator()

        select_action = edit_menu.addAction("&Select Mode")
        select_action.triggered.connect(self._toggle_selection_mode)

        edit_menu.addSeparator()

        prefs_action = edit_menu.addAction("&Preferences")
        prefs_action.setShortcut("Ctrl+,")
        prefs_action.triggered.connect(self.show_preferences_dialog)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        about_action = help_menu.addAction("&About")
        about_action.triggered.connect(self._show_about)

    def _create_toolbar(self):
        """Create the toolbar."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Add channel button
        add_btn = QToolButton()
        add_btn.setText("+")
        add_btn.setToolTip("Add channel (Ctrl+N)")
        add_btn.clicked.connect(self.show_add_channel_dialog)
        toolbar.addWidget(add_btn)

        # Refresh button
        refresh_btn = QToolButton()
        refresh_btn.setText("↻")
        refresh_btn.setToolTip("Refresh (Ctrl+R)")
        refresh_btn.clicked.connect(self._on_refresh)
        toolbar.addWidget(refresh_btn)

        toolbar.addSeparator()

        # Selection mode button
        self.select_btn = QToolButton()
        self.select_btn.setText("☑")
        self.select_btn.setToolTip("Selection mode")
        self.select_btn.setCheckable(True)
        self.select_btn.clicked.connect(self._toggle_selection_mode)
        toolbar.addWidget(self.select_btn)

    def _create_filter_bar(self, parent_layout):
        """Create the filter/sort bar."""
        filter_widget = QWidget()
        filter_layout = QHBoxLayout(filter_widget)
        filter_layout.setContentsMargins(8, 4, 8, 4)

        # Hide offline checkbox
        self.hide_offline_cb = QCheckBox("Hide Offline")
        self.hide_offline_cb.setChecked(self.app.settings.hide_offline)
        self.hide_offline_cb.stateChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.hide_offline_cb)

        # Favorites checkbox
        self.favorites_cb = QCheckBox("Favorites")
        self.favorites_cb.setChecked(self.app.settings.favorites_only)
        self.favorites_cb.stateChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.favorites_cb)

        # Name filter
        self.name_filter_edit = QLineEdit()
        self.name_filter_edit.setPlaceholderText("Filter by name...")
        self.name_filter_edit.setMaximumWidth(200)
        self.name_filter_edit.textChanged.connect(self._on_name_filter_changed)
        filter_layout.addWidget(self.name_filter_edit)

        filter_layout.addStretch()

        # Platform filter
        filter_layout.addWidget(QLabel("Platform:"))
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("All", None)
        self.platform_combo.addItem("Twitch", StreamPlatform.TWITCH)
        self.platform_combo.addItem("YouTube", StreamPlatform.YOUTUBE)
        self.platform_combo.addItem("Kick", StreamPlatform.KICK)
        self.platform_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.platform_combo)

        # Sort dropdown
        filter_layout.addWidget(QLabel("Sort:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Name", SortMode.NAME)
        self.sort_combo.addItem("Viewers", SortMode.VIEWERS)
        self.sort_combo.addItem("Playing", SortMode.PLAYING)
        self.sort_combo.addItem("Last Seen", SortMode.LAST_SEEN)
        self.sort_combo.addItem("Time Live", SortMode.TIME_LIVE)
        self.sort_combo.setCurrentIndex(self.app.settings.sort_mode.value)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        filter_layout.addWidget(self.sort_combo)

        parent_layout.addWidget(filter_widget)

    def _setup_shortcuts(self):
        """Set up keyboard shortcuts."""
        QShortcut(QKeySequence("F5"), self, self._on_refresh)

    def _connect_signals(self):
        """Connect application signals."""
        self.app.stream_online.connect(self._on_stream_online)
        self.app.refresh_complete.connect(self._on_refresh_complete)
        self.app.refresh_error.connect(self._on_refresh_error)

    def _apply_settings(self):
        """Apply current settings to the UI."""
        self.hide_offline_cb.setChecked(self.app.settings.hide_offline)
        self.favorites_cb.setChecked(self.app.settings.favorites_only)
        self.sort_combo.setCurrentIndex(self.app.settings.sort_mode.value)

    def set_loading_complete(self):
        """Switch from loading view to appropriate content view."""
        self._update_view()

    def set_status(self, message: str):
        """Set the status bar message."""
        self.status_label.setText(message)

    def set_loading_status(self, message: str, detail: str = ""):
        """Set loading status message."""
        self.loading_label.setText(message)
        self.loading_detail.setText(detail)

    def refresh_stream_list(self):
        """Refresh the stream list display."""
        self._update_view()

    def _update_view(self):
        """Update the view based on current state."""
        monitor = self.app.monitor
        if not monitor:
            self.stack.setCurrentIndex(0)  # Loading
            return

        channels = monitor.channels
        if not channels:
            self.stack.setCurrentIndex(1)  # Empty
            return

        # Get filtered and sorted livestreams
        livestreams = self._get_filtered_sorted_livestreams()

        if not livestreams:
            # Check why empty
            if self.hide_offline_cb.isChecked():
                if not self._initial_check_complete:
                    self.all_offline_label.setText("Checking stream status...")
                else:
                    self.all_offline_label.setText("All channels are offline")
            elif self.favorites_cb.isChecked():
                self.all_offline_label.setText("No favorite channels")
            elif self._name_filter:
                self.all_offline_label.setText(f"No channels match '{self._name_filter}'")
            elif self._platform_filter:
                platform_name = (
                    self._platform_filter.value
                    if hasattr(self._platform_filter, 'value')
                    else str(self._platform_filter)
                )
                self.all_offline_label.setText(f"No {platform_name} channels")
            else:
                self.all_offline_label.setText("No channels to show")
            self.stack.setCurrentIndex(2)  # All offline
            return

        # Show list
        self.stack.setCurrentIndex(3)
        self._populate_list(livestreams)
        self._update_live_count()

    def _get_filtered_sorted_livestreams(self) -> list[Livestream]:
        """Get filtered and sorted list of livestreams."""
        monitor = self.app.monitor
        if not monitor:
            return []

        livestreams = monitor.livestreams

        # Apply filters
        hide_offline = self.hide_offline_cb.isChecked()
        favorites_only = self.favorites_cb.isChecked()

        filtered = []
        for ls in livestreams:
            # Hide offline filter
            if hide_offline and not ls.live:
                continue

            # Favorites filter
            if favorites_only and not ls.channel.favorite:
                continue

            # Name filter
            if self._name_filter:
                name = (ls.channel.display_name or ls.channel.channel_id).lower()
                pattern = self._name_filter.lower()
                if "*" in pattern:
                    if not fnmatch.fnmatch(name, pattern):
                        continue
                elif pattern not in name:
                    continue

            # Platform filter
            if self._platform_filter and ls.channel.platform != self._platform_filter:
                continue

            filtered.append(ls)

        # Sort
        sort_mode = self.sort_combo.currentData()
        streamlink = self.app.streamlink

        def sort_key(ls: Livestream):
            live = 0 if ls.live else 1
            is_playing = 0 if streamlink and streamlink.is_playing(ls.channel.unique_key) else 1

            if sort_mode == SortMode.NAME:
                name = (ls.channel.display_name or ls.channel.channel_id).lower()
                return (live, name)
            elif sort_mode == SortMode.VIEWERS:
                viewers = -(ls.viewers or 0)
                return (live, viewers)
            elif sort_mode == SortMode.PLAYING:
                name = (ls.channel.display_name or ls.channel.channel_id).lower()
                return (is_playing, live, name)
            elif sort_mode == SortMode.LAST_SEEN:
                if ls.live and ls.start_time:
                    # Live streams: sort by time live (longest first = earliest start_time)
                    start = (
                        ls.start_time if ls.start_time.tzinfo
                        else ls.start_time.replace(tzinfo=timezone.utc)
                    )
                    return (live, start.timestamp())
                else:
                    # Offline streams: sort by last seen (most recent first)
                    last_live = ls.last_live_time or datetime.min.replace(tzinfo=timezone.utc)
                    return (live, -last_live.timestamp())
            elif sort_mode == SortMode.TIME_LIVE:
                if ls.live and ls.start_time:
                    now = datetime.now(timezone.utc) if ls.start_time.tzinfo else datetime.now()
                    uptime = (now - ls.start_time).total_seconds()
                else:
                    uptime = 0
                return (live, -uptime)
            else:
                return (live, 0)

        filtered.sort(key=sort_key)
        return filtered

    def _populate_list(self, livestreams: list[Livestream]):
        """Populate the list widget with stream rows."""
        self.stream_list.clear()
        self._stream_rows.clear()

        streamlink = self.app.streamlink

        for ls in livestreams:
            key = ls.channel.unique_key
            is_playing = streamlink.is_playing(key) if streamlink else False

            row = StreamRow(ls, is_playing, self.app.settings)
            row.play_clicked.connect(self._on_play_stream)
            row.stop_clicked.connect(self._on_stop_stream)
            row.favorite_clicked.connect(self._on_toggle_favorite)
            row.chat_clicked.connect(self._on_open_chat)
            row.browser_clicked.connect(self._on_open_browser)
            row.checkbox.stateChanged.connect(self._update_selection_count)

            if self._selection_mode:
                row.set_selection_mode(True)

            item = QListWidgetItem()
            item.setSizeHint(row.sizeHint())
            self.stream_list.addItem(item)
            self.stream_list.setItemWidget(item, row)

            self._stream_rows[key] = row

    def _update_live_count(self):
        """Update the live count label."""
        monitor = self.app.monitor
        if not monitor:
            self.live_count_label.setText("")
            return

        total = len(monitor.channels)
        live = len([ls for ls in monitor.livestreams if ls.live])
        self.live_count_label.setText(f"{live} live / {total} total")

    def _on_filter_changed(self):
        """Handle filter checkbox changes."""
        self.app.settings.hide_offline = self.hide_offline_cb.isChecked()
        self.app.settings.favorites_only = self.favorites_cb.isChecked()
        self._platform_filter = self.platform_combo.currentData()
        self.app.save_settings()
        self.refresh_stream_list()

    def _on_name_filter_changed(self, text: str):
        """Handle name filter text change."""
        self._name_filter = text
        self.refresh_stream_list()

    def _on_sort_changed(self, index: int):
        """Handle sort mode change."""
        self.app.settings.sort_mode = self.sort_combo.currentData()
        self.app.save_settings()
        self.refresh_stream_list()

    def _on_stream_online(self, livestream):
        """Handle stream going online."""
        self.refresh_stream_list()

    def _on_refresh_complete(self):
        """Handle refresh completion."""
        self.refresh_stream_list()

    def _on_refresh_error(self, error_msg: str):
        """Handle refresh error - show message in status bar."""
        self.set_status(f"⚠ {error_msg}")

    def _on_refresh(self):
        """Handle refresh action."""
        self.set_status("Refreshing...")
        self.app.refresh(on_complete=lambda: self.set_status("Ready"))

    def _on_item_double_clicked(self, item: QListWidgetItem):
        """Handle double-click on list item."""
        row = self.stream_list.itemWidget(item)
        if isinstance(row, StreamRow) and row.livestream:
            self._on_play_stream(row.livestream)

    def _on_play_stream(self, livestream: Livestream):
        """Handle playing a stream."""
        self.play_stream(livestream)

    def play_stream(self, livestream: Livestream):
        """Launch a stream for playback."""
        if not self.app.streamlink:
            return

        self.set_status(f"Launching {livestream.channel.display_name}...")

        # Launch in background
        def launch():
            try:
                self.app.streamlink.launch(livestream)
                # Open browser chat if auto-open enabled and in browser mode
                if (self.app.settings.chat.auto_open and self.app.settings.chat.enabled
                        and self.app.settings.chat.mode == "browser"):
                    ch = livestream.channel
                    video_id = getattr(livestream, 'video_id', None) or ""
                    self._chat_launcher.open_chat(ch.channel_id, ch.platform.value, video_id)
            except Exception as e:
                logger.error(f"Launch error: {e}")

        thread = threading.Thread(target=launch, daemon=True)
        thread.start()

        # Auto-open built-in chat on main thread (if enabled)
        if (self.app.settings.chat.auto_open and self.app.settings.chat.enabled
                and self.app.settings.chat.mode == "builtin" and self.app.chat_manager):
            self.app.open_builtin_chat(livestream)

        # Update UI after short delay
        QTimer.singleShot(500, self.refresh_stream_list)
        name = livestream.channel.display_name
        QTimer.singleShot(1000, lambda: self.set_status(f"Playing {name}"))

    def _on_stop_stream(self, channel_key: str):
        """Handle stopping a stream."""
        if self.app.streamlink:
            self.app.streamlink.stop_stream(channel_key)
            self.refresh_stream_list()
            self.set_status("Playback stopped")

    def _on_toggle_favorite(self, channel_key: str):
        """Handle toggling favorite status."""
        monitor = self.app.monitor
        if not monitor:
            return

        # Find channel by key
        channel = None
        for ch in monitor.channels:
            if ch.unique_key == channel_key:
                channel = ch
                break

        if channel:
            monitor.set_favorite(channel, not channel.favorite)
            self.app.save_channels()
            self.refresh_stream_list()

    def _on_open_chat(self, channel_id: str, platform: str, video_id: str):
        """Handle opening chat."""
        if self.app.settings.chat.mode == "builtin" and self.app.chat_manager:
            # Find the livestream for this channel
            livestream = self._find_livestream(channel_id, platform)
            if livestream:
                self.app.open_builtin_chat(livestream)
            return
        self._chat_launcher.open_chat(channel_id, platform, video_id)

    def _find_livestream(self, channel_id: str, platform: str) -> "Livestream | None":
        """Find a livestream by channel_id and platform."""
        if not self.app.monitor:
            return None
        key = f"{platform}:{channel_id}"
        for ls in self.app.monitor.livestreams:
            if ls.channel.unique_key == key:
                return ls
        return None

    def _on_open_browser(self, channel_id: str, platform: str):
        """Handle opening in browser."""
        self._chat_launcher.open_channel(channel_id, platform)

    def _toggle_selection_mode(self):
        """Toggle selection mode."""
        self._selection_mode = not self._selection_mode
        self.select_btn.setChecked(self._selection_mode)
        self.selection_bar.setVisible(self._selection_mode)

        for row in self._stream_rows.values():
            row.set_selection_mode(self._selection_mode)

        self._update_selection_count()

    def _exit_selection_mode(self):
        """Exit selection mode."""
        self._selection_mode = False
        self.select_btn.setChecked(False)
        self.selection_bar.setVisible(False)

        for row in self._stream_rows.values():
            row.set_selection_mode(False)
            row.set_selected(False)

    def _select_all(self):
        """Select all visible rows."""
        for row in self._stream_rows.values():
            row.set_selected(True)
        self._update_selection_count()

    def _deselect_all(self):
        """Deselect all rows."""
        for row in self._stream_rows.values():
            row.set_selected(False)
        self._update_selection_count()

    def _update_selection_count(self):
        """Update the selection count label."""
        count = sum(1 for row in self._stream_rows.values() if row.is_selected())
        self.selection_count_label.setText(f"{count} selected")

    def _delete_selected(self):
        """Delete selected channels."""
        selected_keys = [key for key, row in self._stream_rows.items() if row.is_selected()]
        if not selected_keys:
            return

        reply = QMessageBox.question(
            self,
            "Delete Channels",
            f"Delete {len(selected_keys)} channel(s)?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            monitor = self.app.monitor
            for key in selected_keys:
                if key in monitor._channels:
                    del monitor._channels[key]
                if key in monitor._livestreams:
                    del monitor._livestreams[key]
            self.app.save_channels()
            self._exit_selection_mode()
            self.refresh_stream_list()

    def _show_about(self):
        """Show about dialog."""
        dialog = AboutDialog(self, self.app)
        dialog.exec()

    def show_add_channel_dialog(self):
        """Show the add channel dialog."""
        dialog = AddChannelDialog(self, self.app)
        if dialog.exec() == QDialog.Accepted:
            self.refresh_stream_list()

    def show_preferences_dialog(self, initial_tab: int = 0):
        """Show the preferences dialog."""
        dialog = PreferencesDialog(self, self.app, initial_tab=initial_tab)
        dialog.exec()
        self._apply_settings()
        self.refresh_stream_list()

    def _show_chat_preferences(self):
        """Open preferences dialog directly on the Chat tab."""
        self.show_preferences_dialog(initial_tab=2)

    def show_export_dialog(self):
        """Show the export dialog."""
        dialog = ExportDialog(self, self.app)
        dialog.exec()

    def show_import_dialog(self):
        """Show the import dialog."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Channels",
            "",
            "JSON Files (*.json);;All Files (*)"
        )
        if file_path:
            self._import_from_file(file_path)

    def _import_from_file(self, file_path: str):
        """Import channels and settings from a file."""
        import json

        from ..core.settings import Settings
        try:
            with open(file_path) as f:
                data = json.load(f)

            channels_data = data.get('channels', [])
            imported = 0

            for ch_data in channels_data:
                platform = StreamPlatform(ch_data['platform'])
                channel = Channel(
                    channel_id=ch_data['channel_id'],
                    platform=platform,
                    display_name=ch_data.get('display_name'),
                    favorite=ch_data.get('favorite', False),
                )

                key = channel.unique_key
                if key not in self.app.monitor._channels:
                    self.app.monitor._channels[key] = channel
                    self.app.monitor._livestreams[key] = Livestream(channel=channel)
                    imported += 1

            # Import settings if present
            settings_imported = False
            if 'settings' in data:
                imported_settings = data['settings']
                # Preserve current auth tokens and window geometry
                imported_settings['twitch'] = self.app.settings._to_dict().get('twitch', {})
                imported_settings['youtube'] = self.app.settings._to_dict().get('youtube', {})
                imported_settings['window'] = self.app.settings._to_dict().get('window', {})
                # Also preserve close_to_tray_asked state
                imported_settings['close_to_tray_asked'] = self.app.settings.close_to_tray_asked
                # Apply imported settings
                new_settings = Settings._from_dict(imported_settings)
                # Copy all fields to current settings
                for field_name in ['refresh_interval', 'minimize_to_tray', 'start_minimized',
                                   'check_for_updates', 'autostart', 'close_to_tray',
                                   'sort_mode', 'hide_offline', 'favorites_only', 'ui_style',
                                   'platform_colors', 'streamlink', 'notifications', 'chat',
                                   'channel_info', 'channel_icons']:
                    if hasattr(new_settings, field_name):
                        setattr(self.app.settings, field_name, getattr(new_settings, field_name))
                self.app.save_settings()
                settings_imported = True

            self.app.save_channels()
            self.refresh_stream_list()

            if imported > 0:
                # Trigger refresh to check stream status of newly imported channels
                msg = f"Imported {imported} channels"
                if settings_imported:
                    msg += " and settings"
                self.set_status(f"{msg}. Checking stream status...")
                self.app.refresh(on_complete=lambda: self.set_status("Ready"))
            elif settings_imported:
                QMessageBox.information(
                    self, "Import Complete",
                    "Settings imported. No new channels.",
                )
            else:
                QMessageBox.information(self, "Import Complete", "No new channels imported.")

        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import: {e}")

    def closeEvent(self, event):  # noqa: N802
        """Handle window close event."""
        # First-time close prompt
        if not self.app.settings.close_to_tray_asked and self.app.tray_icon:
            msg = QMessageBox(self)
            msg.setWindowTitle("Close Application")
            msg.setText("What would you like to do when closing the window?")
            msg.setInformativeText("You can change this later in Preferences.")

            # Add "Don't ask again" checkbox (checked by default)
            dont_ask_cb = QCheckBox("Don't ask me again")
            dont_ask_cb.setChecked(True)
            msg.setCheckBox(dont_ask_cb)

            run_bg_btn = msg.addButton("Run in Background", QMessageBox.AcceptRole)
            msg.addButton("Quit", QMessageBox.RejectRole)
            msg.setDefaultButton(run_bg_btn)

            msg.exec()

            if msg.clickedButton() == run_bg_btn:
                self.app.settings.close_to_tray = True
            else:
                self.app.settings.close_to_tray = False

            # Only remember choice if checkbox is checked
            if dont_ask_cb.isChecked():
                self.app.settings.close_to_tray_asked = True

            self.app.save_settings()

        # Check if should run in background (unless force quit was requested)
        if self.app.settings.close_to_tray and self.app.tray_icon and not self._force_quit:
            # Save window geometry before minimizing
            self._save_window_geometry()
            event.ignore()
            # Minimize instead of hide to preserve window position on Wayland.
            # On Wayland, hidden windows lose their position state, but minimized
            # windows stay mapped and retain their geometry when restored.
            self.showMinimized()
        else:
            # Close the chat window if it's open
            if hasattr(self.app, "_chat_window") and self.app._chat_window:
                self.app._chat_window.close()
            # Save window geometry before quitting
            self._save_window_geometry()
            event.accept()

    def _save_window_geometry(self):
        """Save current window position and size to settings."""
        pos = self.pos()
        self.app.settings.window.width = self.width()
        self.app.settings.window.height = self.height()
        self.app.settings.window.x = pos.x()
        self.app.settings.window.y = pos.y()
        self.app.save_settings()

    def _quit_app(self):
        """Quit the application (bypasses minimize-to-tray)."""
        self._force_quit = True
        self.close()


class AboutDialog(QDialog):
    """About dialog with update check functionality."""

    GITHUB_REPO = "mkeguy106/livestream.list.qt"

    def __init__(self, parent, app: "Application"):
        super().__init__(parent)
        self.app = app

        self.setWindowTitle("About Livestream List (Qt)")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)

        # App icon and name
        title_label = QLabel("<h2>Livestream List (Qt)</h2>")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # Version
        version_label = QLabel(f"<p>Version {__version__}</p>")
        version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(version_label)

        # Description
        desc_label = QLabel(
            "<p>Monitor your favorite livestreams on<br>"
            "Twitch, YouTube, and Kick.</p>"
        )
        desc_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc_label)

        # License
        license_label = QLabel("<p>Licensed under GPL-2.0</p>")
        license_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(license_label)

        # GitHub link
        github_label = QLabel(
            f'<p><a href="https://github.com/{self.GITHUB_REPO}">GitHub Repository</a></p>'
        )
        github_label.setAlignment(Qt.AlignCenter)
        github_label.setOpenExternalLinks(True)
        layout.addWidget(github_label)

        layout.addSpacing(10)

        # Update status label (hidden initially)
        self.update_status = QLabel()
        self.update_status.setAlignment(Qt.AlignCenter)
        self.update_status.setWordWrap(True)
        self.update_status.hide()
        layout.addWidget(self.update_status)

        # Buttons
        button_layout = QHBoxLayout()

        self.check_updates_btn = QPushButton("Check for Updates")
        self.check_updates_btn.clicked.connect(self._check_for_updates)
        button_layout.addWidget(self.check_updates_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _check_for_updates(self):
        """Check GitHub for the latest release."""
        import json
        import urllib.request

        self.check_updates_btn.setEnabled(False)
        self.check_updates_btn.setText("Checking...")
        self.update_status.setText("Checking for updates...")
        self.update_status.setStyleSheet("")
        self.update_status.show()

        # Force UI update
        QApplication.processEvents()

        try:
            url = f"https://api.github.com/repos/{self.GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Livestream-List-Qt"}
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())

            latest_version = data.get("tag_name", "").lstrip("v")
            current_version = __version__

            if self._compare_versions(latest_version, current_version) > 0:
                self.update_status.setText(
                    f"<b>Update available!</b><br>"
                    f"Current: v{current_version}<br>"
                    f"Latest: v{latest_version}<br><br>"
                    f"<a href='{data.get('html_url', '')}'>Download from GitHub</a>"
                )
                self.update_status.setStyleSheet("color: #4CAF50;")
                self.update_status.setOpenExternalLinks(True)
            else:
                self.update_status.setText("You're running the latest version!")
                self.update_status.setStyleSheet("color: #2196F3;")

        except Exception as e:
            self.update_status.setText(f"Failed to check for updates:\n{str(e)}")
            self.update_status.setStyleSheet("color: #f44336;")

        self.check_updates_btn.setEnabled(True)
        self.check_updates_btn.setText("Check for Updates")

    def _compare_versions(self, v1: str, v2: str) -> int:
        """Compare two version strings. Returns >0 if v1 > v2, <0 if v1 < v2, 0 if equal."""
        def parse_version(v):
            parts = []
            for part in v.split("."):
                try:
                    parts.append(int(part))
                except ValueError:
                    parts.append(0)
            return parts

        p1 = parse_version(v1)
        p2 = parse_version(v2)

        # Pad to same length
        max_len = max(len(p1), len(p2))
        p1.extend([0] * (max_len - len(p1)))
        p2.extend([0] * (max_len - len(p2)))

        for a, b in zip(p1, p2):
            if a > b:
                return 1
            if a < b:
                return -1
        return 0


class AddChannelDialog(QDialog):
    """Dialog for adding channels manually or importing from Twitch."""

    def __init__(self, parent, app: "Application"):
        super().__init__(parent)
        self.app = app
        self._detected_platform = None
        self._detected_channel = None

        self.setWindowTitle("Add Channel")
        self.setMinimumWidth(450)
        self.setMinimumHeight(350)

        layout = QVBoxLayout(self)

        # Tab widget
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # Manual Add tab
        manual_tab = QWidget()
        manual_layout = QVBoxLayout(manual_tab)

        # Channel input
        form_layout = QFormLayout()

        self.channel_edit = QLineEdit()
        self.channel_edit.setPlaceholderText("Channel name or URL")
        self.channel_edit.textChanged.connect(self._on_text_changed)
        self.channel_edit.installEventFilter(self)
        self._has_auto_pasted = False
        form_layout.addRow("Channel:", self.channel_edit)

        self.platform_combo = QComboBox()
        self.platform_combo.addItem("Twitch", StreamPlatform.TWITCH)
        self.platform_combo.addItem("YouTube", StreamPlatform.YOUTUBE)
        self.platform_combo.addItem("Kick", StreamPlatform.KICK)
        form_layout.addRow("Platform:", self.platform_combo)

        manual_layout.addLayout(form_layout)

        # Detection hint
        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: gray; font-style: italic;")
        manual_layout.addWidget(self.hint_label)

        # Add button
        add_btn = QPushButton("Add Channel")
        add_btn.clicked.connect(self._on_add)
        manual_layout.addWidget(add_btn)

        manual_layout.addStretch()

        tabs.addTab(manual_tab, "Manual Add")

        # Import from Twitch tab
        twitch_tab = QWidget()
        twitch_layout = QVBoxLayout(twitch_tab)

        # Twitch status
        self.twitch_status_label = QLabel()
        twitch_layout.addWidget(self.twitch_status_label)

        # Twitch buttons
        twitch_btn_layout = QHBoxLayout()
        self.twitch_login_btn = QPushButton("Login to Twitch")
        self.twitch_login_btn.clicked.connect(self._on_twitch_login)
        twitch_btn_layout.addWidget(self.twitch_login_btn)

        self.twitch_import_btn = QPushButton("Import Follows")
        self.twitch_import_btn.clicked.connect(self._on_import_follows)
        twitch_btn_layout.addWidget(self.twitch_import_btn)

        self.twitch_logout_btn = QPushButton("Logout")
        self.twitch_logout_btn.clicked.connect(self._on_twitch_logout)
        twitch_btn_layout.addWidget(self.twitch_logout_btn)

        twitch_layout.addLayout(twitch_btn_layout)

        # Import info
        import_info = QLabel(
            "<p><b>Import your followed Twitch channels</b></p>"
            "<p>Login to your Twitch account and click Import Follows "
            "to add all channels you follow.</p>"
        )
        import_info.setWordWrap(True)
        twitch_layout.addWidget(import_info)

        twitch_layout.addStretch()

        # Note about other platforms
        note_label = QLabel(
            "<p style='color: gray;'><i>Note: YouTube and Kick channels must be added "
            "manually using the Manual Add tab. These platforms don't support "
            "importing followed channels.</i></p>"
        )
        note_label.setWordWrap(True)
        twitch_layout.addWidget(note_label)

        tabs.addTab(twitch_tab, "Import from Twitch")

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)

        # Update Twitch status
        self._update_twitch_status()

        # Auto-paste from clipboard on dialog open
        self._try_paste_clipboard()

    def eventFilter(self, obj, event):  # noqa: N802
        """Handle focus events for auto-paste."""
        from PySide6.QtCore import QEvent
        if obj == self.channel_edit and event.type() == QEvent.FocusIn:
            if not self._has_auto_pasted and not self.channel_edit.text():
                self._try_paste_clipboard()
        return super().eventFilter(obj, event)

    def _try_paste_clipboard(self):
        """Try to paste URL from clipboard."""
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text and self._looks_like_url(text):
            self.channel_edit.setText(text)
            self._has_auto_pasted = True

    def _looks_like_url(self, text: str) -> bool:
        """Check if text looks like a streaming URL."""
        patterns = ['twitch.tv', 'youtube.com', 'youtu.be', 'kick.com']
        return any(p in text.lower() for p in patterns)

    def _on_text_changed(self, text: str):
        """Handle channel text change."""
        result = self._parse_channel_url(text)
        if result:
            platform, channel = result
            self._detected_platform = platform
            self._detected_channel = channel

            # Update platform dropdown
            for i in range(self.platform_combo.count()):
                if self.platform_combo.itemData(i) == platform:
                    self.platform_combo.setCurrentIndex(i)
                    break

            self.hint_label.setText(f"Detected: {platform.value.title()} / {channel}")
        else:
            self._detected_platform = None
            self._detected_channel = None
            self.hint_label.setText("")

    def _parse_channel_url(self, text: str):
        """Parse a channel URL and return (platform, channel_id) or None."""
        text = text.strip()

        # Twitch
        twitch_patterns = [
            r'(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]+)',
        ]
        for pattern in twitch_patterns:
            match = re.match(pattern, text, re.IGNORECASE)
            if match:
                return (StreamPlatform.TWITCH, match.group(1))

        # YouTube
        youtube_patterns = [
            r'(?:https?://)?(?:www\.)?youtube\.com/@([a-zA-Z0-9_-]+)',
            r'(?:https?://)?(?:www\.)?youtube\.com/c/([a-zA-Z0-9_-]+)',
            r'(?:https?://)?(?:www\.)?youtube\.com/channel/([a-zA-Z0-9_-]+)',
            r'(?:https?://)?(?:www\.)?youtube\.com/user/([a-zA-Z0-9_-]+)',
            r'(?:https?://)?(?:www\.)?youtube\.com/([a-zA-Z0-9_-]+)',
        ]
        for pattern in youtube_patterns:
            match = re.match(pattern, text, re.IGNORECASE)
            if match:
                channel = match.group(1)
                if not channel.startswith('@') and not channel.startswith('UC'):
                    channel = '@' + channel
                return (StreamPlatform.YOUTUBE, channel)

        # Kick
        kick_patterns = [
            r'(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)',
        ]
        for pattern in kick_patterns:
            match = re.match(pattern, text, re.IGNORECASE)
            if match:
                return (StreamPlatform.KICK, match.group(1))

        return None

    def _on_add(self):
        """Handle add button click."""
        channel_id = self._detected_channel or self.channel_edit.text().strip()
        platform = self._detected_platform or self.platform_combo.currentData()

        if not channel_id:
            QMessageBox.warning(self, "Error", "Please enter a channel name or URL.")
            return

        # Add channel in background
        self.setEnabled(False)

        async def add_channel():
            return await self.app.monitor.add_channel(channel_id, platform)

        def on_complete(result):
            self.setEnabled(True)
            if result:
                self.accept()
            else:
                QMessageBox.warning(self, "Error", "Channel not found.")

        def on_error(error):
            self.setEnabled(True)
            QMessageBox.warning(self, "Error", f"Failed to add channel: {error}")

        from .app import AsyncWorker
        worker = AsyncWorker(add_channel, self.app.monitor, parent=self)
        worker.finished.connect(on_complete)
        worker.error.connect(on_error)
        worker.start()

    def _update_twitch_status(self):
        """Update Twitch login status display."""
        if self.app.settings.twitch.access_token:
            self.twitch_status_label.setText(
                "<p style='color: #4CAF50;'><b>Logged in to Twitch</b></p>"
            )
            self.twitch_login_btn.hide()
            self.twitch_import_btn.show()
            self.twitch_logout_btn.show()
        else:
            self.twitch_status_label.setText(
                "<p style='color: gray;'>Not logged in to Twitch</p>"
            )
            self.twitch_login_btn.show()
            self.twitch_import_btn.hide()
            self.twitch_logout_btn.hide()

    def _on_twitch_login(self):
        """Handle Twitch login."""
        dialog = ImportFollowsDialog(self, self.app, StreamPlatform.TWITCH)
        dialog.exec()
        self._update_twitch_status()

        # Reconnect chat with new token/scopes
        if self.app.settings.twitch.access_token and self.app.chat_manager:
            self.app.chat_manager.reconnect_twitch()

        # Suppress notifications for any channels imported during login
        added = getattr(dialog, '_added_count', 0)
        if added > 0:
            self.app.monitor._initial_load_complete = False

            def on_refresh_complete():
                self.app.monitor._initial_load_complete = True
                if self.app.main_window:
                    self.app.main_window.refresh_stream_list()

            if self.app.main_window:
                self.app.main_window.refresh_stream_list()

            self.app.refresh(on_complete=on_refresh_complete)

    def _on_import_follows(self):
        """Handle import follows."""
        dialog = ImportFollowsDialog(self, self.app, StreamPlatform.TWITCH, start_import=True)
        dialog.exec()

        # Suppress notifications during import refresh
        added = getattr(dialog, '_added_count', 0)
        if added > 0:
            self.app.monitor._initial_load_complete = False

            def on_refresh_complete():
                self.app.monitor._initial_load_complete = True
                if self.app.main_window:
                    self.app.main_window.refresh_stream_list()

            if self.app.main_window:
                self.app.main_window.refresh_stream_list()

            self.app.refresh(on_complete=on_refresh_complete)

    def _on_twitch_logout(self):
        """Handle Twitch logout."""
        self.app.settings.twitch.access_token = None
        self.app.settings.twitch.user_id = None
        self.app.save_settings()
        self._update_twitch_status()


class PreferencesDialog(QDialog):
    """Preferences dialog with multiple tabs."""

    def __init__(self, parent, app: "Application", initial_tab: int = 0):
        super().__init__(parent)
        self.app = app

        self.setWindowTitle("Preferences")
        self.setMinimumSize(500, 500)
        self.resize(550, 550)

        layout = QVBoxLayout(self)

        # Tab widget
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # General tab
        general_tab = self._create_general_tab()
        tabs.addTab(general_tab, "General")

        # Streamlink tab
        streamlink_tab = self._create_streamlink_tab()
        tabs.addTab(streamlink_tab, "Playback")

        # Chat tab
        chat_tab = self._create_chat_tab()
        tabs.addTab(chat_tab, "Chat")

        # Accounts tab
        accounts_tab = self._create_accounts_tab()
        tabs.addTab(accounts_tab, "Accounts")

        if initial_tab:
            tabs.setCurrentIndex(initial_tab)

        # Close button
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def _create_general_tab(self) -> QWidget:
        """Create the General settings tab."""

        # Create scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Content widget
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        # Startup group
        startup_group = QGroupBox("Startup")
        startup_layout = QFormLayout(startup_group)

        self.autostart_cb = QCheckBox("Launch on login")
        self.autostart_cb.setChecked(is_autostart_enabled())
        self.autostart_cb.stateChanged.connect(self._on_autostart_changed)
        startup_layout.addRow(self.autostart_cb)

        self.background_cb = QCheckBox("Run in background when closed")
        self.background_cb.setChecked(self.app.settings.close_to_tray)
        self.background_cb.stateChanged.connect(self._on_background_changed)
        startup_layout.addRow(self.background_cb)

        layout.addWidget(startup_group)

        # Refresh group
        refresh_group = QGroupBox("Refresh")
        refresh_layout = QFormLayout(refresh_group)

        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(10, 300)
        self.refresh_spin.setSuffix(" seconds")
        self.refresh_spin.setValue(self.app.settings.refresh_interval)
        self.refresh_spin.valueChanged.connect(self._on_refresh_changed)
        refresh_layout.addRow("Refresh interval:", self.refresh_spin)

        layout.addWidget(refresh_group)

        # Notifications group
        notif_group = QGroupBox("Notifications")
        notif_layout = QFormLayout(notif_group)

        self.notif_enabled_cb = QCheckBox("Enable notifications")
        self.notif_enabled_cb.setChecked(self.app.settings.notifications.enabled)
        self.notif_enabled_cb.stateChanged.connect(self._on_notif_changed)
        notif_layout.addRow(self.notif_enabled_cb)

        self.notif_sound_cb = QCheckBox("Play sound")
        self.notif_sound_cb.setChecked(self.app.settings.notifications.sound_enabled)
        self.notif_sound_cb.stateChanged.connect(self._on_notif_changed)
        notif_layout.addRow(self.notif_sound_cb)

        # Notification backend selector
        self.notif_backend_combo = QComboBox()
        self.notif_backend_combo.addItem("Auto", "auto")
        self.notif_backend_combo.addItem("D-Bus", "dbus")
        self.notif_backend_combo.addItem("notify-send", "notify-send")
        current_backend = self.app.settings.notifications.backend
        for i in range(self.notif_backend_combo.count()):
            if self.notif_backend_combo.itemData(i) == current_backend:
                self.notif_backend_combo.setCurrentIndex(i)
                break
        self.notif_backend_combo.currentIndexChanged.connect(self._on_notif_backend_changed)
        notif_layout.addRow("Backend:", self.notif_backend_combo)

        # Test notification button
        self.test_notif_btn = QPushButton("Test Notification")
        self.test_notif_btn.clicked.connect(self._on_test_notification)
        notif_layout.addRow(self.test_notif_btn)

        layout.addWidget(notif_group)

        # Appearance group
        appear_group = QGroupBox("Appearance")
        appear_layout = QFormLayout(appear_group)

        self.style_combo = QComboBox()
        for i, style in UI_STYLES.items():
            self.style_combo.addItem(style["name"], i)
        self.style_combo.setCurrentIndex(self.app.settings.ui_style)
        self.style_combo.currentIndexChanged.connect(self._on_style_changed)
        appear_layout.addRow("UI Style:", self.style_combo)

        self.platform_colors_cb = QCheckBox("Platform colors")
        self.platform_colors_cb.setChecked(self.app.settings.platform_colors)
        self.platform_colors_cb.stateChanged.connect(self._on_platform_colors_changed)
        appear_layout.addRow(self.platform_colors_cb)

        layout.addWidget(appear_group)

        # Channel Information group
        info_group = QGroupBox("Channel Information")
        info_layout = QFormLayout(info_group)

        self.show_duration_cb = QCheckBox("Show live duration")
        self.show_duration_cb.setChecked(self.app.settings.channel_info.show_live_duration)
        self.show_duration_cb.stateChanged.connect(self._on_channel_info_changed)
        info_layout.addRow(self.show_duration_cb)

        self.show_viewers_cb = QCheckBox("Show viewer count")
        self.show_viewers_cb.setChecked(self.app.settings.channel_info.show_viewers)
        self.show_viewers_cb.stateChanged.connect(self._on_channel_info_changed)
        info_layout.addRow(self.show_viewers_cb)

        layout.addWidget(info_group)

        # Channel Icons group
        icons_group = QGroupBox("Channel Icons")
        icons_layout = QFormLayout(icons_group)

        self.show_platform_cb = QCheckBox("Show platform icon")
        self.show_platform_cb.setChecked(self.app.settings.channel_icons.show_platform)
        self.show_platform_cb.stateChanged.connect(self._on_channel_icons_changed)
        icons_layout.addRow(self.show_platform_cb)

        self.show_play_cb = QCheckBox("Show play button")
        self.show_play_cb.setChecked(self.app.settings.channel_icons.show_play)
        self.show_play_cb.stateChanged.connect(self._on_channel_icons_changed)
        icons_layout.addRow(self.show_play_cb)

        self.show_favorite_cb = QCheckBox("Show favorite button")
        self.show_favorite_cb.setChecked(self.app.settings.channel_icons.show_favorite)
        self.show_favorite_cb.stateChanged.connect(self._on_channel_icons_changed)
        icons_layout.addRow(self.show_favorite_cb)

        self.show_chat_cb = QCheckBox("Show chat button")
        self.show_chat_cb.setChecked(self.app.settings.channel_icons.show_chat)
        self.show_chat_cb.stateChanged.connect(self._on_channel_icons_changed)
        icons_layout.addRow(self.show_chat_cb)

        self.show_browser_cb = QCheckBox("Show browser button")
        self.show_browser_cb.setChecked(self.app.settings.channel_icons.show_browser)
        self.show_browser_cb.stateChanged.connect(self._on_channel_icons_changed)
        icons_layout.addRow(self.show_browser_cb)

        layout.addWidget(icons_group)

        layout.addStretch()
        scroll.setWidget(widget)
        return scroll

    def _create_streamlink_tab(self) -> QWidget:
        """Create the Streamlink settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Streamlink group
        sl_group = QGroupBox("Streamlink")
        sl_layout = QFormLayout(sl_group)

        self.sl_path_edit = QLineEdit()
        self.sl_path_edit.setText(self.app.settings.streamlink.path)
        self.sl_path_edit.setPlaceholderText("streamlink")
        self.sl_path_edit.textChanged.connect(self._on_streamlink_changed)
        sl_layout.addRow("Path:", self.sl_path_edit)

        self.sl_args_edit = QLineEdit()
        self.sl_args_edit.setText(self.app.settings.streamlink.additional_args)
        self.sl_args_edit.setPlaceholderText("--twitch-low-latency")
        self.sl_args_edit.textChanged.connect(self._on_streamlink_changed)
        sl_layout.addRow("Arguments:", self.sl_args_edit)

        layout.addWidget(sl_group)

        # Player group
        player_group = QGroupBox("Player")
        player_layout = QFormLayout(player_group)

        self.player_path_edit = QLineEdit()
        self.player_path_edit.setText(self.app.settings.streamlink.player)
        self.player_path_edit.setPlaceholderText("mpv")
        self.player_path_edit.textChanged.connect(self._on_streamlink_changed)
        player_layout.addRow("Path:", self.player_path_edit)

        self.player_args_edit = QLineEdit()
        self.player_args_edit.setText(self.app.settings.streamlink.player_args)
        self.player_args_edit.setPlaceholderText("--fullscreen")
        self.player_args_edit.textChanged.connect(self._on_streamlink_changed)
        player_layout.addRow("Arguments:", self.player_args_edit)

        layout.addWidget(player_group)

        # Launch Method group (per-platform)
        launch_group = QGroupBox("Launch Method")
        launch_layout = QFormLayout(launch_group)

        # Twitch launch method
        self.twitch_launch_combo = QComboBox()
        self.twitch_launch_combo.addItem("Streamlink", "streamlink")
        self.twitch_launch_combo.addItem("yt-dlp (via player)", "yt-dlp")
        current_twitch = self.app.settings.streamlink.twitch_launch_method.value
        for i in range(self.twitch_launch_combo.count()):
            if self.twitch_launch_combo.itemData(i) == current_twitch:
                self.twitch_launch_combo.setCurrentIndex(i)
                break
        self.twitch_launch_combo.currentIndexChanged.connect(self._on_launch_method_changed)
        launch_layout.addRow("Twitch:", self.twitch_launch_combo)

        # YouTube launch method
        self.youtube_launch_combo = QComboBox()
        self.youtube_launch_combo.addItem("Streamlink", "streamlink")
        self.youtube_launch_combo.addItem("yt-dlp (via player)", "yt-dlp")
        current_youtube = self.app.settings.streamlink.youtube_launch_method.value
        for i in range(self.youtube_launch_combo.count()):
            if self.youtube_launch_combo.itemData(i) == current_youtube:
                self.youtube_launch_combo.setCurrentIndex(i)
                break
        self.youtube_launch_combo.currentIndexChanged.connect(self._on_launch_method_changed)
        launch_layout.addRow("YouTube:", self.youtube_launch_combo)

        # Kick launch method
        self.kick_launch_combo = QComboBox()
        self.kick_launch_combo.addItem("Streamlink", "streamlink")
        self.kick_launch_combo.addItem("yt-dlp (via player)", "yt-dlp")
        current_kick = self.app.settings.streamlink.kick_launch_method.value
        for i in range(self.kick_launch_combo.count()):
            if self.kick_launch_combo.itemData(i) == current_kick:
                self.kick_launch_combo.setCurrentIndex(i)
                break
        self.kick_launch_combo.currentIndexChanged.connect(self._on_launch_method_changed)
        launch_layout.addRow("Kick:", self.kick_launch_combo)

        layout.addWidget(launch_group)

        layout.addStretch()
        return widget

    def _create_chat_tab(self) -> QWidget:
        """Create the Chat settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Chat group
        chat_group = QGroupBox("Chat")
        chat_layout = QFormLayout(chat_group)

        self.chat_auto_cb = QCheckBox("Auto-open when launching stream")
        self.chat_auto_cb.setChecked(self.app.settings.chat.auto_open)
        self.chat_auto_cb.stateChanged.connect(self._on_chat_changed)
        chat_layout.addRow(self.chat_auto_cb)

        layout.addWidget(chat_group)

        # Chat Client group
        client_group = QGroupBox("Chat Client")
        client_layout = QFormLayout(client_group)

        # Chat client type dropdown
        self.chat_client_combo = QComboBox()
        self.chat_client_combo.addItem("Browser", "browser")
        self.chat_client_combo.addItem("Built-in", "builtin")

        current_mode = self.app.settings.chat.mode
        for i in range(self.chat_client_combo.count()):
            if self.chat_client_combo.itemData(i) == current_mode:
                self.chat_client_combo.setCurrentIndex(i)
                break

        self.chat_client_combo.currentIndexChanged.connect(self._on_chat_client_changed)
        client_layout.addRow("Client:", self.chat_client_combo)

        # Browser selection (shown when Browser client is selected)
        self.browser_combo = QComboBox()
        self.browser_combo.addItem("System Default", "default")
        self.browser_combo.addItem("Chrome", "chrome")
        self.browser_combo.addItem("Chromium", "chromium")
        self.browser_combo.addItem("Firefox", "firefox")
        self.browser_combo.addItem("Edge", "edge")

        current_browser = self.app.settings.chat.browser
        for i in range(self.browser_combo.count()):
            if self.browser_combo.itemData(i) == current_browser:
                self.browser_combo.setCurrentIndex(i)
                break

        self.browser_combo.currentIndexChanged.connect(self._on_chat_changed)
        self.browser_label = QLabel("Browser:")
        client_layout.addRow(self.browser_label, self.browser_combo)

        # Open in new window checkbox (for browser client)
        self.new_window_cb = QCheckBox("Open in new window")
        self.new_window_cb.setChecked(self.app.settings.chat.new_window)
        self.new_window_cb.stateChanged.connect(self._on_chat_changed)
        client_layout.addRow(self.new_window_cb)

        layout.addWidget(client_group)

        # Built-in chat settings group (shown when Built-in is selected)
        self.builtin_group = QGroupBox("Built-in Chat Settings")
        builtin_layout = QFormLayout(self.builtin_group)

        self.chat_font_spin = QSpinBox()
        self.chat_font_spin.setRange(4, 24)
        self.chat_font_spin.setValue(self.app.settings.chat.builtin.font_size)
        self.chat_font_spin.valueChanged.connect(self._on_chat_changed)
        builtin_layout.addRow("Font size:", self.chat_font_spin)

        self.chat_spacing_spin = QSpinBox()
        self.chat_spacing_spin.setRange(0, 12)
        self.chat_spacing_spin.setSuffix(" px")
        self.chat_spacing_spin.setValue(self.app.settings.chat.builtin.line_spacing)
        self.chat_spacing_spin.valueChanged.connect(self._on_chat_changed)
        builtin_layout.addRow("Line spacing:", self.chat_spacing_spin)

        self.chat_timestamps_cb = QCheckBox("Show timestamps")
        self.chat_timestamps_cb.setChecked(self.app.settings.chat.builtin.show_timestamps)
        self.chat_timestamps_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_timestamps_cb)

        self.chat_badges_cb = QCheckBox("Show badges")
        self.chat_badges_cb.setChecked(self.app.settings.chat.builtin.show_badges)
        self.chat_badges_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_badges_cb)

        self.chat_mod_badges_cb = QCheckBox("Show mod/VIP badges")
        self.chat_mod_badges_cb.setChecked(self.app.settings.chat.builtin.show_mod_badges)
        self.chat_mod_badges_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_mod_badges_cb)

        self.chat_emotes_cb = QCheckBox("Show emotes")
        self.chat_emotes_cb.setChecked(self.app.settings.chat.builtin.show_emotes)
        self.chat_emotes_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_emotes_cb)

        self.chat_alt_rows_cb = QCheckBox("Alternating row colors")
        self.chat_alt_rows_cb.setChecked(self.app.settings.chat.builtin.show_alternating_rows)
        self.chat_alt_rows_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_alt_rows_cb)

        # Emote provider checkboxes
        emote_providers = self.app.settings.chat.builtin.emote_providers
        self.emote_7tv_cb = QCheckBox("7TV")
        self.emote_7tv_cb.setChecked("7tv" in emote_providers)
        self.emote_7tv_cb.stateChanged.connect(self._on_chat_changed)
        self.emote_bttv_cb = QCheckBox("BTTV")
        self.emote_bttv_cb.setChecked("bttv" in emote_providers)
        self.emote_bttv_cb.stateChanged.connect(self._on_chat_changed)
        self.emote_ffz_cb = QCheckBox("FFZ")
        self.emote_ffz_cb.setChecked("ffz" in emote_providers)
        self.emote_ffz_cb.stateChanged.connect(self._on_chat_changed)

        emote_row = QHBoxLayout()
        emote_row.addWidget(self.emote_7tv_cb)
        emote_row.addWidget(self.emote_bttv_cb)
        emote_row.addWidget(self.emote_ffz_cb)
        emote_row.addStretch()
        builtin_layout.addRow("Emote providers:", emote_row)

        self.chat_name_colors_cb = QCheckBox("Use platform name colors")
        self.chat_name_colors_cb.setChecked(
            self.app.settings.chat.builtin.use_platform_name_colors
        )
        self.chat_name_colors_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_name_colors_cb)

        # Tab active color with swatch
        active_row = QHBoxLayout()
        self.tab_active_swatch = QPushButton()
        self.tab_active_swatch.setFixedSize(24, 24)
        self.tab_active_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        active_row.addWidget(self.tab_active_swatch)
        self.tab_active_color_edit = QLineEdit()
        self.tab_active_color_edit.setText(self.app.settings.chat.builtin.tab_active_color)
        self.tab_active_color_edit.setMaximumWidth(100)
        self.tab_active_color_edit.editingFinished.connect(self._on_chat_changed)
        self.tab_active_color_edit.textChanged.connect(
            lambda t: self._update_swatch(self.tab_active_swatch, t)
        )
        active_row.addWidget(self.tab_active_color_edit)
        active_row.addStretch()
        self.tab_active_swatch.clicked.connect(
            lambda: self._pick_color(self.tab_active_color_edit, self.tab_active_swatch)
        )
        self._update_swatch(self.tab_active_swatch, self.tab_active_color_edit.text())
        builtin_layout.addRow("Tab active color:", active_row)

        # Tab inactive color with swatch
        inactive_row = QHBoxLayout()
        self.tab_inactive_swatch = QPushButton()
        self.tab_inactive_swatch.setFixedSize(24, 24)
        self.tab_inactive_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        inactive_row.addWidget(self.tab_inactive_swatch)
        self.tab_inactive_color_edit = QLineEdit()
        self.tab_inactive_color_edit.setText(self.app.settings.chat.builtin.tab_inactive_color)
        self.tab_inactive_color_edit.setMaximumWidth(100)
        self.tab_inactive_color_edit.editingFinished.connect(self._on_chat_changed)
        self.tab_inactive_color_edit.textChanged.connect(
            lambda t: self._update_swatch(self.tab_inactive_swatch, t)
        )
        inactive_row.addWidget(self.tab_inactive_color_edit)
        inactive_row.addStretch()
        self.tab_inactive_swatch.clicked.connect(
            lambda: self._pick_color(self.tab_inactive_color_edit, self.tab_inactive_swatch)
        )
        self._update_swatch(self.tab_inactive_swatch, self.tab_inactive_color_edit.text())
        builtin_layout.addRow("Tab inactive color:", inactive_row)

        layout.addWidget(self.builtin_group)

        # Set initial visibility based on current mode
        show_browser = (current_mode == "browser")
        self.browser_label.setVisible(show_browser)
        self.browser_combo.setVisible(show_browser)
        self.new_window_cb.setVisible(show_browser)
        self.builtin_group.setVisible(not show_browser)

        layout.addStretch()
        return widget

    def _create_accounts_tab(self) -> QWidget:
        """Create the Accounts tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Twitch group
        twitch_group = QGroupBox("Twitch")
        twitch_layout = QVBoxLayout(twitch_group)

        self.twitch_status = QLabel()
        self._update_twitch_status()
        twitch_layout.addWidget(self.twitch_status)

        twitch_buttons = QHBoxLayout()

        self.twitch_login_btn = QPushButton("Login")
        self.twitch_login_btn.clicked.connect(self._on_twitch_login)
        twitch_buttons.addWidget(self.twitch_login_btn)

        self.twitch_import_btn = QPushButton("Import Follows")
        self.twitch_import_btn.clicked.connect(self._on_import_follows)
        twitch_buttons.addWidget(self.twitch_import_btn)

        self.twitch_logout_btn = QPushButton("Logout")
        self.twitch_logout_btn.setStyleSheet("color: red;")
        self.twitch_logout_btn.clicked.connect(self._on_twitch_logout)
        twitch_buttons.addWidget(self.twitch_logout_btn)

        twitch_buttons.addStretch()
        twitch_layout.addLayout(twitch_buttons)

        layout.addWidget(twitch_group)

        # YouTube group
        yt_group = QGroupBox("YouTube")
        yt_layout = QVBoxLayout(yt_group)
        yt_label = QLabel("No login required. Add channels manually.")
        yt_label.setStyleSheet("color: gray;")
        yt_layout.addWidget(yt_label)
        layout.addWidget(yt_group)

        # Kick group
        kick_group = QGroupBox("Kick")
        kick_layout = QVBoxLayout(kick_group)
        kick_label = QLabel("No login required. Add channels manually.")
        kick_label.setStyleSheet("color: gray;")
        kick_layout.addWidget(kick_label)
        layout.addWidget(kick_group)

        layout.addStretch()
        self._update_account_buttons()
        return widget

    def _update_twitch_status(self):
        """Update Twitch login status display."""
        if self.app.settings.twitch.access_token:
            self.twitch_status.setText("Status: Logged in")
            self.twitch_status.setStyleSheet("color: green;")
        else:
            self.twitch_status.setText("Status: Not logged in")
            self.twitch_status.setStyleSheet("color: gray;")

    def _update_account_buttons(self):
        """Update account button visibility based on login state."""
        is_logged_in = bool(self.app.settings.twitch.access_token)
        self.twitch_login_btn.setVisible(not is_logged_in)
        self.twitch_import_btn.setVisible(is_logged_in)
        self.twitch_logout_btn.setVisible(is_logged_in)

    def _on_autostart_changed(self, state):
        enabled = (state == Qt.CheckState.Checked.value)
        set_autostart(enabled)
        self.app.settings.autostart = enabled
        self.app.save_settings()

    def _on_background_changed(self, state):
        enabled = (state == Qt.CheckState.Checked.value)
        self.app.settings.close_to_tray = enabled
        self.app.save_settings()

    def _on_refresh_changed(self, value):
        self.app.update_refresh_interval(value)

    def _on_notif_changed(self):
        self.app.settings.notifications.enabled = self.notif_enabled_cb.isChecked()
        self.app.settings.notifications.sound_enabled = self.notif_sound_cb.isChecked()
        self.app.save_settings()

    def _on_style_changed(self, index):
        self.app.settings.ui_style = self.style_combo.currentData()
        self.app.save_settings()
        # Refresh the stream list to apply the new style
        if self.parent():
            self.parent().refresh_stream_list()

    def _on_notif_backend_changed(self, index):
        self.app.settings.notifications.backend = self.notif_backend_combo.currentData()
        self.app.save_settings()

    def _on_test_notification(self):
        """Send a test notification."""
        from ..core.models import Channel, Livestream, StreamPlatform

        # Create a fake livestream for testing
        test_channel = Channel(
            channel_id="test_channel",
            platform=StreamPlatform.TWITCH,
            display_name="Test Channel",
        )
        test_livestream = Livestream(
            channel=test_channel,
            live=True,
            title="Test Stream - Notification Preview",
            game="Testing",
            viewers=1234,
        )

        # Send test notification (bypasses enabled check, handles flatpak)
        if self.app.notification_bridge:
            self.app.notification_bridge.send_test_notification(test_livestream)

    def _on_platform_colors_changed(self, state):
        self.app.settings.platform_colors = self.platform_colors_cb.isChecked()
        self.app.save_settings()
        # Refresh main window to apply changes
        if self.parent():
            self.parent().refresh_stream_list()

    def _on_channel_info_changed(self, state):
        self.app.settings.channel_info.show_live_duration = self.show_duration_cb.isChecked()
        self.app.settings.channel_info.show_viewers = self.show_viewers_cb.isChecked()
        self.app.save_settings()
        # Refresh main window to apply changes
        if self.app.main_window:
            self.app.main_window.refresh_stream_list()

    def _on_channel_icons_changed(self, state):
        self.app.settings.channel_icons.show_platform = self.show_platform_cb.isChecked()
        self.app.settings.channel_icons.show_play = self.show_play_cb.isChecked()
        self.app.settings.channel_icons.show_favorite = self.show_favorite_cb.isChecked()
        self.app.settings.channel_icons.show_chat = self.show_chat_cb.isChecked()
        self.app.settings.channel_icons.show_browser = self.show_browser_cb.isChecked()
        self.app.save_settings()
        # Refresh main window to apply changes
        if self.app.main_window:
            self.app.main_window.refresh_stream_list()

    def _on_streamlink_changed(self):
        self.app.settings.streamlink.path = self.sl_path_edit.text()
        self.app.settings.streamlink.additional_args = self.sl_args_edit.text()
        self.app.settings.streamlink.player = self.player_path_edit.text()
        self.app.settings.streamlink.player_args = self.player_args_edit.text()
        self.app.save_settings()

    def _on_launch_method_changed(self):
        from ..core.models import LaunchMethod
        self.app.settings.streamlink.twitch_launch_method = LaunchMethod(
            self.twitch_launch_combo.currentData()
        )
        self.app.settings.streamlink.youtube_launch_method = LaunchMethod(
            self.youtube_launch_combo.currentData()
        )
        self.app.settings.streamlink.kick_launch_method = LaunchMethod(
            self.kick_launch_combo.currentData()
        )
        self.app.save_settings()

    def _on_chat_client_changed(self, index):
        """Handle chat client type change."""
        client_type = self.chat_client_combo.currentData()
        # Show browser options only when Browser client is selected
        show_browser = (client_type == "browser")
        self.browser_label.setVisible(show_browser)
        self.browser_combo.setVisible(show_browser)
        self.new_window_cb.setVisible(show_browser)
        self.builtin_group.setVisible(not show_browser)
        self._on_chat_changed()

    def _on_chat_changed(self):
        self.app.settings.chat.mode = self.chat_client_combo.currentData()
        self.app.settings.chat.auto_open = self.chat_auto_cb.isChecked()
        self.app.settings.chat.browser = self.browser_combo.currentData()
        self.app.settings.chat.new_window = self.new_window_cb.isChecked()
        # Built-in chat settings
        self.app.settings.chat.builtin.font_size = self.chat_font_spin.value()
        self.app.settings.chat.builtin.line_spacing = self.chat_spacing_spin.value()
        self.app.settings.chat.builtin.show_timestamps = self.chat_timestamps_cb.isChecked()
        self.app.settings.chat.builtin.show_badges = self.chat_badges_cb.isChecked()
        self.app.settings.chat.builtin.show_mod_badges = self.chat_mod_badges_cb.isChecked()
        self.app.settings.chat.builtin.show_emotes = self.chat_emotes_cb.isChecked()
        self.app.settings.chat.builtin.show_alternating_rows = self.chat_alt_rows_cb.isChecked()
        self.app.settings.chat.builtin.use_platform_name_colors = (
            self.chat_name_colors_cb.isChecked()
        )
        self.app.settings.chat.builtin.tab_active_color = (
            self.tab_active_color_edit.text().strip() or "#6441a5"
        )
        self.app.settings.chat.builtin.tab_inactive_color = (
            self.tab_inactive_color_edit.text().strip() or "#16213e"
        )
        providers = []
        if self.emote_7tv_cb.isChecked():
            providers.append("7tv")
        if self.emote_bttv_cb.isChecked():
            providers.append("bttv")
        if self.emote_ffz_cb.isChecked():
            providers.append("ffz")
        self.app.settings.chat.builtin.emote_providers = providers
        self.app.save_settings()
        # Live-update chat window tab style if open
        if self.app._chat_window:
            self.app._chat_window.update_tab_style()

    def _update_swatch(self, button: QPushButton, hex_color: str) -> None:
        """Update a color swatch button's background from a hex string."""
        color = QColor(hex_color)
        if color.isValid():
            button.setStyleSheet(
                f"background-color: {hex_color}; border: 1px solid #666; border-radius: 3px;"
            )
        else:
            button.setStyleSheet(
                "background-color: #333; border: 1px solid #666; border-radius: 3px;"
            )

    def _pick_color(self, line_edit: QLineEdit, swatch: QPushButton) -> None:
        """Open a color picker dialog and update the line edit and swatch."""
        current = QColor(line_edit.text().strip())
        if not current.isValid():
            current = QColor("#6441a5")
        color = QColorDialog.getColor(current, self, "Pick a color")
        if color.isValid():
            line_edit.setText(color.name())
            self._update_swatch(swatch, color.name())
            self._on_chat_changed()

    def _on_twitch_login(self):
        """Handle Twitch login."""
        dialog = ImportFollowsDialog(self, self.app, StreamPlatform.TWITCH)
        dialog.exec()
        self._update_twitch_status()
        self._update_account_buttons()

        # Reconnect chat with new token/scopes
        if self.app.settings.twitch.access_token and self.app.chat_manager:
            self.app.chat_manager.reconnect_twitch()

        # Suppress notifications for any channels imported during login
        added = getattr(dialog, '_added_count', 0)
        if added > 0:
            self.app.monitor._initial_load_complete = False

            def on_refresh_complete():
                self.app.monitor._initial_load_complete = True
                if self.app.main_window:
                    self.app.main_window.refresh_stream_list()

            if self.app.main_window:
                self.app.main_window.refresh_stream_list()

            self.app.refresh(on_complete=on_refresh_complete)

    def _on_twitch_logout(self):
        """Handle Twitch logout."""
        self.app.settings.twitch.access_token = None
        self.app.settings.twitch.user_id = None
        self.app.save_settings()
        self._update_twitch_status()
        self._update_account_buttons()

    def _on_import_follows(self):
        """Handle import follows."""
        dialog = ImportFollowsDialog(self, self.app, StreamPlatform.TWITCH, start_import=True)
        dialog.exec()

        # After dialog closes, refresh stream status with notifications suppressed
        added = getattr(dialog, '_added_count', 0)
        if added > 0:
            # Suppress notifications during import refresh
            self.app.monitor._initial_load_complete = False

            main_window = self.app.main_window

            def on_refresh_complete():
                # Re-enable notifications after refresh
                self.app.monitor._initial_load_complete = True
                if main_window:
                    main_window.refresh_stream_list()

            # Update UI immediately with new channels
            if main_window:
                main_window.refresh_stream_list()

            # Then check their live status
            self.app.refresh(on_complete=on_refresh_complete)


class ImportFollowsDialog(QDialog):
    """Dialog for OAuth login and importing followed channels."""

    # Signals for thread-safe UI updates
    login_complete = Signal()
    import_complete = Signal(list)

    def __init__(
        self, parent, app: "Application",
        platform: StreamPlatform, start_import: bool = False,
    ):
        super().__init__(parent)
        self.app = app
        self.platform = platform
        self._start_import = start_import
        self._added_count = 0  # Track imported channels

        self.setWindowTitle(f"Import {platform.value.title()} Follows")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Stack for different states
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        # Login page
        login_page = QWidget()
        login_layout = QVBoxLayout(login_page)
        login_layout.setAlignment(Qt.AlignCenter)

        login_label = QLabel(
            f"Log in to {platform.value.title()} to import "
            "your followed channels."
        )
        login_label.setAlignment(Qt.AlignCenter)
        login_layout.addWidget(login_label)

        login_btn = QPushButton(f"Login with {platform.value.title()}")
        login_btn.clicked.connect(self._start_login)
        login_layout.addWidget(login_btn, 0, Qt.AlignCenter)

        self.stack.addWidget(login_page)

        # Waiting page
        waiting_page = QWidget()
        waiting_layout = QVBoxLayout(waiting_page)
        waiting_layout.setAlignment(Qt.AlignCenter)

        waiting_label = QLabel(
            "Waiting for authorization...\n"
            "Please complete login in your browser."
        )
        waiting_label.setAlignment(Qt.AlignCenter)
        waiting_layout.addWidget(waiting_label)

        self.stack.addWidget(waiting_page)

        # Ready page
        ready_page = QWidget()
        ready_layout = QVBoxLayout(ready_page)
        ready_layout.setAlignment(Qt.AlignCenter)

        ready_label = QLabel(
            f"You're logged in to {platform.value.title()}!\n"
            "Ready to import your followed channels."
        )
        ready_label.setAlignment(Qt.AlignCenter)
        ready_layout.addWidget(ready_label)

        import_btn = QPushButton("Import Followed Channels")
        import_btn.clicked.connect(self._start_import_follows)
        ready_layout.addWidget(import_btn, 0, Qt.AlignCenter)

        self.stack.addWidget(ready_page)

        # Importing page
        importing_page = QWidget()
        importing_layout = QVBoxLayout(importing_page)
        importing_layout.setAlignment(Qt.AlignCenter)

        self.import_label = QLabel("Fetching followed channels...")
        self.import_label.setAlignment(Qt.AlignCenter)
        importing_layout.addWidget(self.import_label)

        self.import_progress = QProgressBar()
        self.import_progress.setMaximumWidth(300)
        importing_layout.addWidget(self.import_progress, 0, Qt.AlignCenter)

        self.import_detail = QLabel("")
        self.import_detail.setAlignment(Qt.AlignCenter)
        self.import_detail.setStyleSheet("color: gray;")
        importing_layout.addWidget(self.import_detail)

        self.stack.addWidget(importing_page)

        # Close button
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        layout.addWidget(self.close_btn, 0, Qt.AlignCenter)

        # Determine initial state
        if self.app.settings.twitch.access_token:
            if start_import:
                self.stack.setCurrentIndex(2)  # Ready
                QTimer.singleShot(100, self._start_import_follows)
            else:
                self.stack.setCurrentIndex(2)  # Ready
        else:
            self.stack.setCurrentIndex(0)  # Login

    def _start_login(self):
        """Start the OAuth login flow."""
        self.stack.setCurrentIndex(1)  # Waiting

        def login_thread():
            try:
                from ..api.twitch import TwitchApiClient
                client = TwitchApiClient(self.app.settings.twitch)

                async def do_login():
                    success = await client.oauth_login(timeout=120)
                    if success:
                        self.app.settings.save()
                    return success

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(do_login())
                    return result
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Login error: {e}")
                return False

        def on_complete():
            if self.app.settings.twitch.access_token:
                self.stack.setCurrentIndex(2)  # Ready
            else:
                self.stack.setCurrentIndex(0)  # Back to login
                QMessageBox.warning(self, "Login Failed", "Failed to log in. Please try again.")

        # Connect signal for thread-safe callback
        self.login_complete.connect(on_complete)

        def run_login():
            login_thread()
            # Emit signal to update UI on main thread
            self.login_complete.emit()

        import threading
        thread = threading.Thread(target=run_login)
        thread.daemon = True
        thread.start()

    def _start_import_follows(self):
        """Start importing followed channels."""
        self.stack.setCurrentIndex(3)  # Importing
        self.close_btn.setEnabled(False)

        def import_thread():
            try:
                from ..api.twitch import TwitchApiClient

                async def do_import():
                    client = TwitchApiClient(self.app.settings.twitch)
                    client._session = None

                    # Get followed channels (uses current authenticated user)
                    try:
                        channels = await client.get_followed_channels()
                    except PermissionError as e:
                        logger.error(f"Permission error: {e}")
                        return []

                    await client.close()
                    return channels

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    channels = loop.run_until_complete(do_import())
                    return channels
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Import error: {e}")
                return []

        def process_channels(channels):
            if not channels:
                self.import_label.setText("No channels found or error occurred.")
                self.close_btn.setEnabled(True)
                return

            self.import_progress.setRange(0, len(channels))

            added = 0
            for i, ch in enumerate(channels):
                # ch is a Channel object from get_followed_channels
                key = ch.unique_key
                if key not in self.app.monitor._channels:
                    self.app.monitor._channels[key] = ch
                    self.app.monitor._livestreams[key] = Livestream(channel=ch)
                    added += 1

                self.import_progress.setValue(i + 1)
                self.import_detail.setText(f"Added: {ch.display_name or ch.channel_id}")
                QApplication.processEvents()

            self.app.save_channels()
            self.import_label.setText(f"Import complete! Added {added} channels.")
            self.close_btn.setEnabled(True)
            self._added_count = added  # Store for later use

        # Connect signal for thread-safe callback
        self.import_complete.connect(process_channels)

        def run_import():
            channels = import_thread()
            self.import_complete.emit(channels)

        import threading
        thread = threading.Thread(target=run_import, daemon=True)
        thread.start()


class ExportDialog(QDialog):
    """Dialog for exporting channels and settings."""

    def __init__(self, parent, app: "Application"):
        super().__init__(parent)
        self.app = app

        self.setWindowTitle("Export")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)

        # Info
        channel_count = len(self.app.monitor.channels)
        info_label = QLabel(f"Export {channel_count} channels")
        layout.addWidget(info_label)

        # Options
        self.include_settings_cb = QCheckBox("Include settings")
        self.include_settings_cb.setChecked(True)
        layout.addWidget(self.include_settings_cb)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_export)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_export(self):
        """Handle export."""
        import json
        from datetime import datetime

        default_name = f"livestream-list-export-{datetime.now().strftime('%Y-%m-%d')}.json"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Channels",
            default_name,
            "JSON Files (*.json)"
        )

        if not file_path:
            return

        try:
            data = {
                "meta": {
                    "schema_version": 1,
                    "app_version": __version__,
                    "export_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
                "channels": []
            }

            for channel in self.app.monitor.channels:
                ch_data = {
                    "channel_id": channel.channel_id,
                    "platform": channel.platform.value,
                    "display_name": channel.display_name,
                    "favorite": channel.favorite,
                }
                data["channels"].append(ch_data)

            if self.include_settings_cb.isChecked():
                # Export all settings except sensitive auth tokens
                settings_dict = self.app.settings._to_dict()
                # Remove sensitive data
                if "twitch" in settings_dict:
                    settings_dict["twitch"] = {}  # Don't export tokens
                if "youtube" in settings_dict:
                    settings_dict["youtube"] = {}  # Don't export API key
                # Remove window geometry (machine-specific)
                if "window" in settings_dict:
                    del settings_dict["window"]
                data["settings"] = settings_dict

            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)

            self.accept()
            QMessageBox.information(self.parent(), "Export Complete", f"Exported to {file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {e}")
