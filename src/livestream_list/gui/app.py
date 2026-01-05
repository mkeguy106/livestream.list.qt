"""Main Qt application."""

import asyncio
import logging
import sys
from typing import Optional

from PySide6.QtCore import QThread, Signal, QTimer, QObject
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from ..__version__ import __version__
from ..core.settings import Settings
from ..core.monitor import StreamMonitor
from ..core.streamlink import StreamlinkLauncher
from ..notifications.notifier import Notifier

logger = logging.getLogger(__name__)


class AsyncWorker(QThread):
    """Worker thread for running async operations."""

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str, str)  # message, detail

    def __init__(self, coro_func, monitor=None, parent=None):
        super().__init__(parent)
        self.coro_func = coro_func
        self.monitor = monitor

    def run(self):
        """Run the async operation in a new event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Reset aiohttp sessions for this event loop
            if self.monitor:
                for client in self.monitor._clients.values():
                    client._session = None

            result = loop.run_until_complete(self.coro_func())
            self.finished.emit(result)

            # Close sessions before closing loop
            if self.monitor:
                async def close_sessions():
                    for client in self.monitor._clients.values():
                        await client.close()
                loop.run_until_complete(close_sessions())

        except Exception as e:
            logger.error(f"Async worker error: {e}")
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))
        finally:
            if self.monitor:
                for client in self.monitor._clients.values():
                    client._session = None
            loop.close()


class NotificationBridge(QObject):
    """Bridge for handling notifications from background threads."""

    notification_received = Signal(object)  # Livestream

    def __init__(self, notifier: Notifier):
        super().__init__()
        self.notifier = notifier
        self._pending = []
        self._timer = QTimer()
        self._timer.timeout.connect(self._process_pending)
        self._timer.start(100)

    def queue_notification(self, livestream):
        """Queue a notification to be sent."""
        self._pending.append(livestream)

    def _process_pending(self):
        """Process pending notifications."""
        if not self._pending:
            return

        pending = self._pending[:]
        self._pending.clear()

        # Run notifications in a worker thread
        async def send_notifications():
            for livestream in pending:
                try:
                    await self.notifier.notify_stream_online(livestream)
                except Exception as e:
                    logger.error(f"Notification error: {e}")

        worker = AsyncWorker(send_notifications, parent=self)
        worker.start()


class Application(QApplication):
    """Main application class."""

    # Signals for cross-thread communication
    stream_online = Signal(object)  # Livestream
    refresh_complete = Signal()
    status_changed = Signal(str)
    open_stream_requested = Signal(object)  # Livestream - for notification Watch button

    def __init__(self, argv=None):
        super().__init__(argv or sys.argv)

        self.setApplicationName("Livestream List")
        self.setApplicationDisplayName("Livestream List")
        self.setApplicationVersion(__version__)
        self.setOrganizationName("life.covert")
        self.setOrganizationDomain("life.covert")

        # Core components
        self.settings: Optional[Settings] = None
        self.monitor: Optional[StreamMonitor] = None
        self.notifier: Optional[Notifier] = None
        self.streamlink: Optional[StreamlinkLauncher] = None
        self.notification_bridge: Optional[NotificationBridge] = None

        # UI components (set after window creation)
        self.main_window = None
        self.tray_icon = None

        # Timers
        self._refresh_timer: Optional[QTimer] = None
        self._process_check_timer: Optional[QTimer] = None

        # Track active workers to prevent garbage collection
        self._active_workers = []

    def initialize(self):
        """Initialize application components."""
        # Load settings
        self.settings = Settings.load()

        # Connect signal for notification watch button (thread-safe)
        self.open_stream_requested.connect(self._on_notification_open_stream)

        # Initialize core services
        self.monitor = StreamMonitor(self.settings)
        self.streamlink = StreamlinkLauncher(self.settings.streamlink)
        self.notifier = Notifier(
            self.settings.notifications,
            on_open_stream=lambda ls: self.open_stream_requested.emit(ls),
        )

        # Set up notification bridge
        self.notification_bridge = NotificationBridge(self.notifier)

        # Set up monitor callbacks
        self.monitor.on_stream_online(self._on_stream_online)

        # Set up process check timer (every 2 seconds)
        self._process_check_timer = QTimer()
        self._process_check_timer.timeout.connect(self._check_processes)
        self._process_check_timer.start(2000)

    def start_async_init(self, on_channels_loaded=None, on_init_complete=None):
        """Start asynchronous initialization (loading channels, refreshing)."""

        async def init():
            # Load saved channels
            await self.monitor._load_channels()
            channel_count = len(self.monitor.channels)
            return channel_count

        def on_loaded(channel_count):
            if on_channels_loaded:
                on_channels_loaded(channel_count)

            if channel_count > 0:
                self._start_refresh(on_complete=on_init_complete)
            elif on_init_complete:
                on_init_complete()

        worker = AsyncWorker(init, self.monitor, parent=self)
        worker.finished.connect(on_loaded)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._active_workers.append(worker)
        worker.start()

    def _start_refresh(self, on_complete=None, on_progress=None):
        """Start a refresh operation."""

        async def refresh():
            await self.monitor.refresh()
            return self.monitor.livestreams

        def on_finished(result):
            self.refresh_complete.emit()
            if on_complete:
                on_complete()

        worker = AsyncWorker(refresh, self.monitor, parent=self)
        worker.finished.connect(on_finished)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        if on_progress:
            worker.progress.connect(on_progress)
        self._active_workers.append(worker)
        worker.start()

    def refresh(self, on_complete=None):
        """Trigger a manual refresh."""
        self._start_refresh(on_complete=on_complete)

    def start_refresh_timer(self):
        """Start the automatic refresh timer."""
        if self._refresh_timer:
            self._refresh_timer.stop()

        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._on_timed_refresh)
        interval_ms = self.settings.refresh_interval * 1000
        self._refresh_timer.start(interval_ms)

    def update_refresh_interval(self, interval_seconds: int):
        """Update the refresh interval."""
        self.settings.refresh_interval = interval_seconds
        self.settings.save()
        self.start_refresh_timer()

    def _on_timed_refresh(self):
        """Handle timed refresh."""
        logger.info("Timed refresh triggered")
        self._start_refresh()

    def _on_stream_online(self, livestream):
        """Handle stream going online."""
        # Queue notification
        if self.notification_bridge:
            self.notification_bridge.queue_notification(livestream)

        # Emit signal for UI update
        self.stream_online.emit(livestream)

    def _on_notification_open_stream(self, livestream):
        """Handle opening stream from notification."""
        if self.main_window:
            self.main_window.play_stream(livestream)
        elif self.streamlink:
            self.streamlink.launch(livestream)

    def _check_processes(self):
        """Check for dead stream processes and update UI."""
        if self.streamlink:
            stopped = self.streamlink.cleanup_dead_processes()
            if stopped and self.main_window:
                self.main_window.refresh_stream_list()

    def _cleanup_worker(self, worker):
        """Remove worker from active list."""
        if worker in self._active_workers:
            self._active_workers.remove(worker)

    def save_settings(self):
        """Save current settings."""
        if self.settings:
            self.settings.save()

    def save_channels(self):
        """Save channels to disk."""
        if not self.monitor:
            return

        async def do_save():
            await self.monitor.save_channels()

        worker = AsyncWorker(do_save, self.monitor, parent=self)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._active_workers.append(worker)
        worker.start()

    def cleanup(self):
        """Clean up resources."""
        # Stop timers
        if self._refresh_timer:
            self._refresh_timer.stop()
            self._refresh_timer = None

        if self._process_check_timer:
            self._process_check_timer.stop()
            self._process_check_timer = None

        # Wait for active workers to finish
        for worker in self._active_workers[:]:
            if worker.isRunning():
                worker.wait(5000)  # Wait up to 5 seconds
        self._active_workers.clear()

        # Save settings
        self.save_settings()

        # Stop all streams
        if self.streamlink:
            self.streamlink.stop_all_streams()


def run() -> int:
    """Run the application."""
    # Import here to avoid circular imports
    from .main_window import MainWindow
    from .tray import TrayIcon, is_tray_available

    app = Application()
    app.initialize()

    # Create main window
    main_window = MainWindow(app)
    app.main_window = main_window

    # Create tray icon if available
    if is_tray_available():
        tray = TrayIcon(
            main_window,
            on_open=lambda: main_window.show() or main_window.raise_() or main_window.activateWindow(),
            on_quit=app.quit,
            get_notifications_enabled=lambda: app.settings.notifications.enabled,
            set_notifications_enabled=lambda enabled: setattr(app.settings.notifications, 'enabled', enabled) or app.save_settings(),
        )
        tray.show()
        app.tray_icon = tray

    # Show window
    main_window.show()

    # Connect cleanup
    app.aboutToQuit.connect(app.cleanup)

    # Start async initialization
    def on_channels_loaded(count):
        main_window.set_loading_complete()
        if count > 0:
            main_window.set_status("Updating stream status...")

    def on_init_complete():
        main_window._initial_check_complete = True
        main_window.refresh_stream_list()
        main_window.set_status("Ready")
        app.start_refresh_timer()
        # Mark initial load complete for notifications
        app.monitor._initial_load_complete = True

    app.start_async_init(
        on_channels_loaded=on_channels_loaded,
        on_init_complete=on_init_complete,
    )

    return app.exec()
