"""Dialog for importing YouTube subscriptions using cookie auth."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ..app import Application

logger = logging.getLogger(__name__)


class YouTubeImportDialog(QDialog):
    """Dialog for importing YouTube subscriptions using cookie auth."""

    import_complete = Signal(object)  # list[Channel] or Exception
    filter_progress = Signal(int, int, str)  # checked, total, channel_name

    def __init__(self, parent, app: Application):
        super().__init__(parent)
        self.app = app
        self._added_count = 0

        self.setWindowTitle("Import YouTube Subscriptions")
        self.setMinimumWidth(400)
        self.setMinimumHeight(250)

        layout = QVBoxLayout(self)

        # Stack for different states
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        # Ready page (0)
        ready_page = QWidget()
        ready_layout = QVBoxLayout(ready_page)
        ready_layout.setAlignment(Qt.AlignCenter)

        ready_label = QLabel(
            "Import your YouTube subscriptions as channels.\n"
            "This uses your saved cookies to fetch your subscription list."
        )
        ready_label.setAlignment(Qt.AlignCenter)
        ready_label.setWordWrap(True)
        ready_layout.addWidget(ready_label)

        # Filter checkbox
        self.filter_checkbox = QCheckBox("Only import channels that do livestreams")
        self.filter_checkbox.setChecked(True)
        self.filter_checkbox.setToolTip(
            "Check each channel's /live tab to see if they stream.\n"
            "This takes longer but avoids importing channels that never go live."
        )
        ready_layout.addWidget(self.filter_checkbox, 0, Qt.AlignCenter)

        import_btn = QPushButton("Import Subscriptions")
        import_btn.clicked.connect(self._start_import)
        ready_layout.addWidget(import_btn, 0, Qt.AlignCenter)

        self.stack.addWidget(ready_page)

        # Importing page (1)
        importing_page = QWidget()
        importing_layout = QVBoxLayout(importing_page)
        importing_layout.setAlignment(Qt.AlignCenter)

        self.import_label = QLabel("Fetching subscriptions...")
        self.import_label.setAlignment(Qt.AlignCenter)
        importing_layout.addWidget(self.import_label)

        self.import_progress = QProgressBar()
        self.import_progress.setMaximumWidth(300)
        self.import_progress.setRange(0, 0)  # Indeterminate
        importing_layout.addWidget(self.import_progress, 0, Qt.AlignCenter)

        self.stack.addWidget(importing_page)

        # Done page (2)
        done_page = QWidget()
        done_layout = QVBoxLayout(done_page)
        done_layout.setAlignment(Qt.AlignCenter)

        self.done_label = QLabel("")
        self.done_label.setAlignment(Qt.AlignCenter)
        self.done_label.setWordWrap(True)
        done_layout.addWidget(self.done_label)

        self.stack.addWidget(done_page)

        # Close button
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        layout.addWidget(self.close_btn, 0, Qt.AlignCenter)

        self.import_complete.connect(self._on_import_complete)
        self.filter_progress.connect(self._on_filter_progress)

    def _start_import(self):
        """Start fetching YouTube subscriptions in a background thread."""
        self.stack.setCurrentIndex(1)
        self.close_btn.setEnabled(False)
        self._filter_livestreams = self.filter_checkbox.isChecked()

        cookies = self.app.settings.youtube.cookies
        if not cookies:
            self._on_import_complete(ValueError("No YouTube cookies configured"))
            return

        import threading

        def run():
            try:
                from ...api.youtube import YouTubeApiClient

                client = YouTubeApiClient(
                    self.app.settings.youtube,
                    concurrency=self.app.settings.performance.youtube_concurrency,
                )

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    channels = loop.run_until_complete(client.get_subscriptions(cookies))

                    if self._filter_livestreams and channels:
                        # Filter to only channels that do livestreams
                        def progress_callback(checked, total, name):
                            self.filter_progress.emit(checked, total, name)

                        channels = loop.run_until_complete(
                            client.filter_channels_by_livestream(channels, progress_callback)
                        )

                    self.import_complete.emit(channels)
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"YouTube import error: {e}")
                self.import_complete.emit(e)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    def _on_filter_progress(self, checked: int, total: int, channel_name: str):
        """Update UI with filter progress."""
        self.import_label.setText(f"Checking livestream capability...\n{channel_name}")
        self.import_progress.setRange(0, total)
        self.import_progress.setValue(checked)

    def _on_import_complete(self, result):
        """Handle import completion on the main thread."""
        if isinstance(result, Exception):
            self.done_label.setText(f"Import failed: {result}")
            self.stack.setCurrentIndex(2)
            self.close_btn.setEnabled(True)
            return

        channels = result
        if not channels:
            self.done_label.setText("No subscriptions found.")
            self.stack.setCurrentIndex(2)
            self.close_btn.setEnabled(True)
            return

        # Add channels
        self.import_progress.setRange(0, len(channels))
        added = 0
        for i, ch in enumerate(channels):
            if self.app.monitor.add_channel_direct(ch):
                added += 1
            self.import_progress.setValue(i + 1)
            QApplication.processEvents()

        self.app.save_channels()
        self._added_count = added
        self.done_label.setText(f"Import complete!\nAdded {added} of {len(channels)} channels.")
        self.stack.setCurrentIndex(2)
        self.close_btn.setEnabled(True)
