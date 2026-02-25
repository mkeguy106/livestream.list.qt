"""Accounts tab for Preferences dialog."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....core.models import StreamPlatform
from ..import_follows import ImportFollowsDialog
from ..youtube_import import YouTubeImportDialog

if TYPE_CHECKING:
    from .dialog import PreferencesDialog

logger = logging.getLogger(__name__)


class AccountsTab(QScrollArea):
    """Accounts settings tab."""

    def __init__(self, dialog: PreferencesDialog, parent: QWidget | None = None):
        super().__init__(parent)
        self.dialog = dialog
        self.app = dialog.app
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

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

        self.yt_auto_refresh_cb = QCheckBox("Automatically refresh cookies when expired")
        self.yt_auto_refresh_cb.setChecked(self.app.settings.youtube.cookie_auto_refresh)
        self.yt_auto_refresh_cb.toggled.connect(self._on_yt_auto_refresh_toggled)
        yt_layout.addWidget(self.yt_auto_refresh_cb)

        # YouTube handle (auto-detected on first chat connection)
        yt_handle_row = QFormLayout()
        yt_handle = self.app.settings.youtube.login_name
        self.yt_login_name_label = QLabel(f"@{yt_handle}" if yt_handle else "Not detected yet")
        self.yt_login_name_label.setStyleSheet("color: gray;")
        yt_handle_row.addRow("YouTube handle:", self.yt_login_name_label)
        yt_layout.addLayout(yt_handle_row)

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
        self.setWidget(widget)

    # --- Status helpers ---

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

        # Update Twitch Turbo checkbox availability via PlaybackTab
        self.dialog.playback_tab.update_twitch_turbo_state(is_logged_in)

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

    def _update_yt_status(self):
        """Update YouTube cookie status display."""
        from ....chat.connections.youtube import validate_cookies

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
        # Only show auto-refresh when cookies are configured and a browser is known
        has_browser = bool(self.app.settings.youtube.cookie_browser)
        self.yt_auto_refresh_cb.setVisible(is_configured and has_browser)

    # --- Twitch callbacks ---

    def _on_twitch_login(self):
        """Handle Twitch login."""
        dialog = ImportFollowsDialog(self, self.app, StreamPlatform.TWITCH)
        dialog.exec()
        self._update_twitch_status()
        self._update_account_buttons()

        # Extract browser auth-token cookie for streamlink Turbo
        if self.app.settings.twitch.access_token:
            from ...youtube_login import extract_twitch_auth_token

            token = extract_twitch_auth_token()
            if token:
                self.app.settings.twitch.browser_auth_token = token
                self.app.save_settings()
                logger.info("Extracted Twitch browser auth-token for streamlink")

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
        self.app.settings.twitch.browser_auth_token = ""
        self.app.settings.twitch.login_name = ""
        self.app.save_settings()
        self._update_twitch_status()
        self._update_account_buttons()

    def _on_import_follows(self):
        """Handle import follows."""
        dialog = ImportFollowsDialog(self, self.app, StreamPlatform.TWITCH, start_import=True)
        dialog.exec()

        # After dialog closes, refresh stream status with notifications suppressed
        added = getattr(dialog, "_added_count", 0)
        if added > 0:
            self.app.monitor.suppress_notifications()

            main_window = self.app.main_window

            def on_refresh_complete():
                self.app.monitor.resume_notifications()
                if main_window:
                    main_window.refresh_stream_list()

            if main_window:
                main_window.refresh_stream_list()

            self.app.refresh(on_complete=on_refresh_complete)

    # --- Kick callbacks ---

    def _on_kick_login(self):
        """Handle Kick OAuth login."""
        self.kick_status.setText("Status: Waiting for authorization...")
        self.kick_status.setStyleSheet("color: orange;")
        self.kick_login_btn.setEnabled(False)

        async def do_login():
            from ....chat.auth.kick_auth import KickAuthFlow

            auth = KickAuthFlow(self.app.settings.kick)
            success = await auth.authenticate(timeout=120)
            if success:
                self.app.save_settings()
            return success

        from ...app import AsyncWorker

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

    # --- YouTube callbacks ---

    def _on_yt_save_cookies(self):
        """Save YouTube cookies and validate."""
        from ....chat.connections.youtube import validate_cookies

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
        self.app.chat_manager.reconnect_youtube()

    def _on_yt_clear_cookies(self):
        """Clear YouTube cookies."""
        self.yt_cookies_edit.setPlainText("")
        self.app.settings.youtube.cookies = ""
        self.app.settings.youtube.cookie_browser = ""
        self.app.save_settings()
        self._update_yt_status()
        self.app.chat_manager.reconnect_youtube()

    def _on_yt_login(self):
        """Handle YouTube cookie import from browser."""
        from ...youtube_login import import_cookies_from_browser

        result = import_cookies_from_browser(self)
        if result:
            cookie_string, browser_id = result
            self.app.settings.youtube.cookies = cookie_string
            self.app.settings.youtube.cookie_browser = browser_id or ""
            self.app.save_settings()
            self.yt_cookies_edit.setPlainText(cookie_string)
            self._update_yt_status()
            self.app.chat_manager.reconnect_youtube()

    def _on_yt_auto_refresh_toggled(self, checked: bool):
        """Save auto-refresh preference."""
        self.app.settings.youtube.cookie_auto_refresh = checked
        self.app.save_settings()

    def _on_yt_import_subs(self):
        """Open YouTube subscription import dialog."""
        dialog = YouTubeImportDialog(self, self.app)
        dialog.exec()
        if dialog._added_count > 0 and self.dialog.parent():
            self.dialog.parent().refresh_stream_list()

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
