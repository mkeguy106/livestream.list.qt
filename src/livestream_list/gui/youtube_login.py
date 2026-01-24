"""YouTube cookie import from installed browsers using rookiepy."""

import logging
import os
import subprocess
import sys

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
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

# Browsers supported by rookiepy, in display order
SUPPORTED_BROWSERS = [
    ("chrome", "Google Chrome"),
    ("chromium", "Chromium"),
    ("brave", "Brave"),
    ("edge", "Microsoft Edge"),
    ("firefox", "Firefox"),
    ("opera", "Opera"),
    ("opera_gx", "Opera GX"),
    ("vivaldi", "Vivaldi"),
    ("librewolf", "LibreWolf"),
]

# Domains to extract cookies from
COOKIE_DOMAINS = [".youtube.com", ".google.com", "youtube.com", "google.com"]


def is_rookiepy_available() -> bool:
    """Check if rookiepy is importable."""
    try:
        import rookiepy  # noqa: F401
        return True
    except ImportError:
        return False


def is_flatpak() -> bool:
    """Check if running inside a Flatpak sandbox."""
    return os.path.exists("/.flatpak-info") or "FLATPAK_ID" in os.environ


def _detect_available_browsers() -> list[tuple[str, str]]:
    """Detect which supported browsers are installed.

    Returns list of (rookiepy_name, display_name) for browsers that
    appear to have cookie stores on the system.
    """
    available = []
    for browser_id, display_name in SUPPORTED_BROWSERS:
        if _browser_has_cookies(browser_id):
            available.append((browser_id, display_name))
    return available


def _browser_has_cookies(browser_id: str) -> bool:
    """Check if a browser likely has a cookie store on this system."""
    home = os.path.expanduser("~")

    # Common cookie database paths on Linux
    cookie_paths = {
        "chrome": [
            os.path.join(home, ".config/google-chrome/Default/Cookies"),
            os.path.join(home, ".config/google-chrome/Profile 1/Cookies"),
        ],
        "chromium": [
            os.path.join(home, ".config/chromium/Default/Cookies"),
        ],
        "brave": [
            os.path.join(home, ".config/BraveSoftware/Brave-Browser/Default/Cookies"),
        ],
        "edge": [
            os.path.join(home, ".config/microsoft-edge/Default/Cookies"),
        ],
        "firefox": [
            # Firefox uses profiles - check if the directory exists
            os.path.join(home, ".mozilla/firefox"),
        ],
        "opera": [
            os.path.join(home, ".config/opera/Cookies"),
        ],
        "opera_gx": [
            os.path.join(home, ".config/opera-gx/Cookies"),
        ],
        "vivaldi": [
            os.path.join(home, ".config/vivaldi/Default/Cookies"),
        ],
        "librewolf": [
            os.path.join(home, ".librewolf"),
        ],
    }

    paths = cookie_paths.get(browser_id, [])
    for path in paths:
        if os.path.exists(path):
            return True
    return False


class RookiepyInstallWorker(QThread):
    """Background worker to install rookiepy via pip."""

    finished_ok = Signal()
    finished_error = Signal(str)

    def run(self):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "rookiepy"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                self.finished_ok.emit()
            else:
                error = result.stderr.strip() or result.stdout.strip()
                self.finished_error.emit(f"pip install failed:\n{error[:500]}")
        except subprocess.TimeoutExpired:
            self.finished_error.emit("Installation timed out")
        except Exception as e:
            self.finished_error.emit(str(e))


class RookiepyInstallDialog(QDialog):
    """Dialog that installs rookiepy with progress feedback."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Installing cookie reader")
        self.setMinimumWidth(350)
        self._success = False

        layout = QVBoxLayout(self)

        self._label = QLabel(
            "Installing rookiepy (small package for reading browser cookies)..."
        )
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # Indeterminate
        layout.addWidget(self._progress)

        self._worker = RookiepyInstallWorker(parent=self)
        self._worker.finished_ok.connect(self._on_success)
        self._worker.finished_error.connect(self._on_error)
        self._worker.start()

    @property
    def success(self) -> bool:
        return self._success

    def _on_success(self):
        self._success = True
        self.accept()

    def _on_error(self, error: str):
        self._label.setText("Installation failed.")
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        error_label = QLabel(error)
        error_label.setStyleSheet("color: red;")
        error_label.setWordWrap(True)
        self.layout().addWidget(error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.layout().addWidget(buttons)

    def closeEvent(self, event):  # noqa: N802
        if self._worker.isRunning():
            self._worker.wait(2000)
        super().closeEvent(event)


class BrowserSelectDialog(QDialog):
    """Dialog for selecting which browser to import cookies from."""

    def __init__(self, browsers: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import YouTube Cookies")
        self.setMinimumWidth(380)
        self._selected_browser: str = ""

        layout = QVBoxLayout(self)

        info = QLabel(
            "Select the browser where you are logged into YouTube.\n"
            "Cookies will be read from its local cookie store."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._combo = QComboBox()
        for browser_id, display_name in browsers:
            self._combo.addItem(display_name, browser_id)
        layout.addWidget(self._combo)

        note = QLabel(
            "Make sure you are logged into YouTube in the selected browser."
        )
        note.setStyleSheet("color: gray; font-style: italic;")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def selected_browser(self) -> str:
        return self._combo.currentData() or ""


def _extract_cookies_from_browser(browser_id: str) -> str | None:
    """Extract YouTube/Google cookies from the specified browser.

    Returns cookie string on success, None on failure.
    """
    import rookiepy

    # Map our browser IDs to rookiepy functions
    browser_funcs = {
        "chrome": rookiepy.chrome,
        "chromium": rookiepy.chromium,
        "brave": rookiepy.brave,
        "edge": rookiepy.edge,
        "firefox": rookiepy.firefox,
        "opera": rookiepy.opera,
        "opera_gx": rookiepy.opera_gx,
        "vivaldi": rookiepy.vivaldi,
        "librewolf": rookiepy.librewolf,
    }

    func = browser_funcs.get(browser_id)
    if not func:
        logger.error(f"Unknown browser: {browser_id}")
        return None

    try:
        cookies = func(domains=COOKIE_DOMAINS)
    except Exception as e:
        logger.error(f"Failed to read cookies from {browser_id}: {e}")
        raise

    # Build cookie dict from rookiepy results
    cookie_dict: dict[str, str] = {}
    for cookie in cookies:
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if name and value:
            cookie_dict[name] = value

    # Check if we have the required cookies
    if not REQUIRED_COOKIE_KEYS.issubset(cookie_dict.keys()):
        missing = REQUIRED_COOKIE_KEYS - cookie_dict.keys()
        logger.warning(f"Missing required cookies: {missing}")
        return None

    # Build cookie string
    parts = [f"{k}={v}" for k, v in sorted(cookie_dict.items())]
    return "; ".join(parts)


def import_cookies_from_browser(parent: QWidget) -> str | None:
    """High-level helper: ensure rookiepy is available, pick browser, extract cookies.

    Returns the cookie string on success, or None if cancelled/failed.
    """
    # Flatpak can't access host browser cookie stores
    if is_flatpak():
        QMessageBox.information(
            parent,
            "Not Available in Flatpak",
            "Browser cookie import is not available in Flatpak\n"
            "because the sandbox cannot access browser data.\n\n"
            "Please use the manual cookie paste method instead.\n"
            "Click 'How to get cookies' for instructions.",
        )
        return None

    # Install rookiepy if needed
    if not is_rookiepy_available():
        reply = QMessageBox.question(
            parent,
            "Install Cookie Reader?",
            "Importing cookies requires the 'rookiepy' package\n"
            "(small download, reads browser cookie stores).\n\n"
            "Install now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return None

        install_dialog = RookiepyInstallDialog(parent)
        install_dialog.exec()
        if not install_dialog.success:
            return None

        if not is_rookiepy_available():
            QMessageBox.critical(
                parent,
                "Installation Error",
                "rookiepy was installed but cannot be imported.\n"
                "You may need to restart the application.",
            )
            return None

    # Detect available browsers
    browsers = _detect_available_browsers()
    if not browsers:
        QMessageBox.warning(
            parent,
            "No Browsers Found",
            "Could not detect any supported browsers with cookie stores.\n\n"
            "Supported: Chrome, Chromium, Brave, Edge, Firefox, Opera, Vivaldi, LibreWolf\n\n"
            "Please use the manual cookie paste method instead.",
        )
        return None

    # Let user pick browser
    dialog = BrowserSelectDialog(browsers, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    browser_id = dialog.selected_browser
    if not browser_id:
        return None

    # Extract cookies
    try:
        cookie_string = _extract_cookies_from_browser(browser_id)
    except Exception as e:
        QMessageBox.critical(
            parent,
            "Cookie Read Error",
            f"Failed to read cookies from {browser_id}:\n\n{e}\n\n"
            "Make sure the browser is closed or try a different browser.",
        )
        return None

    if not cookie_string:
        QMessageBox.warning(
            parent,
            "Not Logged In",
            "The required YouTube cookies were not found in that browser.\n\n"
            "Make sure you are logged into YouTube in the selected browser,\n"
            "then try again.",
        )
        return None

    return cookie_string
