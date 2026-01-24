"""YouTube login dialog using embedded browser for cookie capture."""

import logging
import os
import subprocess
import sys

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Required cookies for InnerTube authentication
REQUIRED_COOKIE_KEYS = {"SID", "HSID", "SSID", "APISID", "SAPISID"}

YOUTUBE_LOGIN_URL = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https://www.youtube.com/"


def is_webengine_available() -> bool:
    """Check if PySide6 QtWebEngine is importable."""
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        return True
    except ImportError:
        return False


def is_flatpak() -> bool:
    """Check if running inside a Flatpak sandbox."""
    return os.path.exists("/.flatpak-info") or "FLATPAK_ID" in os.environ


class WebEngineInstallWorker(QThread):
    """Background worker to install PySide6-Addons via pip."""

    progress = Signal(str)  # status message
    finished_ok = Signal()
    finished_error = Signal(str)

    def run(self):
        """Run pip install PySide6-Addons."""
        try:
            self.progress.emit("Installing QtWebEngine (this may take a moment)...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "PySide6-Addons"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode == 0:
                self.finished_ok.emit()
            else:
                error = result.stderr.strip() or result.stdout.strip()
                self.finished_error.emit(f"pip install failed:\n{error[:500]}")
        except subprocess.TimeoutExpired:
            self.finished_error.emit("Installation timed out (5 minutes)")
        except Exception as e:
            self.finished_error.emit(str(e))


class WebEngineInstallDialog(QDialog):
    """Dialog that installs QtWebEngine with progress feedback."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Installing QtWebEngine")
        self.setMinimumWidth(400)
        self._success = False

        layout = QVBoxLayout(self)

        self._label = QLabel(
            "YouTube login requires QtWebEngine (~150MB download).\n"
            "Installing PySide6-Addons..."
        )
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # Indeterminate
        layout.addWidget(self._progress)

        self._status = QLabel("Starting...")
        self._status.setStyleSheet("color: gray;")
        layout.addWidget(self._status)

        self._worker = WebEngineInstallWorker(parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_success)
        self._worker.finished_error.connect(self._on_error)
        self._worker.start()

    @property
    def success(self) -> bool:
        return self._success

    def _on_progress(self, msg: str):
        self._status.setText(msg)

    def _on_success(self):
        self._success = True
        self.accept()

    def _on_error(self, error: str):
        self._label.setText("Installation failed.")
        self._status.setText(error)
        self._status.setStyleSheet("color: red;")
        self._progress.setRange(0, 1)
        self._progress.setValue(0)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.layout().addWidget(buttons)

    def closeEvent(self, event):  # noqa: N802
        if self._worker.isRunning():
            self._worker.wait(1000)
        super().closeEvent(event)


class YouTubeLoginDialog(QDialog):
    """Embedded browser dialog for YouTube/Google login.

    Opens Google sign-in, captures cookies as the user logs in,
    and returns the cookie string when the required keys are found.
    """

    cookies_captured = Signal(str)  # Full cookie string

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Login to YouTube")
        self.setMinimumSize(500, 600)
        self.resize(520, 700)
        self._cookies: dict[str, str] = {}
        self._cookie_string: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Info bar at top
        info_bar = QWidget()
        info_bar.setStyleSheet("background-color: #1a3a6b; padding: 6px;")
        info_layout = QVBoxLayout(info_bar)
        info_layout.setContentsMargins(10, 6, 10, 6)
        info_label = QLabel(
            "Log in with your Google account. "
            "Cookies will be captured automatically when login completes."
        )
        info_label.setStyleSheet("color: white; font-size: 12px;")
        info_label.setWordWrap(True)
        info_layout.addWidget(info_label)
        layout.addWidget(info_bar)

        # Import WebEngine (should be available at this point)
        from PySide6.QtWebEngineCore import QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView

        # Create a fresh profile so we don't interfere with any existing sessions
        self._profile = QWebEngineProfile("youtube-login", self)
        self._profile.setHttpUserAgent(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Hook into cookie store
        cookie_store = self._profile.cookieStore()
        cookie_store.cookieAdded.connect(self._on_cookie_added)

        # Create web view with the profile
        from PySide6.QtWebEngineCore import QWebEnginePage

        self._page = QWebEnginePage(self._profile, self)
        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        layout.addWidget(self._view)

        # Status bar at bottom
        self._status_label = QLabel("Waiting for login...")
        self._status_label.setStyleSheet(
            "padding: 4px 10px; color: gray; font-size: 11px;"
        )
        layout.addWidget(self._status_label)

        # Navigate to Google sign-in
        from PySide6.QtCore import QUrl
        self._view.setUrl(QUrl(YOUTUBE_LOGIN_URL))

    def _on_cookie_added(self, cookie):
        """Handle a cookie being set in the browser session."""
        name = bytes(cookie.name()).decode("utf-8", errors="replace")
        value = bytes(cookie.value()).decode("utf-8", errors="replace")
        domain = cookie.domain()

        # Only capture Google/YouTube cookies
        if ".google.com" in domain or ".youtube.com" in domain:
            self._cookies[name] = value

            # Check if we have all required cookies
            if REQUIRED_COOKIE_KEYS.issubset(self._cookies.keys()):
                self._finalize()

    def _finalize(self):
        """All required cookies captured - build string and close."""
        # Build cookie string from required + useful extra cookies
        cookie_parts = []
        for key in sorted(self._cookies.keys()):
            cookie_parts.append(f"{key}={self._cookies[key]}")
        self._cookie_string = "; ".join(cookie_parts)

        self._status_label.setText("Login successful! Cookies captured.")
        self._status_label.setStyleSheet(
            "padding: 4px 10px; color: #00cc00; font-size: 11px;"
        )

        # Emit and close after a brief pause so user sees the success message
        from PySide6.QtCore import QTimer
        QTimer.singleShot(800, self._finish)

    def _finish(self):
        self.cookies_captured.emit(self._cookie_string)
        self.accept()

    @property
    def cookie_string(self) -> str:
        """Get the captured cookie string (available after dialog closes)."""
        return self._cookie_string

    def closeEvent(self, event):  # noqa: N802
        # Clean up the profile's cookie store
        try:
            self._profile.cookieStore().deleteAllCookies()
        except Exception:
            pass
        super().closeEvent(event)


def ensure_webengine_and_login(parent: QWidget) -> str | None:
    """High-level helper: ensure WebEngine is available, then show login dialog.

    Returns the cookie string on success, or None if cancelled/failed.
    """
    # Check if running in Flatpak (can't pip install)
    if is_flatpak() and not is_webengine_available():
        QMessageBox.information(
            parent,
            "Not Available in Flatpak",
            "Automatic YouTube login requires QtWebEngine which cannot be\n"
            "installed at runtime in Flatpak.\n\n"
            "Please use the manual cookie paste method instead.\n"
            "Click 'How to get cookies' for instructions.",
        )
        return None

    # Install WebEngine if needed
    if not is_webengine_available():
        reply = QMessageBox.question(
            parent,
            "Install QtWebEngine?",
            "YouTube login requires QtWebEngine (~150MB download).\n\n"
            "This is a one-time download that enables the embedded\n"
            "browser for Google sign-in.\n\n"
            "Install now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return None

        install_dialog = WebEngineInstallDialog(parent)
        install_dialog.exec()
        if not install_dialog.success:
            return None

        # Verify it's now importable
        if not is_webengine_available():
            QMessageBox.critical(
                parent,
                "Installation Error",
                "QtWebEngine was installed but cannot be imported.\n"
                "You may need to restart the application.",
            )
            return None

    # Show login dialog
    dialog = YouTubeLoginDialog(parent)
    result = dialog.exec()
    if result == QDialog.DialogCode.Accepted and dialog.cookie_string:
        return dialog.cookie_string
    return None
