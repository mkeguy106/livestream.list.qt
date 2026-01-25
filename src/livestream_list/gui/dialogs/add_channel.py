"""Dialog for adding channels manually or importing from Twitch."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.models import StreamPlatform

if TYPE_CHECKING:
    from ..app import Application


class AddChannelDialog(QDialog):
    """Dialog for adding channels manually or importing from Twitch."""

    def __init__(self, parent, app: Application):
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
        patterns = ["twitch.tv", "youtube.com", "youtu.be", "kick.com"]
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
            r"(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]+)",
        ]
        for pattern in twitch_patterns:
            match = re.match(pattern, text, re.IGNORECASE)
            if match:
                return (StreamPlatform.TWITCH, match.group(1))

        # YouTube
        youtube_patterns = [
            r"(?:https?://)?(?:www\.)?youtube\.com/@([a-zA-Z0-9_-]+)",
            r"(?:https?://)?(?:www\.)?youtube\.com/c/([a-zA-Z0-9_-]+)",
            r"(?:https?://)?(?:www\.)?youtube\.com/channel/([a-zA-Z0-9_-]+)",
            r"(?:https?://)?(?:www\.)?youtube\.com/user/([a-zA-Z0-9_-]+)",
            r"(?:https?://)?(?:www\.)?youtube\.com/([a-zA-Z0-9_-]+)",
        ]
        for pattern in youtube_patterns:
            match = re.match(pattern, text, re.IGNORECASE)
            if match:
                channel = match.group(1)
                if not channel.startswith("@") and not channel.startswith("UC"):
                    channel = "@" + channel
                return (StreamPlatform.YOUTUBE, channel)

        # Kick
        kick_patterns = [
            r"(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)",
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

        from ..app import AsyncWorker

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
            self.twitch_status_label.setText("<p style='color: gray;'>Not logged in to Twitch</p>")
            self.twitch_login_btn.show()
            self.twitch_import_btn.hide()
            self.twitch_logout_btn.hide()

    def _on_twitch_login(self):
        """Handle Twitch login."""
        from .import_follows import ImportFollowsDialog

        dialog = ImportFollowsDialog(self, self.app, StreamPlatform.TWITCH)
        dialog.exec()
        self._update_twitch_status()

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

    def _on_import_follows(self):
        """Handle import follows."""
        from .import_follows import ImportFollowsDialog

        dialog = ImportFollowsDialog(self, self.app, StreamPlatform.TWITCH, start_import=True)
        dialog.exec()

        # Suppress notifications during import refresh
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
        self.app.save_settings()
        self._update_twitch_status()
