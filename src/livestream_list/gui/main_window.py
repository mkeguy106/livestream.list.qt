"""Main window and UI components for the Qt application."""

import fnmatch
import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
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
from ..core.models import Channel, Livestream, SortMode, StreamPlatform
from ..core.settings import ThemeMode
from .dialogs import (
    AboutDialog,
    AddChannelDialog,
    ExportDialog,
    PreferencesDialog,
)
from .stream_list import StreamListModel, StreamRole, StreamRowDelegate
from .theme import ThemeManager, get_app_stylesheet, get_theme

if TYPE_CHECKING:
    from .app import Application

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, app: "Application"):
        super().__init__()
        self.app = app
        self._stream_model: StreamListModel | None = None
        self._stream_delegate: StreamRowDelegate | None = None
        self._stream_keys_order: list[str] = []  # Track order for fast path check
        self._initial_check_complete = False
        self._name_filter = ""
        self._platform_filter: StreamPlatform | None = None
        self._chat_launcher = ChatLauncher(app.settings.chat)
        self._force_quit = False  # When True, closeEvent quits instead of minimizing
        self._last_clicked_index = None  # Anchor for shift+click range selection

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
        self.setMinimumWidth(460)
        self.resize(self.app.settings.window.width, self.app.settings.window.height)
        # Restore window position if saved (validate against current screens)
        if self.app.settings.window.x is not None and self.app.settings.window.y is not None:
            from PySide6.QtGui import QGuiApplication
            target_pos = (self.app.settings.window.x, self.app.settings.window.y)
            for screen in QGuiApplication.screens():
                geom = screen.availableGeometry()
                if geom.contains(target_pos[0] + 50, target_pos[1] + 50):
                    self.move(target_pos[0], target_pos[1])
                    break

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Menu bar
        self._create_menu_bar()

        # Toolbar (includes all buttons, filters, and sort)
        self._create_toolbar()

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
        loading_layout.addWidget(self.loading_detail)

        self.stack.addWidget(loading_page)  # Index 0

        # Empty page
        empty_page = QWidget()
        empty_layout = QVBoxLayout(empty_page)
        empty_layout.setAlignment(Qt.AlignCenter)
        self.empty_label = QLabel("No channels added yet.\nClick the + button to add a channel.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(self.empty_label)
        self.stack.addWidget(empty_page)  # Index 1

        # All offline page
        all_offline_page = QWidget()
        all_offline_layout = QVBoxLayout(all_offline_page)
        all_offline_layout.setAlignment(Qt.AlignCenter)
        self.all_offline_label = QLabel("All channels are offline")
        self.all_offline_label.setAlignment(Qt.AlignCenter)
        all_offline_layout.addWidget(self.all_offline_label)
        self.stack.addWidget(all_offline_page)  # Index 2

        # Stream list page (virtualized QListView)
        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)

        # Create model and delegate
        self._stream_model = StreamListModel(parent=self)
        self._stream_delegate = StreamRowDelegate(self.app.settings, parent=self)

        # Connect delegate signals
        self._stream_delegate.play_clicked.connect(self._on_play_stream)
        self._stream_delegate.stop_clicked.connect(self._on_stop_stream)
        self._stream_delegate.favorite_clicked.connect(self._on_toggle_favorite)
        self._stream_delegate.chat_clicked.connect(self._on_open_chat)
        self._stream_delegate.browser_clicked.connect(self._on_open_browser)

        # Create virtualized list view
        self.stream_list = QListView()
        self.stream_list.setModel(self._stream_model)
        self.stream_list.setItemDelegate(self._stream_delegate)
        self.stream_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.stream_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stream_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.stream_list.setUniformItemSizes(False)
        self.stream_list.setSpacing(0)

        # Connect signals
        self.stream_list.doubleClicked.connect(self._on_item_double_clicked)
        self.stream_list.viewport().installEventFilter(self)

        # Clear button rects on scroll to prevent stale click detection
        self.stream_list.verticalScrollBar().valueChanged.connect(
            lambda: self._stream_delegate.clear_button_rects()
        )

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

        self.delete_selected_btn = QPushButton("Delete Selected")
        self.delete_selected_btn.clicked.connect(self._delete_selected)
        selection_layout.addWidget(self.delete_selected_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._exit_selection_mode)
        selection_layout.addWidget(cancel_btn)

        self.selection_bar.setVisible(False)
        layout.addWidget(self.selection_bar)

        # Status bar
        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)

        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label, 1)

        self.live_count_label = QLabel("")
        self.live_count_label.setContentsMargins(0, 0, 6, 0)
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

        # Hide offline toggle
        self.hide_offline_btn = QToolButton()
        self.hide_offline_btn.setText("\u25c9")  # ◉ fisheye - "live only"
        self.hide_offline_btn.setToolTip("Hide offline channels")
        self.hide_offline_btn.setCheckable(True)
        self.hide_offline_btn.setChecked(self.app.settings.hide_offline)
        self.hide_offline_btn.setProperty("filterToggle", True)
        self.hide_offline_btn.clicked.connect(self._on_filter_changed)
        toolbar.addWidget(self.hide_offline_btn)

        # Favorites toggle
        self.favorites_btn = QToolButton()
        self.favorites_btn.setText("\u2605")  # ★ black star
        self.favorites_btn.setToolTip("Show favorites only")
        self.favorites_btn.setCheckable(True)
        self.favorites_btn.setChecked(self.app.settings.favorites_only)
        self.favorites_btn.setProperty("filterToggle", True)
        self.favorites_btn.clicked.connect(self._on_filter_changed)
        toolbar.addWidget(self.favorites_btn)

        toolbar.addSeparator()

        # Selection mode button
        self.select_btn = QToolButton()
        self.select_btn.setText("☑")
        self.select_btn.setToolTip("Selection mode (Escape to exit, Shift+click for range)")
        self.select_btn.setCheckable(True)
        self.select_btn.clicked.connect(self._toggle_selection_mode)
        toolbar.addWidget(self.select_btn)

        # Trash bin button
        self.trash_btn = QToolButton()
        self.trash_btn.setText("\u2672")
        self.trash_btn.setToolTip("Trash bin (deleted channels)")
        self.trash_btn.clicked.connect(self._show_trash_dialog)
        toolbar.addWidget(self.trash_btn)

        toolbar.addSeparator()

        # Name filter
        self.name_filter_edit = QLineEdit()
        self.name_filter_edit.setPlaceholderText("Filter by name...")
        self.name_filter_edit.setAccessibleName("Filter channels by name")
        self.name_filter_edit.setMaximumWidth(200)
        self.name_filter_edit.textChanged.connect(self._on_name_filter_changed)
        toolbar.addWidget(self.name_filter_edit)

        # Platform filter
        self.platform_combo = QComboBox()
        self.platform_combo.setAccessibleName("Filter by platform")
        self.platform_combo.addItem("All", None)
        self.platform_combo.addItem("Twitch", StreamPlatform.TWITCH)
        self.platform_combo.addItem("YouTube", StreamPlatform.YOUTUBE)
        self.platform_combo.addItem("Kick", StreamPlatform.KICK)
        self.platform_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.platform_combo)

        # Sort dropdown
        self.sort_combo = QComboBox()
        self.sort_combo.setAccessibleName("Sort channels by")
        self.sort_combo.addItem("Name", SortMode.NAME)
        self.sort_combo.addItem("Viewers", SortMode.VIEWERS)
        self.sort_combo.addItem("Playing", SortMode.PLAYING)
        self.sort_combo.addItem("Last Seen", SortMode.LAST_SEEN)
        self.sort_combo.addItem("Time Live", SortMode.TIME_LIVE)
        self.sort_combo.setCurrentIndex(self.app.settings.sort_mode.value)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        toolbar.addWidget(self.sort_combo)

    def _setup_shortcuts(self):
        """Set up keyboard shortcuts."""
        QShortcut(QKeySequence("F5"), self, self._on_refresh)
        QShortcut(QKeySequence("Escape"), self, self._on_escape)
        QShortcut(QKeySequence("Delete"), self, self._on_delete_key)
        QShortcut(QKeySequence("Ctrl+A"), self, self._on_ctrl_a)

    def _connect_signals(self):
        """Connect application signals."""
        self.app.stream_online.connect(self._on_stream_online)
        self.app.refresh_complete.connect(self._on_refresh_complete)
        self.app.refresh_error.connect(self._on_refresh_error)

    def _apply_settings(self):
        """Apply current settings to the UI."""
        self.hide_offline_btn.setChecked(self.app.settings.hide_offline)
        self.favorites_btn.setChecked(self.app.settings.favorites_only)
        self.sort_combo.setCurrentIndex(self.app.settings.sort_mode.value)
        # Invalidate delegate cache in case layout settings changed
        if self._stream_delegate:
            self._stream_delegate.invalidate_size_cache()
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
        import time

        t0 = time.perf_counter()
        monitor = self.app.monitor
        if not monitor:
            self.stack.setCurrentIndex(0)  # Loading
            return

        channels = monitor.channels
        if not channels:
            self.stack.setCurrentIndex(1)  # Empty
            return

        # Get filtered and sorted livestreams
        t1 = time.perf_counter()
        livestreams = self._get_filtered_sorted_livestreams()
        t2 = time.perf_counter()
        if t2 - t1 > 0.05:
            logger.warning(f"_get_filtered_sorted_livestreams took {(t2 - t1) * 1000:.1f}ms")

        if not livestreams:
            # Check why empty
            if self.hide_offline_btn.isChecked():
                if not self._initial_check_complete:
                    self.all_offline_label.setText("Checking stream status...")
                else:
                    self.all_offline_label.setText("All channels are offline")
            elif self.favorites_btn.isChecked():
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
        t3 = time.perf_counter()
        self._populate_list(livestreams)
        t4 = time.perf_counter()
        self._update_live_count()
        t5 = time.perf_counter()
        total = t5 - t0
        if total > 0.1:
            logger.warning(
                f"_update_view took {total * 1000:.1f}ms "
                f"(filter/sort: {(t2 - t1) * 1000:.1f}ms, populate: {(t4 - t3) * 1000:.1f}ms)"
            )

    def _get_filtered_sorted_livestreams(self) -> list[Livestream]:
        """Get filtered and sorted list of livestreams."""
        monitor = self.app.monitor
        if not monitor:
            return []

        livestreams = monitor.livestreams

        # Apply filters
        hide_offline = self.hide_offline_btn.isChecked()
        favorites_only = self.favorites_btn.isChecked()

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

        # Pre-compute playing keys once (avoid calling is_playing 343*log(343) times during sort)
        playing_keys = set(streamlink.get_playing_streams()) if streamlink else set()

        def sort_key(ls: Livestream):
            live = 0 if ls.live else 1
            is_playing = 0 if ls.channel.unique_key in playing_keys else 1

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
        """Populate the virtualized list view with stream data.

        Uses in-place updates when possible (same streams in same order).
        Otherwise does a full model reset which is still fast since no widgets
        are created - only visible rows are painted by the delegate.
        """
        streamlink = self.app.streamlink
        new_keys = [ls.channel.unique_key for ls in livestreams]

        # Update playing keys (get all playing keys in one call rather than checking each)
        if streamlink:
            all_playing = set(streamlink.get_playing_streams())
            playing_keys = all_playing & set(new_keys)
        else:
            playing_keys = set()
        self._stream_model.update_playing_keys(playing_keys)

        # Check if we can do incremental update (same streams in same order)
        if new_keys == self._stream_keys_order:
            # Fast path: just update stream data in-place (order unchanged)
            live_count = sum(1 for ls in livestreams if ls.live)
            logger.info(f"Fast path update: {len(livestreams)} streams, {live_count} live")
            result = self._stream_model.update_streams_in_place(livestreams)
            if result:
                # Force repaint of visible items
                self.stream_list.viewport().update()
                return

        # Slow path: full model reset (different streams, different order, or first time)
        live_count = sum(1 for ls in livestreams if ls.live)
        logger.info(f"Slow path rebuild: {len(livestreams)} streams, {live_count} live")

        # Disable updates during model reset to prevent intermediate repaints
        self.stream_list.setUpdatesEnabled(False)
        try:
            self._stream_model.set_streams(livestreams)
            self._stream_keys_order = new_keys

            # Clear cached button rects since rows have changed
            self._stream_delegate.clear_button_rects()
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
        self.app.settings.hide_offline = self.hide_offline_btn.isChecked()
        self.app.settings.favorites_only = self.favorites_btn.isChecked()
        self._platform_filter = self.platform_combo.currentData()
        self.app.save_settings()
        self.refresh_stream_list()

    def _on_name_filter_changed(self, text: str):
        """Handle name filter text change (debounced)."""
        self._name_filter = text
        if not hasattr(self, "_name_filter_timer"):
            self._name_filter_timer = QTimer(self)
            self._name_filter_timer.setSingleShot(True)
            self._name_filter_timer.setInterval(150)
            self._name_filter_timer.timeout.connect(self.refresh_stream_list)
        self._name_filter_timer.start()

    def _on_sort_changed(self, index: int):
        """Handle sort mode change."""
        self.app.settings.sort_mode = self.sort_combo.currentData()
        self.app.save_settings()
        self.refresh_stream_list()

    def _on_stream_online(self, livestream):
        """Handle stream going online."""
        self.set_status(f"{livestream.display_name} is live!")
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
            self.theme_btn.setToolTip("Theme: Auto (click to cycle)")
        elif mode == ThemeMode.LIGHT:
            self.theme_btn.setText("☀")  # Sun for light
            self.theme_btn.setToolTip("Theme: Light (click to cycle)")
        elif mode == ThemeMode.DARK:
            self.theme_btn.setText("☾")  # Moon for dark
            self.theme_btn.setToolTip("Theme: Dark (click to cycle)")
        elif mode == ThemeMode.HIGH_CONTRAST:
            self.theme_btn.setText("◉")  # High contrast icon
            self.theme_btn.setToolTip("Theme: High Contrast (click to cycle)")

    def _on_theme_toggle(self):
        """Cycle through theme modes: Auto -> Light -> Dark -> Auto."""
        mode = self.app.settings.theme_mode
        # Remember current visual state before changing
        was_dark = ThemeManager.is_dark_mode()

        if mode == ThemeMode.AUTO:
            new_mode = ThemeMode.LIGHT
        elif mode == ThemeMode.LIGHT:
            new_mode = ThemeMode.DARK
        elif mode == ThemeMode.DARK:
            new_mode = ThemeMode.HIGH_CONTRAST
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
                QToolButton[filterToggle="true"]:checked {{
                    background-color: {theme.accent_hover};
                    color: {theme.text_primary};
                    border: 1px solid {theme.accent};
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
            # Update muted text label colors from theme
            muted_style = f"color: {theme.text_muted};"
            if hasattr(self, "loading_detail"):
                self.loading_detail.setStyleSheet(muted_style)
            if hasattr(self, "all_offline_label"):
                self.all_offline_label.setStyleSheet(muted_style)
            if hasattr(self, "empty_label"):
                self.empty_label.setStyleSheet(muted_style)
            if hasattr(self, "delete_selected_btn"):
                self.delete_selected_btn.setStyleSheet(f"color: {theme.status_error};")
            # Update delegate theme and repaint
            if self._stream_delegate:
                self._stream_delegate.apply_theme()
            self.stream_list.viewport().update()
            # Notify chat window if open
            if hasattr(self.app, "_chat_window") and self.app._chat_window:
                self.app._chat_window.apply_theme()
        finally:
            self.setUpdatesEnabled(True)

    def _on_item_double_clicked(self, index):
        """Handle double-click on list item."""
        livestream = index.data(StreamRole)
        if livestream:
            self._on_play_stream(livestream)

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
        if self._stream_model:
            is_selection = self._stream_model.is_selection_mode()
            self._stream_model.set_selection_mode(not is_selection)
            self.select_btn.setChecked(not is_selection)
            self.selection_bar.setVisible(not is_selection)
            self._update_selection_count()

    def _exit_selection_mode(self):
        """Exit selection mode."""
        if self._stream_model:
            self._stream_model.set_selection_mode(False)
        self.select_btn.setChecked(False)
        self.selection_bar.setVisible(False)

    def _on_escape(self):
        """Handle Escape key - exit selection mode or clear name filter."""
        if self._stream_model and self._stream_model.is_selection_mode():
            self._exit_selection_mode()
        elif self._name_filter:
            self.name_filter_edit.clear()

    def _on_delete_key(self):
        """Handle Delete key - delete selected in selection mode."""
        if self._stream_model and self._stream_model.is_selection_mode():
            self._delete_selected()

    def _on_ctrl_a(self):
        """Handle Ctrl+A - select all in selection mode, or enter selection mode."""
        if self.name_filter_edit.hasFocus():
            self.name_filter_edit.selectAll()
            return
        if self._stream_model:
            if not self._stream_model.is_selection_mode():
                self._toggle_selection_mode()
            self._select_all()

    def _select_all(self):
        """Select all visible rows."""
        if self._stream_model:
            self._stream_model.select_all()
        self._update_selection_count()

    def _deselect_all(self):
        """Deselect all rows."""
        if self._stream_model:
            self._stream_model.deselect_all()
        self._update_selection_count()

    def _update_selection_count(self):
        """Update the selection count label."""
        count = self._stream_model.get_selection_count() if self._stream_model else 0
        self.selection_count_label.setText(f"{count} selected")

    def _delete_selected(self):
        """Move selected channels to trash."""
        selected_keys = self._stream_model.get_selected_keys() if self._stream_model else []
        if not selected_keys:
            return

        reply = QMessageBox.question(
            self,
            "Move to Trash",
            f"Move {len(selected_keys)} channel(s) to trash?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.app.monitor.trash_channels(selected_keys)
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
        """Handle mouse/wheel events on stream list viewport."""
        from PySide6.QtCore import QEvent

        if obj != self.stream_list.viewport():
            return super().eventFilter(obj, event)

        # Ctrl+Wheel for font scaling
        if event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                default_size = QApplication.font().pointSize()
                current = self.app.settings.font_size
                if current == 0:
                    current = default_size
                delta = 1 if event.angleDelta().y() > 0 else -1
                new_size = max(6, min(30, current + delta))
                self.app.settings.font_size = new_size
                self.app.save_settings()
                if self._stream_delegate:
                    self._stream_delegate.invalidate_size_cache()
                if self._stream_model:
                    self._stream_model.layoutChanged.emit()
                return True

        # Selection mode: shift+click for range, track anchor on normal click
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and self._stream_model
            and self._stream_model.is_selection_mode()
        ):
            index = self.stream_list.indexAt(event.pos())
            if index.isValid():
                has_shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                if has_shift and self._last_clicked_index is not None:
                    self._stream_model.select_range(self._last_clicked_index.row(), index.row())
                    self._update_selection_count()
                    return True
                else:
                    # Track anchor for next shift+click (let editorEvent handle toggle)
                    self._last_clicked_index = index

        return super().eventFilter(obj, event)

    # --- Trash dialog ---

    def _show_trash_dialog(self):
        """Show the trash bin dialog."""
        if not self.app.monitor:
            return
        dialog = TrashDialog(self, self.app.monitor)
        dialog.exec()
        if dialog.restored_any:
            self.app.refresh(on_complete=lambda: self.set_status("Ready"))
            self.refresh_stream_list()

    def _quit_app(self):
        """Quit the application (bypasses minimize-to-tray)."""
        self._force_quit = True
        self.close()


class TrashDialog(QDialog):
    """Dialog for managing trashed channels."""

    def __init__(self, parent, monitor):
        super().__init__(parent)
        self.monitor = monitor
        self.restored_any = False
        self.setWindowTitle("Trash Bin")
        self.resize(450, 400)

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()

        restore_btn = QPushButton("Restore Selected")
        restore_btn.clicked.connect(self._restore_selected)
        btn_layout.addWidget(restore_btn)

        delete_btn = QPushButton("Delete Permanently")
        error_color = get_theme().status_error
        delete_btn.setStyleSheet(f"color: {error_color};")
        delete_btn.clicked.connect(self._delete_permanently)
        btn_layout.addWidget(delete_btn)

        empty_btn = QPushButton("Empty Trash")
        empty_btn.setStyleSheet(f"color: {error_color};")
        empty_btn.clicked.connect(self._empty_trash)
        btn_layout.addWidget(empty_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        self._populate()

    def _populate(self):
        """Populate the list with trashed channels."""
        self.list_widget.clear()
        trash = self.monitor.get_trash()
        if not trash:
            item = QListWidgetItem("Trash is empty")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.list_widget.addItem(item)
            return

        now = datetime.now(timezone.utc)
        for i, entry in enumerate(trash):
            name = entry.get("display_name") or entry.get("channel_id", "?")
            platform = entry.get("platform", "?")
            trashed_at = entry.get("trashed_at", "")
            age = ""
            if trashed_at:
                try:
                    dt = datetime.fromisoformat(trashed_at)
                    delta = now - dt
                    if delta.days > 0:
                        age = f"{delta.days}d ago"
                    elif delta.seconds >= 3600:
                        age = f"{delta.seconds // 3600}h ago"
                    else:
                        age = f"{max(1, delta.seconds // 60)}m ago"
                except ValueError:
                    pass
            label = f"{name} ({platform})"
            if age:
                label += f" \u2014 trashed {age}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.list_widget.addItem(item)

    def _get_selected_indices(self) -> list[int]:
        """Get trash indices of selected items."""
        indices = []
        for item in self.list_widget.selectedItems():
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is not None:
                indices.append(idx)
        return indices

    def _restore_selected(self):
        """Restore selected channels from trash."""
        indices = self._get_selected_indices()
        if not indices:
            return
        self.monitor.restore_from_trash(indices)
        self.restored_any = True
        self._populate()

    def _delete_permanently(self):
        """Permanently delete selected channels from trash."""
        indices = self._get_selected_indices()
        if not indices:
            return
        reply = QMessageBox.question(
            self,
            "Delete Permanently",
            f"Permanently delete {len(indices)} channel(s)? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.monitor.permanently_delete_trash(indices)
            self._populate()

    def _empty_trash(self):
        """Empty the entire trash."""
        trash = self.monitor.get_trash()
        if not trash:
            return
        reply = QMessageBox.question(
            self,
            "Empty Trash",
            f"Permanently delete all {len(trash)} trashed channel(s)? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.monitor.empty_trash()
            self._populate()
