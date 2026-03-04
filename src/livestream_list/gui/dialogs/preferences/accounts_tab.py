"""Accounts tab for Preferences dialog."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....core.models import StreamPlatform
from ..import_follows import ImportFollowsDialog
from ..youtube_import import YouTubeImportDialog

# Persistent Chaturbate login window — never destroyed (Wayland workaround).
_chaturbate_login_window: _ChaturbateLoginWindow | None = None

# Persistent TikTok login window — never destroyed (Wayland workaround).
_tiktok_login_window: _TikTokLoginWindow | None = None

if TYPE_CHECKING:
    from .dialog import PreferencesDialog

logger = logging.getLogger(__name__)

# Persistent login window — never destroyed, only hidden.
# Destroying QWebEngineView on Wayland corrupts the focus state (QTBUG-73321).
_login_window: _YouTubeLoginWindow | None = None


class _YouTubeLoginWindow(QWidget):
    """Persistent top-level window for YouTube/Google sign-in.

    Uses the shared QWebEngineProfile. On login completion (or close),
    navigates to about:blank and hides — never destroyed.
    """

    login_finished = Signal(bool)  # True if login completed

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Sign in to YouTube")
        self.resize(500, 650)
        self.setWindowFlag(Qt.WindowType.Dialog)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        from ...chat.youtube_web_chat import _ensure_webengine, _get_shared_profile

        self._web_view = None
        if _ensure_webengine():
            from PySide6.QtWebEngineCore import QWebEnginePage
            from PySide6.QtWebEngineWidgets import QWebEngineView

            profile = _get_shared_profile()
            if profile is not None:
                page = QWebEnginePage(profile, self)
                self._web_view = QWebEngineView(self)
                self._web_view.setPage(page)
                self._web_view.urlChanged.connect(self._on_url_changed)
                layout.addWidget(self._web_view)

        self._active = False

    def start_login(self, parent: QWidget | None = None) -> None:
        """Load the Google login page and show the window."""
        if self._web_view is None:
            self.login_finished.emit(False)
            return
        self._active = True
        login_url = (
            "https://accounts.google.com/ServiceLogin"
            "?service=youtube&continue=https://www.youtube.com"
        )
        self._web_view.setUrl(QUrl(login_url))
        # Set Wayland transient parent BEFORE showing so the WM stacks us on top
        if parent:
            parent_handle = parent.window().windowHandle()
            if parent_handle:
                self.winId()  # Force native window handle creation
                self.windowHandle().setTransientParent(parent_handle)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_url_changed(self, url: QUrl) -> None:
        """Detect login completion (navigated to youtube.com)."""
        if self._active and url.host() == "www.youtube.com":
            self._finish(True)

    def _finish(self, success: bool) -> None:
        """Navigate to blank, hide, and emit result."""
        if not self._active:
            return
        self._active = False
        if self._web_view is not None:
            self._web_view.setUrl(QUrl("about:blank"))
        self.hide()
        self.login_finished.emit(success)

    def closeEvent(self, event) -> None:  # noqa: N802
        """Hide instead of closing — never destroy the QWebEngineView."""
        event.ignore()
        self._finish(False)


def _get_login_window() -> _YouTubeLoginWindow:
    """Get or create the persistent YouTube login window."""
    global _login_window
    if _login_window is None:
        _login_window = _YouTubeLoginWindow()
    return _login_window


class _ChaturbateLoginWindow(QWidget):
    """Persistent top-level window for Chaturbate sign-in.

    Uses a shared Chaturbate QWebEngineProfile. On login completion (or close),
    navigates to about:blank and hides — never destroyed (Wayland workaround).
    """

    login_finished = Signal(bool)  # True if login completed

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Sign in to Chaturbate")
        self.resize(500, 650)
        self.setWindowFlag(Qt.WindowType.Dialog)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        from ...chat.chaturbate_web_chat import _ensure_webengine, _get_shared_profile

        self._web_view = None
        if _ensure_webengine():
            from PySide6.QtWebEngineCore import QWebEnginePage
            from PySide6.QtWebEngineWidgets import QWebEngineView

            profile = _get_shared_profile()
            if profile is not None:
                page = QWebEnginePage(profile, self)
                self._web_view = QWebEngineView(self)
                self._web_view.setPage(page)
                self._web_view.urlChanged.connect(self._on_url_changed)
                layout.addWidget(self._web_view)

        self._active = False

    def start_login(self, parent: QWidget | None = None) -> None:
        """Load the Chaturbate login page and show the window."""
        if self._web_view is None:
            self.login_finished.emit(False)
            return
        self._active = True
        self._web_view.setUrl(QUrl("https://chaturbate.com/auth/login/"))
        if parent:
            parent_handle = parent.window().windowHandle()
            if parent_handle:
                self.winId()
                self.windowHandle().setTransientParent(parent_handle)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_url_changed(self, url: QUrl) -> None:
        """Detect login completion (navigated to chaturbate.com main page)."""
        if not self._active:
            return
        path = url.path()
        host = url.host()
        # After login, Chaturbate redirects to "/" or "/followed-cams/" etc.
        if host and "chaturbate.com" in host and path != "/auth/login/":
            # Check it's not another auth page
            if not path.startswith("/auth/"):
                self._finish(True)

    def _finish(self, success: bool) -> None:
        if not self._active:
            return
        self._active = False
        if self._web_view is not None:
            self._web_view.setUrl(QUrl("about:blank"))
        self.hide()
        self.login_finished.emit(success)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self._finish(False)


def _get_chaturbate_login_window() -> _ChaturbateLoginWindow:
    """Get or create the persistent Chaturbate login window."""
    global _chaturbate_login_window
    if _chaturbate_login_window is None:
        _chaturbate_login_window = _ChaturbateLoginWindow()
    return _chaturbate_login_window


class _TikTokLoginWindow(QWidget):
    """Persistent top-level window for TikTok sign-in.

    Uses the shared TikTok QWebEngineProfile. On login completion (or close),
    navigates to about:blank and hides — never destroyed (Wayland workaround).
    """

    login_finished = Signal(bool)  # True if login completed

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Sign in to TikTok")
        self.resize(500, 650)
        self.setWindowFlag(Qt.WindowType.Dialog)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        from ...chat.tiktok_web_chat import _ensure_webengine, _get_shared_profile

        self._web_view = None
        if _ensure_webengine():
            from PySide6.QtWebEngineCore import QWebEnginePage
            from PySide6.QtWebEngineWidgets import QWebEngineView

            profile = _get_shared_profile()
            if profile is not None:
                page = QWebEnginePage(profile, self)
                self._web_view = QWebEngineView(self)
                self._web_view.setPage(page)
                self._web_view.urlChanged.connect(self._on_url_changed)
                layout.addWidget(self._web_view)

        self._active = False

    def start_login(self, parent: QWidget | None = None) -> None:
        """Load the TikTok login page and show the window."""
        if self._web_view is None:
            self.login_finished.emit(False)
            return
        self._active = True
        self._web_view.setUrl(QUrl("https://www.tiktok.com/login"))
        if parent:
            parent_handle = parent.window().windowHandle()
            if parent_handle:
                self.winId()
                self.windowHandle().setTransientParent(parent_handle)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_url_changed(self, url: QUrl) -> None:
        """Detect login completion (navigated away from /login)."""
        if not self._active:
            return
        path = url.path()
        host = url.host()
        if host and "tiktok.com" in host and "/login" not in path:
            self._finish(True)

    def _finish(self, success: bool) -> None:
        if not self._active:
            return
        self._active = False
        if self._web_view is not None:
            self._web_view.setUrl(QUrl("about:blank"))
        self.hide()
        self.login_finished.emit(success)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self._finish(False)


def _get_tiktok_login_window() -> _TikTokLoginWindow:
    """Get or create the persistent TikTok login window."""
    global _tiktok_login_window
    if _tiktok_login_window is None:
        _tiktok_login_window = _TikTokLoginWindow()
    return _tiktok_login_window


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

        from ...chat.youtube_web_chat import _ensure_webengine

        self._yt_webengine_ok = _ensure_webengine()

        self.yt_status = QLabel("Status: Not logged in")
        self.yt_status.setStyleSheet("color: gray;")
        yt_layout.addWidget(self.yt_status)

        if not self._yt_webengine_ok:
            unavail_label = QLabel("YouTube sign-in unavailable (QWebEngine not installed)")
            unavail_label.setStyleSheet("color: orange;")
            yt_layout.addWidget(unavail_label)

        yt_buttons = QHBoxLayout()
        self.yt_login_btn = QPushButton("Sign in")
        self.yt_login_btn.clicked.connect(self._on_yt_login)
        self.yt_login_btn.setEnabled(self._yt_webengine_ok)
        yt_buttons.addWidget(self.yt_login_btn)
        self.yt_import_subs_btn = QPushButton("Import Subscriptions")
        self.yt_import_subs_btn.clicked.connect(self._on_yt_import_subs)
        yt_buttons.addWidget(self.yt_import_subs_btn)
        self.yt_logout_btn = QPushButton("Logout")
        self.yt_logout_btn.setStyleSheet("color: red;")
        self.yt_logout_btn.clicked.connect(self._on_yt_clear_cookies)
        yt_buttons.addWidget(self.yt_logout_btn)
        yt_buttons.addStretch()
        yt_layout.addLayout(yt_buttons)

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

        # Chaturbate group
        cb_group = QGroupBox("Chaturbate")
        cb_layout = QVBoxLayout(cb_group)

        self.cb_status = QLabel("Status: Not logged in")
        self.cb_status.setStyleSheet("color: gray;")
        cb_layout.addWidget(self.cb_status)

        from ...chat.chaturbate_web_chat import _ensure_webengine as _cb_ensure_webengine

        self._cb_webengine_ok = _cb_ensure_webengine()

        if not self._cb_webengine_ok:
            cb_unavail = QLabel("Chaturbate sign-in unavailable (QWebEngine not installed)")
            cb_unavail.setStyleSheet("color: orange;")
            cb_layout.addWidget(cb_unavail)

        cb_buttons = QHBoxLayout()
        self.cb_login_btn = QPushButton("Sign in")
        self.cb_login_btn.clicked.connect(self._on_cb_login)
        self.cb_login_btn.setEnabled(self._cb_webengine_ok)
        cb_buttons.addWidget(self.cb_login_btn)
        self.cb_import_btn = QPushButton("Import Follows")
        self.cb_import_btn.clicked.connect(self._on_cb_import_follows)
        cb_buttons.addWidget(self.cb_import_btn)
        self.cb_logout_btn = QPushButton("Logout")
        self.cb_logout_btn.setStyleSheet("color: red;")
        self.cb_logout_btn.clicked.connect(self._on_cb_logout)
        cb_buttons.addWidget(self.cb_logout_btn)
        cb_buttons.addStretch()
        cb_layout.addLayout(cb_buttons)

        layout.addWidget(cb_group)

        # TikTok group
        tk_group = QGroupBox("TikTok")
        tk_layout = QVBoxLayout(tk_group)

        self.tk_status = QLabel("Status: Not logged in")
        self.tk_status.setStyleSheet("color: gray;")
        tk_layout.addWidget(self.tk_status)

        from ...chat.tiktok_web_chat import _ensure_webengine as _tk_ensure_webengine

        self._tk_webengine_ok = _tk_ensure_webengine()

        if not self._tk_webengine_ok:
            tk_unavail = QLabel("TikTok sign-in unavailable (QWebEngine not installed)")
            tk_unavail.setStyleSheet("color: orange;")
            tk_layout.addWidget(tk_unavail)

        tk_note = QLabel(
            "<i>Login enables sending messages in TikTok LIVE chat. "
            "Monitoring and chat reading work without login.</i>"
        )
        tk_note.setStyleSheet("color: gray;")
        tk_note.setWordWrap(True)
        tk_layout.addWidget(tk_note)

        tk_buttons = QHBoxLayout()
        self.tk_login_btn = QPushButton("Sign in")
        self.tk_login_btn.clicked.connect(self._on_tk_login)
        self.tk_login_btn.setEnabled(self._tk_webengine_ok)
        tk_buttons.addWidget(self.tk_login_btn)
        self.tk_logout_btn = QPushButton("Logout")
        self.tk_logout_btn.setStyleSheet("color: red;")
        self.tk_logout_btn.clicked.connect(self._on_tk_logout)
        tk_buttons.addWidget(self.tk_logout_btn)
        tk_buttons.addStretch()
        tk_layout.addLayout(tk_buttons)

        layout.addWidget(tk_group)

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

        # Chaturbate
        self._update_cb_status()

        # TikTok
        self._update_tk_status()

    def _update_cb_status(self):
        """Update Chaturbate login status display."""
        from ...chat.chaturbate_web_chat import has_chaturbate_login

        is_logged_in = has_chaturbate_login()

        if is_logged_in:
            login = self.app.settings.chaturbate.login_name
            if login:
                self.cb_status.setText(f"Status: Logged in as {login}")
            else:
                self.cb_status.setText("Status: Logged in")
            self.cb_status.setStyleSheet("color: green;")
        else:
            self.cb_status.setText("Status: Not logged in")
            self.cb_status.setStyleSheet("color: gray;")

        self.cb_login_btn.setVisible(not is_logged_in)
        self.cb_import_btn.setVisible(is_logged_in)
        self.cb_logout_btn.setVisible(is_logged_in)

    def _update_yt_status(self):
        """Update YouTube login status display."""
        from ...chat.youtube_web_chat import has_youtube_login

        is_logged_in = has_youtube_login()

        if is_logged_in:
            self.yt_status.setText("Status: Logged in")
            self.yt_status.setStyleSheet("color: green;")
        else:
            self.yt_status.setText("Status: Not logged in")
            self.yt_status.setStyleSheet("color: gray;")

        self.yt_login_btn.setVisible(not is_logged_in)
        self.yt_import_subs_btn.setVisible(is_logged_in)
        self.yt_logout_btn.setVisible(is_logged_in)

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

    def _on_yt_login(self):
        """Open persistent YouTube sign-in window (never destroyed)."""
        self.yt_login_btn.setEnabled(False)
        win = _get_login_window()
        # Disconnect any stale connections, then connect fresh
        try:
            win.login_finished.disconnect(self._on_yt_login_finished)
        except RuntimeError:
            pass
        win.login_finished.connect(self._on_yt_login_finished)
        win.start_login(parent=self)

    def _on_yt_login_finished(self, success: bool) -> None:
        """Handle YouTube login window result."""
        self.yt_login_btn.setEnabled(True)
        # Re-activate the preferences window (Chromium may have confused Wayland focus)
        self.window().activateWindow()
        self.window().raise_()
        if success:
            self._update_yt_status()
            QMessageBox.information(
                self,
                "YouTube Login",
                "Successfully signed in to YouTube.\nYouTube chat tabs will now use your account.",
            )

    def _on_yt_clear_cookies(self):
        """Clear YouTube cookies from the shared web profile."""
        from ...chat.youtube_web_chat import clear_youtube_cookies

        clear_youtube_cookies()
        self._update_yt_status()

    def _on_yt_import_subs(self):
        """Open YouTube subscription import dialog."""
        dialog = YouTubeImportDialog(self, self.app)
        dialog.exec()
        if dialog._added_count > 0 and self.dialog.parent():
            self.dialog.parent().refresh_stream_list()

    # --- Chaturbate callbacks ---

    def _on_cb_login(self):
        """Open persistent Chaturbate sign-in window."""
        self.cb_login_btn.setEnabled(False)
        win = _get_chaturbate_login_window()
        try:
            win.login_finished.disconnect(self._on_cb_login_finished)
        except RuntimeError:
            pass
        win.login_finished.connect(self._on_cb_login_finished)
        win.start_login(parent=self)

    def _on_cb_login_finished(self, success: bool) -> None:
        """Handle Chaturbate login window result."""
        self.cb_login_btn.setEnabled(True)
        self.window().activateWindow()
        self.window().raise_()
        if success:
            # Try to detect the logged-in username from cookies
            self._detect_cb_username()
            self._update_cb_status()
            self._update_account_buttons()
            QMessageBox.information(
                self,
                "Chaturbate Login",
                "Successfully signed in to Chaturbate.",
            )

    def _detect_cb_username(self) -> None:
        """Detect the Chaturbate username from cookies after login."""
        from ...chat.chaturbate_web_chat import _get_all_cookies

        cookies = _get_all_cookies()
        # Chaturbate stores username in various cookies
        for name in ("csrftoken", "sessionid"):
            if name in cookies:
                # sessionid exists means we're logged in, but doesn't contain username
                pass
        # The username is not directly in cookies; we'll detect it via API later
        # For now, mark as logged in without a specific username
        if "sessionid" in cookies:
            self.app.settings.chaturbate.login_name = "(logged in)"
            self.app.save_settings()

    def _on_cb_import_follows(self):
        """Open Chaturbate followed channels import dialog."""
        from ..chaturbate_import import ChaturbateImportDialog

        dialog = ChaturbateImportDialog(self, self.app)
        dialog.exec()
        if dialog._added_count > 0:
            self.app.monitor.suppress_notifications()

            def on_refresh_complete():
                self.app.monitor.resume_notifications()
                if self.app.main_window:
                    self.app.main_window.refresh_stream_list()

            if self.app.main_window:
                self.app.main_window.refresh_stream_list()

            self.app.refresh(on_complete=on_refresh_complete)

    def _on_cb_logout(self):
        """Clear Chaturbate cookies and reset login state."""
        from ...chat.chaturbate_web_chat import clear_chaturbate_cookies

        clear_chaturbate_cookies()
        self.app.settings.chaturbate.login_name = ""
        self.app.save_settings()
        self._update_cb_status()
        self._update_account_buttons()

    # --- TikTok callbacks ---

    def _update_tk_status(self):
        """Update TikTok login status display."""
        from ...chat.tiktok_web_chat import has_tiktok_login

        is_logged_in = has_tiktok_login()

        if is_logged_in:
            login = self.app.settings.tiktok.login_name
            if login:
                self.tk_status.setText(f"Status: Logged in as {login}")
            else:
                self.tk_status.setText("Status: Logged in")
            self.tk_status.setStyleSheet("color: green;")
        else:
            self.tk_status.setText("Status: Not logged in")
            self.tk_status.setStyleSheet("color: gray;")

        self.tk_login_btn.setVisible(not is_logged_in)
        self.tk_logout_btn.setVisible(is_logged_in)

    def _on_tk_login(self):
        """Open persistent TikTok sign-in window."""
        self.tk_login_btn.setEnabled(False)
        win = _get_tiktok_login_window()
        try:
            win.login_finished.disconnect(self._on_tk_login_finished)
        except RuntimeError:
            pass
        win.login_finished.connect(self._on_tk_login_finished)
        win.start_login(parent=self)

    def _on_tk_login_finished(self, success: bool) -> None:
        """Handle TikTok login window result."""
        self.tk_login_btn.setEnabled(True)
        self.window().activateWindow()
        self.window().raise_()
        if success:
            self._detect_tk_username()
            self._update_tk_status()
            self._update_account_buttons()
            QMessageBox.information(
                self,
                "TikTok Login",
                "Successfully signed in to TikTok.\n"
                "TikTok LIVE chat tabs will now use your account.",
            )

    def _detect_tk_username(self) -> None:
        """Mark TikTok as logged in after successful login."""
        from ...chat.tiktok_web_chat import _get_all_cookies

        cookies = _get_all_cookies()
        if "sessionid" in cookies:
            self.app.settings.tiktok.login_name = "(logged in)"
            self.app.save_settings()

    def _on_tk_logout(self):
        """Clear TikTok cookies and reset login state."""
        from ...chat.tiktok_web_chat import clear_tiktok_cookies

        clear_tiktok_cookies()
        self.app.settings.tiktok.login_name = ""
        self.app.save_settings()
        self._update_tk_status()
        self._update_account_buttons()
