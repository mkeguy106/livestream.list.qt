"""Shared window utility functions (always-on-top via KWin / Qt fallback)."""

import logging
import os
import subprocess
import tempfile

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMainWindow

logger = logging.getLogger(__name__)


def is_kde_plasma() -> bool:
    """Check if running on KDE Plasma (works for both X11 and Wayland)."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    return "kde" in desktop


def kwin_set_keep_above(windows: list[QMainWindow], on_top: bool) -> None:
    """Use KWin scripting via D-Bus to set keepAbove on windows.

    This works on both KDE X11 and Wayland without recreating the window.
    """
    titles = [w.windowTitle() for w in windows if w.isVisible()]
    if not titles:
        return

    # Match by prefix — KDE appends " — AppName" to the Qt windowTitle
    conditions = " || ".join(f'c.caption.indexOf("{title}") === 0' for title in titles)
    value = "true" if on_top else "false"
    script_content = (
        "var clients = workspace.windowList();\n"
        "for (var i = 0; i < clients.length; i++) {\n"
        "    var c = clients[i];\n"
        f"    if ({conditions}) {{\n"
        f"        c.keepAbove = {value};\n"
        "    }\n"
        "}\n"
    )

    fd, script_path = tempfile.mkstemp(suffix=".js", prefix="llqt_aot_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(script_content)

        plugin_name = "llqt_always_on_top"
        # Unload any previous instance
        subprocess.run(
            [
                "qdbus6",
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting.unloadScript",
                plugin_name,
            ],
            capture_output=True,
            timeout=3,
        )
        # Load and run the script
        result = subprocess.run(
            [
                "qdbus6",
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting.loadScript",
                script_path,
                plugin_name,
            ],
            capture_output=True,
            timeout=3,
            text=True,
        )
        if result.returncode == 0:
            subprocess.run(
                ["qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.start"],
                capture_output=True,
                timeout=3,
            )
            # Unload after a short delay via a singleshot
            QTimer.singleShot(500, lambda: _kwin_unload_script(plugin_name))
        else:
            logger.warning(f"KWin loadScript failed: {result.stderr}")
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"KWin scripting unavailable: {e}")
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def _kwin_unload_script(plugin_name: str) -> None:
    """Unload a KWin script by plugin name (cleanup)."""
    try:
        subprocess.run(
            [
                "qdbus6",
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting.unloadScript",
                plugin_name,
            ],
            capture_output=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def apply_always_on_top_qt(window: QMainWindow, on_top: bool) -> None:
    """Fallback: toggle WindowStaysOnTopHint via Qt (causes window recreation)."""
    flags = window.windowFlags()
    if on_top:
        flags |= Qt.WindowType.WindowStaysOnTopHint
    else:
        flags &= ~Qt.WindowType.WindowStaysOnTopHint
    was_visible = window.isVisible()
    geo = window.geometry()
    window.setWindowFlags(flags)
    window.setGeometry(geo)
    if was_visible:
        window.show()
        window.raise_()
        window.activateWindow()


def apply_always_on_top(windows: list[QMainWindow], on_top: bool) -> None:
    """Apply always-on-top to a list of windows, using KWin on KDE or Qt fallback."""
    if is_kde_plasma():
        kwin_set_keep_above(windows, on_top)
    else:
        for win in windows:
            apply_always_on_top_qt(win, on_top)
