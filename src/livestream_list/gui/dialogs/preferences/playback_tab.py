"""Playback tab for Preferences dialog."""

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
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .dialog import PreferencesDialog

logger = logging.getLogger(__name__)


class PlaybackTab(QScrollArea):
    """Playback / Streamlink settings tab."""

    def __init__(self, dialog: PreferencesDialog, parent: QWidget | None = None):
        super().__init__(parent)
        self.dialog = dialog
        self.app = dialog.app
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
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

        self.twitch_turbo_cb = QCheckBox("Include Twitch OAuth for Turbo/subscriber benefits")
        self.twitch_turbo_cb.setChecked(self.app.settings.streamlink.twitch_turbo)
        if not self.app.settings.twitch.access_token:
            self.twitch_turbo_cb.setEnabled(False)
            self.twitch_turbo_cb.setToolTip("Login to Twitch in Accounts tab first")
        self.twitch_turbo_cb.toggled.connect(self._on_streamlink_changed)
        sl_layout.addRow("", self.twitch_turbo_cb)

        self.show_console_cb = QCheckBox("Show streamlink console output")
        self.show_console_cb.setChecked(self.app.settings.streamlink.show_console)
        self.show_console_cb.setToolTip(
            "Open a window showing streamlink/yt-dlp output when launching a stream"
        )
        self.show_console_cb.toggled.connect(self._on_streamlink_changed)
        sl_layout.addRow("", self.show_console_cb)

        self.auto_close_console_cb = QCheckBox("Auto-close console when stream ends")
        self.auto_close_console_cb.setChecked(self.app.settings.streamlink.auto_close_console)
        self.auto_close_console_cb.setToolTip(
            "Automatically close the console window when the streamlink process exits"
        )
        self.auto_close_console_cb.toggled.connect(self._on_streamlink_changed)
        sl_layout.addRow("", self.auto_close_console_cb)

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

        # Recording group
        record_group = QGroupBox("Recording")
        record_layout = QFormLayout(record_group)

        self.record_streams_cb = QCheckBox("Record streams to disk")
        self.record_streams_cb.setChecked(self.app.settings.streamlink.record_streams)
        self.record_streams_cb.setToolTip(
            "Save a copy of the stream to disk while watching (streamlink only)"
        )
        self.record_streams_cb.toggled.connect(self._on_streamlink_changed)
        record_layout.addRow("", self.record_streams_cb)

        dir_row = QHBoxLayout()
        self.record_dir_edit = QLineEdit()
        self.record_dir_edit.setText(self.app.settings.streamlink.record_directory)
        self.record_dir_edit.setPlaceholderText("Select a directory...")
        self.record_dir_edit.textChanged.connect(self._on_streamlink_changed)
        dir_row.addWidget(self.record_dir_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._on_browse_record_directory)
        dir_row.addWidget(browse_btn)
        record_layout.addRow("Save to:", dir_row)

        layout.addWidget(record_group)

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
        reset_btn.clicked.connect(self.reset_defaults)
        layout.addWidget(reset_btn, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()
        self.setWidget(widget)

    # --- Public method for cross-tab interaction ---

    def update_twitch_turbo_state(self, is_logged_in: bool) -> None:
        """Enable/disable the Twitch Turbo checkbox based on login state."""
        self.twitch_turbo_cb.setEnabled(is_logged_in)
        if is_logged_in:
            self.twitch_turbo_cb.setToolTip("")
        else:
            self.twitch_turbo_cb.setToolTip("Login to Twitch in Accounts tab first")

    # --- Callbacks ---

    def _on_streamlink_changed(self):
        self.app.settings.streamlink.path = self.sl_path_edit.text()
        self.app.settings.streamlink.additional_args = self.sl_args_edit.text()
        self.app.settings.streamlink.player = self.player_path_edit.text()
        self.app.settings.streamlink.player_args = self.player_args_edit.text()
        self.app.settings.streamlink.twitch_turbo = self.twitch_turbo_cb.isChecked()
        self.app.settings.streamlink.show_console = self.show_console_cb.isChecked()
        self.app.settings.streamlink.auto_close_console = self.auto_close_console_cb.isChecked()
        self.app.settings.streamlink.record_streams = self.record_streams_cb.isChecked()
        self.app.settings.streamlink.record_directory = self.record_dir_edit.text()
        self.app.save_settings()

    def _on_browse_record_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select Recording Directory", self.record_dir_edit.text()
        )
        if directory:
            self.record_dir_edit.setText(directory)

    def _on_launch_method_changed(self):
        from ....core.models import LaunchMethod

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

    def reset_defaults(self) -> None:
        """Reset Playback tab settings to defaults."""
        from ....core.settings import StreamlinkSettings

        result = QMessageBox.question(
            self,
            "Reset to Defaults",
            "Reset all Playback settings to their default values?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result != QMessageBox.StandardButton.Ok:
            return

        defaults = StreamlinkSettings()
        self.sl_path_edit.setText(defaults.path)
        self.sl_args_edit.setText(defaults.additional_args)
        self.player_path_edit.setText(defaults.player)
        self.player_args_edit.setText(defaults.player_args)
        self.twitch_turbo_cb.setChecked(defaults.twitch_turbo)
        self.show_console_cb.setChecked(defaults.show_console)
        self.auto_close_console_cb.setChecked(defaults.auto_close_console)
        self.record_streams_cb.setChecked(defaults.record_streams)
        self.record_dir_edit.setText(defaults.record_directory)
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
