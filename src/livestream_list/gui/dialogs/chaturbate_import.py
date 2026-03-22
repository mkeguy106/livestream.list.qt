"""Dialog for importing Chaturbate followed channels using QWebEngine.

Uses the Chaturbate room-list API:
- Online:  /api/ts/roomlist/room-list/?follow=true&limit=90&offset=0
- Offline: /api/ts/roomlist/room-list/?follow=true&limit=90&offline=true&offset=0

These endpoints require session cookies from QWebEngine (logged-in user).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.models import Channel, StreamPlatform

if TYPE_CHECKING:
    from ..app import Application

logger = logging.getLogger(__name__)

# Dismiss Chaturbate's age verification overlay
_DISMISS_AGE_GATE_JS = """
(function() {
    var btn = document.getElementById('close_entrance_terms');
    if (btn) { btn.click(); return 'clicked'; }
    var overlay = document.getElementById('entrance_terms_overlay');
    if (overlay && (overlay.style.visibility === 'visible'
                    || overlay.style.display !== 'none')) {
        overlay.style.display = 'none';
        overlay.style.visibility = 'hidden';
        return 'force_hidden';
    }
    return 'no_gate';
})();
"""

# Fetch ALL followed rooms (online + offline) via API using sync XHR.
# PySide6 runJavaScript can't await Promises, so we use synchronous XHR.
_FETCH_ALL_FOLLOWS_JS = """
(function() {
    function syncGet(url) {
        try {
            var xhr = new XMLHttpRequest();
            xhr.open('GET', url, false);
            xhr.send();
            if (xhr.status === 200) return JSON.parse(xhr.responseText);
            return null;
        } catch(e) { return null; }
    }

    var onlineRooms = [];
    var offlineRooms = [];

    // 1. Fetch online followed rooms (paginated)
    var offset = 0;
    while (true) {
        var data = syncGet(
            '/api/ts/roomlist/room-list/?follow=true&limit=90&offset=' + offset
        );
        if (data && data.rooms && data.rooms.length > 0) {
            data.rooms.forEach(function(r) {
                var name = (r.username || r.room || r.slug || r.name || '')
                    .toLowerCase();
                if (name && onlineRooms.indexOf(name) === -1)
                    onlineRooms.push(name);
            });
            offset += 90;
            if (offset >= (data.total_count || 0)) break;
        } else break;
    }

    // Also extract from the online_followed_rooms endpoint
    var metaData = syncGet('/follow/api/online_followed_rooms/');
    var total = 0;
    if (metaData) {
        total = metaData.total || 0;
        if (metaData.online_rooms) {
            metaData.online_rooms.forEach(function(r) {
                if (r && r.room) {
                    var name = r.room.toLowerCase();
                    if (onlineRooms.indexOf(name) === -1)
                        onlineRooms.push(name);
                }
            });
        }
    }

    // 2. Fetch offline followed rooms (paginated, using offline=true)
    offset = 0;
    while (true) {
        var data2 = syncGet(
            '/api/ts/roomlist/room-list/?follow=true&limit=90&offline=true&offset='
            + offset
        );
        if (data2 && data2.rooms && data2.rooms.length > 0) {
            data2.rooms.forEach(function(r) {
                var name = (r.username || r.room || r.slug || r.name || '')
                    .toLowerCase();
                if (name && offlineRooms.indexOf(name) === -1
                    && onlineRooms.indexOf(name) === -1)
                    offlineRooms.push(name);
            });
            var totalOffline = data2.total_count || 0;
            offset += 90;
            if (offset >= totalOffline) break;
        } else break;
    }

    return JSON.stringify({
        online: onlineRooms,
        offline: offlineRooms,
        total: total
    });
})();
"""


class ChaturbateImportDialog(QDialog):
    """Dialog for importing Chaturbate followed channels.

    Uses QWebEngine to load a Chaturbate page (for session cookies), then
    calls the room-list API via synchronous XHR to get all followed rooms.
    """

    import_complete = Signal(object)  # list[Channel] or Exception

    def __init__(self, parent, app: Application):
        super().__init__(parent)
        self.app = app
        self._page = None
        self._added_count = 0
        self._age_gate_handled = False

        self.setWindowTitle("Import Chaturbate Follows")
        self.setMinimumWidth(400)
        self.setMinimumHeight(250)

        layout = QVBoxLayout(self)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        # Ready page (0)
        ready_page = QWidget()
        ready_layout = QVBoxLayout(ready_page)
        ready_layout.setAlignment(Qt.AlignCenter)

        ready_label = QLabel(
            "Import your followed Chaturbate channels.\n"
            "This uses your Chaturbate login to fetch your followed rooms."
        )
        ready_label.setAlignment(Qt.AlignCenter)
        ready_label.setWordWrap(True)
        ready_layout.addWidget(ready_label)

        import_btn = QPushButton("Import Follows")
        import_btn.clicked.connect(self._start_import)
        ready_layout.addWidget(import_btn, 0, Qt.AlignCenter)

        self.stack.addWidget(ready_page)

        # Importing page (1)
        importing_page = QWidget()
        importing_layout = QVBoxLayout(importing_page)
        importing_layout.setAlignment(Qt.AlignCenter)

        self.import_label = QLabel("Fetching followed channels...")
        self.import_label.setAlignment(Qt.AlignCenter)
        self.import_label.setWordWrap(True)
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

    def _start_import(self):
        """Load Chaturbate page to establish session, then call APIs."""
        self.stack.setCurrentIndex(1)
        self.close_btn.setEnabled(False)

        from ..chat.chaturbate_web_chat import _get_shared_profile, has_chaturbate_login

        if not has_chaturbate_login():
            self._on_import_complete(ValueError("Not logged in to Chaturbate"))
            return

        profile = _get_shared_profile()
        if not profile:
            self._on_import_complete(ValueError("QWebEngine not available"))
            return

        from PySide6.QtWebEngineCore import QWebEnginePage

        self._page = QWebEnginePage(profile, self)
        self._page.loadFinished.connect(self._on_page_loaded)
        self._age_gate_handled = False
        self.import_label.setText("Loading Chaturbate...")
        self._page.setUrl(QUrl("https://chaturbate.com/followed-cams/"))

    def _on_page_loaded(self, ok: bool) -> None:
        """Handle page load."""
        url = self._page.url().toString() if self._page else "unknown"
        logger.info(f"Chaturbate import: page loaded ok={ok} url={url}")

        if not ok:
            self._on_import_complete(RuntimeError("Failed to load Chaturbate page"))
            return

        if not self._age_gate_handled:
            self._age_gate_handled = True
            self._page.runJavaScript(_DISMISS_AGE_GATE_JS, 0, self._on_age_gate)
        else:
            # Second load after age gate — fetch API data
            self.import_label.setText("Fetching followed rooms...")
            QTimer.singleShot(1000, self._fetch_follows)

    def _on_age_gate(self, result) -> None:
        """Handle age gate dismissal."""
        logger.info(f"Chaturbate age gate: {result}")
        if result in ("clicked", "force_hidden"):
            self.import_label.setText("Reloading after age verification...")
            QTimer.singleShot(
                2000,
                lambda: (
                    self._page.setUrl(QUrl("https://chaturbate.com/followed-cams/"))
                    if self._page
                    else None
                ),
            )
        else:
            self.import_label.setText("Fetching followed rooms...")
            QTimer.singleShot(1000, self._fetch_follows)

    def _fetch_follows(self) -> None:
        """Call Chaturbate API to get all followed rooms."""
        if not self._page:
            self._on_import_complete(RuntimeError("Page not available"))
            return

        self.import_label.setText("Querying Chaturbate API...")
        self._page.runJavaScript(_FETCH_ALL_FOLLOWS_JS, 0, self._on_api_data)

    def _on_api_data(self, raw) -> None:
        """Process API response and create Channel objects."""
        if not isinstance(raw, str) or not raw:
            logger.warning("Chaturbate API: empty result from JS")
            self._on_import_complete(RuntimeError("Empty API response"))
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"Chaturbate API: JSON parse error: {e}")
            self._on_import_complete(RuntimeError(f"JSON parse error: {e}"))
            return

        online = data.get("online", [])
        offline = data.get("offline", [])
        total = data.get("total", 0)

        logger.info(
            f"Chaturbate API: {len(online)} online + {len(offline)} offline "
            f"= {len(online) + len(offline)} total (expected {total})"
        )

        # Create Channel objects
        channels: list[Channel] = []
        seen: set[str] = set()
        for username in online + offline:
            username = username.lower()
            if username in seen:
                continue
            seen.add(username)
            channels.append(
                Channel(
                    channel_id=username,
                    platform=StreamPlatform.CHATURBATE,
                    display_name=username,
                    imported_by="chaturbate_follows",
                )
            )

        if self._page:
            self._page.deleteLater()
            self._page = None

        self.import_complete.emit(channels)

    def _on_import_complete(self, result):
        """Handle import completion on the main thread."""
        if isinstance(result, Exception):
            self.done_label.setText(f"Import failed: {result}")
            self.stack.setCurrentIndex(2)
            self.close_btn.setEnabled(True)
            return

        channels = result
        if not channels:
            self.done_label.setText("No followed channels found.")
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
