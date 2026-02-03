"""Main window and UI components for the Qt application."""

import fnmatch
import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.chat import ChatLauncher
from ..core.models import Channel, Livestream, SortMode, StreamPlatform, UIStyle
from ..core.settings import ThemeMode
from .dialogs import (
    AboutDialog,
    AddChannelDialog,
    ExportDialog,
    PreferencesDialog,
)
from .theme import ThemeManager, get_app_stylesheet, get_theme

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
        "name": "Default",
        "margin_v": 4,
        "margin_h": 12,
        "spacing": 10,
        "icon_size": 16,
    },
    UIStyle.COMPACT_1: {
        "name": "Compact 1",
        "margin_v": 4,
        "margin_h": 12,
        "spacing": 8,
        "icon_size": 14,
    },
    UIStyle.COMPACT_2: {
        "name": "Compact 2",
        "margin_v": 2,
        "margin_h": 6,
        "spacing": 4,
        "icon_size": 12,
    },
    UIStyle.COMPACT_3: {
        "name": "Compact 3",
        "margin_v": 1,
        "margin_h": 4,
        "spacing": 2,
        "icon_size": 10,
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
        theme = get_theme()

        # Font scaling
        default_font_size = QApplication.font().pointSize()
        font_size = self._settings.font_size if self._settings.font_size > 0 else default_font_size
        self._font_size = font_size
        scale = font_size / default_font_size if default_font_size > 0 else 1.0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            style["margin_h"], style["margin_v"], style["margin_h"], style["margin_v"]
        )
        layout.setSpacing(style["spacing"])

        # Selection checkbox (hidden by default)
        self.checkbox = QCheckBox()
        self.checkbox.setVisible(False)
        layout.addWidget(self.checkbox)

        # Live indicator (emoji)
        self.live_indicator = QLabel()
        self.live_indicator.setFixedWidth(20)
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
        self.name_label.setStyleSheet(f"font-weight: bold; font-size: {font_size}pt;")
        self.name_label.setMinimumWidth(80)  # Ensure channel name stays visible
        name_row.addWidget(self.name_label)

        self.duration_label = QLabel()
        self.duration_label.setStyleSheet(
            f"color: {theme.text_muted}; font-size: {font_size}pt;"
        )
        name_row.addWidget(self.duration_label)

        self.playing_label = QLabel()
        self.playing_label.setStyleSheet(
            f"color: {theme.status_live}; font-weight: bold; font-size: {font_size}pt;"
        )
        self.playing_label.setVisible(False)
        name_row.addWidget(self.playing_label)

        name_row.addStretch()

        self.viewers_label = QLabel()
        self.viewers_label.setStyleSheet(f"color: {theme.text_muted}; font-size: {font_size}pt;")
        name_row.addWidget(self.viewers_label)

        info_layout.addLayout(name_row)

        # Title row (only in default style)
        if self._settings.ui_style == UIStyle.DEFAULT:
            from PySide6.QtWidgets import QSizePolicy

            title_size = max(8, int(font_size * 0.85))
            self.title_label = QLabel()
            self.title_label.setStyleSheet(f"color: {theme.text_muted}; font-size: {title_size}pt;")
            self.title_label.setWordWrap(False)
            # Allow title to shrink and hide when window is small
            self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self.title_label.setMinimumWidth(0)
            info_layout.addWidget(self.title_label)
        else:
            self.title_label = None

        layout.addLayout(info_layout, 1)

        # Buttons
        icon_size = int(style["icon_size"] * scale)

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

        # Live indicator (emoji)
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
        fs = self._font_size
        if self._settings.platform_colors:
            color = PLATFORM_COLORS.get(channel.platform, "#888888")
            self.platform_label.setStyleSheet(
                f"color: {color}; font-weight: bold; font-size: {fs}pt;"
            )
        else:
            self.platform_label.setStyleSheet(f"font-weight: bold; font-size: {fs}pt;")

        # Channel name
        self.name_label.setText(channel.display_name or channel.channel_id)
        if self._settings.platform_colors:
            color = PLATFORM_COLORS.get(channel.platform, "#888888")
            self.name_label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: {fs}pt;")
        else:
            self.name_label.setStyleSheet(f"font-weight: bold; font-size: {fs}pt;")

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
            theme = get_theme()
            if is_playing:
                self.play_btn.setText("■")
                self.play_btn.setToolTip("Stop playback")
                self.play_btn.setStyleSheet(f"color: {theme.status_error};")
            else:
                self.play_btn.setText("▶")
                self.play_btn.setToolTip("Play stream")
                self.play_btn.setStyleSheet("")

    def apply_theme(self) -> None:
        """Apply current theme colors without rebuilding the widget.

        This is much faster than destroying and recreating the row
        since it only updates the stylesheet colors.
        """
        theme = get_theme()
        fs = self._font_size

        # Update duration/last seen label
        self.duration_label.setStyleSheet(f"color: {theme.text_muted}; font-size: {fs}pt;")

        # Update playing indicator
        self.playing_label.setStyleSheet(
            f"color: {theme.status_live}; font-weight: bold; font-size: {fs}pt;"
        )

        # Update viewers label
        self.viewers_label.setStyleSheet(f"color: {theme.text_muted}; font-size: {fs}pt;")

        # Update title label if present
        if self.title_label:
            title_size = max(8, int(fs * 0.85))
            self.title_label.setStyleSheet(f"color: {theme.text_muted}; font-size: {title_size}pt;")

        # Update play button color if playing
        if self.play_btn and self._is_playing:
            self.play_btn.setStyleSheet(f"color: {theme.status_error};")

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
        video_id = getattr(self.livestream, "video_id", None) or ""
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
        self._stream_keys_order: list[str] = []  # Track order for fast path check
        self._selection_mode = False
        self._initial_check_complete = False
        self._name_filter = ""
        self._platform_filter: StreamPlatform | None = None
        self._chat_launcher = ChatLauncher(app.settings.chat)
        self._force_quit = False  # When True, closeEvent quits instead of minimizing

        # Debounce mechanism for refresh_stream_list to prevent rapid successive calls
        self._refresh_pending = False
        self._refresh_in_progress = False
        self._refresh_debounce_timer = QTimer(self)
        self._refresh_debounce_timer.setSingleShot(True)
        self._refresh_debounce_timer.setInterval(100)  # 100ms debounce
        self._refresh_debounce_timer.timeout.connect(self._do_refresh_stream_list)

        self._setup_ui()
        self._setup_shortcuts()
        self._connect_signals()
        self._apply_settings()

    def _setup_ui(self):
        """Set up the main window UI."""
        self.setWindowTitle("Livestream List (Qt)")
        self.resize(self.app.settings.window.width, self.app.settings.window.height)
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
        self.stream_list.viewport().installEventFilter(self)
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

        # Theme toggle button
        self.theme_btn = QToolButton()
        self._update_theme_button()
        self.theme_btn.clicked.connect(self._on_theme_toggle)
        toolbar.addWidget(self.theme_btn)

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
        # Apply theme
        self._apply_theme()

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
        """Refresh the stream list display (rate-limited to prevent rapid successive calls)."""
        if self._refresh_in_progress:
            # A refresh is already running, queue another one for when it's done
            self._refresh_pending = True
            return
        if self._refresh_debounce_timer.isActive():
            # Recently refreshed, schedule another after cooldown
            self._refresh_pending = True
            return
        # Do immediate update
        self._refresh_in_progress = True
        try:
            self._update_view()
        finally:
            self._refresh_in_progress = False
            # Start cooldown timer to prevent rapid successive refreshes
            self._refresh_debounce_timer.start()

    def _do_refresh_stream_list(self):
        """Actually perform the refresh (called after debounce delay)."""
        if not self._refresh_pending:
            return
        self._refresh_pending = False
        self._refresh_in_progress = True
        try:
            self._update_view()
        finally:
            self._refresh_in_progress = False
            # If another refresh was requested while we were running, schedule it
            if self._refresh_pending:
                self._refresh_debounce_timer.start()

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
                    if hasattr(self._platform_filter, "value")
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
                        ls.start_time
                        if ls.start_time.tzinfo
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
        """Populate the list widget with stream rows.

        Uses incremental updates when possible to avoid rebuilding 343+ widgets.
        """
        streamlink = self.app.streamlink
        new_keys = [ls.channel.unique_key for ls in livestreams]

        # Check if we can do incremental update (same streams in same order)
        if new_keys == self._stream_keys_order:
            # Fast path: just update existing rows in-place (order unchanged)
            live_count = sum(1 for ls in livestreams if ls.live)
            logger.info(f"Fast path update: {len(livestreams)} streams, {live_count} live")
            for ls in livestreams:
                key = ls.channel.unique_key
                row = self._stream_rows.get(key)
                if row:
                    is_playing = streamlink.is_playing(key) if streamlink else False
                    row.update(ls, is_playing)
            # Force QListWidget to repaint all visible items
            self.stream_list.viewport().update()
            return

        # Slow path: rebuild the list (different streams or first time)
        live_count = sum(1 for ls in livestreams if ls.live)
        logger.info(f"Slow path rebuild: {len(livestreams)} streams, {live_count} live")

        # Disable updates during rebuild to prevent intermediate repaints
        self.stream_list.setUpdatesEnabled(False)
        try:
            self.stream_list.clear()
            self._stream_rows.clear()
            self._stream_keys_order = []

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
                self._stream_keys_order.append(key)
        finally:
            self.stream_list.setUpdatesEnabled(True)

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

    def _update_theme_button(self):
        """Update the theme button text and tooltip based on current mode."""
        mode = self.app.settings.theme_mode
        if mode == ThemeMode.AUTO:
            self.theme_btn.setText("◐")  # Half circle for auto
            self.theme_btn.setToolTip("Theme: Auto (click to switch to Light)")
        elif mode == ThemeMode.LIGHT:
            self.theme_btn.setText("☀")  # Sun for light
            self.theme_btn.setToolTip("Theme: Light (click to switch to Dark)")
        else:  # DARK
            self.theme_btn.setText("☾")  # Moon for dark
            self.theme_btn.setToolTip("Theme: Dark (click to switch to Auto)")

    def _on_theme_toggle(self):
        """Cycle through theme modes: Auto -> Light -> Dark -> Auto."""
        mode = self.app.settings.theme_mode
        # Remember current visual state before changing
        was_dark = ThemeManager.is_dark_mode()

        if mode == ThemeMode.AUTO:
            new_mode = ThemeMode.LIGHT
        elif mode == ThemeMode.LIGHT:
            new_mode = ThemeMode.DARK
        else:
            new_mode = ThemeMode.AUTO

        self.app.settings.theme_mode = new_mode
        self.app.save_settings()
        ThemeManager.set_settings(self.app.settings)
        ThemeManager.invalidate_cache()
        self._update_theme_button()

        # Only apply theme if the visual appearance actually changed
        is_dark = ThemeManager.is_dark_mode()
        if was_dark != is_dark:
            self._apply_theme()

    def _apply_theme(self):
        """Apply the current theme to all windows."""
        # Disable updates during theme change to prevent cascading repaints
        self.setUpdatesEnabled(False)
        try:
            theme = get_theme()
            # Apply global app stylesheet (affects all dialogs)
            self.app.setStyleSheet(get_app_stylesheet())
            # Apply comprehensive stylesheet to main window
            self.setStyleSheet(f"""
                QMainWindow {{
                    background-color: {theme.window_bg};
                    color: {theme.text_primary};
                }}
                QWidget {{
                    background-color: {theme.window_bg};
                    color: {theme.text_primary};
                }}
                QToolBar {{
                    background-color: {theme.toolbar_bg};
                    border: none;
                }}
                QToolButton {{
                    color: {theme.text_primary};
                }}
                QToolButton:checked {{
                    background-color: {theme.accent};
                    color: {theme.selection_text};
                }}
                QListWidget {{
                    background-color: {theme.widget_bg};
                    border: none;
                }}
                QListWidget::item {{
                    background-color: {theme.widget_bg};
                }}
                QListWidget::item:selected {{
                    background-color: {theme.selection_bg};
                }}
                QLabel {{
                    background-color: transparent;
                }}
                QCheckBox {{
                    color: {theme.text_primary};
                }}
                QComboBox {{
                    background-color: {theme.input_bg};
                    color: {theme.text_primary};
                    border: 1px solid {theme.border};
                    padding: 4px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: {theme.popup_bg};
                    color: {theme.text_primary};
                    selection-background-color: {theme.selection_bg};
                }}
                QLineEdit {{
                    background-color: {theme.input_bg};
                    color: {theme.text_primary};
                    border: 1px solid {theme.border};
                    padding: 4px;
                }}
                QPushButton {{
                    background-color: {theme.input_bg};
                    color: {theme.text_primary};
                    border: 1px solid {theme.border};
                }}
                QPushButton:hover {{
                    background-color: {theme.accent_hover};
                }}
                QStatusBar {{
                    background-color: {theme.toolbar_bg};
                    color: {theme.text_secondary};
                }}
                QProgressBar {{
                    background-color: {theme.input_bg};
                    border: 1px solid {theme.border};
                }}
                QProgressBar::chunk {{
                    background-color: {theme.accent};
                }}
            """)
            # Update loading detail label color
            if hasattr(self, "loading_detail"):
                self.loading_detail.setStyleSheet(f"color: {theme.text_muted};")
            if hasattr(self, "all_offline_label"):
                self.all_offline_label.setStyleSheet(f"color: {theme.text_muted};")
            # Update existing StreamRows in-place instead of rebuilding
            # This is much faster than refresh_stream_list() which destroys/recreates all rows
            for row in self._stream_rows.values():
                row.apply_theme()
            # Notify chat window if open
            if hasattr(self.app, "_chat_window") and self.app._chat_window:
                self.app._chat_window.apply_theme()
        finally:
            self.setUpdatesEnabled(True)

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
                if (
                    self.app.settings.chat.auto_open
                    and self.app.settings.chat.enabled
                    and self.app.settings.chat.mode == "browser"
                ):
                    ch = livestream.channel
                    video_id = getattr(livestream, "video_id", None) or ""
                    self._chat_launcher.open_chat(ch.channel_id, ch.platform.value, video_id)
            except Exception as e:
                logger.error(f"Launch error: {e}")

        thread = threading.Thread(target=launch, daemon=True)
        thread.start()

        # Auto-open built-in chat on main thread (if enabled)
        if (
            self.app.settings.chat.auto_open
            and self.app.settings.chat.enabled
            and self.app.settings.chat.mode == "builtin"
            and self.app.chat_manager
        ):
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

    def _find_livestream(self, channel_id: str, platform: str) -> Livestream | None:
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
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.app.monitor.remove_channels(selected_keys)
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
            self, "Import Channels", "", "JSON Files (*.json);;All Files (*)"
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

            channels_data = data.get("channels", [])
            imported = 0

            for ch_data in channels_data:
                platform = StreamPlatform(ch_data["platform"])
                channel = Channel(
                    channel_id=ch_data["channel_id"],
                    platform=platform,
                    display_name=ch_data.get("display_name"),
                    favorite=ch_data.get("favorite", False),
                )

                if self.app.monitor.add_channel_direct(channel):
                    imported += 1

            # Import settings if present
            settings_imported = False
            if "settings" in data:
                imported_settings = data["settings"]
                # Preserve current auth tokens and window geometry
                current = self.app.settings._to_dict()
                imported_settings["twitch"] = current.get("twitch", {})
                imported_settings["youtube"] = current.get("youtube", {})
                imported_settings["kick"] = current.get("kick", {})
                imported_settings["window"] = current.get("window", {})
                # Also preserve close_to_tray_asked state
                imported_settings["close_to_tray_asked"] = self.app.settings.close_to_tray_asked
                # Apply imported settings
                new_settings = Settings._from_dict(imported_settings)
                # Copy all fields to current settings
                for field_name in [
                    "refresh_interval",
                    "minimize_to_tray",
                    "start_minimized",
                    "check_for_updates",
                    "autostart",
                    "close_to_tray",
                    "sort_mode",
                    "hide_offline",
                    "favorites_only",
                    "ui_style",
                    "platform_colors",
                    "font_size",
                    "theme_mode",
                    "streamlink",
                    "notifications",
                    "chat",
                    "channel_info",
                    "channel_icons",
                    "performance",
                ]:
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
                    self,
                    "Import Complete",
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

    def eventFilter(self, obj, event):  # noqa: N802
        """Handle Ctrl+Wheel on stream list for font scaling."""
        from PySide6.QtCore import QEvent

        if obj == self.stream_list.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                default_size = QApplication.font().pointSize()
                current = self.app.settings.font_size
                if current == 0:
                    current = default_size
                delta = 1 if event.angleDelta().y() > 0 else -1
                new_size = max(6, min(30, current + delta))
                self.app.settings.font_size = new_size
                self.app.save_settings()
                self.refresh_stream_list()
                return True
        return super().eventFilter(obj, event)

    def _quit_app(self):
        """Quit the application (bypasses minimize-to-tray)."""
        self._force_quit = True
        self.close()
