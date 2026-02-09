"""Preferences dialog with multiple tabs for application settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.autostart import is_autostart_enabled, set_autostart
from ...core.models import Channel, Livestream, StreamPlatform, UIStyle
from .import_follows import ImportFollowsDialog
from .youtube_import import YouTubeImportDialog

if TYPE_CHECKING:
    from ..app import Application

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


class PreferencesDialog(QDialog):
    """Preferences dialog with multiple tabs."""

    def __init__(self, parent, app: Application, initial_tab: int = 0):
        super().__init__(parent)
        self.app = app
        self._loading = True  # Prevent cascading updates during init

        self.setWindowTitle("Preferences")
        self.setMinimumSize(500, 500)
        # Restore saved size or use default
        pref_w = getattr(self.app.settings, "_prefs_width", 550)
        pref_h = getattr(self.app.settings, "_prefs_height", 550)
        self.resize(pref_w, pref_h)

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

        # Appearance tab
        appearance_tab = self._create_appearance_tab()
        tabs.addTab(appearance_tab, "Appearance")

        # Accounts tab
        accounts_tab = self._create_accounts_tab()
        tabs.addTab(accounts_tab, "Accounts")

        if initial_tab:
            tabs.setCurrentIndex(initial_tab)

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        self._loading = False  # Init complete, allow updates

    def closeEvent(self, event):  # noqa: N802
        """Save dialog size on close."""
        self.app.settings._prefs_width = self.width()
        self.app.settings._prefs_height = self.height()
        super().closeEvent(event)

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

        # Storage group
        storage_group = QGroupBox("Storage")
        storage_layout = QFormLayout(storage_group)

        self.emote_cache_spin = QSpinBox()
        self.emote_cache_spin.setRange(50, 5000)
        self.emote_cache_spin.setSuffix(" MB")
        self.emote_cache_spin.setValue(self.app.settings.emote_cache_mb)
        self.emote_cache_spin.valueChanged.connect(self._on_emote_cache_changed)
        storage_layout.addRow("Emote cache size:", self.emote_cache_spin)

        layout.addWidget(storage_group)

        # UI Style group (moved from Appearance group)
        style_group = QGroupBox("UI Style")
        style_layout = QFormLayout(style_group)

        self.style_combo = QComboBox()
        for i, style in UI_STYLES.items():
            self.style_combo.addItem(style["name"], i)
        self.style_combo.setCurrentIndex(self.app.settings.ui_style)
        self.style_combo.currentIndexChanged.connect(self._on_style_changed)
        style_layout.addRow("UI Style:", self.style_combo)

        self.platform_colors_cb = QCheckBox("Platform colors")
        self.platform_colors_cb.setChecked(self.app.settings.platform_colors)
        self.platform_colors_cb.stateChanged.connect(self._on_platform_colors_changed)
        style_layout.addRow(self.platform_colors_cb)

        layout.addWidget(style_group)

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

        # Custom sound
        self.notif_sound_path = QLineEdit()
        self.notif_sound_path.setPlaceholderText("System default")
        self.notif_sound_path.setText(self.app.settings.notifications.custom_sound_path)
        self.notif_sound_path.textChanged.connect(self._on_notif_changed)
        sound_browse_btn = QPushButton("Browse...")
        sound_browse_btn.clicked.connect(self._on_browse_notification_sound)
        sound_row = QHBoxLayout()
        sound_row.addWidget(self.notif_sound_path)
        sound_row.addWidget(sound_browse_btn)
        notif_layout.addRow("Custom sound:", sound_row)

        # Urgency
        self.notif_urgency_combo = QComboBox()
        self.notif_urgency_combo.addItem("Low", "low")
        self.notif_urgency_combo.addItem("Normal", "normal")
        self.notif_urgency_combo.addItem("Critical", "critical")
        current_urgency = self.app.settings.notifications.urgency
        for i in range(self.notif_urgency_combo.count()):
            if self.notif_urgency_combo.itemData(i) == current_urgency:
                self.notif_urgency_combo.setCurrentIndex(i)
                break
        self.notif_urgency_combo.currentIndexChanged.connect(self._on_notif_changed)
        notif_layout.addRow("Urgency:", self.notif_urgency_combo)

        # Timeout
        self.notif_timeout_spin = QSpinBox()
        self.notif_timeout_spin.setRange(0, 60)
        self.notif_timeout_spin.setSuffix(" sec")
        self.notif_timeout_spin.setSpecialValueText("System default")
        self.notif_timeout_spin.setValue(self.app.settings.notifications.timeout_seconds)
        self.notif_timeout_spin.valueChanged.connect(self._on_notif_changed)
        notif_layout.addRow("Timeout:", self.notif_timeout_spin)

        # Platform filter
        pf = self.app.settings.notifications.platform_filter
        self.notif_twitch_cb = QCheckBox("Twitch")
        self.notif_twitch_cb.setChecked("twitch" in pf)
        self.notif_twitch_cb.stateChanged.connect(self._on_notif_changed)
        self.notif_youtube_cb = QCheckBox("YouTube")
        self.notif_youtube_cb.setChecked("youtube" in pf)
        self.notif_youtube_cb.stateChanged.connect(self._on_notif_changed)
        self.notif_kick_cb = QCheckBox("Kick")
        self.notif_kick_cb.setChecked("kick" in pf)
        self.notif_kick_cb.stateChanged.connect(self._on_notif_changed)
        platform_row = QHBoxLayout()
        platform_row.addWidget(self.notif_twitch_cb)
        platform_row.addWidget(self.notif_youtube_cb)
        platform_row.addWidget(self.notif_kick_cb)
        platform_row.addStretch()
        notif_layout.addRow("Platforms:", platform_row)

        # Quiet hours
        self.notif_quiet_cb = QCheckBox("Enable quiet hours")
        self.notif_quiet_cb.setChecked(self.app.settings.notifications.quiet_hours_enabled)
        self.notif_quiet_cb.stateChanged.connect(self._on_notif_changed)
        notif_layout.addRow(self.notif_quiet_cb)

        from PySide6.QtCore import QTime

        self.notif_quiet_start = QTimeEdit()
        self.notif_quiet_start.setDisplayFormat("HH:mm")
        start_parts = self.app.settings.notifications.quiet_hours_start.split(":")
        self.notif_quiet_start.setTime(QTime(int(start_parts[0]), int(start_parts[1])))
        self.notif_quiet_start.timeChanged.connect(self._on_notif_changed)

        self.notif_quiet_end = QTimeEdit()
        self.notif_quiet_end.setDisplayFormat("HH:mm")
        end_parts = self.app.settings.notifications.quiet_hours_end.split(":")
        self.notif_quiet_end.setTime(QTime(int(end_parts[0]), int(end_parts[1])))
        self.notif_quiet_end.timeChanged.connect(self._on_notif_changed)

        quiet_row = QHBoxLayout()
        quiet_row.addWidget(QLabel("From:"))
        quiet_row.addWidget(self.notif_quiet_start)
        quiet_row.addWidget(QLabel("To:"))
        quiet_row.addWidget(self.notif_quiet_end)
        quiet_row.addStretch()
        notif_layout.addRow("Quiet hours:", quiet_row)

        # Raid notifications
        self.notif_raid_cb = QCheckBox("Raid notifications (chat channels)")
        self.notif_raid_cb.setChecked(self.app.settings.notifications.raid_notifications_enabled)
        self.notif_raid_cb.stateChanged.connect(self._on_notif_changed)
        notif_layout.addRow(self.notif_raid_cb)

        # Test notification button
        self.test_notif_btn = QPushButton("Test Notification")
        self.test_notif_btn.clicked.connect(self._on_test_notification)
        notif_layout.addRow(self.test_notif_btn)

        layout.addWidget(notif_group)

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

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(lambda: self._reset_tab_defaults("General"))
        layout.addWidget(reset_btn, 0, Qt.AlignmentFlag.AlignLeft)

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

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(lambda: self._reset_tab_defaults("Playback"))
        layout.addWidget(reset_btn, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()
        return widget

    def _create_chat_tab(self) -> QWidget:
        """Create the Chat settings tab."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
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

        self.scrollback_spin = QSpinBox()
        self.scrollback_spin.setRange(100, 50000)
        self.scrollback_spin.setSingleStep(100)
        self.scrollback_spin.setSuffix(" messages")
        self.scrollback_spin.setValue(self.app.settings.chat.builtin.max_messages)
        self.scrollback_spin.valueChanged.connect(self._on_chat_changed)
        builtin_layout.addRow("Scrollback buffer:", self.scrollback_spin)

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

        self.chat_timestamps_cb = QCheckBox("Show timestamps")
        self.chat_timestamps_cb.setChecked(self.app.settings.chat.builtin.show_timestamps)
        self.chat_timestamps_cb.stateChanged.connect(self._on_chat_changed)

        self.chat_ts_format_combo = QComboBox()
        self.chat_ts_format_combo.addItem("24-hour", "24h")
        self.chat_ts_format_combo.addItem("12-hour", "12h")
        current_fmt = self.app.settings.chat.builtin.timestamp_format
        self.chat_ts_format_combo.setCurrentIndex(1 if current_fmt == "12h" else 0)
        self.chat_ts_format_combo.currentIndexChanged.connect(self._on_chat_changed)

        ts_row = QHBoxLayout()
        ts_row.addWidget(self.chat_timestamps_cb)
        ts_row.addWidget(self.chat_ts_format_combo)
        ts_row.addStretch()
        builtin_layout.addRow(ts_row)

        self.chat_name_colors_cb = QCheckBox("Use platform name colors")
        self.chat_name_colors_cb.setChecked(self.app.settings.chat.builtin.use_platform_name_colors)
        self.chat_name_colors_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_name_colors_cb)

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

        self.chat_animate_emotes_cb = QCheckBox("Animate emotes")
        self.chat_animate_emotes_cb.setChecked(self.app.settings.chat.builtin.animate_emotes)
        self.chat_animate_emotes_cb.setEnabled(self.app.settings.chat.builtin.show_emotes)
        self.chat_animate_emotes_cb.stateChanged.connect(self._on_chat_changed)
        self.chat_emotes_cb.stateChanged.connect(
            lambda state: self.chat_animate_emotes_cb.setEnabled(bool(state))
        )
        builtin_layout.addRow(self.chat_animate_emotes_cb)

        self.chat_alt_rows_cb = QCheckBox("Alternating row colors")
        self.chat_alt_rows_cb.setChecked(self.app.settings.chat.builtin.show_alternating_rows)
        self.chat_alt_rows_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_alt_rows_cb)

        self.chat_metrics_cb = QCheckBox("Show metrics in status bar")
        self.chat_metrics_cb.setChecked(self.app.settings.chat.builtin.show_metrics)
        self.chat_metrics_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_metrics_cb)

        self.chat_spellcheck_cb = QCheckBox("Enable spellcheck")
        self.chat_spellcheck_cb.setChecked(self.app.settings.chat.builtin.spellcheck_enabled)
        self.chat_spellcheck_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_spellcheck_cb)

        self.chat_user_card_hover_cb = QCheckBox("Show user card on hover")
        self.chat_user_card_hover_cb.setChecked(self.app.settings.chat.builtin.user_card_hover)
        self.chat_user_card_hover_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_user_card_hover_cb)

        # Moderated message display
        self.moderated_display_combo = QComboBox()
        self.moderated_display_combo.addItem("Strikethrough", "strikethrough")
        self.moderated_display_combo.addItem("Truncated", "truncated")
        self.moderated_display_combo.addItem("Hidden", "hidden")
        current_mod = self.app.settings.chat.builtin.moderated_message_display
        idx = self.moderated_display_combo.findData(current_mod)
        if idx >= 0:
            self.moderated_display_combo.setCurrentIndex(idx)
        self.moderated_display_combo.currentIndexChanged.connect(self._on_chat_changed)
        builtin_layout.addRow("Deleted messages:", self.moderated_display_combo)

        # Banner settings separator
        builtin_layout.addRow(QLabel("<b>Chat Banners</b>"))

        # Show stream title toggle
        self.show_stream_title_cb = QCheckBox("Show stream title banner")
        self.show_stream_title_cb.setChecked(self.app.settings.chat.builtin.show_stream_title)
        self.show_stream_title_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.show_stream_title_cb)

        # Show socials toggle
        self.show_socials_cb = QCheckBox("Show channel socials banner")
        self.show_socials_cb.setChecked(self.app.settings.chat.builtin.show_socials_banner)
        self.show_socials_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.show_socials_cb)

        layout.addWidget(self.builtin_group)

        # Highlight Keywords group
        self.keywords_group = QGroupBox("Highlight Keywords")
        kw_layout = QVBoxLayout(self.keywords_group)
        kw_info = QLabel("Messages containing these words will be highlighted (case-insensitive).")
        kw_info.setStyleSheet("color: gray; font-style: italic;")
        kw_info.setWordWrap(True)
        kw_layout.addWidget(kw_info)
        self.kw_search = QLineEdit()
        self.kw_search.setPlaceholderText("Filter keywords\u2026")
        self.kw_search.setClearButtonEnabled(True)
        self.kw_search.textChanged.connect(self._refresh_keywords_list)
        kw_layout.addWidget(self.kw_search)
        self.keywords_list = QListWidget()
        self.keywords_list.setMaximumHeight(100)
        self.keywords_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        kw_layout.addWidget(self.keywords_list)
        kw_buttons = QHBoxLayout()
        kw_add_btn = QPushButton("Add")
        kw_add_btn.clicked.connect(self._add_keyword)
        kw_buttons.addWidget(kw_add_btn)
        kw_remove_btn = QPushButton("Remove Selected")
        kw_remove_btn.clicked.connect(self._remove_keywords)
        kw_buttons.addWidget(kw_remove_btn)
        kw_buttons.addStretch()
        kw_layout.addLayout(kw_buttons)
        self._refresh_keywords_list()
        layout.addWidget(self.keywords_group)

        # Blocked Users group
        self.blocked_group = QGroupBox("Blocked Users")
        bl_layout = QVBoxLayout(self.blocked_group)
        bl_filter_row = QHBoxLayout()
        self.bl_search = QLineEdit()
        self.bl_search.setPlaceholderText("Filter users\u2026")
        self.bl_search.setClearButtonEnabled(True)
        self.bl_search.textChanged.connect(self._refresh_blocked_list)
        bl_filter_row.addWidget(self.bl_search)
        self.bl_platform_filter = self._create_platform_filter_combo()
        self.bl_platform_filter.currentIndexChanged.connect(lambda: self._refresh_blocked_list())
        bl_filter_row.addWidget(self.bl_platform_filter)
        bl_layout.addLayout(bl_filter_row)
        self.blocked_list = QListWidget()
        self.blocked_list.setMaximumHeight(120)
        self.blocked_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        bl_layout.addWidget(self.blocked_list)
        bl_buttons = QHBoxLayout()
        bl_remove_btn = QPushButton("Remove Selected")
        bl_remove_btn.clicked.connect(self._remove_blocked_users)
        bl_buttons.addWidget(bl_remove_btn)
        bl_clear_btn = QPushButton("Clear All")
        bl_clear_btn.clicked.connect(self._clear_all_blocked)
        bl_buttons.addWidget(bl_clear_btn)
        bl_buttons.addStretch()
        bl_layout.addLayout(bl_buttons)
        self._refresh_blocked_list()
        layout.addWidget(self.blocked_group)

        # User Nicknames group
        self.nicknames_group = QGroupBox("User Nicknames")
        nn_layout = QVBoxLayout(self.nicknames_group)
        nn_filter_row = QHBoxLayout()
        self.nn_search = QLineEdit()
        self.nn_search.setPlaceholderText("Filter nicknames\u2026")
        self.nn_search.setClearButtonEnabled(True)
        self.nn_search.textChanged.connect(self._refresh_nicknames_list)
        nn_filter_row.addWidget(self.nn_search)
        self.nn_platform_filter = self._create_platform_filter_combo()
        self.nn_platform_filter.currentIndexChanged.connect(lambda: self._refresh_nicknames_list())
        nn_filter_row.addWidget(self.nn_platform_filter)
        nn_layout.addLayout(nn_filter_row)
        self.nicknames_list = QListWidget()
        self.nicknames_list.setMaximumHeight(120)
        self.nicknames_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        nn_layout.addWidget(self.nicknames_list)
        nn_buttons = QHBoxLayout()
        nn_add_btn = QPushButton("Add")
        nn_add_btn.clicked.connect(self._add_nickname)
        nn_buttons.addWidget(nn_add_btn)
        nn_edit_btn = QPushButton("Edit")
        nn_edit_btn.clicked.connect(self._edit_nickname)
        nn_buttons.addWidget(nn_edit_btn)
        nn_remove_btn = QPushButton("Remove Selected")
        nn_remove_btn.clicked.connect(self._remove_nicknames)
        nn_buttons.addWidget(nn_remove_btn)
        nn_buttons.addStretch()
        nn_layout.addLayout(nn_buttons)
        self._refresh_nicknames_list()
        layout.addWidget(self.nicknames_group)

        # User Notes group
        self.notes_group = QGroupBox("User Notes")
        nt_layout = QVBoxLayout(self.notes_group)
        nt_filter_row = QHBoxLayout()
        self.nt_search = QLineEdit()
        self.nt_search.setPlaceholderText("Filter notes\u2026")
        self.nt_search.setClearButtonEnabled(True)
        self.nt_search.textChanged.connect(self._refresh_notes_list)
        nt_filter_row.addWidget(self.nt_search)
        self.nt_platform_filter = self._create_platform_filter_combo()
        self.nt_platform_filter.currentIndexChanged.connect(lambda: self._refresh_notes_list())
        nt_filter_row.addWidget(self.nt_platform_filter)
        nt_layout.addLayout(nt_filter_row)
        self.notes_list = QListWidget()
        self.notes_list.setMaximumHeight(120)
        self.notes_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        nt_layout.addWidget(self.notes_list)
        nt_buttons = QHBoxLayout()
        nt_add_btn = QPushButton("Add")
        nt_add_btn.clicked.connect(self._add_note)
        nt_buttons.addWidget(nt_add_btn)
        nt_edit_btn = QPushButton("Edit")
        nt_edit_btn.clicked.connect(self._edit_note)
        nt_buttons.addWidget(nt_edit_btn)
        nt_remove_btn = QPushButton("Remove Selected")
        nt_remove_btn.clicked.connect(self._remove_notes)
        nt_buttons.addWidget(nt_remove_btn)
        nt_buttons.addStretch()
        nt_layout.addLayout(nt_buttons)
        self._refresh_notes_list()
        layout.addWidget(self.notes_group)

        # Chat Logging group
        self.logging_group = QGroupBox("Chat Logging")
        log_layout = QFormLayout(self.logging_group)
        log_settings = self.app.settings.chat.logging

        self.log_enabled_cb = QCheckBox("Enable chat logging to disk")
        self.log_enabled_cb.setChecked(log_settings.enabled)
        self.log_enabled_cb.stateChanged.connect(self._on_chat_logging_changed)
        log_layout.addRow(self.log_enabled_cb)

        self.log_disk_spin = QSpinBox()
        self.log_disk_spin.setRange(10, 5000)
        self.log_disk_spin.setSuffix(" MB")
        self.log_disk_spin.setValue(log_settings.max_disk_mb)
        self.log_disk_spin.valueChanged.connect(self._on_chat_logging_changed)
        log_layout.addRow("Max disk usage:", self.log_disk_spin)

        self.log_format_combo = QComboBox()
        self.log_format_combo.addItem("JSONL (supports history loading)", "jsonl")
        self.log_format_combo.addItem("Plain text", "text")
        for i in range(self.log_format_combo.count()):
            if self.log_format_combo.itemData(i) == log_settings.log_format:
                self.log_format_combo.setCurrentIndex(i)
                break
        self.log_format_combo.currentIndexChanged.connect(self._on_chat_logging_changed)
        log_layout.addRow("Format:", self.log_format_combo)

        self.log_history_cb = QCheckBox("Load history on chat open")
        self.log_history_cb.setChecked(log_settings.load_history_on_open)
        self.log_history_cb.stateChanged.connect(self._on_chat_logging_changed)
        log_layout.addRow(self.log_history_cb)

        self.log_history_spin = QSpinBox()
        self.log_history_spin.setRange(10, 1000)
        self.log_history_spin.setSuffix(" messages")
        self.log_history_spin.setValue(log_settings.history_lines)
        self.log_history_spin.valueChanged.connect(self._on_chat_logging_changed)
        log_layout.addRow("History lines:", self.log_history_spin)

        # Current disk usage label
        self.log_disk_usage_label = QLabel()
        self._update_log_disk_usage_label()
        log_layout.addRow("Current usage:", self.log_disk_usage_label)

        layout.addWidget(self.logging_group)

        # Set initial visibility based on current mode
        show_browser = current_mode == "browser"
        self.browser_label.setVisible(show_browser)
        self.browser_combo.setVisible(show_browser)
        self.new_window_cb.setVisible(show_browser)
        self.builtin_group.setVisible(not show_browser)
        self.keywords_group.setVisible(not show_browser)
        self.blocked_group.setVisible(not show_browser)
        self.nicknames_group.setVisible(not show_browser)
        self.notes_group.setVisible(not show_browser)
        self.logging_group.setVisible(not show_browser)

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(lambda: self._reset_tab_defaults("Chat"))
        layout.addWidget(reset_btn, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()
        scroll.setWidget(widget)
        return scroll

    def _create_appearance_tab(self) -> QWidget:
        """Create the Appearance tab with the theme editor."""
        from .theme_editor import ThemeEditorWidget

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        self._theme_editor = ThemeEditorWidget(self.app, parent=widget)
        layout.addWidget(self._theme_editor)

        return widget

    def _create_accounts_tab(self) -> QWidget:
        """Create the Accounts tab."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

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

        self.yt_status = QLabel("Status: Not configured")
        self.yt_status.setStyleSheet("color: gray;")
        yt_layout.addWidget(self.yt_status)

        yt_info = QLabel(
            "Import cookies from your browser to enable YouTube chat sending.\n"
            "You must be logged into YouTube in the browser you select."
        )
        yt_info.setStyleSheet("color: gray; font-style: italic;")
        yt_info.setWordWrap(True)
        yt_layout.addWidget(yt_info)

        # Primary action: Login button
        yt_main_buttons = QHBoxLayout()
        self.yt_login_btn = QPushButton("Import from Browser")
        self.yt_login_btn.clicked.connect(self._on_yt_login)
        yt_main_buttons.addWidget(self.yt_login_btn)
        self.yt_import_subs_btn = QPushButton("Import Subscriptions")
        self.yt_import_subs_btn.clicked.connect(self._on_yt_import_subs)
        yt_main_buttons.addWidget(self.yt_import_subs_btn)
        self.yt_logout_btn = QPushButton("Logout")
        self.yt_logout_btn.setStyleSheet("color: red;")
        self.yt_logout_btn.clicked.connect(self._on_yt_clear_cookies)
        yt_main_buttons.addWidget(self.yt_logout_btn)
        yt_main_buttons.addStretch()
        yt_layout.addLayout(yt_main_buttons)

        # Manual cookie paste (collapsible/advanced)
        yt_manual_label = QLabel("Or paste cookies manually:")
        yt_manual_label.setStyleSheet("color: gray; margin-top: 8px;")
        yt_layout.addWidget(yt_manual_label)

        self.yt_cookies_edit = QPlainTextEdit()
        self.yt_cookies_edit.setPlaceholderText(
            "SID=...; HSID=...; SSID=...; APISID=...; SAPISID=..."
        )
        self.yt_cookies_edit.setMinimumHeight(120)
        self.yt_cookies_edit.setPlainText(self.app.settings.youtube.cookies)
        yt_layout.addWidget(self.yt_cookies_edit)

        yt_buttons = QHBoxLayout()
        self.yt_save_btn = QPushButton("Save Cookies")
        self.yt_save_btn.clicked.connect(self._on_yt_save_cookies)
        yt_buttons.addWidget(self.yt_save_btn)
        yt_help_btn = QPushButton("How to get cookies")
        yt_help_btn.setStyleSheet("color: #5599ff; border: none; text-decoration: underline;")
        yt_help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        yt_help_btn.clicked.connect(self._on_yt_cookie_help)
        yt_buttons.addWidget(yt_help_btn)
        yt_buttons.addStretch()
        yt_layout.addLayout(yt_buttons)

        layout.addWidget(yt_group)
        self._update_yt_status()

        # Kick group
        kick_group = QGroupBox("Kick")
        kick_layout = QVBoxLayout(kick_group)

        self.kick_status = QLabel("Status: Not logged in")
        self.kick_status.setStyleSheet("color: gray;")
        kick_layout.addWidget(self.kick_status)

        kick_note = QLabel(
            "Kick does not support importing follows.\n"
            "Add Kick channels manually via the main window."
        )
        kick_note.setStyleSheet("color: gray; font-style: italic;")
        kick_note.setWordWrap(True)
        kick_layout.addWidget(kick_note)

        kick_buttons = QHBoxLayout()
        self.kick_login_btn = QPushButton("Login to Kick")
        self.kick_login_btn.clicked.connect(self._on_kick_login)
        kick_buttons.addWidget(self.kick_login_btn)
        self.kick_logout_btn = QPushButton("Logout")
        self.kick_logout_btn.setStyleSheet("color: red;")
        self.kick_logout_btn.clicked.connect(self._on_kick_logout)
        kick_buttons.addWidget(self.kick_logout_btn)
        kick_buttons.addStretch()
        kick_layout.addLayout(kick_buttons)

        layout.addWidget(kick_group)

        layout.addStretch()
        self._update_account_buttons()
        scroll.setWidget(widget)
        return scroll

    def _update_twitch_status(self):
        """Update Twitch login status display."""
        if self.app.settings.twitch.access_token:
            login = self.app.settings.twitch.login_name
            if login:
                self.twitch_status.setText(f"Status: Logged in as {login}")
            else:
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

        # Kick
        kick_logged_in = bool(self.app.settings.kick.access_token)
        self.kick_login_btn.setVisible(not kick_logged_in)
        self.kick_logout_btn.setVisible(kick_logged_in)
        if kick_logged_in:
            login = self.app.settings.kick.login_name
            if login:
                self.kick_status.setText(f"Status: Logged in as {login}")
            else:
                self.kick_status.setText("Status: Logged in")
            self.kick_status.setStyleSheet("color: green;")
        else:
            self.kick_status.setText("Status: Not logged in")
            self.kick_status.setStyleSheet("color: gray;")

    def _on_autostart_changed(self, state):
        enabled = state == Qt.CheckState.Checked.value
        set_autostart(enabled)
        self.app.settings.autostart = enabled
        self.app.save_settings()

    def _on_background_changed(self, state):
        enabled = state == Qt.CheckState.Checked.value
        self.app.settings.close_to_tray = enabled
        self.app.save_settings()

    def _on_refresh_changed(self, value):
        self.app.update_refresh_interval(value)

    def _on_emote_cache_changed(self, value):
        self.app.settings.emote_cache_mb = value
        if self.app.chat_manager:
            self.app.chat_manager.set_emote_cache_limit(value)
        self.app.save_settings()

    def _on_notif_changed(self):
        if self._loading:
            return
        notif = self.app.settings.notifications
        notif.enabled = self.notif_enabled_cb.isChecked()
        notif.sound_enabled = self.notif_sound_cb.isChecked()
        notif.custom_sound_path = self.notif_sound_path.text().strip()
        notif.urgency = self.notif_urgency_combo.currentData()
        notif.timeout_seconds = self.notif_timeout_spin.value()
        # Platform filter
        pf = []
        if self.notif_twitch_cb.isChecked():
            pf.append("twitch")
        if self.notif_youtube_cb.isChecked():
            pf.append("youtube")
        if self.notif_kick_cb.isChecked():
            pf.append("kick")
        notif.platform_filter = pf
        # Quiet hours
        notif.quiet_hours_enabled = self.notif_quiet_cb.isChecked()
        notif.quiet_hours_start = self.notif_quiet_start.time().toString("HH:mm")
        notif.quiet_hours_end = self.notif_quiet_end.time().toString("HH:mm")
        notif.raid_notifications_enabled = self.notif_raid_cb.isChecked()
        self.app.save_settings()

    def _on_style_changed(self, index):
        self.app.settings.ui_style = self.style_combo.currentData()
        self.app.save_settings()
        # Refresh the stream list to apply the new style
        if self.parent():
            self.parent().refresh_stream_list()

    def _on_browse_notification_sound(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Notification Sound",
            "",
            "Audio Files (*.wav *.ogg *.mp3 *.flac *.opus);;All Files (*)",
        )
        if path:
            self.notif_sound_path.setText(path)

    def _on_notif_backend_changed(self, index):
        self.app.settings.notifications.backend = self.notif_backend_combo.currentData()
        self.app.save_settings()

    def _on_test_notification(self):
        """Send a test notification."""
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
        from ...core.models import LaunchMethod

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
        show_browser = client_type == "browser"
        self.browser_label.setVisible(show_browser)
        self.browser_combo.setVisible(show_browser)
        self.new_window_cb.setVisible(show_browser)
        self.builtin_group.setVisible(not show_browser)
        self.keywords_group.setVisible(not show_browser)
        self.blocked_group.setVisible(not show_browser)
        self.nicknames_group.setVisible(not show_browser)
        self.notes_group.setVisible(not show_browser)
        self.logging_group.setVisible(not show_browser)
        self._on_chat_changed()

    def _on_chat_changed(self):
        if self._loading:
            return  # Don't save during init or theme switch
        self.app.settings.chat.mode = self.chat_client_combo.currentData()
        self.app.settings.chat.auto_open = self.chat_auto_cb.isChecked()
        self.app.settings.chat.browser = self.browser_combo.currentData()
        self.app.settings.chat.new_window = self.new_window_cb.isChecked()
        # Built-in chat settings
        self.app.settings.chat.builtin.font_size = self.chat_font_spin.value()
        self.app.settings.chat.builtin.line_spacing = self.chat_spacing_spin.value()
        self.app.settings.chat.builtin.max_messages = self.scrollback_spin.value()
        self.app.settings.chat.builtin.show_timestamps = self.chat_timestamps_cb.isChecked()
        self.app.settings.chat.builtin.timestamp_format = self.chat_ts_format_combo.currentData()
        self.app.settings.chat.builtin.show_badges = self.chat_badges_cb.isChecked()
        self.app.settings.chat.builtin.show_mod_badges = self.chat_mod_badges_cb.isChecked()
        self.app.settings.chat.builtin.show_emotes = self.chat_emotes_cb.isChecked()
        self.app.settings.chat.builtin.animate_emotes = self.chat_animate_emotes_cb.isChecked()
        self.app.settings.chat.builtin.show_alternating_rows = self.chat_alt_rows_cb.isChecked()
        self.app.settings.chat.builtin.show_metrics = self.chat_metrics_cb.isChecked()
        self.app.settings.chat.builtin.spellcheck_enabled = self.chat_spellcheck_cb.isChecked()
        self.app.settings.chat.builtin.user_card_hover = self.chat_user_card_hover_cb.isChecked()
        self.app.settings.chat.builtin.moderated_message_display = (
            self.moderated_display_combo.currentData()
        )
        self.app.settings.chat.builtin.use_platform_name_colors = (
            self.chat_name_colors_cb.isChecked()
        )
        # Banner settings
        self.app.settings.chat.builtin.show_stream_title = self.show_stream_title_cb.isChecked()
        self.app.settings.chat.builtin.show_socials_banner = self.show_socials_cb.isChecked()

        providers = []
        if self.emote_7tv_cb.isChecked():
            providers.append("7tv")
        if self.emote_bttv_cb.isChecked():
            providers.append("bttv")
        if self.emote_ffz_cb.isChecked():
            providers.append("ffz")
        self.app.settings.chat.builtin.emote_providers = providers
        self.app.save_settings()
        # Live-update chat window if open
        if self.app._chat_window:
            self.app._chat_window.update_tab_style()
            self.app._chat_window.update_animation_state()
            self.app._chat_window.update_banner_settings()
            self.app._chat_window.update_metrics_bar()
            self.app._chat_window.update_spellcheck()

    def _on_chat_logging_changed(self):
        """Handle chat logging settings change."""
        log = self.app.settings.chat.logging
        log.enabled = self.log_enabled_cb.isChecked()
        log.max_disk_mb = self.log_disk_spin.value()
        log.log_format = self.log_format_combo.currentData()
        log.load_history_on_open = self.log_history_cb.isChecked()
        log.history_lines = self.log_history_spin.value()
        self.app.save_settings()
        # Update chat manager timers
        if self.app.chat_manager:
            self.app.chat_manager.update_chat_logging_settings(log)
        self._update_log_disk_usage_label()

    def _update_log_disk_usage_label(self):
        """Update the disk usage display for chat logs."""
        if self.app.chat_manager:
            usage = self.app.chat_manager.chat_log_writer.get_total_disk_usage()
        else:
            from ...chat.chat_log_store import ChatLogWriter

            writer = ChatLogWriter(self.app.settings.chat.logging)
            usage = writer.get_total_disk_usage()
        if usage < 1024 * 1024:
            text = f"{usage / 1024:.1f} KB"
        else:
            text = f"{usage / (1024 * 1024):.1f} MB"
        self.log_disk_usage_label.setText(text)

    def _reset_tab_defaults(self, tab_name: str):
        """Reset a preferences tab to default values after confirmation."""
        from ...core.settings import (
            BuiltinChatSettings,
            ChannelIconSettings,
            ChannelInfoSettings,
            ChatSettings,
            NotificationSettings,
            Settings,
            StreamlinkSettings,
        )

        result = QMessageBox.question(
            self,
            "Reset to Defaults",
            f"Reset all {tab_name} settings to their default values?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result != QMessageBox.StandardButton.Ok:
            return

        if tab_name == "General":
            defaults = Settings()
            info_defaults = ChannelInfoSettings()
            icon_defaults = ChannelIconSettings()
            notif_defaults = NotificationSettings()
            # Startup
            self.autostart_cb.setChecked(defaults.autostart)
            self.background_cb.setChecked(defaults.close_to_tray)
            # Refresh
            self.refresh_spin.setValue(defaults.refresh_interval)
            # Storage
            self.emote_cache_spin.setValue(defaults.emote_cache_mb)
            # Notifications
            self.notif_enabled_cb.setChecked(notif_defaults.enabled)
            self.notif_sound_cb.setChecked(notif_defaults.sound_enabled)
            for i in range(self.notif_backend_combo.count()):
                if self.notif_backend_combo.itemData(i) == notif_defaults.backend:
                    self.notif_backend_combo.setCurrentIndex(i)
                    break
            self.notif_sound_path.setText(notif_defaults.custom_sound_path)
            for i in range(self.notif_urgency_combo.count()):
                if self.notif_urgency_combo.itemData(i) == notif_defaults.urgency:
                    self.notif_urgency_combo.setCurrentIndex(i)
                    break
            self.notif_timeout_spin.setValue(notif_defaults.timeout_seconds)
            self.notif_twitch_cb.setChecked("twitch" in notif_defaults.platform_filter)
            self.notif_youtube_cb.setChecked("youtube" in notif_defaults.platform_filter)
            self.notif_kick_cb.setChecked("kick" in notif_defaults.platform_filter)
            self.notif_quiet_cb.setChecked(notif_defaults.quiet_hours_enabled)
            from PySide6.QtCore import QTime

            q_start = notif_defaults.quiet_hours_start.split(":")
            self.notif_quiet_start.setTime(QTime(int(q_start[0]), int(q_start[1])))
            q_end = notif_defaults.quiet_hours_end.split(":")
            self.notif_quiet_end.setTime(QTime(int(q_end[0]), int(q_end[1])))
            self.notif_raid_cb.setChecked(notif_defaults.raid_notifications_enabled)
            # Appearance
            self.style_combo.setCurrentIndex(defaults.ui_style)
            self.platform_colors_cb.setChecked(defaults.platform_colors)
            # Channel info
            self.show_duration_cb.setChecked(info_defaults.show_live_duration)
            self.show_viewers_cb.setChecked(info_defaults.show_viewers)
            # Channel icons
            self.show_platform_cb.setChecked(icon_defaults.show_platform)
            self.show_play_cb.setChecked(icon_defaults.show_play)
            self.show_favorite_cb.setChecked(icon_defaults.show_favorite)
            self.show_chat_cb.setChecked(icon_defaults.show_chat)
            self.show_browser_cb.setChecked(icon_defaults.show_browser)
            # Reset font_size (stream list scaling)
            self.app.settings.font_size = defaults.font_size
            self.app.save_settings()
            self.app.main_window.refresh_stream_list()

        elif tab_name == "Playback":
            defaults = StreamlinkSettings()
            self.sl_path_edit.setText(defaults.path)
            self.sl_args_edit.setText(defaults.additional_args)
            self.player_path_edit.setText(defaults.player)
            self.player_args_edit.setText(defaults.player_args)
            for combo, value in (
                (self.twitch_launch_combo, defaults.twitch_launch_method.value),
                (self.youtube_launch_combo, defaults.youtube_launch_method.value),
                (self.kick_launch_combo, defaults.kick_launch_method.value),
            ):
                for i in range(combo.count()):
                    if combo.itemData(i) == value:
                        combo.setCurrentIndex(i)
                        break
            self._on_streamlink_changed()

        elif tab_name == "Chat":
            chat_defaults = ChatSettings()
            builtin = BuiltinChatSettings()
            self.chat_auto_cb.setChecked(chat_defaults.auto_open)
            for i in range(self.chat_client_combo.count()):
                if self.chat_client_combo.itemData(i) == chat_defaults.mode:
                    self.chat_client_combo.setCurrentIndex(i)
                    break
            for i in range(self.browser_combo.count()):
                if self.browser_combo.itemData(i) == chat_defaults.browser:
                    self.browser_combo.setCurrentIndex(i)
                    break
            self.new_window_cb.setChecked(chat_defaults.new_window)
            self.chat_font_spin.setValue(builtin.font_size)
            self.chat_spacing_spin.setValue(builtin.line_spacing)
            self.scrollback_spin.setValue(builtin.max_messages)
            self.emote_7tv_cb.setChecked("7tv" in builtin.emote_providers)
            self.emote_bttv_cb.setChecked("bttv" in builtin.emote_providers)
            self.emote_ffz_cb.setChecked("ffz" in builtin.emote_providers)
            self.chat_timestamps_cb.setChecked(builtin.show_timestamps)
            self.chat_ts_format_combo.setCurrentIndex(0 if builtin.timestamp_format == "24h" else 1)
            self.chat_badges_cb.setChecked(builtin.show_badges)
            self.chat_mod_badges_cb.setChecked(builtin.show_mod_badges)
            self.chat_emotes_cb.setChecked(builtin.show_emotes)
            self.chat_animate_emotes_cb.setChecked(builtin.animate_emotes)
            self.chat_alt_rows_cb.setChecked(builtin.show_alternating_rows)
            self.chat_metrics_cb.setChecked(builtin.show_metrics)
            self.chat_spellcheck_cb.setChecked(builtin.spellcheck_enabled)
            self.chat_user_card_hover_cb.setChecked(builtin.user_card_hover)
            idx = self.moderated_display_combo.findData(builtin.moderated_message_display)
            if idx >= 0:
                self.moderated_display_combo.setCurrentIndex(idx)
            self.chat_name_colors_cb.setChecked(builtin.use_platform_name_colors)
            # Banner settings
            self.show_stream_title_cb.setChecked(builtin.show_stream_title)
            self.show_socials_cb.setChecked(builtin.show_socials_banner)
            # Logging defaults
            from ...core.settings import ChatLoggingSettings

            log_defaults = ChatLoggingSettings()
            self.log_enabled_cb.setChecked(log_defaults.enabled)
            self.log_disk_spin.setValue(log_defaults.max_disk_mb)
            for i in range(self.log_format_combo.count()):
                if self.log_format_combo.itemData(i) == log_defaults.log_format:
                    self.log_format_combo.setCurrentIndex(i)
                    break
            self.log_history_cb.setChecked(log_defaults.load_history_on_open)
            self.log_history_spin.setValue(log_defaults.history_lines)
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
        added = getattr(dialog, "_added_count", 0)
        if added > 0:
            self.app.monitor.suppress_notifications()

            def on_refresh_complete():
                self.app.monitor.resume_notifications()
                if self.app.main_window:
                    self.app.main_window.refresh_stream_list()

            if self.app.main_window:
                self.app.main_window.refresh_stream_list()

            self.app.refresh(on_complete=on_refresh_complete)

    def _on_twitch_logout(self):
        """Handle Twitch logout."""
        self.app.settings.twitch.access_token = None
        self.app.settings.twitch.user_id = None
        self.app.settings.twitch.login_name = ""
        self.app.save_settings()
        self._update_twitch_status()
        self._update_account_buttons()

    def _on_kick_login(self):
        """Handle Kick OAuth login."""
        self.kick_status.setText("Status: Waiting for authorization...")
        self.kick_status.setStyleSheet("color: orange;")
        self.kick_login_btn.setEnabled(False)

        async def do_login():
            from ...chat.auth.kick_auth import KickAuthFlow

            auth = KickAuthFlow(self.app.settings.kick)
            success = await auth.authenticate(timeout=120)
            if success:
                self.app.save_settings()
            return success

        from ..app import AsyncWorker

        worker = AsyncWorker(do_login, parent=self)
        worker.finished.connect(self._on_kick_login_done)
        worker.error.connect(self._on_kick_login_error)
        worker.start()

    def _on_kick_login_done(self, success):
        """Handle Kick login result."""
        self.kick_login_btn.setEnabled(True)
        self._update_account_buttons()
        if success:
            self.app.chat_manager.reconnect_kick()

    def _on_kick_login_error(self, error_msg):
        """Handle Kick login error."""
        self.kick_login_btn.setEnabled(True)
        self.kick_status.setText(f"Error: {error_msg}")
        self.kick_status.setStyleSheet("color: red;")

    def _on_kick_logout(self):
        """Handle Kick logout."""
        self.app.settings.kick.access_token = ""
        self.app.settings.kick.refresh_token = ""
        self.app.settings.kick.login_name = ""
        self.app.save_settings()
        self._update_account_buttons()
        self.app.chat_manager.reconnect_kick()

    def _on_yt_save_cookies(self):
        """Save YouTube cookies and validate."""
        from ...chat.connections.youtube import validate_cookies

        cookie_text = self.yt_cookies_edit.toPlainText().strip()
        if cookie_text and not validate_cookies(cookie_text):
            QMessageBox.warning(
                self,
                "Invalid Cookies",
                "The cookies are missing required keys.\n"
                "Required: SID, HSID, SSID, APISID, SAPISID",
            )
            return

        self.app.settings.youtube.cookies = cookie_text
        self.app.save_settings()
        self._update_yt_status()
        # Reconnect YouTube chats to pick up new cookies
        self.app.chat_manager.reconnect_youtube()

    def _on_yt_clear_cookies(self):
        """Clear YouTube cookies."""
        self.yt_cookies_edit.setPlainText("")
        self.app.settings.youtube.cookies = ""
        self.app.save_settings()
        self._update_yt_status()
        self.app.chat_manager.reconnect_youtube()

    def _on_yt_login(self):
        """Handle YouTube cookie import from browser."""
        from ..youtube_login import import_cookies_from_browser

        cookie_string = import_cookies_from_browser(self)
        if cookie_string:
            self.app.settings.youtube.cookies = cookie_string
            self.app.save_settings()
            self.yt_cookies_edit.setPlainText(cookie_string)
            self._update_yt_status()
            self.app.chat_manager.reconnect_youtube()

    def _on_yt_import_subs(self):
        """Open YouTube subscription import dialog."""
        dialog = YouTubeImportDialog(self, self.app)
        dialog.exec()
        # Refresh after import
        if dialog._added_count > 0 and self.parent():
            self.parent().refresh_stream_list()

    def _update_yt_status(self):
        """Update YouTube cookie status display."""
        from ...chat.connections.youtube import validate_cookies

        cookies = self.app.settings.youtube.cookies
        is_configured = bool(cookies and validate_cookies(cookies))

        if is_configured:
            self.yt_status.setText("Status: Logged in")
            self.yt_status.setStyleSheet("color: green;")
        elif cookies:
            self.yt_status.setText("Status: Cookies incomplete (missing required keys)")
            self.yt_status.setStyleSheet("color: orange;")
        else:
            self.yt_status.setText("Status: Not logged in")
            self.yt_status.setStyleSheet("color: gray;")

        self.yt_login_btn.setVisible(not is_configured)
        self.yt_import_subs_btn.setVisible(is_configured)
        self.yt_logout_btn.setVisible(is_configured)

    def _on_yt_cookie_help(self):
        """Show instructions for obtaining YouTube cookies."""
        msg = QMessageBox(self)
        msg.setWindowTitle("How to Get YouTube Cookies")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            "<h3>Getting YouTube Cookies (Manual Method)</h3>"
            "<p>The easiest way is to click <b>Import from Browser</b> above.<br>"
            "If that's not available, paste cookies manually:</p>"
            "<ol>"
            "<li>Open <b>YouTube</b> in your browser and log in</li>"
            "<li>Press <b>F12</b> to open Developer Tools</li>"
            "<li>Go to the <b>Application</b> tab (Chrome) or "
            "<b>Storage</b> tab (Firefox)</li>"
            "<li>Expand <b>Cookies</b> &rarr; <code>https://www.youtube.com</code></li>"
            "<li>Find and copy the <b>Value</b> for each of these cookies:<br>"
            "<code>SID</code>, <code>HSID</code>, <code>SSID</code>, "
            "<code>APISID</code>, <code>SAPISID</code></li>"
            "<li>Paste them in the format:<br>"
            "<code>SID=value; HSID=value; SSID=value; APISID=value; SAPISID=value</code></li>"
            "</ol>"
            "<p><b>Notes:</b></p>"
            "<ul>"
            "<li>Cookies typically last 1-2 years</li>"
            "<li>They are stored locally and only used to send chat messages</li>"
            "<li>See <code>docs/youtube-cookies.md</code> for the full guide</li>"
            "</ul>"
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def _on_import_follows(self):
        """Handle import follows."""
        dialog = ImportFollowsDialog(self, self.app, StreamPlatform.TWITCH, start_import=True)
        dialog.exec()

        # After dialog closes, refresh stream status with notifications suppressed
        added = getattr(dialog, "_added_count", 0)
        if added > 0:
            # Suppress notifications during import refresh
            self.app.monitor.suppress_notifications()

            main_window = self.app.main_window

            def on_refresh_complete():
                # Re-enable notifications after refresh
                self.app.monitor.resume_notifications()
                if main_window:
                    main_window.refresh_stream_list()

            # Update UI immediately with new channels
            if main_window:
                main_window.refresh_stream_list()

            # Then check their live status
            self.app.refresh(on_complete=on_refresh_complete)

    # --- Shared filter helpers ---

    def _create_platform_filter_combo(self) -> QComboBox:
        """Create a platform filter dropdown with All + each platform."""
        combo = QComboBox()
        combo.addItem("All", "all")
        for p in StreamPlatform:
            combo.addItem(p.value.capitalize(), p.value)
        combo.setFixedWidth(100)
        return combo

    def _format_platform_label(self, user_key: str) -> str:
        """Extract platform from user_key and return a capitalised label."""
        platform = user_key.split(":")[0] if ":" in user_key else "?"
        return platform.capitalize()

    def _matches_platform_filter(self, user_key: str, platform_filter: str) -> bool:
        """Check if a user_key matches the selected platform filter."""
        if platform_filter == "all":
            return True
        return user_key.startswith(platform_filter + ":")

    # --- Highlight Keywords helpers ---

    def _refresh_keywords_list(self):
        self.keywords_list.clear()
        search = self.kw_search.text().strip().lower()
        for kw in self.app.settings.chat.builtin.highlight_keywords:
            if search and search not in kw.lower():
                continue
            self.keywords_list.addItem(kw)

    def _add_keyword(self):
        text, ok = QInputDialog.getText(self, "Add Highlight Keyword", "Keyword:")
        if ok and text.strip():
            kw = text.strip()
            if kw not in self.app.settings.chat.builtin.highlight_keywords:
                self.app.settings.chat.builtin.highlight_keywords.append(kw)
                self.app.save_settings()
                self._refresh_keywords_list()

    def _remove_keywords(self):
        for item in self.keywords_list.selectedItems():
            kw = item.text()
            if kw in self.app.settings.chat.builtin.highlight_keywords:
                self.app.settings.chat.builtin.highlight_keywords.remove(kw)
        self.app.save_settings()
        self._refresh_keywords_list()

    # --- Blocked Users helpers ---

    def _refresh_blocked_list(self):
        self.blocked_list.clear()
        search = self.bl_search.text().strip().lower()
        platform_filter = self.bl_platform_filter.currentData()
        builtin = self.app.settings.chat.builtin
        for user_key in builtin.blocked_users:
            if not self._matches_platform_filter(user_key, platform_filter):
                continue
            display = builtin.blocked_user_names.get(user_key, user_key)
            platform = self._format_platform_label(user_key)
            label = f"[{platform}]  {display}"
            if search and search not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, user_key)
            self.blocked_list.addItem(item)

    def _remove_blocked_users(self):
        builtin = self.app.settings.chat.builtin
        for item in self.blocked_list.selectedItems():
            user_key = item.data(Qt.ItemDataRole.UserRole)
            if user_key and user_key in builtin.blocked_users:
                builtin.blocked_users.remove(user_key)
            builtin.blocked_user_names.pop(user_key, None)
        self.app.save_settings()
        self._refresh_blocked_list()

    def _clear_all_blocked(self):
        if not self.app.settings.chat.builtin.blocked_users:
            return
        result = QMessageBox.question(
            self,
            "Clear All Blocked Users",
            "Remove all blocked users?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Ok:
            self.app.settings.chat.builtin.blocked_users.clear()
            self.app.settings.chat.builtin.blocked_user_names.clear()
            self.app.save_settings()
            self._refresh_blocked_list()

    # --- User Nicknames helpers ---

    def _refresh_nicknames_list(self):
        self.nicknames_list.clear()
        search = self.nn_search.text().strip().lower()
        platform_filter = self.nn_platform_filter.currentData()
        builtin = self.app.settings.chat.builtin
        for user_key, nickname in builtin.user_nicknames.items():
            if not self._matches_platform_filter(user_key, platform_filter):
                continue
            original = builtin.user_nickname_display_names.get(user_key, user_key)
            platform = self._format_platform_label(user_key)
            label = f"[{platform}]  {original} \u2192 {nickname}"
            if search and search not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, user_key)
            self.nicknames_list.addItem(item)

    def _add_nickname(self):
        """Add a nickname for a user via dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Nickname")
        dialog.setMinimumWidth(350)
        form = QFormLayout(dialog)

        platform_combo = QComboBox()
        for p in StreamPlatform:
            platform_combo.addItem(p.value.capitalize(), p.value)
        form.addRow("Platform:", platform_combo)

        username_edit = QLineEdit()
        username_edit.setPlaceholderText("e.g. ninja, pokimane")
        form.addRow("Username:", username_edit)

        nickname_edit = QLineEdit()
        nickname_edit.setPlaceholderText("Nickname to display")
        form.addRow("Nickname:", nickname_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            username = username_edit.text().strip()
            nickname = nickname_edit.text().strip()
            if username and nickname:
                platform = platform_combo.currentData()
                user_key = f"{platform}:{username}"
                self.app.settings.chat.builtin.user_nicknames[user_key] = nickname
                self.app.settings.chat.builtin.user_nickname_display_names[user_key] = username
                self.app.save_settings()
                self._refresh_nicknames_list()

    def _edit_nickname(self):
        """Edit the selected nickname."""
        items = self.nicknames_list.selectedItems()
        if not items:
            return
        user_key = items[0].data(Qt.ItemDataRole.UserRole)
        if not user_key:
            return
        builtin = self.app.settings.chat.builtin
        current = builtin.user_nicknames.get(user_key, "")
        display = builtin.user_nickname_display_names.get(user_key, user_key)
        text, ok = QInputDialog.getText(
            self, "Edit Nickname", f"Nickname for {display}:", text=current
        )
        if ok and text.strip():
            builtin.user_nicknames[user_key] = text.strip()
            self.app.save_settings()
            self._refresh_nicknames_list()

    def _remove_nicknames(self):
        builtin = self.app.settings.chat.builtin
        for item in self.nicknames_list.selectedItems():
            user_key = item.data(Qt.ItemDataRole.UserRole)
            if user_key:
                builtin.user_nicknames.pop(user_key, None)
                builtin.user_nickname_display_names.pop(user_key, None)
        self.app.save_settings()
        self._refresh_nicknames_list()

    # --- User Notes helpers ---

    def _refresh_notes_list(self):
        self.notes_list.clear()
        search = self.nt_search.text().strip().lower()
        platform_filter = self.nt_platform_filter.currentData()
        builtin = self.app.settings.chat.builtin
        for user_key, note in builtin.user_notes.items():
            if not self._matches_platform_filter(user_key, platform_filter):
                continue
            display = builtin.user_note_display_names.get(user_key, user_key)
            platform = self._format_platform_label(user_key)
            truncated = note if len(note) <= 60 else note[:57] + "\u2026"
            label = f"[{platform}]  {display}: {truncated}"
            if search and search not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, user_key)
            self.notes_list.addItem(item)

    def _add_note(self):
        """Add a note for a user via dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Add User Note")
        dialog.setMinimumWidth(350)
        form = QFormLayout(dialog)

        platform_combo = QComboBox()
        for p in StreamPlatform:
            platform_combo.addItem(p.value.capitalize(), p.value)
        form.addRow("Platform:", platform_combo)

        username_edit = QLineEdit()
        username_edit.setPlaceholderText("e.g. ninja, pokimane")
        form.addRow("Username:", username_edit)

        note_edit = QLineEdit()
        note_edit.setPlaceholderText("Note text")
        form.addRow("Note:", note_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            username = username_edit.text().strip()
            note = note_edit.text().strip()
            if username and note:
                platform = platform_combo.currentData()
                user_key = f"{platform}:{username}"
                self.app.settings.chat.builtin.user_notes[user_key] = note
                self.app.settings.chat.builtin.user_note_display_names[user_key] = username
                self.app.save_settings()
                self._refresh_notes_list()

    def _edit_note(self):
        """Edit the selected note."""
        items = self.notes_list.selectedItems()
        if not items:
            return
        user_key = items[0].data(Qt.ItemDataRole.UserRole)
        if not user_key:
            return
        builtin = self.app.settings.chat.builtin
        current = builtin.user_notes.get(user_key, "")
        display = builtin.user_note_display_names.get(user_key, user_key)
        text, ok = QInputDialog.getText(self, "Edit Note", f"Note for {display}:", text=current)
        if ok and text.strip():
            builtin.user_notes[user_key] = text.strip()
            self.app.save_settings()
            self._refresh_notes_list()

    def _remove_notes(self):
        builtin = self.app.settings.chat.builtin
        for item in self.notes_list.selectedItems():
            user_key = item.data(Qt.ItemDataRole.UserRole)
            if user_key:
                builtin.user_notes.pop(user_key, None)
                builtin.user_note_display_names.pop(user_key, None)
        self.app.save_settings()
        self._refresh_notes_list()
