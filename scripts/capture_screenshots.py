#!/usr/bin/env python3
"""Capture screenshots of the app with sample data for README/docs.

Usage:
    # With a real display (dev machine)
    python scripts/capture_screenshots.py

    # With Xvfb (CI or headless)
    xvfb-run -a --server-args="-screen 0 1920x1080x24" python scripts/capture_screenshots.py

Screenshots are saved to docs/screenshots/.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add src to path so we can import the app
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from livestream_list.core.models import Channel, Livestream, StreamPlatform
from livestream_list.core.monitor import StreamMonitor
from livestream_list.core.settings import Settings
from livestream_list.gui.theme import ThemeManager, get_app_stylesheet

OUTPUT_DIR = Path(__file__).parent.parent / "docs" / "screenshots"


def create_sample_data() -> list[tuple[Channel, Livestream]]:
    """Create realistic sample channels and livestreams."""
    now = datetime.now(timezone.utc)
    data = [
        # Live Twitch channels
        (
            Channel(
                channel_id="shroud",
                platform=StreamPlatform.TWITCH,
                display_name="shroud",
                favorite=True,
            ),
            Livestream(
                channel=None,  # set below
                live=True,
                title="CS2 Ranked Grind - Road to Global",
                game="Counter-Strike 2",
                viewers=42_831,
                start_time=now - timedelta(hours=3, minutes=22),
            ),
        ),
        (
            Channel(
                channel_id="pokimane",
                platform=StreamPlatform.TWITCH,
                display_name="pokimane",
            ),
            Livestream(
                channel=None,
                live=True,
                title="cozy morning stream | chatting & reacting",
                game="Just Chatting",
                viewers=18_204,
                start_time=now - timedelta(hours=1, minutes=45),
            ),
        ),
        (
            Channel(
                channel_id="lirik",
                platform=StreamPlatform.TWITCH,
                display_name="LIRIK",
                favorite=True,
            ),
            Livestream(
                channel=None,
                live=True,
                title="NEW GAME MONDAY - trying out the new survival game",
                game="Schedule I",
                viewers=8_912,
                start_time=now - timedelta(hours=5, minutes=10),
            ),
        ),
        # Offline Twitch
        (
            Channel(
                channel_id="summit1g",
                platform=StreamPlatform.TWITCH,
                display_name="summit1g",
            ),
            Livestream(
                channel=None,
                live=False,
                last_live_time=now - timedelta(hours=6),
            ),
        ),
        # Live YouTube
        (
            Channel(
                channel_id="UCX6OQ3DkcsbYNE6H8uQQuVA",
                platform=StreamPlatform.YOUTUBE,
                display_name="MrBeast",
                favorite=True,
            ),
            Livestream(
                channel=None,
                live=True,
                title="LIVE: Building 100 Houses For People In Need",
                game="Entertainment",
                viewers=127_450,
                start_time=now - timedelta(hours=2, minutes=15),
            ),
        ),
        # Offline YouTube
        (
            Channel(
                channel_id="UC-lHJZR3Gqxm24_Vd_AJ5Yw",
                platform=StreamPlatform.YOUTUBE,
                display_name="PewDiePie",
            ),
            Livestream(
                channel=None,
                live=False,
                last_live_time=now - timedelta(days=3),
            ),
        ),
        # Live Kick
        (
            Channel(
                channel_id="xqc",
                platform=StreamPlatform.KICK,
                display_name="xQc",
            ),
            Livestream(
                channel=None,
                live=True,
                title="JUICING | variety gaming and reacting all day",
                game="Just Chatting",
                viewers=65_210,
                start_time=now - timedelta(hours=7, minutes=30),
            ),
        ),
        # Offline Kick
        (
            Channel(
                channel_id="nickmercs",
                platform=StreamPlatform.KICK,
                display_name="NICKMERCS",
                favorite=True,
            ),
            Livestream(
                channel=None,
                live=False,
                last_live_time=now - timedelta(hours=14),
            ),
        ),
        # Live Chaturbate
        (
            Channel(
                channel_id="exampleroom",
                platform=StreamPlatform.CHATURBATE,
                display_name="exampleroom",
            ),
            Livestream(
                channel=None,
                live=True,
                title="",
                viewers=3_412,
                start_time=now - timedelta(hours=1),
            ),
        ),
        # Offline Chaturbate
        (
            Channel(
                channel_id="anotherroom",
                platform=StreamPlatform.CHATURBATE,
                display_name="anotherroom",
            ),
            Livestream(
                channel=None,
                live=False,
                last_live_time=now - timedelta(days=1, hours=8),
            ),
        ),
    ]

    # Set channel references on livestreams
    for channel, livestream in data:
        livestream.channel = channel

    return data


class MockApplication(QObject):
    """Minimal mock of Application with just enough for MainWindow to initialize.

    MainWindow treats self.app as both an Application and a QApplication (since
    Application extends QApplication). We proxy QApplication methods to the real
    QApplication instance.
    """

    stream_online = Signal(object)
    refresh_complete = Signal()
    refresh_error = Signal(str)

    def __init__(self, settings: Settings, monitor: StreamMonitor):
        super().__init__()
        self.settings = settings
        self.monitor = monitor
        self.streamlink = None
        self.chat_manager = None
        self._chat_window = None
        self._qt_app = QApplication.instance()
        self.tray_icon = None

    def styleSheet(self):  # noqa: N802
        return self._qt_app.styleSheet()

    def setStyleSheet(self, stylesheet):  # noqa: N802
        self._qt_app.setStyleSheet(stylesheet)

    def save_settings(self):
        pass

    def save_channels(self):
        pass

    def refresh(self, on_complete=None):
        if on_complete:
            on_complete()


def capture_screenshot(window, filename: str) -> Path:
    """Capture a widget screenshot and save it."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    pixmap = window.grab()
    pixmap.save(str(path))
    print(f"  Saved: {path}")
    return path


def main():
    print("Starting screenshot capture...")

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Livestream List (Qt)")

    # Create settings with defaults (no disk I/O)
    settings = Settings()

    # Create monitor and inject sample data
    monitor = StreamMonitor(settings)
    sample_data = create_sample_data()

    for channel, livestream in sample_data:
        key = channel.unique_key
        monitor._channels[key] = channel
        monitor._livestreams[key] = livestream

    # Initialize theme
    ThemeManager.set_settings(settings)
    qt_app.setStyleSheet(get_app_stylesheet())

    # Create mock app and main window
    mock_app = MockApplication(settings, monitor)

    from livestream_list.gui.main_window import MainWindow

    window = MainWindow(mock_app)
    window._initial_check_complete = True
    window.resize(1280, 800)
    window.show()

    # Trigger the stream list to populate from injected data
    window.refresh_stream_list()

    # Process events to let the UI render fully
    qt_app.processEvents()

    # Use a timer to capture after the window is fully painted
    def do_captures():
        print("\nCapturing screenshots...")

        # 1. Main window - dark theme (default)
        capture_screenshot(window, "main-window-dark.png")

        print("\nDone! Screenshots saved to docs/screenshots/")
        qt_app.quit()

    # Delay capture to ensure full render
    QTimer.singleShot(500, do_captures)

    qt_app.exec()


if __name__ == "__main__":
    main()
