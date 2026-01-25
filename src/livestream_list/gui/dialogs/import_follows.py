"""Dialog for OAuth login and importing followed channels."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.models import StreamPlatform

if TYPE_CHECKING:
    from ..app import Application

logger = logging.getLogger(__name__)


class ImportFollowsDialog(QDialog):
    """Dialog for OAuth login and importing followed channels."""

    # Signals for thread-safe UI updates
    login_complete = Signal()
    import_complete = Signal(list)

    def __init__(
        self,
        parent,
        app: Application,
        platform: StreamPlatform,
        start_import: bool = False,
    ):
        super().__init__(parent)
        self.app = app
        self.platform = platform
        self._start_import = start_import
        self._added_count = 0  # Track imported channels

        self.setWindowTitle(f"Import {platform.value.title()} Follows")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Stack for different states
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        # Login page
        login_page = QWidget()
        login_layout = QVBoxLayout(login_page)
        login_layout.setAlignment(Qt.AlignCenter)

        login_label = QLabel(
            f"Log in to {platform.value.title()} to import your followed channels."
        )
        login_label.setAlignment(Qt.AlignCenter)
        login_layout.addWidget(login_label)

        login_btn = QPushButton(f"Login with {platform.value.title()}")
        login_btn.clicked.connect(self._start_login)
        login_layout.addWidget(login_btn, 0, Qt.AlignCenter)

        self.stack.addWidget(login_page)

        # Waiting page
        waiting_page = QWidget()
        waiting_layout = QVBoxLayout(waiting_page)
        waiting_layout.setAlignment(Qt.AlignCenter)

        waiting_label = QLabel(
            "Waiting for authorization...\nPlease complete login in your browser."
        )
        waiting_label.setAlignment(Qt.AlignCenter)
        waiting_layout.addWidget(waiting_label)

        self.stack.addWidget(waiting_page)

        # Ready page
        ready_page = QWidget()
        ready_layout = QVBoxLayout(ready_page)
        ready_layout.setAlignment(Qt.AlignCenter)

        ready_label = QLabel(
            f"You're logged in to {platform.value.title()}!\n"
            "Ready to import your followed channels."
        )
        ready_label.setAlignment(Qt.AlignCenter)
        ready_layout.addWidget(ready_label)

        import_btn = QPushButton("Import Followed Channels")
        import_btn.clicked.connect(self._start_import_follows)
        ready_layout.addWidget(import_btn, 0, Qt.AlignCenter)

        self.stack.addWidget(ready_page)

        # Importing page
        importing_page = QWidget()
        importing_layout = QVBoxLayout(importing_page)
        importing_layout.setAlignment(Qt.AlignCenter)

        self.import_label = QLabel("Fetching followed channels...")
        self.import_label.setAlignment(Qt.AlignCenter)
        importing_layout.addWidget(self.import_label)

        self.import_progress = QProgressBar()
        self.import_progress.setMaximumWidth(300)
        importing_layout.addWidget(self.import_progress, 0, Qt.AlignCenter)

        self.import_detail = QLabel("")
        self.import_detail.setAlignment(Qt.AlignCenter)
        self.import_detail.setStyleSheet("color: gray;")
        importing_layout.addWidget(self.import_detail)

        self.stack.addWidget(importing_page)

        # Close button
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        layout.addWidget(self.close_btn, 0, Qt.AlignCenter)

        # Determine initial state
        if self.app.settings.twitch.access_token:
            if start_import:
                self.stack.setCurrentIndex(2)  # Ready
                QTimer.singleShot(100, self._start_import_follows)
            else:
                self.stack.setCurrentIndex(2)  # Ready
        else:
            self.stack.setCurrentIndex(0)  # Login

    def _start_login(self):
        """Start the OAuth login flow."""
        self.stack.setCurrentIndex(1)  # Waiting

        def login_thread():
            try:
                from ...api.twitch import TwitchApiClient

                client = TwitchApiClient(self.app.settings.twitch)

                async def do_login():
                    success = await client.oauth_login(timeout=120)
                    if success:
                        self.app.settings.save()
                    return success

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(do_login())
                    return result
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Login error: {e}")
                return False

        def on_complete():
            if self.app.settings.twitch.access_token:
                self.stack.setCurrentIndex(2)  # Ready
            else:
                self.stack.setCurrentIndex(0)  # Back to login
                QMessageBox.warning(self, "Login Failed", "Failed to log in. Please try again.")

        # Connect signal for thread-safe callback
        self.login_complete.connect(on_complete)

        def run_login():
            login_thread()
            # Emit signal to update UI on main thread
            self.login_complete.emit()

        import threading

        thread = threading.Thread(target=run_login)
        thread.daemon = True
        thread.start()

    def _start_import_follows(self):
        """Start importing followed channels."""
        self.stack.setCurrentIndex(3)  # Importing
        self.close_btn.setEnabled(False)

        def import_thread():
            try:
                from ...api.twitch import TwitchApiClient

                async def do_import():
                    client = TwitchApiClient(self.app.settings.twitch)
                    client._session = None

                    # Get followed channels (uses current authenticated user)
                    try:
                        channels = await client.get_followed_channels()
                    except PermissionError as e:
                        logger.error(f"Permission error: {e}")
                        return []

                    await client.close()
                    return channels

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    channels = loop.run_until_complete(do_import())
                    return channels
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Import error: {e}")
                return []

        def process_channels(channels):
            if not channels:
                self.import_label.setText("No channels found or error occurred.")
                self.close_btn.setEnabled(True)
                return

            self.import_progress.setRange(0, len(channels))

            added = 0
            for i, ch in enumerate(channels):
                if self.app.monitor.add_channel_direct(ch):
                    added += 1

                self.import_progress.setValue(i + 1)
                self.import_detail.setText(f"Added: {ch.display_name or ch.channel_id}")
                QApplication.processEvents()

            self.app.save_channels()
            self.import_label.setText(f"Import complete! Added {added} channels.")
            self.close_btn.setEnabled(True)
            self._added_count = added  # Store for later use

        # Connect signal for thread-safe callback
        self.import_complete.connect(process_channels)

        def run_import():
            channels = import_thread()
            self.import_complete.emit(channels)

        import threading

        thread = threading.Thread(target=run_import, daemon=True)
        thread.start()
