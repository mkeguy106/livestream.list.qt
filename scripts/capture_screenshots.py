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
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow

from livestream_list.chat.emotes.cache import EmoteCache
from livestream_list.chat.emotes.image import ImageRef, ImageSet
from livestream_list.chat.models import ChatBadge, ChatEmote, ChatMessage, ChatUser
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


# ── Emote helpers ───────────────────────────────────────────────────

# Emote definitions: (name, background_color, text_color, emoji_char, animated)
EMOTE_DEFS = {
    "LUL": ("#FFD700", "#000", "\U0001f602", False),
    "Kappa": ("#6441A5", "#FFF", "\U0001f60f", False),
    "PogChamp": ("#FF4500", "#FFF", "\U0001f632", False),
    "catJAM": ("#FF69B4", "#FFF", "\U0001f431", True),   # Animated
    "KEKW": ("#32CD32", "#000", "\U0001f923", False),
    "monkaS": ("#8B4513", "#FFF", "\U0001f630", False),
    "peepoHappy": ("#90EE90", "#000", "\U0001f60a", True),  # Animated
    "OMEGALUL": ("#FF6347", "#FFF", "\U0001f606", True),  # Animated
    "Sadge": ("#4682B4", "#FFF", "\U0001f622", False),
    "EZ": ("#00CED1", "#000", "\U0001f60e", False),
}

ANIMATED_FRAME_COUNT = 8
ANIMATED_FRAME_DELAY_MS = 100


def create_emote_pixmap(
    bg_color: str, text_color: str, emoji: str, hue_shift: int = 0,
) -> QPixmap:
    """Create a small emote-like pixmap with an emoji character."""
    size = 56  # 2x for HiDPI scaling
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))  # Transparent background

    bg = QColor(bg_color)
    if hue_shift:
        h, s, v, a = bg.getHsv()
        bg.setHsv((h + hue_shift) % 360, s, v, a)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Draw rounded background
    painter.setBrush(bg)
    painter.setPen(QColor(0, 0, 0, 0))
    painter.drawRoundedRect(2, 2, size - 4, size - 4, 8, 8)

    # Draw emoji
    font = QFont()
    font.setPixelSize(32)
    painter.setFont(font)
    painter.setPen(QColor(text_color))
    painter.drawText(pixmap.rect(), 0x0084, emoji)  # AlignCenter

    painter.end()
    return pixmap


def setup_emote_cache() -> EmoteCache:
    """Create an EmoteCache pre-populated with static and animated emotes."""
    cache = EmoteCache()

    for name, (bg, fg, emoji, animated) in EMOTE_DEFS.items():
        key = f"emote:7tv:{name}"
        if animated:
            # Create multiple frames with hue rotation for animation effect
            frames = []
            delays = []
            for i in range(ANIMATED_FRAME_COUNT):
                hue_shift = int(360 * i / ANIMATED_FRAME_COUNT)
                frame = create_emote_pixmap(bg, fg, emoji, hue_shift=hue_shift)
                frames.append(frame)
                delays.append(ANIMATED_FRAME_DELAY_MS)
            cache._animated[key] = frames
            cache._frame_delays[key] = delays
            # Also put the first frame as static fallback
            cache._memory[key] = frames[0]
        else:
            pixmap = create_emote_pixmap(bg, fg, emoji)
            cache._memory[key] = pixmap

    return cache


def make_emote(name: str, cache: EmoteCache) -> ChatEmote:
    """Create a ChatEmote with a pre-loaded ImageSet."""
    key = f"emote:7tv:{name}"
    animated = EMOTE_DEFS.get(name, ("", "", "", False))[3]
    image_ref = ImageRef(scale=2, key=key, url="", store=cache, animated=animated)
    image_set = ImageSet({2: image_ref})
    return ChatEmote(
        id=name, name=name, url_template="", provider="7tv",
        image_set=image_set,
    )


def emote_pos(text: str, emote_name: str, cache: EmoteCache):
    """Find emote position in text and return (start, end, ChatEmote) tuple."""
    start = text.find(emote_name)
    if start == -1:
        return None
    return (start, start + len(emote_name), make_emote(emote_name, cache))


# ── Sample chat messages ────────────────────────────────────────────


def create_sample_chat_messages(cache: EmoteCache) -> list[ChatMessage]:
    """Create realistic chat messages for screenshot."""
    now = datetime.now(timezone.utc)
    t = now - timedelta(minutes=5)

    def ts(offset_s: int) -> datetime:
        return t + timedelta(seconds=offset_s)

    def ep(text, name):
        """Shorthand for emote_pos."""
        return emote_pos(text, name, cache)

    msgs = []

    def msg(id, ts_offset, text, user, emote_names=None, **kwargs):
        positions = []
        if emote_names:
            for name in emote_names:
                pos = ep(text, name)
                if pos:
                    positions.append(pos)
        msgs.append(ChatMessage(
            id=id, platform=StreamPlatform.TWITCH, timestamp=ts(ts_offset),
            text=text, user=user, emote_positions=positions, **kwargs,
        ))

    # Users
    u_gamer = ChatUser(
        id="u1", name="gamerfan42", display_name="GamerFan42",
        platform=StreamPlatform.TWITCH, color="#1E90FF", badges=[],
    )
    u_pixel = ChatUser(
        id="u2", name="pixelwarrior", display_name="PixelWarrior",
        platform=StreamPlatform.TWITCH, color="#FF6347",
        badges=[ChatBadge(id="subscriber/12", name="subscriber", image_url="")],
    )
    u_sniper = ChatUser(
        id="u3", name="streamsniper99", display_name="StreamSniper99",
        platform=StreamPlatform.TWITCH, color="#9ACD32", badges=[],
    )
    u_owl = ChatUser(
        id="u4", name="nightowl_tv", display_name="NightOwl_TV",
        platform=StreamPlatform.TWITCH, color="#DDA0DD",
        badges=[
            ChatBadge(id="moderator/1", name="moderator", image_url=""),
            ChatBadge(id="subscriber/24", name="subscriber", image_url=""),
        ],
    )
    u_new = ChatUser(
        id="u5", name="newviewer2024", display_name="NewViewer2024",
        platform=StreamPlatform.TWITCH, color="#FFD700", badges=[],
    )
    u_tips = ChatUser(
        id="u6", name="csgo_tips", display_name="CSGO_Tips",
        platform=StreamPlatform.TWITCH, color="#00CED1", badges=[],
    )
    u_hype = ChatUser(
        id="u7", name="hypemaster", display_name="HypeMaster",
        platform=StreamPlatform.TWITCH, color="#FF4500", badges=[],
    )
    u_clip = ChatUser(
        id="u8", name="clipchamp", display_name="ClipChamp",
        platform=StreamPlatform.TWITCH, color="#20B2AA",
        badges=[ChatBadge(id="subscriber/3", name="subscriber", image_url="")],
    )
    u_map = ChatUser(
        id="u9", name="mapexpert", display_name="MapExpert",
        platform=StreamPlatform.TWITCH, color="#BA55D3", badges=[],
    )
    u_frag = ChatUser(
        id="u10", name="fragmovie", display_name="FragMovie",
        platform=StreamPlatform.TWITCH, color="#FF1493", badges=[],
    )

    # Messages with emotes
    msg("m1", 0, "let's gooo shroud is live! PogChamp", u_gamer, ["PogChamp"])
    msg("m2", 3, "that clutch was insane LUL", u_pixel, ["LUL"])
    msg("m3", 7, "@GamerFan42 yeah he's been playing really well today", u_sniper)
    msg("m4", 12, "GG EZ no re Kappa", u_owl, ["EZ", "Kappa"])
    msg("m5", 15, "first time watching, this is amazing peepoHappy",
        u_new, ["peepoHappy"], is_first_message=True)
    msg("m6", 18, "anyone know what sens he uses?", u_tips)
    msg("m7", 22, "subscribed for 6 months! catJAM", u_pixel, ["catJAM"],
        is_system=True,
        system_text="PixelWarrior subscribed at Tier 1. They've subscribed for 6 months!")
    msg("m8", 25, "LET'S GO SHROUD PogChamp PogChamp", u_hype, ["PogChamp"])
    msg("m9", 30, "400 IQ play right there KEKW", u_gamer, ["KEKW"],
        reply_parent_msg_id="m4", reply_parent_display_name="NightOwl_TV",
        reply_parent_text="GG EZ no re Kappa")
    msg("m10", 34, "can someone clip that? monkaS I missed it", u_clip, ["monkaS"])
    msg("m11", 38, "this map is so good for cs2", u_map)
    msg("m12", 42, "@NewViewer2024 welcome! peepoHappy you picked a great stream",
        u_owl, ["peepoHappy"])
    msg("m13", 46, "ace ace ace!!! OMEGALUL LETS GOOO", u_frag, ["OMEGALUL"])
    msg("m14", 50, "how does he make it look so easy Sadge", u_sniper, ["Sadge"])

    return msgs


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
    window.resize(540, 700)  # 900 * 0.6 = 540 (40% narrower)
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
        window.resize(360, 700)  # 900 * 0.4 = 360 (60% narrower)
        if window._stream_delegate:
            window._stream_delegate.invalidate_size_cache()
        window.refresh_stream_list()
        qt_app.processEvents()
        capture_screenshot(window, "compact-mode.png")

        # Reset to default style and size
        settings.ui_style = UIStyle.DEFAULT
        window.resize(540, 700)
        if window._stream_delegate:
            window._stream_delegate.invalidate_size_cache()
        window.refresh_stream_list()
        qt_app.processEvents()

        # ── 4. Chat window (dark theme) ──
        capture_chat_window(settings, qt_app)

        # ── 5. Animated chat GIF (dark theme) ──
        capture_chat_animated_gif(settings, qt_app)

        # ── 6. mpv player window ──
        capture_mpv_window(qt_app)

        # ── 7. Preferences dialogs (dark theme) ──
        capture_preferences(mock_app, window, qt_app)

        print("\nDone! Screenshots saved to docs/screenshots/")
        qt_app.quit()

    QTimer.singleShot(500, do_captures)
    qt_app.exec()


def capture_chat_window(settings, qt_app):
    """Capture a standalone chat widget with sample messages and emotes."""
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

    # Set up emote cache with pre-populated emotes
    emote_cache = setup_emote_cache()

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

    # Inject emote cache into the delegate so it can render emote pixmaps
    chat_widget._delegate.set_image_store(emote_cache)

    chat_window.setCentralWidget(chat_widget)
    chat_window.show()
    qt_app.processEvents()

    # Switch from "Connecting..." to message list view
    chat_widget.set_connected()

    # Add sample messages with emotes
    messages = create_sample_chat_messages(emote_cache)
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


def capture_mpv_window(qt_app):
    """Generate a fake mpv player window with a streamer-like image."""
    from PySide6.QtGui import QLinearGradient

    width, height = 640, 360
    pixmap = QPixmap(width, height)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Draw a colorful gradient background (simulating a game/stream scene)
    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0.0, QColor("#1a1a2e"))
    gradient.setColorAt(0.25, QColor("#16213e"))
    gradient.setColorAt(0.5, QColor("#0f3460"))
    gradient.setColorAt(0.75, QColor("#533483"))
    gradient.setColorAt(1.0, QColor("#e94560"))
    painter.fillRect(0, 0, width, height, gradient)

    # Add some "game elements" — colored rectangles to simulate a scene
    painter.setOpacity(0.3)
    painter.fillRect(50, 200, 200, 120, QColor("#2ecc71"))
    painter.fillRect(300, 150, 180, 150, QColor("#3498db"))
    painter.fillRect(150, 80, 250, 100, QColor("#e74c3c"))
    painter.setOpacity(1.0)

    # Draw mpv OSD-style overlay text (top-left)
    font = QFont("monospace", 11)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255, 200))

    # Shadow for readability
    shadow_color = QColor(0, 0, 0, 180)
    osd_lines = [
        "Twitch: shroud",
        "480p | 3.2 Mbps",
        "streamlink 7.1.3",
    ]
    y = 16
    for line in osd_lines:
        painter.setPen(shadow_color)
        painter.drawText(13, y + 1, line)
        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawText(12, y, line)
        y += 18

    # mpv bottom bar
    bar_h = 28
    painter.fillRect(0, height - bar_h, width, bar_h, QColor(0, 0, 0, 160))
    painter.setPen(QColor(200, 200, 200))
    font.setPointSize(9)
    painter.setFont(font)
    painter.drawText(10, height - 8, "03:22:15")
    painter.drawText(width - 80, height - 8, "LIVE")

    # Progress line
    painter.fillRect(80, height - 15, width - 170, 2, QColor(100, 100, 100))
    painter.fillRect(80, height - 15, width - 190, 2, QColor("#e94560"))

    painter.end()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "mpv-playback.png"
    pixmap.save(str(path))
    print(f"  Saved: {path}")


def capture_chat_animated_gif(settings, qt_app):
    """Capture animated GIF of chat with animated emotes cycling."""
    from PIL import Image as PILImage

    from livestream_list.gui.chat.chat_widget import ChatWidget

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
        animate_emotes=True,
    )

    emote_cache = setup_emote_cache()

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
    chat_widget._delegate.set_image_store(emote_cache)

    chat_window.setCentralWidget(chat_widget)
    chat_window.show()
    qt_app.processEvents()

    chat_widget.set_connected()
    messages = create_sample_chat_messages(emote_cache)
    chat_widget._model.add_messages(messages)
    qt_app.processEvents()
    chat_widget._list_view.scrollToBottom()
    qt_app.processEvents()

    from livestream_list.gui.theme import get_theme

    theme = get_theme()
    chat_window.setStyleSheet(f"QMainWindow {{ background-color: {theme.chat_bg}; }}")
    qt_app.processEvents()

    # Capture frames at different animation times
    total_duration_ms = ANIMATED_FRAME_COUNT * ANIMATED_FRAME_DELAY_MS  # 800ms
    frame_interval_ms = ANIMATED_FRAME_DELAY_MS  # 100ms per GIF frame
    num_frames = total_duration_ms // frame_interval_ms

    pil_frames = []
    for i in range(num_frames):
        elapsed = i * frame_interval_ms
        chat_widget._delegate.set_animation_frame(elapsed)
        chat_widget._list_view.viewport().update()
        qt_app.processEvents()

        # Grab the window as QPixmap -> convert to PIL Image
        qpixmap = chat_window.grab()
        qimage = qpixmap.toImage().convertToFormat(qpixmap.toImage().Format.Format_RGBA8888)
        pil_img = PILImage.frombytes(
            "RGBA",
            (qimage.width(), qimage.height()),
            qimage.constBits().tobytes(),
        )
        pil_frames.append(pil_img)

    # Save as animated GIF
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    gif_path = OUTPUT_DIR / "chat-animated.gif"
    pil_frames[0].save(
        str(gif_path),
        save_all=True,
        append_images=pil_frames[1:],
        duration=frame_interval_ms,
        loop=0,
    )
    print(f"  Saved: {gif_path}")

    chat_window.close()


if __name__ == "__main__":
    main()
