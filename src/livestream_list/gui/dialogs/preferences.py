"""Preferences dialog with multiple tabs for application settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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

        # Accounts tab
        accounts_tab = self._create_accounts_tab()
        tabs.addTab(accounts_tab, "Accounts")

        if initial_tab:
            tabs.setCurrentIndex(initial_tab)

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        apply_btn = buttons.addButton(QDialogButtonBox.Apply)
        apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(buttons)

        self._loading = False  # Init complete, allow updates

    def _on_apply(self):
        """Explicitly save all current settings."""
        self.app.save_settings()

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
        self.chat_user_card_hover_cb.setChecked(
            self.app.settings.chat.builtin.user_card_hover
        )
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

        # Theme color selector - allows editing dark or light mode colors
        builtin_layout.addRow(QLabel("<b>Theme Colors</b>"))
        self.color_theme_combo = QComboBox()
        self.color_theme_combo.addItem("Dark Mode", "dark")
        self.color_theme_combo.addItem("Light Mode", "light")
        self.color_theme_combo.currentIndexChanged.connect(self._on_color_theme_changed)
        builtin_layout.addRow("Edit colors for:", self.color_theme_combo)

        # Even row color picker
        even_color_row = QHBoxLayout()
        self.alt_row_even_swatch = QPushButton()
        self.alt_row_even_swatch.setFixedSize(24, 24)
        self.alt_row_even_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        even_color_row.addWidget(self.alt_row_even_swatch)
        self.alt_row_even_edit = QLineEdit()
        self.alt_row_even_edit.setText(
            self.app.settings.chat.builtin.dark_colors.alt_row_color_even
        )
        self.alt_row_even_edit.setMaximumWidth(100)
        self.alt_row_even_edit.editingFinished.connect(self._on_chat_changed)
        self.alt_row_even_edit.textChanged.connect(
            lambda t: self._update_swatch(self.alt_row_even_swatch, t)
        )
        even_color_row.addWidget(self.alt_row_even_edit)
        self.alt_row_even_reset = QPushButton("Reset")
        self.alt_row_even_reset.setFixedWidth(50)
        self.alt_row_even_reset.clicked.connect(
            lambda: self._reset_color(self.alt_row_even_edit, "#00000000")
        )
        even_color_row.addWidget(self.alt_row_even_reset)
        even_color_row.addStretch()
        self.alt_row_even_swatch.clicked.connect(
            lambda: self._pick_color_alpha(self.alt_row_even_edit, self.alt_row_even_swatch)
        )
        self._update_swatch(self.alt_row_even_swatch, self.alt_row_even_edit.text())
        self.alt_row_even_label = QLabel("Even row color:")
        builtin_layout.addRow(self.alt_row_even_label, even_color_row)

        # Odd row color picker
        odd_color_row = QHBoxLayout()
        self.alt_row_odd_swatch = QPushButton()
        self.alt_row_odd_swatch.setFixedSize(24, 24)
        self.alt_row_odd_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        odd_color_row.addWidget(self.alt_row_odd_swatch)
        self.alt_row_odd_edit = QLineEdit()
        self.alt_row_odd_edit.setText(self.app.settings.chat.builtin.dark_colors.alt_row_color_odd)
        self.alt_row_odd_edit.setMaximumWidth(100)
        self.alt_row_odd_edit.editingFinished.connect(self._on_chat_changed)
        self.alt_row_odd_edit.textChanged.connect(
            lambda t: self._update_swatch(self.alt_row_odd_swatch, t)
        )
        odd_color_row.addWidget(self.alt_row_odd_edit)
        self.alt_row_odd_reset = QPushButton("Reset")
        self.alt_row_odd_reset.setFixedWidth(50)
        self.alt_row_odd_reset.clicked.connect(
            lambda: self._reset_color(self.alt_row_odd_edit, "#0fffffff")
        )
        odd_color_row.addWidget(self.alt_row_odd_reset)
        odd_color_row.addStretch()
        self.alt_row_odd_swatch.clicked.connect(
            lambda: self._pick_color_alpha(self.alt_row_odd_edit, self.alt_row_odd_swatch)
        )
        self._update_swatch(self.alt_row_odd_swatch, self.alt_row_odd_edit.text())
        self.alt_row_odd_label = QLabel("Odd row color:")
        builtin_layout.addRow(self.alt_row_odd_label, odd_color_row)

        # Show/hide color pickers based on checkbox state
        alt_visible = self.chat_alt_rows_cb.isChecked()
        for w in (
            self.alt_row_even_label,
            self.alt_row_even_swatch,
            self.alt_row_even_edit,
            self.alt_row_even_reset,
            self.alt_row_odd_label,
            self.alt_row_odd_swatch,
            self.alt_row_odd_edit,
            self.alt_row_odd_reset,
        ):
            w.setVisible(alt_visible)
        self.chat_alt_rows_cb.stateChanged.connect(self._toggle_alt_row_colors)

        # Tab active color with swatch
        active_row = QHBoxLayout()
        self.tab_active_swatch = QPushButton()
        self.tab_active_swatch.setFixedSize(24, 24)
        self.tab_active_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        active_row.addWidget(self.tab_active_swatch)
        self.tab_active_color_edit = QLineEdit()
        self.tab_active_color_edit.setText(
            self.app.settings.chat.builtin.dark_colors.tab_active_color
        )
        self.tab_active_color_edit.setMaximumWidth(100)
        self.tab_active_color_edit.editingFinished.connect(self._on_chat_changed)
        self.tab_active_color_edit.textChanged.connect(
            lambda t: self._update_swatch(self.tab_active_swatch, t)
        )
        active_row.addWidget(self.tab_active_color_edit)
        self.tab_active_reset = QPushButton("Reset")
        self.tab_active_reset.setFixedWidth(50)
        self.tab_active_reset.clicked.connect(
            lambda: self._reset_color(self.tab_active_color_edit, "#6441a5")
        )
        active_row.addWidget(self.tab_active_reset)
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
        self.tab_inactive_color_edit.setText(
            self.app.settings.chat.builtin.dark_colors.tab_inactive_color
        )
        self.tab_inactive_color_edit.setMaximumWidth(100)
        self.tab_inactive_color_edit.editingFinished.connect(self._on_chat_changed)
        self.tab_inactive_color_edit.textChanged.connect(
            lambda t: self._update_swatch(self.tab_inactive_swatch, t)
        )
        inactive_row.addWidget(self.tab_inactive_color_edit)
        self.tab_inactive_reset = QPushButton("Reset")
        self.tab_inactive_reset.setFixedWidth(50)
        self.tab_inactive_reset.clicked.connect(
            lambda: self._reset_color(self.tab_inactive_color_edit, "#16213e")
        )
        inactive_row.addWidget(self.tab_inactive_reset)
        inactive_row.addStretch()
        self.tab_inactive_swatch.clicked.connect(
            lambda: self._pick_color(self.tab_inactive_color_edit, self.tab_inactive_swatch)
        )
        self._update_swatch(self.tab_inactive_swatch, self.tab_inactive_color_edit.text())
        builtin_layout.addRow("Tab inactive color:", inactive_row)

        # Mention highlight color picker
        mention_row = QHBoxLayout()
        self.mention_color_swatch = QPushButton()
        self.mention_color_swatch.setFixedSize(24, 24)
        self.mention_color_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        mention_row.addWidget(self.mention_color_swatch)
        self.mention_color_edit = QLineEdit()
        self.mention_color_edit.setText(
            self.app.settings.chat.builtin.dark_colors.mention_highlight_color
        )
        self.mention_color_edit.setMaximumWidth(100)
        self.mention_color_edit.editingFinished.connect(self._on_chat_changed)
        self.mention_color_edit.textChanged.connect(
            lambda t: self._update_swatch(self.mention_color_swatch, t)
        )
        mention_row.addWidget(self.mention_color_edit)
        self.mention_color_reset = QPushButton("Reset")
        self.mention_color_reset.setFixedWidth(50)
        self.mention_color_reset.clicked.connect(
            lambda: self._reset_color(self.mention_color_edit, "#33ff8800")
        )
        mention_row.addWidget(self.mention_color_reset)
        mention_row.addStretch()
        self.mention_color_swatch.clicked.connect(
            lambda: self._pick_color_alpha(self.mention_color_edit, self.mention_color_swatch)
        )
        self._update_swatch(self.mention_color_swatch, self.mention_color_edit.text())
        builtin_layout.addRow("Mention highlight:", mention_row)

        # Banner settings separator
        builtin_layout.addRow(QLabel("<b>Chat Banners</b>"))

        # Show stream title toggle
        self.show_stream_title_cb = QCheckBox("Show stream title banner")
        self.show_stream_title_cb.setChecked(self.app.settings.chat.builtin.show_stream_title)
        self.show_stream_title_cb.stateChanged.connect(self._on_chat_changed)
        self.show_stream_title_cb.stateChanged.connect(self._toggle_banner_colors)
        builtin_layout.addRow(self.show_stream_title_cb)

        # Show socials toggle
        self.show_socials_cb = QCheckBox("Show channel socials banner")
        self.show_socials_cb.setChecked(self.app.settings.chat.builtin.show_socials_banner)
        self.show_socials_cb.stateChanged.connect(self._on_chat_changed)
        self.show_socials_cb.stateChanged.connect(self._toggle_banner_colors)
        builtin_layout.addRow(self.show_socials_cb)

        # Banner background color picker with reset button
        banner_bg_row = QHBoxLayout()
        self.banner_bg_swatch = QPushButton()
        self.banner_bg_swatch.setFixedSize(24, 24)
        self.banner_bg_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        banner_bg_row.addWidget(self.banner_bg_swatch)
        self.banner_bg_edit = QLineEdit()
        self.banner_bg_edit.setText(self.app.settings.chat.builtin.dark_colors.banner_bg_color)
        self.banner_bg_edit.setMaximumWidth(100)
        self.banner_bg_edit.editingFinished.connect(self._on_chat_changed)
        self.banner_bg_edit.textChanged.connect(
            lambda t: self._update_swatch(self.banner_bg_swatch, t)
        )
        banner_bg_row.addWidget(self.banner_bg_edit)
        self.banner_bg_reset = QPushButton("Reset")
        self.banner_bg_reset.setFixedWidth(50)
        self.banner_bg_reset.clicked.connect(
            lambda: self._reset_color(self.banner_bg_edit, "#16213e")
        )
        banner_bg_row.addWidget(self.banner_bg_reset)
        banner_bg_row.addStretch()
        self.banner_bg_swatch.clicked.connect(
            lambda: self._pick_color(self.banner_bg_edit, self.banner_bg_swatch)
        )
        self._update_swatch(self.banner_bg_swatch, self.banner_bg_edit.text())
        self.banner_bg_label = QLabel("Banner background:")
        builtin_layout.addRow(self.banner_bg_label, banner_bg_row)

        # Banner text color picker with reset button
        banner_text_row = QHBoxLayout()
        self.banner_text_swatch = QPushButton()
        self.banner_text_swatch.setFixedSize(24, 24)
        self.banner_text_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        banner_text_row.addWidget(self.banner_text_swatch)
        self.banner_text_edit = QLineEdit()
        self.banner_text_edit.setText(self.app.settings.chat.builtin.dark_colors.banner_text_color)
        self.banner_text_edit.setMaximumWidth(100)
        self.banner_text_edit.editingFinished.connect(self._on_chat_changed)
        self.banner_text_edit.textChanged.connect(
            lambda t: self._update_swatch(self.banner_text_swatch, t)
        )
        banner_text_row.addWidget(self.banner_text_edit)
        self.banner_text_reset = QPushButton("Reset")
        self.banner_text_reset.setFixedWidth(50)
        self.banner_text_reset.clicked.connect(
            lambda: self._reset_color(self.banner_text_edit, "#cccccc")
        )
        banner_text_row.addWidget(self.banner_text_reset)
        banner_text_row.addStretch()
        self.banner_text_swatch.clicked.connect(
            lambda: self._pick_color(self.banner_text_edit, self.banner_text_swatch)
        )
        self._update_swatch(self.banner_text_swatch, self.banner_text_edit.text())
        self.banner_text_label = QLabel("Banner text:")
        builtin_layout.addRow(self.banner_text_label, banner_text_row)

        # Show/hide banner color pickers based on checkbox states
        banner_visible = self.show_stream_title_cb.isChecked() or self.show_socials_cb.isChecked()
        for w in (
            self.banner_bg_label,
            self.banner_bg_swatch,
            self.banner_bg_edit,
            self.banner_bg_reset,
            self.banner_text_label,
            self.banner_text_swatch,
            self.banner_text_edit,
            self.banner_text_reset,
        ):
            w.setVisible(banner_visible)

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
        self.bl_platform_filter.currentIndexChanged.connect(
            lambda: self._refresh_blocked_list()
        )
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
        self.nn_platform_filter.currentIndexChanged.connect(
            lambda: self._refresh_nicknames_list()
        )
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
        self.nt_platform_filter.currentIndexChanged.connect(
            lambda: self._refresh_notes_list()
        )
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

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(lambda: self._reset_tab_defaults("Chat"))
        layout.addWidget(reset_btn, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()
        scroll.setWidget(widget)
        return scroll

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
        self.app.settings.chat.builtin.timestamp_format = (
            self.chat_ts_format_combo.currentData()
        )
        self.app.settings.chat.builtin.show_badges = self.chat_badges_cb.isChecked()
        self.app.settings.chat.builtin.show_mod_badges = self.chat_mod_badges_cb.isChecked()
        self.app.settings.chat.builtin.show_emotes = self.chat_emotes_cb.isChecked()
        self.app.settings.chat.builtin.animate_emotes = self.chat_animate_emotes_cb.isChecked()
        self.app.settings.chat.builtin.show_alternating_rows = self.chat_alt_rows_cb.isChecked()
        self.app.settings.chat.builtin.show_metrics = self.chat_metrics_cb.isChecked()
        self.app.settings.chat.builtin.spellcheck_enabled = self.chat_spellcheck_cb.isChecked()
        self.app.settings.chat.builtin.user_card_hover = (
            self.chat_user_card_hover_cb.isChecked()
        )
        self.app.settings.chat.builtin.moderated_message_display = (
            self.moderated_display_combo.currentData()
        )
        self.app.settings.chat.builtin.use_platform_name_colors = (
            self.chat_name_colors_cb.isChecked()
        )
        # Banner settings
        self.app.settings.chat.builtin.show_stream_title = self.show_stream_title_cb.isChecked()
        self.app.settings.chat.builtin.show_socials_banner = self.show_socials_cb.isChecked()

        # Save color settings to the appropriate theme (dark or light)
        is_dark = self.color_theme_combo.currentData() == "dark"
        colors = (
            self.app.settings.chat.builtin.dark_colors
            if is_dark
            else self.app.settings.chat.builtin.light_colors
        )
        colors.alt_row_color_even = self.alt_row_even_edit.text().strip() or "#00000000"
        colors.alt_row_color_odd = self.alt_row_odd_edit.text().strip() or "#0fffffff"
        colors.tab_active_color = self.tab_active_color_edit.text().strip() or "#6441a5"
        colors.tab_inactive_color = self.tab_inactive_color_edit.text().strip() or (
            "#16213e" if is_dark else "#e0e0e8"
        )
        colors.mention_highlight_color = self.mention_color_edit.text().strip() or "#33ff8800"
        colors.banner_bg_color = self.banner_bg_edit.text().strip() or (
            "#16213e" if is_dark else "#e8e8f0"
        )
        colors.banner_text_color = self.banner_text_edit.text().strip() or (
            "#cccccc" if is_dark else "#333333"
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
        # Live-update chat window if open
        if self.app._chat_window:
            self.app._chat_window.update_tab_style()
            self.app._chat_window.update_animation_state()
            self.app._chat_window.update_banner_settings()
            self.app._chat_window.update_metrics_bar()
            self.app._chat_window.update_spellcheck()

    def _on_color_theme_changed(self) -> None:
        """Reload color pickers when switching between dark/light mode editing."""
        is_dark = self.color_theme_combo.currentData() == "dark"
        colors = (
            self.app.settings.chat.builtin.dark_colors
            if is_dark
            else self.app.settings.chat.builtin.light_colors
        )

        # Prevent cascading updates while loading new values
        self._loading = True
        try:
            # Update all color pickers with the selected theme's colors
            self.alt_row_even_edit.setText(colors.alt_row_color_even)
            self.alt_row_odd_edit.setText(colors.alt_row_color_odd)
            self.tab_active_color_edit.setText(colors.tab_active_color)
            self.tab_inactive_color_edit.setText(colors.tab_inactive_color)
            self.mention_color_edit.setText(colors.mention_highlight_color)
            self.banner_bg_edit.setText(colors.banner_bg_color)
            self.banner_text_edit.setText(colors.banner_text_color)

            # Update swatches
            self._update_swatch(self.alt_row_even_swatch, colors.alt_row_color_even)
            self._update_swatch(self.alt_row_odd_swatch, colors.alt_row_color_odd)
            self._update_swatch(self.tab_active_swatch, colors.tab_active_color)
            self._update_swatch(self.tab_inactive_swatch, colors.tab_inactive_color)
            self._update_swatch(self.mention_color_swatch, colors.mention_highlight_color)
            self._update_swatch(self.banner_bg_swatch, colors.banner_bg_color)
            self._update_swatch(self.banner_text_swatch, colors.banner_text_color)
        finally:
            self._loading = False

    def _update_swatch(self, button: QPushButton, hex_color: str) -> None:
        """Update a color swatch button's background from a hex string."""
        color = QColor(hex_color)
        if color.isValid():
            if color.alpha() < 255:
                css_color = (
                    f"rgba({color.red()}, {color.green()}, {color.blue()}, "
                    f"{color.alpha() / 255:.2f})"
                )
            else:
                css_color = hex_color
            button.setStyleSheet(
                f"background-color: {css_color}; border: 1px solid #666; border-radius: 3px;"
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

    def _pick_color_alpha(self, line_edit: QLineEdit, swatch: QPushButton) -> None:
        """Open a color picker with alpha channel and update the line edit and swatch."""
        text = line_edit.text().strip()
        current = None
        # Parse #AARRGGBB format (8 hex digits) manually since QColor doesn't handle it well
        if text.startswith("#") and len(text) == 9:
            try:
                a = int(text[1:3], 16)
                r = int(text[3:5], 16)
                g = int(text[5:7], 16)
                b = int(text[7:9], 16)
                current = QColor(r, g, b, a)
            except ValueError:
                pass
        if current is None or not current.isValid():
            # Try standard QColor parsing for #RRGGBB format
            current = QColor(text)
        if not current.isValid():
            current = QColor(255, 255, 255, 15)
        color = QColorDialog.getColor(
            current,
            self,
            "Pick a color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            # If user picked a color but alpha is very low (nearly invisible), assume they
            # want it visible. This handles cases where the starting color was transparent
            # or nearly transparent (like the default #0fffffff with alpha=15).
            a = color.alpha()
            if a < 32:
                a = 255  # Make it fully opaque
            r, g, b = color.red(), color.green(), color.blue()
            # Format as #AARRGGBB for Qt
            hex_color = f"#{a:02x}{r:02x}{g:02x}{b:02x}"
            line_edit.setText(hex_color)
            self._update_swatch(swatch, hex_color)
            self._on_chat_changed()

    def _toggle_alt_row_colors(self, state):
        """Show/hide alternating row color pickers based on checkbox state."""
        visible = bool(state)
        for w in (
            self.alt_row_even_label,
            self.alt_row_even_swatch,
            self.alt_row_even_edit,
            self.alt_row_even_reset,
            self.alt_row_odd_label,
            self.alt_row_odd_swatch,
            self.alt_row_odd_edit,
            self.alt_row_odd_reset,
        ):
            w.setVisible(visible)

    def _toggle_banner_colors(self, _state=None):
        """Show/hide banner color pickers based on checkbox states."""
        visible = self.show_stream_title_cb.isChecked() or self.show_socials_cb.isChecked()
        for w in (
            self.banner_bg_label,
            self.banner_bg_swatch,
            self.banner_bg_edit,
            self.banner_bg_reset,
            self.banner_text_label,
            self.banner_text_swatch,
            self.banner_text_edit,
            self.banner_text_reset,
        ):
            w.setVisible(visible)

    def _reset_color(self, edit: QLineEdit, default_value: str):
        """Reset a color edit field to its default value."""
        edit.setText(default_value)
        self._on_chat_changed()

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
            self.chat_ts_format_combo.setCurrentIndex(
                0 if builtin.timestamp_format == "24h" else 1
            )
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
            # Color settings (use defaults for the currently selected color theme)
            is_dark = self.color_theme_combo.currentData() == "dark"
            colors = builtin.dark_colors if is_dark else builtin.light_colors
            self.alt_row_even_edit.setText(colors.alt_row_color_even)
            self.alt_row_odd_edit.setText(colors.alt_row_color_odd)
            self.tab_active_color_edit.setText(colors.tab_active_color)
            self.tab_inactive_color_edit.setText(colors.tab_inactive_color)
            self.mention_color_edit.setText(colors.mention_highlight_color)
            # Banner settings
            self.show_stream_title_cb.setChecked(builtin.show_stream_title)
            self.show_socials_cb.setChecked(builtin.show_socials_banner)
            self.banner_bg_edit.setText(colors.banner_bg_color)
            self.banner_text_edit.setText(colors.banner_text_color)
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
        text, ok = QInputDialog.getText(
            self, "Edit Note", f"Note for {display}:", text=current
        )
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
