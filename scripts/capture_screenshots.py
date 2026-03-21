#!/usr/bin/env python3
"""Capture screenshots of the app with sample data for README/docs.

Usage:
    # With a real display (dev machine)
    python scripts/capture_screenshots.py

    # With Qt offscreen platform (CI)
    QT_QPA_PLATFORM=offscreen python scripts/capture_screenshots.py

Screenshots are saved to docs/screenshots/.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add src to path so we can import the app
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMainWindow

from livestream_list.chat.models import ChatBadge, ChatMessage, ChatUser
from livestream_list.core.models import Channel, Livestream, StreamPlatform, UIStyle
from livestream_list.core.monitor import StreamMonitor
from livestream_list.core.settings import BuiltinChatSettings, Settings, ThemeMode
from livestream_list.gui.theme import ThemeManager, get_app_stylesheet

OUTPUT_DIR = Path(__file__).parent.parent / "docs" / "screenshots"

# ── Sample stream data ──────────────────────────────────────────────


def create_sample_data() -> list[tuple[Channel, Livestream]]:
    """Create realistic sample channels and livestreams."""
    now = datetime.now(timezone.utc)
    data = [
        (
            Channel("shroud", StreamPlatform.TWITCH, "shroud", favorite=True),
            Livestream(
                channel=None, live=True,
                title="CS2 Ranked Grind - Road to Global",
                game="Counter-Strike 2", viewers=42_831,
                start_time=now - timedelta(hours=3, minutes=22),
            ),
        ),
        (
            Channel("pokimane", StreamPlatform.TWITCH, "pokimane"),
            Livestream(
                channel=None, live=True,
                title="cozy morning stream | chatting & reacting",
                game="Just Chatting", viewers=18_204,
                start_time=now - timedelta(hours=1, minutes=45),
            ),
        ),
        (
            Channel("lirik", StreamPlatform.TWITCH, "LIRIK", favorite=True),
            Livestream(
                channel=None, live=True,
                title="NEW GAME MONDAY - trying out the new survival game",
                game="Schedule I", viewers=8_912,
                start_time=now - timedelta(hours=5, minutes=10),
            ),
        ),
        (
            Channel("summit1g", StreamPlatform.TWITCH, "summit1g"),
            Livestream(channel=None, live=False, last_live_time=now - timedelta(hours=6)),
        ),
        (
            Channel("UCX6OQ3DkcsbYNE6H8uQQuVA", StreamPlatform.YOUTUBE, "MrBeast", favorite=True),
            Livestream(
                channel=None, live=True,
                title="LIVE: Building 100 Houses For People In Need",
                game="Entertainment", viewers=127_450,
                start_time=now - timedelta(hours=2, minutes=15),
            ),
        ),
        (
            Channel("UC-lHJZR3Gqxm24_Vd_AJ5Yw", StreamPlatform.YOUTUBE, "PewDiePie"),
            Livestream(channel=None, live=False, last_live_time=now - timedelta(days=3)),
        ),
        (
            Channel("xqc", StreamPlatform.KICK, "xQc"),
            Livestream(
                channel=None, live=True,
                title="JUICING | variety gaming and reacting all day",
                game="Just Chatting", viewers=65_210,
                start_time=now - timedelta(hours=7, minutes=30),
            ),
        ),
        (
            Channel("nickmercs", StreamPlatform.KICK, "NICKMERCS", favorite=True),
            Livestream(channel=None, live=False, last_live_time=now - timedelta(hours=14)),
        ),
        (
            Channel("exampleroom", StreamPlatform.CHATURBATE, "exampleroom"),
            Livestream(
                channel=None, live=True, title="", viewers=3_412,
                start_time=now - timedelta(hours=1),
            ),
        ),
        (
            Channel("anotherroom", StreamPlatform.CHATURBATE, "anotherroom"),
            Livestream(
                channel=None, live=False,
                last_live_time=now - timedelta(days=1, hours=8),
            ),
        ),
    ]
    for channel, livestream in data:
        livestream.channel = channel
    return data


# ── Sample chat messages ────────────────────────────────────────────


def create_sample_chat_messages() -> list[ChatMessage]:
    """Create realistic chat messages for screenshot."""
    now = datetime.now(timezone.utc)
    t = now - timedelta(minutes=5)

    def ts(offset_s: int) -> datetime:
        return t + timedelta(seconds=offset_s)

    messages = [
        ChatMessage(
            id="m1", platform=StreamPlatform.TWITCH, timestamp=ts(0),
            text="let's gooo shroud is live!",
            user=ChatUser(
                id="u1", name="gamerfan42", display_name="GamerFan42",
                platform=StreamPlatform.TWITCH, color="#1E90FF", badges=[],
            ),
        ),
        ChatMessage(
            id="m2", platform=StreamPlatform.TWITCH, timestamp=ts(3),
            text="that clutch was insane",
            user=ChatUser(
                id="u2", name="pixelwarrior", display_name="PixelWarrior",
                platform=StreamPlatform.TWITCH, color="#FF6347",
                badges=[ChatBadge(id="subscriber/12", name="subscriber", image_url="")],
            ),
        ),
        ChatMessage(
            id="m3", platform=StreamPlatform.TWITCH, timestamp=ts(7),
            text="@GamerFan42 yeah he's been playing really well today",
            user=ChatUser(
                id="u3", name="streamsniper99", display_name="StreamSniper99",
                platform=StreamPlatform.TWITCH, color="#9ACD32", badges=[],
            ),
        ),
        ChatMessage(
            id="m4", platform=StreamPlatform.TWITCH, timestamp=ts(12),
            text="GG EZ no re",
            user=ChatUser(
                id="u4", name="nightowl_tv", display_name="NightOwl_TV",
                platform=StreamPlatform.TWITCH, color="#DDA0DD",
                badges=[
                    ChatBadge(id="moderator/1", name="moderator", image_url=""),
                    ChatBadge(id="subscriber/24", name="subscriber", image_url=""),
                ],
            ),
        ),
        ChatMessage(
            id="m5", platform=StreamPlatform.TWITCH, timestamp=ts(15),
            text="first time watching, this is amazing",
            is_first_message=True,
            user=ChatUser(
                id="u5", name="newviewer2024", display_name="NewViewer2024",
                platform=StreamPlatform.TWITCH, color="#FFD700", badges=[],
            ),
        ),
        ChatMessage(
            id="m6", platform=StreamPlatform.TWITCH, timestamp=ts(18),
            text="anyone know what sens he uses?",
            user=ChatUser(
                id="u6", name="csgo_tips", display_name="CSGO_Tips",
                platform=StreamPlatform.TWITCH, color="#00CED1", badges=[],
            ),
        ),
        ChatMessage(
            id="m7", platform=StreamPlatform.TWITCH, timestamp=ts(22),
            text="subscribed for 6 months!",
            is_system=True,
            system_text="PixelWarrior subscribed at Tier 1. They've subscribed for 6 months!",
            user=ChatUser(
                id="u2", name="pixelwarrior", display_name="PixelWarrior",
                platform=StreamPlatform.TWITCH, color="#FF6347",
                badges=[ChatBadge(id="subscriber/12", name="subscriber", image_url="")],
            ),
        ),
        ChatMessage(
            id="m8", platform=StreamPlatform.TWITCH, timestamp=ts(25),
            text="LET'S GO SHROUD",
            user=ChatUser(
                id="u7", name="hypemaster", display_name="HypeMaster",
                platform=StreamPlatform.TWITCH, color="#FF4500", badges=[],
            ),
        ),
        ChatMessage(
            id="m9", platform=StreamPlatform.TWITCH, timestamp=ts(30),
            text="400 IQ play right there",
            reply_parent_msg_id="m4",
            reply_parent_display_name="NightOwl_TV",
            reply_parent_text="GG EZ no re",
            user=ChatUser(
                id="u1", name="gamerfan42", display_name="GamerFan42",
                platform=StreamPlatform.TWITCH, color="#1E90FF", badges=[],
            ),
        ),
        ChatMessage(
            id="m10", platform=StreamPlatform.TWITCH, timestamp=ts(34),
            text="can someone clip that? I missed it",
            user=ChatUser(
                id="u8", name="clipchamp", display_name="ClipChamp",
                platform=StreamPlatform.TWITCH, color="#20B2AA",
                badges=[ChatBadge(id="subscriber/3", name="subscriber", image_url="")],
            ),
        ),
        ChatMessage(
            id="m11", platform=StreamPlatform.TWITCH, timestamp=ts(38),
            text="this map is so good for cs2",
            user=ChatUser(
                id="u9", name="mapexpert", display_name="MapExpert",
                platform=StreamPlatform.TWITCH, color="#BA55D3", badges=[],
            ),
        ),
        ChatMessage(
            id="m12", platform=StreamPlatform.TWITCH, timestamp=ts(42),
            text="@NewViewer2024 welcome! you picked a great stream to start with",
            user=ChatUser(
                id="u4", name="nightowl_tv", display_name="NightOwl_TV",
                platform=StreamPlatform.TWITCH, color="#DDA0DD",
                badges=[
                    ChatBadge(id="moderator/1", name="moderator", image_url=""),
                    ChatBadge(id="subscriber/24", name="subscriber", image_url=""),
                ],
            ),
        ),
        ChatMessage(
            id="m13", platform=StreamPlatform.TWITCH, timestamp=ts(46),
            text="ace ace ace!!! LETS GOOO",
            user=ChatUser(
                id="u10", name="fragmovie", display_name="FragMovie",
                platform=StreamPlatform.TWITCH, color="#FF1493", badges=[],
            ),
        ),
        ChatMessage(
            id="m14", platform=StreamPlatform.TWITCH, timestamp=ts(50),
            text="how does he make it look so easy",
            user=ChatUser(
                id="u3", name="streamsniper99", display_name="StreamSniper99",
                platform=StreamPlatform.TWITCH, color="#9ACD32", badges=[],
            ),
        ),
    ]
    return messages


# ── Mock Application ────────────────────────────────────────────────


class MockApplication(QObject):
    """Minimal mock of Application for MainWindow and PreferencesDialog."""

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


# ── Helpers ─────────────────────────────────────────────────────────


def capture_screenshot(widget, filename: str) -> Path:
    """Capture a widget screenshot and save it."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    pixmap = widget.grab()
    pixmap.save(str(path))
    print(f"  Saved: {path}")
    return path


def apply_theme(settings, mode: ThemeMode, qt_app, window=None):
    """Switch theme mode and apply stylesheet."""
    from livestream_list.gui.theme import _stylesheet_cache

    settings.theme_mode = mode
    _stylesheet_cache.clear()  # Force stylesheet regeneration
    ThemeManager.set_settings(settings)
    if window:
        # _apply_theme checks if stylesheet changed, so clear app stylesheet first
        qt_app.setStyleSheet("")
        window._apply_theme()
    else:
        qt_app.setStyleSheet(get_app_stylesheet())
    qt_app.processEvents()


# ── Main ────────────────────────────────────────────────────────────


def main():
    print("Starting screenshot capture...")

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Livestream List (Qt)")

    # Create settings with defaults (no disk I/O)
    settings = Settings()

    # Create monitor and inject sample data
    monitor = StreamMonitor(settings)
    for channel, livestream in create_sample_data():
        key = channel.unique_key
        monitor._channels[key] = channel
        monitor._livestreams[key] = livestream

    # Start with dark theme
    apply_theme(settings, ThemeMode.DARK, qt_app)

    # Create mock app and main window
    mock_app = MockApplication(settings, monitor)

    from livestream_list.gui.main_window import MainWindow

    window = MainWindow(mock_app)
    window._initial_check_complete = True
    window.resize(900, 700)
    window.show()
    window.refresh_stream_list()
    qt_app.processEvents()

    def do_captures():
        print("\nCapturing screenshots...")

        # ── 1. Main window - dark theme ──
        capture_screenshot(window, "main-window-dark.png")

        # ── 2. Main window - light theme ──
        apply_theme(settings, ThemeMode.LIGHT, qt_app, window)
        window.refresh_stream_list()
        qt_app.processEvents()
        capture_screenshot(window, "main-window-light.png")

        # ── 3. Main window - compact mode (dark) ──
        apply_theme(settings, ThemeMode.DARK, qt_app, window)
        settings.ui_style = UIStyle.COMPACT_2
        if window._stream_delegate:
            window._stream_delegate.invalidate_size_cache()
        window.refresh_stream_list()
        qt_app.processEvents()
        capture_screenshot(window, "compact-mode.png")

        # Reset to default style
        settings.ui_style = UIStyle.DEFAULT
        if window._stream_delegate:
            window._stream_delegate.invalidate_size_cache()
        window.refresh_stream_list()
        qt_app.processEvents()

        # ── 4. Chat window (dark theme) ──
        capture_chat_window(settings, qt_app)

        # ── 5. Preferences dialogs (dark theme) ──
        capture_preferences(mock_app, window, qt_app)

        print("\nDone! Screenshots saved to docs/screenshots/")
        qt_app.quit()

    QTimer.singleShot(500, do_captures)
    qt_app.exec()


def capture_chat_window(settings, qt_app):
    """Capture a standalone chat widget with sample messages."""
    from livestream_list.gui.chat.chat_widget import ChatWidget

    # Create a livestream for the chat title banner
    now = datetime.now(timezone.utc)
    channel = Channel("shroud", StreamPlatform.TWITCH, "shroud")
    livestream = Livestream(
        channel=channel, live=True,
        title="CS2 Ranked Grind - Road to Global",
        game="Counter-Strike 2", viewers=42_831,
        start_time=now - timedelta(hours=3, minutes=22),
    )

    chat_settings = BuiltinChatSettings(
        show_timestamps=True,
        timestamp_format="24h",
        show_alternating_rows=True,
    )

    # Wrap ChatWidget in a QMainWindow for proper window chrome
    chat_window = QMainWindow()
    chat_window.setWindowTitle("Chat - shroud")
    chat_window.resize(450, 600)

    chat_widget = ChatWidget(
        channel_key="twitch:shroud",
        livestream=livestream,
        settings=chat_settings,
        authenticated=False,
        parent=chat_window,
    )

    chat_window.setCentralWidget(chat_widget)
    chat_window.show()
    qt_app.processEvents()

    # Switch from "Connecting..." to message list view
    chat_widget.set_connected()

    # Add sample messages
    messages = create_sample_chat_messages()
    chat_widget._model.add_messages(messages)

    # Scroll to bottom
    qt_app.processEvents()
    chat_widget._list_view.scrollToBottom()
    qt_app.processEvents()

    # Apply theme styling to the chat widget
    from livestream_list.gui.theme import get_theme

    theme = get_theme()
    chat_window.setStyleSheet(f"""
        QMainWindow {{ background-color: {theme.chat_bg}; }}
    """)
    qt_app.processEvents()

    capture_screenshot(chat_window, "chat-window.png")
    chat_window.close()


def capture_preferences(mock_app, parent_window, qt_app):
    """Capture preferences dialog tabs."""
    from livestream_list.gui.dialogs.preferences.dialog import PreferencesDialog

    tabs_to_capture = [
        (0, "preferences-general.png"),
        (2, "preferences-chat.png"),
        (3, "preferences-appearance.png"),
    ]

    for tab_index, filename in tabs_to_capture:
        dialog = PreferencesDialog(parent_window, mock_app, initial_tab=tab_index)
        dialog.resize(600, 550)
        dialog.show()
        qt_app.processEvents()
        capture_screenshot(dialog, filename)
        dialog.close()
        qt_app.processEvents()


if __name__ == "__main__":
    main()
