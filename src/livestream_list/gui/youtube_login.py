"""YouTube cookie import from installed browsers.

Reads cookies directly from browser SQLite databases on Linux.
- Firefox: unencrypted SQLite
- Chromium-based: AES-CBC encrypted, key from system keyring

In Flatpak, runs the extraction on the host via flatpak-spawn.
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
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

# Additional cookies needed for YouTube InnerTube API
_YOUTUBE_COOKIE_NAMES = {
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "SIDCC",
    "LOGIN_INFO",
    "PREF",
    "YSC",
    "NID",
    "AEC",
    "VISITOR_INFO1_LIVE",
    "VISITOR_PRIVACY_METADATA",
    "GPS",
    "LSID",
    "DV",
    "OTZ",
    "SMSV",
    "ACCOUNT_CHOOSER",
}

# Prefixes for secure auth cookies that YouTube uses
_YOUTUBE_COOKIE_PREFIXES = ("__Secure-", "__Host-")

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
    candidates: list[str] = []

    if os.path.exists(profiles_ini):
        import configparser

        config = configparser.ConfigParser()
        config.read(profiles_ini)

        # Install sections have Default=<path> pointing to the active profile
        for section in config.sections():
            if section.startswith("Install"):
                path = config.get(section, "Default", fallback="")
                if path:
                    candidates.append(os.path.join(profiles_dir, path))

        # Profile sections with Default=1
        for section in config.sections():
            if section.startswith("Profile"):
                if config.get(section, "Default", fallback="0") == "1":
                    path = config.get(section, "Path", fallback="")
                    is_relative = config.get(section, "IsRelative", fallback="1") == "1"
                    if path:
                        full = os.path.join(profiles_dir, path) if is_relative else path
                        if full not in candidates:
                            candidates.append(full)

    # Fallback: try any .default-release or .default profile directory
    try:
        for entry in sorted(os.listdir(profiles_dir)):
            full = os.path.join(profiles_dir, entry)
            if os.path.isdir(full) and (
                entry.endswith(".default-release") or entry.endswith(".default")
            ):
                if full not in candidates:
                    candidates.append(full)
    except OSError:
        pass

    # Return first candidate that has cookies.sqlite
    for profile_dir in candidates:
        cookies_path = os.path.join(profile_dir, "cookies.sqlite")
        if os.path.exists(cookies_path):
            return cookies_path

    return None


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
    v11 includes a 32-byte internal prefix in the plaintext that must be stripped.
    """
    if not encrypted:
        return ""

    # Check for version prefix
    version = None
    if encrypted[:3] in (b"v10", b"v11"):
        version = encrypted[:3]
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

        # v11 format includes a 32-byte internal prefix in the plaintext
        if version == b"v11" and len(decrypted) > 32:
            decrypted = decrypted[32:]

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
        domain_clauses = " OR ".join(f"host LIKE '%{d}'" for d in COOKIE_DOMAINS)
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
        domain_clauses = " OR ".join(f"host_key LIKE '%{d}'" for d in COOKIE_DOMAINS)
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
                available.append((browser_id, display_name, browser_type, db_path, keyring_label))
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

        note = QLabel("Make sure you are logged into YouTube in the selected browser.")
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


def _is_flatpak() -> bool:
    """Check if running inside a Flatpak sandbox."""
    return os.path.exists("/.flatpak-info") or "FLATPAK_ID" in os.environ


# Self-contained Python script run on the host via flatpak-spawn.
# Handles browser detection and cookie extraction without needing
# the app's packages installed on the host.
_HOST_SCRIPT = r"""
import configparser
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile

COOKIE_DOMAINS = {".youtube.com", ".google.com", "youtube.com", "google.com"}
YOUTUBE_COOKIE_NAMES = {
    "SID", "HSID", "SSID", "APISID", "SAPISID", "SIDCC", "LOGIN_INFO",
    "PREF", "YSC", "NID", "AEC", "VISITOR_INFO1_LIVE",
    "VISITOR_PRIVACY_METADATA", "GPS", "LSID", "DV", "OTZ", "SMSV",
    "ACCOUNT_CHOOSER",
}
YOUTUBE_COOKIE_PREFIXES = ("__Secure-", "__Host-")
REQUIRED = {"SID", "HSID", "SSID", "APISID", "SAPISID"}

BROWSERS = [
    ("chrome", "Google Chrome", "chromium",
     [".config/google-chrome/Default/Cookies",
      ".config/google-chrome/Profile 1/Cookies"],
     "Chrome Safe Storage"),
    ("chromium", "Chromium", "chromium",
     [".config/chromium/Default/Cookies"], "Chromium Safe Storage"),
    ("brave", "Brave", "chromium",
     [".config/BraveSoftware/Brave-Browser/Default/Cookies"],
     "Brave Safe Storage"),
    ("vivaldi", "Vivaldi", "chromium",
     [".config/vivaldi/Default/Cookies"], "Vivaldi Safe Storage"),
    ("opera", "Opera", "chromium",
     [".config/opera/Cookies"], "Opera Safe Storage"),
    ("firefox", "Firefox", "firefox", [], ""),
    ("librewolf", "LibreWolf", "firefox", [], ""),
]

def find_firefox_cookies(browser_id):
    home = os.path.expanduser("~")
    if browser_id == "firefox":
        profiles_dir = os.path.join(home, ".mozilla/firefox")
    elif browser_id == "librewolf":
        profiles_dir = os.path.join(home, ".librewolf")
    else:
        return None
    if not os.path.isdir(profiles_dir):
        return None
    profiles_ini = os.path.join(profiles_dir, "profiles.ini")
    candidates = []
    if os.path.exists(profiles_ini):
        config = configparser.ConfigParser()
        config.read(profiles_ini)
        for section in config.sections():
            if section.startswith("Install"):
                path = config.get(section, "Default", fallback="")
                if path:
                    candidates.append(os.path.join(profiles_dir, path))
        for section in config.sections():
            if section.startswith("Profile"):
                if config.get(section, "Default", fallback="0") == "1":
                    path = config.get(section, "Path", fallback="")
                    is_rel = config.get(section, "IsRelative", fallback="1") == "1"
                    if path:
                        full = os.path.join(profiles_dir, path) if is_rel else path
                        if full not in candidates:
                            candidates.append(full)
    try:
        for entry in sorted(os.listdir(profiles_dir)):
            full = os.path.join(profiles_dir, entry)
            if os.path.isdir(full) and (
                entry.endswith(".default-release") or entry.endswith(".default")
            ):
                if full not in candidates:
                    candidates.append(full)
    except OSError:
        pass
    for profile_dir in candidates:
        cookies_path = os.path.join(profile_dir, "cookies.sqlite")
        if os.path.exists(cookies_path):
            return cookies_path
    return None

def find_chromium_cookies(cookie_paths):
    home = os.path.expanduser("~")
    for rel_path in cookie_paths:
        full = os.path.join(home, rel_path)
        if os.path.exists(full):
            return full
    return None

def detect_browsers():
    available = []
    for bid, name, btype, paths, klabel in BROWSERS:
        if btype == "firefox":
            db = find_firefox_cookies(bid)
            if db:
                available.append({"id": bid, "name": name, "type": btype,
                                   "db": db, "keyring": ""})
        else:
            db = find_chromium_cookies(paths)
            if db:
                available.append({"id": bid, "name": name, "type": btype,
                                   "db": db, "keyring": klabel})
    return available

def read_firefox(db_path):
    cookies = {}
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        shutil.copy2(db_path, tmp)
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        clauses = " OR ".join(f"host LIKE '%{d}'" for d in COOKIE_DOMAINS)
        cur.execute(f"SELECT name, value FROM moz_cookies WHERE {clauses}")
        for name, value in cur.fetchall():
            if name and value:
                cookies[name] = value
        conn.close()
    finally:
        os.unlink(tmp)
    return cookies

def get_chromium_password(keyring_label):
    try:
        import secretstorage
        connection = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(connection)
        if collection.is_locked():
            collection.unlock()
        for item in collection.get_all_items():
            label = item.get_label()
            if label == keyring_label or label.endswith(f"/{keyring_label}"):
                return item.get_secret().decode("utf-8")
    except Exception:
        pass
    try:
        import keyring as kr
        browser_name = keyring_label.replace(" Safe Storage", "")
        pw = kr.get_password(keyring_label, browser_name)
        if pw:
            return pw
    except Exception:
        pass
    return None

def decrypt_chromium_value(encrypted, key):
    if not encrypted:
        return ""
    if encrypted[:3] in (b"v10", b"v11"):
        version = encrypted[:3]
        encrypted = encrypted[3:]
    else:
        try:
            return encrypted.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    if len(encrypted) < 16:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        iv = b" " * 16
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(encrypted) + decryptor.finalize()
        if decrypted:
            pad_len = decrypted[-1]
            if 0 < pad_len <= 16:
                decrypted = decrypted[:-pad_len]
        if version == b"v11" and len(decrypted) > 32:
            decrypted = decrypted[32:]
        result = decrypted.decode("utf-8", errors="replace")
        if result and sum(1 for c in result if not c.isprintable()) > len(result) * 0.25:
            return ""
        return result
    except Exception:
        return ""

def read_chromium(db_path, keyring_label):
    password = get_chromium_password(keyring_label)
    if password is None:
        return None, f"Could not find '{keyring_label}' in system keyring"
    key = hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), b"saltysalt", 1, dklen=16)
    cookies = {}
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        shutil.copy2(db_path, tmp)
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        clauses = " OR ".join(f"host_key LIKE '%{d}'" for d in COOKIE_DOMAINS)
        cur.execute(f"SELECT name, encrypted_value, value FROM cookies WHERE {clauses}")
        for name, enc_val, plain_val in cur.fetchall():
            if not name:
                continue
            if plain_val:
                cookies[name] = plain_val
            elif enc_val:
                dec = decrypt_chromium_value(enc_val, key)
                if dec:
                    cookies[name] = dec
        conn.close()
    finally:
        os.unlink(tmp)
    return cookies, None

def extract(browser):
    btype = browser["type"]
    db = browser["db"]
    if btype == "firefox":
        cookies = read_firefox(db)
        error = None
    else:
        cookies, error = read_chromium(db, browser["keyring"])
    if error:
        return {"error": error}
    if not REQUIRED.issubset(cookies.keys()):
        missing = REQUIRED - cookies.keys()
        return {"error": f"Missing required cookies: {', '.join(sorted(missing))}. "
                         f"Make sure you are logged into YouTube."}
    filtered = {k: v for k, v in cookies.items()
                if k in YOUTUBE_COOKIE_NAMES or k.startswith(YOUTUBE_COOKIE_PREFIXES)}
    parts = [f"{k}={v}" for k, v in sorted(filtered.items())]
    return {"cookies": "; ".join(parts)}

mode = sys.argv[1] if len(sys.argv) > 1 else "detect"
if mode == "detect":
    print(json.dumps(detect_browsers()))
elif mode == "extract":
    browser = json.loads(sys.argv[2])
    print(json.dumps(extract(browser)))
"""


def _run_on_host(args: list[str], timeout: int = 30) -> str | None:
    """Run a command on the host via flatpak-spawn, return stdout."""
    cmd = ["flatpak-spawn", "--host"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.strip()
        logger.warning(f"Host command failed: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        logger.warning("Host command timed out")
    except Exception as e:
        logger.warning(f"Host command error: {e}")
    return None


def _import_cookies_flatpak(parent: QWidget) -> str | None:
    """Import cookies by running extraction on the host via flatpak-spawn."""
    # Detect browsers on the host
    output = _run_on_host(["python3", "-c", _HOST_SCRIPT, "detect"])
    if not output:
        QMessageBox.warning(
            parent,
            "Host Access Failed",
            "Could not detect browsers on the host system.\n\n"
            "Make sure python3 is installed on the host\n"
            "and Flatpak has permission to spawn host commands.",
        )
        return None

    try:
        browsers = json.loads(output)
    except json.JSONDecodeError:
        QMessageBox.warning(parent, "Error", "Failed to parse browser detection output.")
        return None

    if not browsers:
        QMessageBox.warning(
            parent,
            "No Browsers Found",
            "Could not find any supported browsers with cookie stores.\n\n"
            "Supported: Chrome, Chromium, Brave, Firefox,\n"
            "Opera, Vivaldi, LibreWolf\n\n"
            "Please use the manual cookie paste method instead.",
        )
        return None

    # Show browser selection dialog
    browser_tuples = [(b["id"], b["name"], b["type"], b["db"], b["keyring"]) for b in browsers]
    dialog = BrowserSelectDialog(browser_tuples, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    selected_id = dialog.selected_browser
    selected = next((b for b in browsers if b["id"] == selected_id), None)
    if not selected:
        return None

    # Extract cookies on the host
    browser_json = json.dumps(selected)
    output = _run_on_host(["python3", "-c", _HOST_SCRIPT, "extract", browser_json], timeout=15)
    if not output:
        QMessageBox.warning(
            parent,
            "Extraction Failed",
            f"Failed to extract cookies from {selected['name']}.\n\n"
            "The host python3 may be missing required packages\n"
            "(cryptography, secretstorage) for Chromium-based browsers.\n\n"
            "Try Firefox, or use the manual cookie paste method.",
        )
        return None

    try:
        result = json.loads(output)
    except json.JSONDecodeError:
        QMessageBox.warning(parent, "Error", "Failed to parse cookie extraction output.")
        return None

    if "error" in result:
        QMessageBox.warning(
            parent,
            "Cookie Import Failed",
            f"{result['error']}\n\nTry a different browser or use the manual cookie paste method.",
        )
        return None

    cookie_string = result.get("cookies", "")
    if not cookie_string:
        QMessageBox.warning(parent, "Error", "No cookies were extracted.")
        return None

    return cookie_string


def import_cookies_from_browser(parent: QWidget) -> str | None:
    """Import YouTube cookies from an installed browser.

    Returns the cookie string on success, or None if cancelled/failed.
    """
    # In Flatpak, run extraction on the host via flatpak-spawn
    if _is_flatpak():
        return _import_cookies_flatpak(parent)

    # Detect available browsers
    browsers = _detect_available_browsers()
    if not browsers:
        QMessageBox.warning(
            parent,
            "No Browsers Found",
            "Could not find any supported browsers with cookie stores.\n\n"
            "Supported: Chrome, Chromium, Brave, Firefox,\n"
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

    # Filter to only YouTube-relevant cookies
    filtered = {
        k: v
        for k, v in cookie_dict.items()
        if k in _YOUTUBE_COOKIE_NAMES or k.startswith(_YOUTUBE_COOKIE_PREFIXES)
    }

    # Build cookie string
    parts = [f"{k}={v}" for k, v in sorted(filtered.items())]
    return "; ".join(parts)
