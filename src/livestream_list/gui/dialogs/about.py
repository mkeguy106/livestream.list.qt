"""About dialog with update check functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ...__version__ import __version__

if TYPE_CHECKING:
    from ..app import Application


class AboutDialog(QDialog):
    """About dialog with update check functionality."""

    GITHUB_REPO = "mkeguy106/livestream.list.qt"

    def __init__(self, parent, app: Application):
        super().__init__(parent)
        self.app = app

        self.setWindowTitle("About Livestream List (Qt)")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)

        # App icon and name
        title_label = QLabel("<h2>Livestream List (Qt)</h2>")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # Version
        version_label = QLabel(f"<p>Version {__version__}</p>")
        version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(version_label)

        # Description
        desc_label = QLabel(
            "<p>Monitor your favorite livestreams on<br>Twitch, YouTube, and Kick.</p>"
        )
        desc_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc_label)

        # License
        license_label = QLabel("<p>Licensed under GPL-2.0</p>")
        license_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(license_label)

        # GitHub link
        github_label = QLabel(
            f'<p><a href="https://github.com/{self.GITHUB_REPO}">GitHub Repository</a></p>'
        )
        github_label.setAlignment(Qt.AlignCenter)
        github_label.setOpenExternalLinks(True)
        layout.addWidget(github_label)

        layout.addSpacing(10)

        # Update status label (hidden initially)
        self.update_status = QLabel()
        self.update_status.setAlignment(Qt.AlignCenter)
        self.update_status.setWordWrap(True)
        self.update_status.hide()
        layout.addWidget(self.update_status)

        # Buttons
        button_layout = QHBoxLayout()

        self.check_updates_btn = QPushButton("Check for Updates")
        self.check_updates_btn.clicked.connect(self._check_for_updates)
        button_layout.addWidget(self.check_updates_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _check_for_updates(self):
        """Check GitHub for the latest release."""
        self.check_updates_btn.setEnabled(False)
        self.check_updates_btn.setText("Checking...")
        self.update_status.setText("Checking for updates...")
        self.update_status.setStyleSheet("")
        self.update_status.show()

        # Defer HTTP request to allow UI to update naturally (avoid processEvents antipattern)
        QTimer.singleShot(0, self._do_update_check)

    def _do_update_check(self) -> None:
        """Perform the actual update check (called after UI updates)."""
        import json
        import urllib.request

        try:
            url = f"https://api.github.com/repos/{self.GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "Livestream-List-Qt"})

            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())

            latest_version = data.get("tag_name", "").lstrip("v")
            current_version = __version__

            if self._compare_versions(latest_version, current_version) > 0:
                self.update_status.setText(
                    f"<b>Update available!</b><br>"
                    f"Current: v{current_version}<br>"
                    f"Latest: v{latest_version}<br><br>"
                    f"<a href='{data.get('html_url', '')}'>Download from GitHub</a>"
                )
                self.update_status.setStyleSheet("color: #4CAF50;")
                self.update_status.setOpenExternalLinks(True)
            else:
                self.update_status.setText("You're running the latest version!")
                self.update_status.setStyleSheet("color: #2196F3;")

        except Exception as e:
            self.update_status.setText(f"Failed to check for updates:\n{str(e)}")
            self.update_status.setStyleSheet("color: #f44336;")

        self.check_updates_btn.setEnabled(True)
        self.check_updates_btn.setText("Check for Updates")

    def _compare_versions(self, v1: str, v2: str) -> int:
        """Compare two version strings. Returns >0 if v1 > v2, <0 if v1 < v2, 0 if equal."""

        def parse_version(v):
            parts = []
            for part in v.split("."):
                try:
                    parts.append(int(part))
                except ValueError:
                    parts.append(0)
            return parts

        p1 = parse_version(v1)
        p2 = parse_version(v2)

        # Pad to same length
        max_len = max(len(p1), len(p2))
        p1.extend([0] * (max_len - len(p1)))
        p2.extend([0] * (max_len - len(p2)))

        for a, b in zip(p1, p2):
            if a > b:
                return 1
            if a < b:
                return -1
        return 0
