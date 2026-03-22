"""General tab for Preferences dialog."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ....core.autostart import is_autostart_enabled, set_autostart
from ....core.models import Channel, Livestream, StreamPlatform, UIStyle

if TYPE_CHECKING:
    from .dialog import PreferencesDialog

logger = logging.getLogger(__name__)

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


class GeneralTab(QScrollArea):
    """General settings tab."""

    def __init__(self, dialog: PreferencesDialog, parent: QWidget | None = None):
        super().__init__(parent)
        self.dialog = dialog
        self.app = dialog.app
        self._setup_ui()

    @property
    def _loading(self) -> bool:
        return self.dialog._loading

    def _setup_ui(self) -> None:
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

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

        # UI Style group
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

        self.notif_sound_cb = QCheckBox("Play sound for channels going live")
        self.notif_sound_cb.setChecked(self.app.settings.notifications.sound_enabled)
        self.notif_sound_cb.stateChanged.connect(self._on_notif_changed)
        notif_layout.addRow(self.notif_sound_cb)

        # Notification backend selector
        from ....core.platform import IS_WINDOWS

        self.notif_backend_combo = QComboBox()
        self.notif_backend_combo.addItem("Auto", "auto")
        self.notif_backend_combo.addItem("D-Bus", "dbus")
        if not IS_WINDOWS:
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
        self.notif_chaturbate_cb = QCheckBox("Chaturbate")
        self.notif_chaturbate_cb.setChecked("chaturbate" in pf)
        self.notif_chaturbate_cb.stateChanged.connect(self._on_notif_changed)
        platform_row = QHBoxLayout()
        platform_row.addWidget(self.notif_twitch_cb)
        platform_row.addWidget(self.notif_youtube_cb)
        platform_row.addWidget(self.notif_kick_cb)
        platform_row.addWidget(self.notif_chaturbate_cb)
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

        # Mention notifications
        self.notif_mention_cb = QCheckBox("Play sound for @mentions")
        self.notif_mention_cb.setChecked(
            self.app.settings.notifications.mention_notifications_enabled
        )
        self.notif_mention_cb.stateChanged.connect(self._on_notif_changed)
        notif_layout.addRow(self.notif_mention_cb)

        # Mention custom sound
        self.notif_mention_sound_path = QLineEdit()
        self.notif_mention_sound_path.setPlaceholderText("Default (bell.oga)")
        self.notif_mention_sound_path.setText(
            self.app.settings.notifications.mention_custom_sound_path
        )
        self.notif_mention_sound_path.textChanged.connect(self._on_notif_changed)
        mention_sound_browse_btn = QPushButton("Browse...")
        mention_sound_browse_btn.clicked.connect(self._on_browse_mention_sound)
        mention_sound_row = QHBoxLayout()
        mention_sound_row.addWidget(self.notif_mention_sound_path)
        mention_sound_row.addWidget(mention_sound_browse_btn)
        notif_layout.addRow("Mention sound:", mention_sound_row)

        # Test notification buttons
        self.test_live_btn = QPushButton("Test Live Sound")
        self.test_live_btn.clicked.connect(self._on_test_notification)
        self.test_mention_btn = QPushButton("Test Mention Sound")
        self.test_mention_btn.clicked.connect(self._on_test_mention_notification)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_live_btn)
        test_row.addWidget(self.test_mention_btn)
        notif_layout.addRow(test_row)

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
        reset_btn.clicked.connect(self.reset_defaults)
        layout.addWidget(reset_btn, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()
        self.setWidget(widget)

    # --- Callbacks ---

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
        if self.notif_chaturbate_cb.isChecked():
            pf.append("chaturbate")
        notif.platform_filter = pf
        # Quiet hours
        notif.quiet_hours_enabled = self.notif_quiet_cb.isChecked()
        notif.quiet_hours_start = self.notif_quiet_start.time().toString("HH:mm")
        notif.quiet_hours_end = self.notif_quiet_end.time().toString("HH:mm")
        notif.raid_notifications_enabled = self.notif_raid_cb.isChecked()
        notif.mention_notifications_enabled = self.notif_mention_cb.isChecked()
        notif.mention_custom_sound_path = self.notif_mention_sound_path.text().strip()
        self.app.save_settings()

    def _on_style_changed(self, index):
        self.app.settings.ui_style = self.style_combo.currentData()
        self.app.save_settings()
        if self.dialog.parent():
            self.dialog.parent().refresh_stream_list()

    def _on_browse_notification_sound(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Notification Sound",
            "",
            "Audio Files (*.wav *.ogg *.mp3 *.flac *.opus);;All Files (*)",
        )
        if path:
            self.notif_sound_path.setText(path)

    def _on_browse_mention_sound(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Mention Sound",
            "",
            "Audio Files (*.wav *.ogg *.oga *.mp3 *.flac *.opus);;All Files (*)",
        )
        if path:
            self.notif_mention_sound_path.setText(path)

    def _on_notif_backend_changed(self, index):
        self.app.settings.notifications.backend = self.notif_backend_combo.currentData()
        self.app.save_settings()

    def _on_test_notification(self):
        """Send a test notification."""
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
        if self.app.notification_bridge:
            self.app.notification_bridge.send_test_notification(test_livestream)

    def _on_test_mention_notification(self):
        """Send a test @mention notification."""
        if self.app.notifier:
            self.app.notifier.test_mention_notification_sync()

    def _on_platform_colors_changed(self, state):
        self.app.settings.platform_colors = self.platform_colors_cb.isChecked()
        self.app.save_settings()
        if self.dialog.parent():
            self.dialog.parent().refresh_stream_list()

    def _on_channel_info_changed(self, state):
        self.app.settings.channel_info.show_live_duration = self.show_duration_cb.isChecked()
        self.app.settings.channel_info.show_viewers = self.show_viewers_cb.isChecked()
        self.app.save_settings()
        if self.app.main_window:
            self.app.main_window.refresh_stream_list()

    def _on_channel_icons_changed(self, state):
        self.app.settings.channel_icons.show_platform = self.show_platform_cb.isChecked()
        self.app.settings.channel_icons.show_play = self.show_play_cb.isChecked()
        self.app.settings.channel_icons.show_favorite = self.show_favorite_cb.isChecked()
        self.app.settings.channel_icons.show_chat = self.show_chat_cb.isChecked()
        self.app.settings.channel_icons.show_browser = self.show_browser_cb.isChecked()
        self.app.save_settings()
        if self.app.main_window:
            self.app.main_window.refresh_stream_list()

    def reset_defaults(self) -> None:
        """Reset General tab settings to defaults."""
        from ....core.settings import (
            ChannelIconSettings,
            ChannelInfoSettings,
            NotificationSettings,
            Settings,
        )

        result = QMessageBox.question(
            self,
            "Reset to Defaults",
            "Reset all General settings to their default values?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result != QMessageBox.StandardButton.Ok:
            return

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
        self.notif_chaturbate_cb.setChecked("chaturbate" in notif_defaults.platform_filter)
        self.notif_quiet_cb.setChecked(notif_defaults.quiet_hours_enabled)
        from PySide6.QtCore import QTime

        q_start = notif_defaults.quiet_hours_start.split(":")
        self.notif_quiet_start.setTime(QTime(int(q_start[0]), int(q_start[1])))
        q_end = notif_defaults.quiet_hours_end.split(":")
        self.notif_quiet_end.setTime(QTime(int(q_end[0]), int(q_end[1])))
        self.notif_raid_cb.setChecked(notif_defaults.raid_notifications_enabled)
        self.notif_mention_cb.setChecked(notif_defaults.mention_notifications_enabled)
        self.notif_mention_sound_path.setText(notif_defaults.mention_custom_sound_path)
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
