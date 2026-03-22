"""Autostart management for Livestream List.

Linux: .desktop file in ~/.config/autostart/
Windows: Registry key in HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
"""

import logging
import sys
from pathlib import Path

from .platform import IS_FLATPAK

logger = logging.getLogger(__name__)

APP_NAME = "LivestreamListQt"

# --- Linux ---

DESKTOP_FILE_NAME = "app.livestreamlist.LivestreamListQt.desktop"

EXEC_COMMAND = (
    "flatpak run app.livestreamlist.LivestreamListQt" if IS_FLATPAK else "livestream-list-qt"
)

DESKTOP_FILE_CONTENT = f"""[Desktop Entry]
Name=Livestream List Qt
Comment=Monitor your favorite livestreams (Qt version)
Exec={EXEC_COMMAND}
Icon=app.livestreamlist.LivestreamListQt
Terminal=false
Type=Application
Categories=AudioVideo;Video;Network;
Keywords=twitch;stream;live;monitor;
StartupNotify=true
X-GNOME-Autostart-enabled=true
"""


def _get_autostart_dir() -> Path:
    """Get the XDG autostart directory."""
    return Path.home() / ".config" / "autostart"


def _get_autostart_file() -> Path:
    """Get the path to our autostart desktop file."""
    return _get_autostart_dir() / DESKTOP_FILE_NAME


# --- Windows ---

_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _get_windows_exe_path() -> str:
    """Get the path to the executable for Windows autostart."""
    return sys.executable


# --- Public API ---


def is_autostart_enabled() -> bool:
    """Check if autostart is currently enabled."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, APP_NAME)
                return True
        except FileNotFoundError:
            return False
        except OSError:
            return False
    return _get_autostart_file().exists()


def enable_autostart() -> bool:
    """Enable autostart. Returns True if successful."""
    if sys.platform == "win32":
        try:
            import winreg

            exe_path = _get_windows_exe_path()
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')
            return True
        except OSError as e:
            logger.error(f"Failed to enable Windows autostart: {e}")
            return False

    try:
        autostart_dir = _get_autostart_dir()
        autostart_dir.mkdir(parents=True, exist_ok=True)
        _get_autostart_file().write_text(DESKTOP_FILE_CONTENT)
        return True
    except OSError:
        return False


def disable_autostart() -> bool:
    """Disable autostart. Returns True if successful (or already disabled)."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_WRITE) as key:
                winreg.DeleteValue(key, APP_NAME)
            return True
        except FileNotFoundError:
            return True  # Already not set
        except OSError as e:
            logger.error(f"Failed to disable Windows autostart: {e}")
            return False

    try:
        autostart_file = _get_autostart_file()
        if autostart_file.exists():
            autostart_file.unlink()
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """Set autostart state. Returns True if successful."""
    if enabled:
        return enable_autostart()
    else:
        return disable_autostart()
