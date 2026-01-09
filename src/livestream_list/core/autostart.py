"""Autostart management for Livestream List."""

import os
import shutil
from pathlib import Path


DESKTOP_FILE_NAME = "life.covert.livestreamListQt.desktop"

# Check if running in Flatpak
IS_FLATPAK = os.path.exists("/.flatpak-info")

# Use appropriate Exec command based on environment
EXEC_COMMAND = "flatpak run life.covert.livestreamListQt" if IS_FLATPAK else "livestream-list-qt"

DESKTOP_FILE_CONTENT = f"""[Desktop Entry]
Name=Livestream List Qt
Comment=Monitor your favorite livestreams (Qt version)
Exec={EXEC_COMMAND}
Icon=life.covert.livestreamListQt
Terminal=false
Type=Application
Categories=AudioVideo;Video;Network;
Keywords=twitch;stream;live;monitor;
StartupNotify=true
X-GNOME-Autostart-enabled=true
"""


def get_autostart_dir() -> Path:
    """Get the XDG autostart directory."""
    return Path.home() / ".config" / "autostart"


def get_autostart_file() -> Path:
    """Get the path to our autostart desktop file."""
    return get_autostart_dir() / DESKTOP_FILE_NAME


def is_autostart_enabled() -> bool:
    """Check if autostart is currently enabled."""
    return get_autostart_file().exists()


def enable_autostart() -> bool:
    """Enable autostart by creating desktop file in autostart directory.

    Returns True if successful, False otherwise.
    """
    try:
        autostart_dir = get_autostart_dir()
        autostart_dir.mkdir(parents=True, exist_ok=True)

        autostart_file = get_autostart_file()
        autostart_file.write_text(DESKTOP_FILE_CONTENT)
        return True
    except (OSError, IOError):
        return False


def disable_autostart() -> bool:
    """Disable autostart by removing desktop file from autostart directory.

    Returns True if successful (or file didn't exist), False on error.
    """
    try:
        autostart_file = get_autostart_file()
        if autostart_file.exists():
            autostart_file.unlink()
        return True
    except (OSError, IOError):
        return False


def set_autostart(enabled: bool) -> bool:
    """Set autostart state.

    Returns True if successful, False otherwise.
    """
    if enabled:
        return enable_autostart()
    else:
        return disable_autostart()
