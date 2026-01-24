"""YouTube cookie import from installed browsers.

Reads cookies directly from browser SQLite databases on Linux.
- Firefox: unencrypted SQLite
- Chromium-based: AES-CBC encrypted, key from system keyring
"""

import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Required cookies for InnerTube authentication
REQUIRED_COOKIE_KEYS = {"SID", "HSID", "SSID", "APISID", "SAPISID"}

# Domains to match
COOKIE_DOMAINS = {".youtube.com", ".google.com", "youtube.com", "google.com"}

# Browser definitions: (id, display_name, type, cookie_paths, keyring_label)
# type is "chromium" or "firefox"
_BROWSERS = [
    (
        "chrome",
        "Google Chrome",
        "chromium",
        [".config/google-chrome/Default/Cookies", ".config/google-chrome/Profile 1/Cookies"],
        "Chrome Safe Storage",
    ),
    (
        "chromium",
        "Chromium",
        "chromium",
        [".config/chromium/Default/Cookies"],
        "Chromium Safe Storage",
    ),
    (
        "brave",
        "Brave",
        "chromium",
        [".config/BraveSoftware/Brave-Browser/Default/Cookies"],
        "Brave Safe Storage",
    ),
    (
        "edge",
        "Microsoft Edge",
        "chromium",
        [".config/microsoft-edge/Default/Cookies"],
        "Microsoft Edge Safe Storage",
    ),
    (
        "vivaldi",
        "Vivaldi",
        "chromium",
        [".config/vivaldi/Default/Cookies"],
        "Vivaldi Safe Storage",
    ),
    (
        "opera",
        "Opera",
        "chromium",
        [".config/opera/Cookies"],
        "Opera Safe Storage",
    ),
    (
        "firefox",
        "Firefox",
        "firefox",
        [],  # Firefox uses profiles, handled separately
        "",
    ),
    (
        "librewolf",
        "LibreWolf",
        "firefox",
        [],  # Uses profiles like Firefox
        "",
    ),
]


def _find_firefox_cookies(browser_id: str) -> str | None:
    """Find the cookies.sqlite file for Firefox or LibreWolf."""
    home = os.path.expanduser("~")
    if browser_id == "firefox":
        profiles_dir = os.path.join(home, ".mozilla/firefox")
    elif browser_id == "librewolf":
        profiles_dir = os.path.join(home, ".librewolf")
    else:
        return None

    if not os.path.isdir(profiles_dir):
        return None

    # Look for profiles.ini to find the default profile
    profiles_ini = os.path.join(profiles_dir, "profiles.ini")
    default_profile = None

    if os.path.exists(profiles_ini):
        import configparser
        config = configparser.ConfigParser()
        config.read(profiles_ini)
        for section in config.sections():
            if section.startswith("Profile") or section.startswith("Install"):
                if config.get(section, "Default", fallback="0") == "1":
                    path = config.get(section, "Path", fallback="")
                    is_relative = config.get(section, "IsRelative", fallback="1") == "1"
                    if path:
                        if is_relative:
                            default_profile = os.path.join(profiles_dir, path)
                        else:
                            default_profile = path
                        break

    # If no default found, try any .default-release or .default profile
    if not default_profile:
        try:
            for entry in os.listdir(profiles_dir):
                full = os.path.join(profiles_dir, entry)
                if os.path.isdir(full) and (
                    entry.endswith(".default-release") or entry.endswith(".default")
                ):
                    default_profile = full
                    break
        except OSError:
            return None

    if not default_profile:
        return None

    cookies_path = os.path.join(default_profile, "cookies.sqlite")
    return cookies_path if os.path.exists(cookies_path) else None


def _find_chromium_cookies(cookie_paths: list[str]) -> str | None:
    """Find the first existing Chromium cookie database."""
    home = os.path.expanduser("~")
    for rel_path in cookie_paths:
        full = os.path.join(home, rel_path)
        if os.path.exists(full):
            return full
    return None


class DecryptionKeyError(Exception):
    """Raised when the browser's encryption key cannot be found."""

    pass


def _get_chromium_password(keyring_label: str) -> str | None:
    """Get the Chromium safe storage password from the system keyring.

    Tries multiple methods:
    1. secretstorage (GNOME Keyring / Secret Service API)
    2. keyring library (handles KDE KWallet, GNOME, etc.)

    Returns the password string, or None if not found.
    """
    # Method 1: secretstorage (direct Secret Service search)
    try:
        import secretstorage
        connection = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(connection)
        if collection.is_locked():
            collection.unlock()
        for item in collection.get_all_items():
            label = item.get_label()
            # Match exact label or "Keys/" prefixed label (KDE migration format)
            if label == keyring_label or label.endswith(f"/{keyring_label}"):
                password = item.get_secret().decode("utf-8")
                logger.debug(f"Found keyring entry: {label!r}")
                return password
    except Exception as e:
        logger.debug(f"secretstorage lookup failed ({keyring_label}): {e}")

    # Method 2: keyring library (works with KWallet, macOS Keychain, etc.)
    try:
        import keyring as kr
        browser_name = keyring_label.replace(" Safe Storage", "")
        password = kr.get_password(keyring_label, browser_name)
        if password:
            logger.debug(f"Found via keyring library: {keyring_label}")
            return password
    except Exception as e:
        logger.debug(f"keyring library lookup failed ({keyring_label}): {e}")

    return None


def _get_chromium_key(keyring_label: str) -> bytes:
    """Get the Chromium AES decryption key.

    Raises DecryptionKeyError if the password cannot be found in the keyring.
    """
    password = _get_chromium_password(keyring_label)

    if password is None:
        # "peanuts" is the default only when no keyring is in use at all.
        # If a keyring IS available but doesn't have our entry, the browser
        # is using a method we can't access (e.g., XDG portal on KDE).
        raise DecryptionKeyError(
            f"Could not find '{keyring_label}' in the system keyring.\n"
            f"This browser may use a storage method not supported by the importer.\n"
            f"Try using Chrome or Firefox instead."
        )

    # Derive the AES key: PBKDF2-SHA1, 1 iteration, salt "saltysalt", 16 bytes
    key = hashlib.pbkdf2_hmac(
        "sha1",
        password.encode("utf-8"),
        b"saltysalt",
        1,
        dklen=16,
    )
    return key


def _decrypt_chromium_value(encrypted: bytes, key: bytes) -> str:
    """Decrypt a Chromium cookie value.

    Chromium on Linux uses v10/v11 prefix + AES-128-CBC.
    IV is 16 bytes of space (0x20).
    """
    if not encrypted:
        return ""

    # Check for version prefix
    if encrypted[:3] in (b"v10", b"v11"):
        encrypted = encrypted[3:]
    else:
        # Not encrypted, return as-is
        try:
            return encrypted.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    if len(encrypted) < 16:
        return ""

    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        iv = b" " * 16  # 16 bytes of 0x20 (space)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(encrypted) + decryptor.finalize()

        # Remove PKCS7 padding
        if decrypted:
            pad_len = decrypted[-1]
            if 0 < pad_len <= 16:
                decrypted = decrypted[:-pad_len]

        result = decrypted.decode("utf-8", errors="replace")

        # Validate: if more than 25% non-printable chars, decryption likely failed
        if result and sum(1 for c in result if not c.isprintable()) > len(result) * 0.25:
            return ""

        return result
    except Exception as e:
        logger.debug(f"Cookie decryption failed: {e}")
        return ""


def _read_firefox_cookies(db_path: str) -> dict[str, str]:
    """Read cookies from a Firefox cookies.sqlite database."""
    cookies: dict[str, str] = {}

    # Copy the database to avoid locking issues with a running browser
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(tmp_fd)
    try:
        shutil.copy2(db_path, tmp_path)
        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()

        # Build domain filter
        domain_clauses = " OR ".join(
            f"host LIKE '%{d}'" for d in COOKIE_DOMAINS
        )
        cursor.execute(
            f"SELECT name, value FROM moz_cookies WHERE {domain_clauses}"  # noqa: S608
        )
        for name, value in cursor.fetchall():
            if name and value:
                cookies[name] = value
        conn.close()
    except Exception as e:
        logger.error(f"Failed to read Firefox cookies: {e}")
    finally:
        os.unlink(tmp_path)

    return cookies


def _read_chromium_cookies(db_path: str, key: bytes) -> dict[str, str]:
    """Read and decrypt cookies from a Chromium cookie database."""
    cookies: dict[str, str] = {}

    # Copy the database to avoid locking issues
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(tmp_fd)
    try:
        shutil.copy2(db_path, tmp_path)
        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()

        # Build domain filter
        domain_clauses = " OR ".join(
            f"host_key LIKE '%{d}'" for d in COOKIE_DOMAINS
        )
        cursor.execute(
            f"SELECT name, encrypted_value, value FROM cookies "  # noqa: S608
            f"WHERE {domain_clauses}"
        )
        for name, encrypted_value, plain_value in cursor.fetchall():
            if not name:
                continue
            # Try plain value first (older Chrome versions)
            if plain_value:
                cookies[name] = plain_value
            elif encrypted_value:
                decrypted = _decrypt_chromium_value(encrypted_value, key)
                if decrypted:
                    cookies[name] = decrypted
        conn.close()
    except Exception as e:
        logger.error(f"Failed to read Chromium cookies: {e}")
    finally:
        os.unlink(tmp_path)

    return cookies


def _detect_available_browsers() -> list[tuple[str, str, str, str, str]]:
    """Detect installed browsers that have cookie stores.

    Returns list of (id, display_name, type, db_path, keyring_label).
    """
    available = []
    for browser_id, display_name, browser_type, cookie_paths, keyring_label in _BROWSERS:
        if browser_type == "firefox":
            db_path = _find_firefox_cookies(browser_id)
            if db_path:
                available.append((browser_id, display_name, browser_type, db_path, ""))
        else:
            db_path = _find_chromium_cookies(cookie_paths)
            if db_path:
                available.append(
                    (browser_id, display_name, browser_type, db_path, keyring_label)
                )
    return available


def _extract_cookies(browser_type: str, db_path: str, keyring_label: str) -> dict[str, str]:
    """Extract cookies from a browser's cookie database."""
    if browser_type == "firefox":
        return _read_firefox_cookies(db_path)
    else:
        key = _get_chromium_key(keyring_label)
        return _read_chromium_cookies(db_path, key)


class BrowserSelectDialog(QDialog):
    """Dialog for selecting which browser to import cookies from."""

    def __init__(self, browsers: list[tuple[str, str, str, str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import YouTube Cookies")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Select the browser where you are logged into YouTube.\n"
            "Cookies will be read from its local cookie store."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._combo = QComboBox()
        for browser_id, display_name, *_ in browsers:
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


def import_cookies_from_browser(parent: QWidget) -> str | None:
    """Import YouTube cookies from an installed browser.

    Returns the cookie string on success, or None if cancelled/failed.
    """
    # Flatpak can't access host browser cookie stores
    if os.path.exists("/.flatpak-info") or "FLATPAK_ID" in os.environ:
        QMessageBox.information(
            parent,
            "Not Available in Flatpak",
            "Browser cookie import is not available in Flatpak\n"
            "because the sandbox cannot access browser data.\n\n"
            "Please use the manual cookie paste method instead.\n"
            "Click 'How to get cookies' for instructions.",
        )
        return None

    # Detect available browsers
    browsers = _detect_available_browsers()
    if not browsers:
        QMessageBox.warning(
            parent,
            "No Browsers Found",
            "Could not find any supported browsers with cookie stores.\n\n"
            "Supported: Chrome, Chromium, Brave, Edge, Firefox,\n"
            "Opera, Vivaldi, LibreWolf\n\n"
            "Please use the manual cookie paste method instead.",
        )
        return None

    # Let user pick browser
    dialog = BrowserSelectDialog(browsers, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    selected_id = dialog.selected_browser
    if not selected_id:
        return None

    # Find the selected browser's info
    browser_info = None
    for entry in browsers:
        if entry[0] == selected_id:
            browser_info = entry
            break
    if not browser_info:
        return None

    _, display_name, browser_type, db_path, keyring_label = browser_info

    # Extract cookies
    try:
        cookie_dict = _extract_cookies(browser_type, db_path, keyring_label)
    except DecryptionKeyError as e:
        QMessageBox.warning(
            parent,
            "Encryption Key Not Found",
            f"{e}\n\n"
            "This typically happens with browsers that use the XDG Desktop\n"
            "Portal for key storage (common on KDE/Plasma).\n\n"
            "Try selecting Chrome or Firefox instead, or use the\n"
            "manual cookie paste method.",
        )
        return None
    except Exception as e:
        QMessageBox.critical(
            parent,
            "Cookie Read Error",
            f"Failed to read cookies from {display_name}:\n\n{e}\n\n"
            "Make sure the browser is not locked or try a different browser.",
        )
        return None

    # Check if we have the required cookies
    if not REQUIRED_COOKIE_KEYS.issubset(cookie_dict.keys()):
        missing = REQUIRED_COOKIE_KEYS - cookie_dict.keys()
        QMessageBox.warning(
            parent,
            "Not Logged In",
            f"The required YouTube cookies were not found in {display_name}.\n\n"
            f"Missing: {', '.join(sorted(missing))}\n\n"
            "Make sure you are logged into YouTube in that browser,\n"
            "then try again.",
        )
        return None

    # Build cookie string
    parts = [f"{k}={v}" for k, v in sorted(cookie_dict.items())]
    return "; ".join(parts)
