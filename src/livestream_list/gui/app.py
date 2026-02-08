"""Main Qt application."""

import asyncio
import logging
import sys
import threading
import time
import traceback
import weakref

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication

from ..__version__ import __version__
from ..chat.manager import ChatManager
from ..core.monitor import StreamMonitor
from ..core.settings import Settings
from ..core.streamlink import StreamlinkLauncher
from ..notifications.notifier import Notifier
from .theme import ThemeManager, get_app_stylesheet

logger = logging.getLogger(__name__)


class MainThreadWatchdog(QObject):
    """Watchdog that detects when the main thread is blocked.

    Logs a warning with stack trace when the main thread doesn't respond
    for more than the threshold time.
    """

    def __init__(self, parent: QObject | None = None, threshold_ms: int = 500):
        super().__init__(parent)
        self._threshold_s = threshold_ms / 1000.0
        self._last_tick = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)  # Check every 100ms
        self._main_thread_id = threading.get_ident()
        logger.info(f"MainThreadWatchdog started (threshold={threshold_ms}ms)")

    def _tick(self) -> None:
        now = time.monotonic()
        delta = now - self._last_tick
        if delta > self._threshold_s:
            # Main thread was blocked - log with stack trace
            logger.warning(f"MAIN THREAD BLOCKED for {delta:.2f}s! Stack traces of all threads:")
            for thread_id, frame in sys._current_frames().items():
                thread_name = "MAIN" if thread_id == self._main_thread_id else f"Thread-{thread_id}"
                stack = "".join(traceback.format_stack(frame))
                logger.warning(f"\n--- {thread_name} ---\n{stack}")
        # Log heartbeat every 10 seconds
        if not hasattr(self, "_last_heartbeat"):
            self._last_heartbeat = now
        if now - self._last_heartbeat >= 10.0:
            logger.warning(f"[WATCHDOG-HEARTBEAT] tick at {now:.1f}")
            self._last_heartbeat = now
        self._last_tick = now

    def stop(self) -> None:
        """Stop the watchdog."""
        self._timer.stop()


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
                self.monitor.reset_all_sessions()

            result = loop.run_until_complete(self.coro_func())
            self.finished.emit(result)

            # Close sessions before closing loop
            if self.monitor:
                loop.run_until_complete(self.monitor.close_all_sessions())

        except Exception as e:
            logger.error(f"Async worker error: {e}")
            import traceback

            traceback.print_exc()
            self.error.emit(str(e))
        finally:
            if self.monitor:
                self.monitor.reset_all_sessions()
            loop.close()


class NotificationBridge(QObject):
    """Bridge for handling notifications from background threads.

    This class queues notifications from background threads and processes them
    on the main thread using a timer. It delegates actual notification sending
    to the Notifier class to avoid code duplication.
    """

    notification_received = Signal(object)  # Livestream

    def __init__(self, notifier: Notifier):
        super().__init__()
        self.notifier = notifier
        self._pending = []
        self._lock = threading.Lock()  # Protect _pending from concurrent access
        self._timer = QTimer(self)  # Parent ensures cleanup on destruction
        self._timer.timeout.connect(self._process_pending)
        self._timer.start(100)

    def queue_notification(self, livestream):
        """Queue a notification to be sent (thread-safe)."""
        with self._lock:
            self._pending.append(livestream)

    def _process_pending(self):
        """Process pending notifications on main thread."""
        # Atomically swap pending list to avoid holding lock during processing
        with self._lock:
            if not self._pending:
                return
            pending = self._pending[:]
            self._pending.clear()

        # Delegate to Notifier's synchronous method (thread-safe, uses subprocess)
        for livestream in pending:
            try:
                self.notifier.send_notification_sync(livestream)
            except Exception as e:
                logger.error(f"Notification error: {e}")

    def cleanup(self):
        """Stop the timer - call on application shutdown."""
        self._timer.stop()

    def send_test_notification(self, livestream):
        """Send a test notification (bypasses enabled check)."""
        self.notifier.send_notification_sync(livestream, is_test=True)


class Application(QApplication):
    """Main application class."""

    # Signals for cross-thread communication
    stream_online = Signal(object)  # Livestream
    refresh_complete = Signal()
    refresh_error = Signal(str)  # Error message for failed refreshes
    status_changed = Signal(str)
    open_stream_requested = Signal(object)  # Livestream - for notification Watch button

    def __init__(self, argv=None):
        super().__init__(argv or sys.argv)

        self.setApplicationName("Livestream List (Qt)")
        self.setApplicationDisplayName("Livestream List (Qt)")
        self.setApplicationVersion(__version__)
        self.setOrganizationName("life.covert")
        self.setOrganizationDomain("life.covert")

        # Core components
        self.settings: Settings | None = None
        self.monitor: StreamMonitor | None = None
        self.notifier: Notifier | None = None
        self.streamlink: StreamlinkLauncher | None = None
        self.notification_bridge: NotificationBridge | None = None
        self.chat_manager: ChatManager | None = None
        self._chat_window = None  # Lazy-initialized ChatWindow

        # UI components (set after window creation)
        # Use weakref for main_window to avoid reference cycles
        self._main_window_ref: weakref.ref | None = None
        self.tray_icon = None

        # Timers
        self._refresh_timer: QTimer | None = None
        self._process_check_timer: QTimer | None = None

        # Track active workers to prevent garbage collection
        self._active_workers = []

        # Prevent concurrent refreshes (causes aiohttp timeout errors)
        self._refresh_in_progress = False

        # Main thread watchdog for debugging lockups
        self._watchdog: MainThreadWatchdog | None = None

    @property
    def main_window(self):
        """Get the main window (may be None if window was destroyed)."""
        if self._main_window_ref is not None:
            return self._main_window_ref()
        return None

    @main_window.setter
    def main_window(self, window):
        """Set the main window using a weak reference."""
        if window is not None:
            self._main_window_ref = weakref.ref(window)
        else:
            self._main_window_ref = None

    def initialize(self):
        """Initialize application components."""
        # Start watchdog to detect main thread lockups (debug tool)
        self._watchdog = MainThreadWatchdog(parent=self, threshold_ms=500)

        # Load settings
        self.settings = Settings.load()

        # Initialize theme manager with settings and apply app stylesheet
        ThemeManager.set_settings(self.settings)
        self.setStyleSheet(get_app_stylesheet())

        # Validate YouTube cookies on startup
        self._validate_youtube_cookies()

        # Connect signal for notification watch button UI updates
        self.open_stream_requested.connect(self._on_notification_open_stream_ui)

        # Initialize core services
        self.monitor = StreamMonitor(self.settings)
        self.streamlink = StreamlinkLauncher(self.settings.streamlink)
        self.notifier = Notifier(
            self.settings.notifications,
            on_open_stream=self._on_notification_watch_clicked,
        )

        # Set up notification bridge
        self.notification_bridge = NotificationBridge(self.notifier)

        # Initialize chat manager
        self.chat_manager = ChatManager(self.settings, monitor=self.monitor, parent=self)
        self.refresh_complete.connect(self.chat_manager.on_refresh_complete)
        self.chat_manager.whisper_received.connect(self._on_whisper_received)
        self.chat_manager.raid_received.connect(self._on_raid_received)

        # Set up monitor callbacks
        self.monitor.on_stream_online(self._on_stream_online)

        # Set up process check timer (every 2 seconds)
        self._process_check_timer = QTimer(self)  # Parent ensures cleanup
        self._process_check_timer.timeout.connect(self._check_processes)
        self._process_check_timer.start(2000)

    def start_async_init(self, on_channels_loaded=None, on_init_complete=None):
        """Start asynchronous initialization (loading channels, refreshing)."""

        async def init():
            # Load saved channels
            await self.monitor.load_channels()
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
        # Prevent concurrent refreshes (causes aiohttp timeout errors)
        if self._refresh_in_progress:
            logger.info("Refresh already in progress, ignoring request")
            if on_complete:
                on_complete()
            return

        self._refresh_in_progress = True

        async def refresh():
            await self.monitor.refresh()
            # Collect any error messages from livestreams
            errors = []
            for ls in self.monitor.livestreams:
                if ls.error_message:
                    errors.append(f"{ls.channel.platform.value}: {ls.error_message}")
            return {"livestreams": self.monitor.livestreams, "errors": errors}

        def on_finished(result):
            self._refresh_in_progress = False
            self.refresh_complete.emit()
            # Emit error signal if there were any errors
            if result and isinstance(result, dict):
                errors = result.get("errors", [])
                if errors:
                    # Show unique errors only
                    unique_errors = list(set(errors))[:3]  # Limit to 3 unique errors
                    error_msg = "; ".join(unique_errors)
                    self.refresh_error.emit(error_msg)
            if on_complete:
                on_complete()

        def on_error(error_msg):
            self._refresh_in_progress = False
            self.refresh_error.emit(f"Refresh failed: {error_msg}")
            if on_complete:
                on_complete()

        worker = AsyncWorker(refresh, self.monitor, parent=self)
        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
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

        self._refresh_timer = QTimer(self)  # Parent ensures cleanup
        self._refresh_timer.timeout.connect(self._on_timed_refresh)
        interval_ms = self.settings.refresh_interval * 1000
        self._refresh_timer.start(interval_ms)

    def update_refresh_interval(self, interval_seconds: int):
        """Update the refresh interval."""
        self.settings.refresh_interval = interval_seconds
        self.settings.save()
        self.start_refresh_timer()

    def _validate_youtube_cookies(self):
        """Check if YouTube cookies are configured and still valid on startup."""
        from PySide6.QtWidgets import QMessageBox

        from ..chat.connections.youtube import validate_cookies

        cookies = self.settings.youtube.cookies
        if not cookies:
            # No cookies configured - nothing to validate
            return

        if not validate_cookies(cookies):
            # Cookies are missing required keys
            QMessageBox.warning(
                None,
                "YouTube Cookies Invalid",
                "Your YouTube cookies appear to be invalid or incomplete.\n\n"
                "Please re-import your cookies from browser in:\n"
                "Preferences > Accounts > YouTube > Import from Browser",
            )
            return

        # Note: We only do basic key validation here.
        # Actual expiry check would require an API call which is slow.
        # Cookie expiry will be detected when trying to send a message.

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

    def _on_notification_watch_clicked(self, livestream):
        """Handle Watch button click from notification.

        Called from desktop-notifier's thread, so we launch streamlink
        directly (thread-safe) for instant response, then schedule UI
        updates via signal.
        """
        # Launch stream immediately (thread-safe - just spawns subprocess)
        if self.streamlink:
            self.streamlink.launch(livestream)

            # Auto-open chat if enabled (browser mode only - built-in handled on main thread)
            if self.settings and self.settings.chat.auto_open and self.settings.chat.enabled:
                if self.settings.chat.mode == "browser":
                    ch = livestream.channel
                    video_id = getattr(livestream, "video_id", None) or ""
                    if self.main_window and hasattr(self.main_window, "_chat_launcher"):
                        self.main_window._chat_launcher.open_chat(
                            ch.channel_id, ch.platform.value, video_id
                        )

        # Schedule UI updates via signal (can be delayed, that's fine)
        self.open_stream_requested.emit(livestream)

    def _on_notification_open_stream_ui(self, livestream):
        """Handle UI updates after stream launched from notification."""
        if self.main_window:
            # Refresh will pick up playing state from streamlink.is_playing()
            self.main_window.refresh_stream_list()
            self.main_window.set_status(f"Playing {livestream.channel.display_name}")

        # Open built-in chat on main thread (if enabled)
        if (
            self.settings
            and self.settings.chat.auto_open
            and self.settings.chat.enabled
            and self.settings.chat.mode == "builtin"
        ):
            self.open_builtin_chat(livestream)

    def _check_processes(self):
        """Check for dead stream processes and update UI."""
        if self.streamlink:
            stopped = self.streamlink.cleanup_dead_processes()
            if stopped and self.main_window:
                self.main_window.refresh_stream_list()
                # Update status based on remaining playing streams
                remaining = self.streamlink._active_streams
                if len(remaining) == 0:
                    self.main_window.set_status("Playback ended")
                elif len(remaining) == 1:
                    # Get the single remaining stream's channel name
                    _, livestream = next(iter(remaining.values()))
                    name = livestream.channel.display_name or livestream.channel.channel_id
                    self.main_window.set_status(f"Playing {name}")
                else:
                    self.main_window.set_status("Playing Streams")

    def _cleanup_worker(self, worker):
        """Remove worker from active list."""
        if worker in self._active_workers:
            self._active_workers.remove(worker)

    def _ensure_chat_window(self):
        """Ensure the ChatWindow exists (lazy-init)."""
        if not self._chat_window:
            from .chat.chat_window import ChatWindow

            self._chat_window = ChatWindow(self.chat_manager, self.settings)
            if self.main_window:
                self._chat_window.chat_settings_requested.connect(
                    self.main_window._show_chat_preferences
                )
        return self._chat_window

    def open_builtin_chat(self, livestream):
        """Open the built-in chat window for a livestream."""
        if not self.chat_manager:
            return
        self._ensure_chat_window()
        self._chat_window.open_chat(livestream)

    def _on_whisper_received(self, platform: str, message) -> None:
        """Handle whisper at app level — show banner in main window."""
        if not self.chat_manager:
            return

        from ..chat.models import ChatMessage as ChatMsg

        # Only show banner for incoming whispers (not our own sent messages)
        is_incoming = (
            isinstance(message, ChatMsg) and message.is_whisper and not message.whisper_target
        )
        if is_incoming and self.main_window:
            sender = message.user.display_name
            sender_id = message.user.id
            self.main_window.show_whisper_banner(sender, sender_id)

    def _on_raid_received(self, channel_key: str, message) -> None:
        """Handle raid event — send desktop notification."""
        if not self.notifier:
            return
        from ..chat.models import ChatMessage as ChatMsg

        if isinstance(message, ChatMsg) and message.is_raid:
            # Get the channel display name from the livestream
            channel_name = channel_key
            if self.chat_manager:
                ls = self.chat_manager._livestreams.get(channel_key)
                if ls:
                    channel_name = ls.display_name or ls.channel.channel_id
            raider_name = message.user.display_name or message.user.name
            self.notifier.send_raid_notification_sync(
                channel_name, raider_name, message.raid_viewer_count
            )

    def open_new_whisper_dialog(self) -> None:
        """Open the New Whisper dialog, ensuring chat window exists."""
        if not self.chat_manager:
            return
        self._ensure_chat_window()
        self._chat_window._show_new_whisper_dialog()
        self._chat_window.show()
        self._chat_window.raise_()
        self._chat_window.activateWindow()

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

    def show_notification_log(self):
        """Show recent notification history in a dialog."""
        from PySide6.QtWidgets import QDialog, QLabel, QListWidget, QVBoxLayout

        if not self.notifier:
            return

        log = self.notifier.notification_log
        parent = self.main_window

        dlg = QDialog(parent)
        dlg.setWindowTitle("Recent Notifications")
        dlg.resize(420, 350)
        layout = QVBoxLayout(dlg)

        if not log:
            label = QLabel("No notifications yet.")
            layout.addWidget(label)
        else:
            list_widget = QListWidget()
            # Show most recent first
            for entry in reversed(log):
                ts = entry["timestamp"].astimezone().strftime("%Y-%m-%d %H:%M")
                platform = entry["platform"].capitalize()
                name = entry["display_name"]
                game = entry["game"]
                line = f"[{ts}] [{platform}] {name}"
                if game:
                    line += f" — {game}"
                list_widget.addItem(line)
            layout.addWidget(list_widget)

        dlg.exec()

    def cleanup(self):
        """Clean up resources."""
        # Stop timers
        if self._refresh_timer:
            self._refresh_timer.stop()
            self._refresh_timer = None

        if self._process_check_timer:
            self._process_check_timer.stop()
            self._process_check_timer = None

        # Stop notification bridge timer
        if self.notification_bridge:
            self.notification_bridge.cleanup()

        # Disconnect all chat connections
        if self.chat_manager:
            self.chat_manager.disconnect_all()

        # Save chat window state
        if self._chat_window:
            self._chat_window.save_window_state()

        # Wait for active workers to finish
        for worker in self._active_workers[:]:
            if worker.isRunning():
                worker.wait(5000)  # Wait up to 5 seconds
        self._active_workers.clear()

        # Flush any pending channel saves (debounced saves)
        if self.monitor:
            self.monitor.flush_pending_save()

        # Save settings
        self.save_settings()

        # Stop all streams
        if self.streamlink:
            self.streamlink.stop_all_streams()


def run() -> int:
    """Run the application."""
    # Import here to avoid circular imports
    from .main_window import MainWindow
    from .tray import TrayIcon, create_app_icon, is_tray_available

    app = Application()
    app.initialize()

    # Set application icon (used for taskbar/window icon)
    app.setWindowIcon(create_app_icon(64))

    # Create main window
    main_window = MainWindow(app)
    app.main_window = main_window

    # Create tray icon if available
    if is_tray_available():

        def restore_window():
            """Restore and focus the main window.

            Uses showNormal() to restore from minimized state. This preserves
            window position on Wayland where hidden windows lose their geometry
            but minimized windows retain it.
            """
            main_window.showNormal()
            main_window.raise_()
            main_window.activateWindow()

        tray = TrayIcon(
            main_window,
            on_open=restore_window,
            on_quit=main_window._quit_app,
            get_notifications_enabled=lambda: app.settings.notifications.enabled,
            set_notifications_enabled=lambda enabled: (
                setattr(app.settings.notifications, "enabled", enabled) or app.save_settings()
            ),
            on_show_notification_log=app.show_notification_log,
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
        app.monitor.set_initial_load_complete()

    app.start_async_init(
        on_channels_loaded=on_channels_loaded,
        on_init_complete=on_init_complete,
    )

    return app.exec()
