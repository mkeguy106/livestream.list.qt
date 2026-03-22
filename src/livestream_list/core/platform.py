"""Platform detection utilities.

Centralizes all platform checks so they aren't duplicated across modules.
"""

import os
import subprocess
import sys
from typing import Any

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform == "linux"
IS_FLATPAK = IS_LINUX and (os.path.exists("/.flatpak-info") or "FLATPAK_ID" in os.environ)

SUBPROCESS_NO_WINDOW: dict[str, Any] = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
    if IS_WINDOWS
    else {}
)


def host_command(cmd: list[str]) -> list[str]:
    """Wrap command to run on host if inside Flatpak."""
    if IS_FLATPAK:
        return ["flatpak-spawn", "--host"] + cmd
    return cmd
